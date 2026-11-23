"""Creality K-series (K1, K2) on stock Creality OS.

Status and commands go through the local WebSocket on port 9999, the same channel
Creality Print and the community HA integration use. The protocol is not documented
by Creality: field names and state codes below come from community work and must be
checked on a real K2 (see docs/SPEC.md, "à valider").

File upload goes through Moonraker’s HTTP API when the printer exposes it.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import websockets

from .base import DriverError, DriverInfo, OptionField, PrinterDriver, PrinterState, PrinterStatus, SpoolTracking
from .moonraker import MoonrakerClient

log = logging.getLogger(__name__)

GCODE_DIR = "/usr/data/printer_data/gcodes"

# Community-documented "state" codes on Creality OS
_STATES = {
    0: PrinterState.IDLE,
    1: PrinterState.PRINTING,
    2: PrinterState.FINISHED,
    3: PrinterState.ERROR,
    4: PrinterState.IDLE,  # stopped by user
    5: PrinterState.PAUSED,
}


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class CrealityDriver(PrinterDriver):
    info = DriverInfo(
        key="creality_k2",
        label="Creality K2 / K1 (Creality OS)",
        has_camera=True,
        options=[
            OptionField(
                "snapshot_url",
                "URL d’instantané caméra",
                default="http://{host}:8080/?action=snapshot",
                help="Laisse la valeur par défaut sauf si ta caméra répond ailleurs.",
            ),
            OptionField("moonraker_port", "Port d’envoi des fichiers", "number", 7125),
        ],
        note="Protocole Creality non documenté : à valider sur ta machine.",
    )

    def __init__(self, host: str, options: dict[str, Any]) -> None:
        super().__init__(host, options)
        self._state: dict[str, Any] = {}
        self._ws: Any = None
        self._task: asyncio.Task[None] | None = None
        self._connected = asyncio.Event()
        self._uploader = MoonrakerClient(host, int(options.get("moonraker_port") or 7125), None)

    # -- connection ---------------------------------------------------------

    async def connect(self) -> None:
        if self.host and self._task is None:
            self._task = asyncio.create_task(self._run(), name=f"creality-{self.host}")

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        await self._uploader.close()

    async def _run(self) -> None:
        url = f"ws://{self.host}:9999"
        while True:
            try:
                async with websockets.connect(url, open_timeout=5, ping_interval=None) as ws:
                    self._ws = ws
                    self._connected.set()
                    await self._send({"method": "get", "params": {"reqPrinterPara": 1}})
                    async for raw in ws:
                        await self._on_message(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep reconnecting whatever happens
                log.debug("creality %s: %s", self.host, exc)
            self._ws = None
            self._connected.clear()
            self._state.clear()
            await asyncio.sleep(5)

    async def _on_message(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(msg, dict):
            return
        if msg.get("ModeCode") == "heart_beat":
            await self._ws.send("ok")
            return
        self._state.update(msg)

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._ws is None:
            raise DriverError("Imprimante injoignable.")
        await self._ws.send(json.dumps(payload))

    # -- driver API ---------------------------------------------------------

    async def status(self) -> PrinterStatus:
        if not self._connected.is_set() or not self._state:
            return PrinterStatus()
        s = self._state
        code = s.get("state")
        state = _STATES.get(int(code), PrinterState.BUSY) if _num(code) is not None else PrinterState.IDLE
        progress = _num(s.get("printProgress"))
        left = _num(s.get("printLeftTime"))
        spent = _num(s.get("printJobTime"))
        return PrinterStatus(
            state=state,
            nozzle_temp=_num(s.get("nozzleTemp")),
            bed_temp=_num(s.get("bedTemp0")),
            progress=progress / 100 if progress is not None else None,
            file=(s.get("printFileName") or "").rsplit("/", 1)[-1] or None,
            remaining_s=int(left) if left is not None else None,
            elapsed_s=int(spent) if spent else None,
        )

    async def upload(self, path: Path, remote_name: str) -> None:
        await self._uploader.upload(path, remote_name)

    # Moonraker on the K2 may run Spoolman's module: it then tracks filament itself
    async def spool_tracking(self) -> SpoolTracking | None:
        return await self._uploader.spoolman_status()

    async def set_active_spool(self, spool_id: int | None) -> bool:
        return await self._uploader.set_active_spool(spool_id)

    async def start(self, remote_name: str) -> None:
        await self._send(
            {"method": "set", "params": {"opGcodeFile": f"printprt:{GCODE_DIR}/{remote_name}"}}
        )

    async def pause(self) -> None:
        await self._send({"method": "set", "params": {"pause": 1}})

    async def resume(self) -> None:
        await self._send({"method": "set", "params": {"pause": 0}})

    async def cancel(self) -> None:
        await self._send({"method": "set", "params": {"stop": 1}})

    async def snapshot(self) -> bytes | None:
        template = self.options.get("snapshot_url") or "http://{host}:8080/?action=snapshot"
        try:
            async with httpx.AsyncClient(timeout=8) as http:
                r = await http.get(template.replace("{host}", self.host))
                r.raise_for_status()
                return r.content or None
        except httpx.HTTPError:
            return None
