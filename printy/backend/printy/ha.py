"""Home Assistant access through the Supervisor proxy.

Outside Home Assistant (development) a small in-memory stand-in is used so the
whole flow can be exercised without a real installation.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
import websockets

from .config import config

log = logging.getLogger(__name__)

ActionHandler = Callable[[str], Awaitable[None]]


@dataclass
class NotifyAction:
    action: str
    title: str
    destructive: bool = False


class HomeAssistant:
    def __init__(self) -> None:
        self.enabled = config.in_home_assistant
        self._http = httpx.AsyncClient(
            base_url=f"{config.ha_url}/api",
            headers={"Authorization": f"Bearer {config.supervisor_token}"} if self.enabled else {},
            timeout=15,
        )
        self._listener: asyncio.Task[None] | None = None
        self._dev_switches: dict[str, bool] = {}

    async def close(self) -> None:
        if self._listener:
            self._listener.cancel()
        await self._http.aclose()

    # -- entities -------------------------------------------------------------

    async def states(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return [
                {"entity_id": "switch.prise_k2", "state": "off", "attributes": {"friendly_name": "Prise K2"}},
                {"entity_id": "camera.atelier", "state": "idle", "attributes": {"friendly_name": "Caméra atelier"}},
                {
                    "entity_id": "sensor.prise_k2_puissance",
                    "state": "4.2",
                    "attributes": {"friendly_name": "Prise K2 puissance", "device_class": "power", "unit_of_measurement": "W"},
                },
            ]
        r = await self._http.get("/states")
        r.raise_for_status()
        return r.json()

    async def notify_services(self) -> list[str]:
        if not self.enabled:
            return ["notify.mobile_app_telephone"]
        r = await self._http.get("/services")
        r.raise_for_status()
        for domain in r.json():
            if domain["domain"] == "notify":
                return sorted(f"notify.{name}" for name in domain["services"])
        return []

    async def is_on(self, entity_id: str) -> bool | None:
        if not self.enabled:
            return self._dev_switches.get(entity_id, False)
        try:
            r = await self._http.get(f"/states/{entity_id}")
            r.raise_for_status()
        except httpx.HTTPError:
            return None
        return r.json().get("state") == "on"

    async def switch_state(self, entity_id: str, *, refresh: bool = False) -> bool | None:
        """True / False only when Home Assistant knows the switch's state; None when it is
        unavailable or unknown. With refresh, asks the integration to poll the device first."""
        if not self.enabled:
            return self._dev_switches.get(entity_id, False)
        if refresh:
            try:
                await self._http.post("/services/homeassistant/update_entity", json={"entity_id": entity_id})
            except httpx.HTTPError:
                pass  # not every integration can be polled: the pushed state is then the reference
        state = await self.state(entity_id)
        return {"on": True, "off": False}.get(state or "")

    async def state(self, entity_id: str) -> str | None:
        if not self.enabled:
            return "4.2"
        try:
            r = await self._http.get(f"/states/{entity_id}")
            r.raise_for_status()
        except httpx.HTTPError:
            return None
        return r.json().get("state")

    async def set_state(self, entity_id: str, state: Any, attributes: dict[str, Any]) -> None:
        """Creates or updates an entity owned by Printy (sensor.printy_*)."""
        if not self.enabled:
            return
        r = await self._http.post(f"/states/{entity_id}", json={"state": state, "attributes": attributes})
        r.raise_for_status()

    async def set_power(self, entity_id: str, on: bool) -> None:
        log.info("power %s -> %s", entity_id, "on" if on else "off")
        if not self.enabled:
            self._dev_switches[entity_id] = on
            return
        service = "turn_on" if on else "turn_off"
        r = await self._http.post(f"/services/homeassistant/{service}", json={"entity_id": entity_id})
        r.raise_for_status()

    async def camera_snapshot(self, entity_id: str) -> bytes | None:
        if not self.enabled:
            return None
        try:
            r = await self._http.get(f"/camera_proxy/{entity_id}")
            r.raise_for_status()
        except httpx.HTTPError:
            return None
        return r.content

    # -- notifications ------------------------------------------------------

    async def notify(
        self,
        targets: list[str],
        title: str,
        message: str,
        *,
        image: str | None = None,
        actions: list[NotifyAction] | None = None,
        tag: str | None = None,
    ) -> None:
        data: dict[str, Any] = {}
        if image:
            data["image"] = image
        if actions:
            data["actions"] = [
                {"action": a.action, "title": a.title, **({"destructive": True} if a.destructive else {})}
                for a in actions
            ]
        if tag:
            data["tag"] = tag
        log.info("notify %s: %s / %s %s", targets, title, message, data)
        if not self.enabled:
            return
        for target in targets:
            domain, _, service = target.partition(".")
            try:
                r = await self._http.post(
                    f"/services/{domain}/{service}",
                    json={"title": title, "message": message, "data": data},
                )
                r.raise_for_status()
            except httpx.HTTPError as exc:
                log.warning("notification to %s failed: %s", target, exc)

    def listen_actions(self, handler: ActionHandler) -> None:
        """Calls handler(action) for each tapped notification button."""
        if self.enabled and self._listener is None:
            self._listener = asyncio.create_task(self._listen(handler), name="ha-events")

    async def _listen(self, handler: ActionHandler) -> None:
        url = config.ha_url.replace("http", "ws", 1) + "/websocket"
        while True:
            try:
                async with websockets.connect(url, max_size=None) as ws:
                    await ws.recv()  # auth_required
                    await ws.send(json.dumps({"type": "auth", "access_token": config.supervisor_token}))
                    auth = json.loads(await ws.recv())
                    if auth.get("type") != "auth_ok":
                        raise RuntimeError(f"auth failed: {auth}")
                    await ws.send(
                        json.dumps(
                            {"id": 1, "type": "subscribe_events", "event_type": "mobile_app_notification_action"}
                        )
                    )
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("type") != "event":
                            continue
                        action = msg["event"]["data"].get("action", "")
                        if action.startswith("PRINTY_"):
                            try:
                                await handler(action)
                            except Exception:  # noqa: BLE001
                                log.exception("notification action %s failed", action)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("HA event listener: %s, retrying", exc)
                await asyncio.sleep(10)


ha = HomeAssistant()
