"""sensor.printy_* entities in Home Assistant, for dashboards and automations.

Preferred: MQTT discovery through the Mosquitto add-on (credentials come from the
Supervisor): real entities with a unique id, grouped in one device per printer.
Fallback without MQTT: states posted through the REST API (no unique id; they
disappear when Home Assistant restarts and come back within a minute).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import config
from .drivers import PrinterState
from .ha import ha
from .models import Job, JobStatus, Model, Printer, StockItem
from .printers import manager

log = logging.getLogger(__name__)

EVERY_S = 30
PREFIX = "printy"

STATE_LABELS = {
    PrinterState.OFFLINE: "Éteinte",
    PrinterState.IDLE: "Prête",
    PrinterState.PRINTING: "En impression",
    PrinterState.PAUSED: "En pause",
    PrinterState.FINISHED: "Terminée",
    PrinterState.ERROR: "Erreur",
    PrinterState.BUSY: "Occupée",
}


def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "x"


@dataclass
class Entity:
    object_id: str  # entity id without "sensor."
    unique_id: str
    name: str
    state: Any
    attributes: dict[str, Any] = field(default_factory=dict)
    unit: str | None = None
    device_class: str | None = None
    icon: str | None = None
    device: tuple[str, str] = ("printy", "Printy")  # (identifier, name)


class MqttLink:
    """Small wrapper around paho-mqtt (its network loop runs in its own thread)."""

    def __init__(self) -> None:
        self.client: Any = None
        self.connected = False
        self.error = ""
        self.announced: set[str] = set()
        self._retry_at = 0.0

    async def ensure(self) -> bool:
        """True when connected; starts the connection the first time."""
        if self.client is not None:
            return self.connected
        if time.monotonic() < self._retry_at:
            return False
        creds = await self._credentials()
        if creds is None:
            self._retry_at = time.monotonic() + 300  # Mosquitto may be installed later
            return False
        import paho.mqtt.client as mqtt

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="printy-addon")
        if creds.get("username"):
            client.username_pw_set(creds["username"], creds.get("password"))
        client.will_set(f"{PREFIX}/status", "offline", retain=True)

        def on_connect(c, _userdata, _flags, reason, _props=None):  # noqa: ANN001
            self.connected = not reason.is_failure
            self.error = "" if self.connected else f"connexion MQTT refusée ({reason})"
            if self.connected:
                c.publish(f"{PREFIX}/status", "online", retain=True)
                self.announced.clear()  # announce again after a reconnection

        def on_disconnect(*_args: Any) -> None:
            self.connected = False

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.connect_async(creds["host"], int(creds.get("port", 1883)))
        client.loop_start()
        self.client = client
        return False

    async def _credentials(self) -> dict[str, Any] | None:
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                r = await http.get(
                    "http://supervisor/services/mqtt",
                    headers={"Authorization": f"Bearer {config.supervisor_token}"},
                )
            if r.status_code != 200:
                self.error = "pas de broker MQTT : installe l’add-on Mosquitto pour des entités complètes"
                return None
            return r.json().get("data") or None
        except httpx.HTTPError as exc:
            self.error = f"Supervisor injoignable ({exc.__class__.__name__})"
            return None

    def publish(self, e: Entity) -> None:
        base = f"{PREFIX}/{e.object_id}"
        if e.unique_id not in self.announced:
            cfg: dict[str, Any] = {
                "name": e.name,
                "unique_id": e.unique_id,
                "default_entity_id": f"sensor.{e.object_id}",
                "state_topic": f"{base}/state",
                "json_attributes_topic": f"{base}/attributes",
                "availability_topic": f"{PREFIX}/status",
                "device": {"identifiers": [e.device[0]], "name": e.device[1], "manufacturer": "Printy"},
            }
            if e.unit:
                cfg["unit_of_measurement"] = e.unit
            if e.device_class:
                cfg["device_class"] = e.device_class
            if e.icon:
                cfg["icon"] = e.icon
            self.client.publish(f"homeassistant/sensor/{PREFIX}/{e.unique_id}/config", json.dumps(cfg), retain=True)
            self.announced.add(e.unique_id)
        state = "None" if e.state is None else str(e.state)
        self.client.publish(f"{base}/state", state, retain=True)
        self.client.publish(f"{base}/attributes", json.dumps(e.attributes, default=str), retain=True)

    def announce_photo(self, p: Printer) -> str:
        """camera.printy_<printer>_photo: the last bed photo taken by Printy."""
        object_id = f"printy_{slug(p.name)}_photo"
        unique_id = f"printy_printer{p.id}_photo"
        if unique_id not in self.announced:
            cfg = {
                "name": "Photo du plateau",
                "unique_id": unique_id,
                "default_entity_id": f"camera.{object_id}",
                "topic": f"{PREFIX}/{object_id}/image",
                "availability_topic": f"{PREFIX}/status",
                "device": {"identifiers": [f"printy_printer_{p.id}"], "name": f"Printy {p.name}", "manufacturer": "Printy"},
            }
            self.client.publish(f"homeassistant/camera/{PREFIX}/{unique_id}/config", json.dumps(cfg), retain=True)
            self.announced.add(unique_id)
        return object_id

    def publish_photo(self, p: Printer, data: bytes) -> str:
        object_id = self.announce_photo(p)
        self.client.publish(f"{PREFIX}/{object_id}/image", data, retain=True).wait_for_publish(5)
        return f"camera.{object_id}"


class EntityPublisher:
    def __init__(self) -> None:
        self._last_run = 0.0
        self.mqtt = MqttLink()
        self.status: dict[str, Any] = {"mode": "off", "count": 0, "last_ok": None, "error": ""}

    async def run(self, s: Session, opts: dict[str, Any]) -> None:
        if not ha.enabled or not opts.get("ha_entities", True):
            self.status.update(mode="off", error="")
            return
        if time.monotonic() - self._last_run < EVERY_S:
            return
        self._last_run = time.monotonic()
        try:
            entities = self.collect(s)
            if await self.mqtt.ensure():
                for e in entities:
                    self.mqtt.publish(e)
                for p in s.scalars(select(Printer)):
                    if p.camera_source != "none":
                        self.mqtt.announce_photo(p)  # exists before the first notification
                self.status.update(mode="mqtt", error="")
            else:
                for e in entities:
                    attrs = {**e.attributes, "friendly_name": f"{e.device[1]} {e.name}"}
                    if e.unit:
                        attrs["unit_of_measurement"] = e.unit
                    if e.device_class:
                        attrs["device_class"] = e.device_class
                    if e.icon:
                        attrs["icon"] = e.icon
                    await ha.set_state(f"sensor.{e.object_id}", "unknown" if e.state is None else e.state, attrs)
                self.status.update(mode="rest", error=self.mqtt.error)
            self.status.update(count=len(entities), last_ok=time.time())
        except Exception as exc:  # noqa: BLE001
            self.status["error"] = f"{exc.__class__.__name__} : {exc}"
            log.warning("Home Assistant entities not published: %s", exc)

    async def photo_url(self, p: Printer, data: bytes) -> str | None:
        """Publishes a bed photo as a camera entity; its URL works in every notification
        (the Home Assistant app fetches it with its own login)."""
        if not ha.enabled or not await self.mqtt.ensure():
            return None
        try:
            entity = await asyncio.to_thread(self.mqtt.publish_photo, p, data)
        except Exception as exc:  # noqa: BLE001
            log.warning("bed photo not published: %s", exc)
            return None
        return f"/api/camera_proxy/{entity}"

    def collect(self, s: Session) -> list[Entity]:
        out: list[Entity] = []
        for p in s.scalars(select(Printer)):
            sl = slug(p.name)
            dev = (f"printy_printer_{p.id}", f"Printy {p.name}")
            uid = f"printy_printer{p.id}"
            st = manager.status(p.id)
            window = manager.print_window(p.id)
            nxt = s.scalar(
                select(Job)
                .where(Job.printer_id == p.id, Job.status == JobStatus.SCHEDULED.value)
                .order_by(Job.start_at)
                .limit(1)
            )
            out += [
                Entity(f"printy_{sl}_etat", f"{uid}_etat", "État", STATE_LABELS.get(st.state, str(st.state)),
                       {"fichier": st.file}, icon="mdi:printer-3d", device=dev),
                Entity(f"printy_{sl}_progression", f"{uid}_progression", "Progression",
                       round((st.progress or 0) * 100) if st.active else 0, unit="%", icon="mdi:progress-clock",
                       device=dev),
                Entity(f"printy_{sl}_temps_restant", f"{uid}_temps_restant", "Temps restant",
                       round(st.remaining_s / 60) if st.active and st.remaining_s is not None else 0,
                       unit="min", device_class="duration", device=dev),
                Entity(f"printy_{sl}_fin_impression", f"{uid}_fin_impression", "Fin d’impression",
                       window[1].isoformat() if window else None, device_class="timestamp", device=dev),
                Entity(f"printy_{sl}_prochaine_impression", f"{uid}_prochaine_impression", "Prochaine impression",
                       nxt.start_at.isoformat() if nxt else None,
                       {"modele": nxt.sliced_file.model.name if nxt else None}, device_class="timestamp", device=dev),
            ]

        attention = s.scalars(
            select(Job).where(Job.status.in_([JobStatus.AWAITING_CONFIRMATION.value, JobStatus.NEEDS_ATTENTION.value]))
        ).all()
        out.append(Entity("printy_a_traiter", "printy_a_traiter", "À traiter", len(attention),
                          {"impressions": [j.sliced_file.model.name for j in attention]}, icon="mdi:bell-alert"))

        low: list[str] = []
        stock: dict[str, int] = {}
        for m in s.scalars(select(Model)):
            qty = sum(i.quantity for i in s.scalars(select(StockItem).where(StockItem.model_id == m.id)))
            if qty:
                stock[m.name] = qty
            if m.stock_alert is not None and qty < m.stock_alert:
                low.append(m.name)
        out.append(Entity("printy_stock", "printy_stock", "Stock", sum(stock.values()), {"produits": stock},
                          unit="pièces", icon="mdi:package-variant"))
        out.append(Entity("printy_stock_bas", "printy_stock_bas", "Stock bas", len(low), {"produits": low},
                          icon="mdi:package-variant-remove"))
        return out


publisher = EntityPublisher()
