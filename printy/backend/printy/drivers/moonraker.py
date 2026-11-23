"""Klipper printers through the Moonraker HTTP API (documented, stable)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from .base import DriverError, DriverInfo, OptionField, PrinterDriver, PrinterState, PrinterStatus, SpoolTracking

_STATES = {
    "standby": PrinterState.IDLE,
    "printing": PrinterState.PRINTING,
    "paused": PrinterState.PAUSED,
    "complete": PrinterState.FINISHED,
    "cancelled": PrinterState.IDLE,
    "error": PrinterState.ERROR,
}


class MoonrakerClient:
    def __init__(self, host: str, port: int, api_key: str | None) -> None:
        self.base = f"http://{host}:{port}"
        headers = {"X-Api-Key": api_key} if api_key else {}
        self.http = httpx.AsyncClient(base_url=self.base, headers=headers, timeout=10)
        self._gcodes_path: str | None = None

    async def gcodes_path(self) -> str | None:
        """Absolute path of Moonraker's "gcodes" root on the printer (where uploads land)."""
        if self._gcodes_path is None:
            try:
                roots = await self.get("/server/files/roots")
            except (httpx.HTTPError, ValueError):
                return None
            for root in roots or []:
                if root.get("name") == "gcodes" and root.get("path"):
                    self._gcodes_path = str(root["path"]).rstrip("/")
        return self._gcodes_path

    async def close(self) -> None:
        await self.http.aclose()

    async def get(self, path: str, **params: Any) -> Any:
        r = await self.http.get(path, params=params)
        r.raise_for_status()
        return r.json().get("result")

    async def post(self, path: str, **params: Any) -> Any:
        try:
            r = await self.http.post(path, params=params)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise DriverError(f"Moonraker a refusé la commande ({exc})") from exc
        return r.json().get("result")

    async def spoolman_status(self) -> SpoolTracking | None:
        """Moonraker's [spoolman] module: None when it is not configured."""
        try:
            r = await self.http.get("/server/spoolman/status", timeout=5)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            res = r.json().get("result") or {}
        except (httpx.HTTPError, ValueError):
            return None
        return SpoolTracking(connected=bool(res.get("spoolman_connected")), spool_id=res.get("spool_id"))

    async def set_active_spool(self, spool_id: int | None) -> bool:
        try:
            r = await self.http.post("/server/spoolman/spool_id", json={"spool_id": spool_id}, timeout=5)
            r.raise_for_status()
        except httpx.HTTPError:
            return False
        return True

    async def upload(self, path: Path, remote_name: str) -> None:
        try:
            with path.open("rb") as fh:
                r = await self.http.post(
                    "/server/files/upload",
                    files={"file": (remote_name, fh, "application/octet-stream")},
                    data={"root": "gcodes"},
                    timeout=300,
                )
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise DriverError(f"Envoi du fichier impossible ({exc})") from exc


class MoonrakerDriver(PrinterDriver):
    info = DriverInfo(
        key="moonraker",
        label="Klipper (Moonraker)",
        has_camera=True,
        options=[
            OptionField("port", "Port Moonraker", "number", 7125),
            OptionField("api_key", "Clé API", "password", help="Seulement si Moonraker en demande une."),
        ],
    )

    def __init__(self, host: str, options: dict[str, Any]) -> None:
        super().__init__(host, options)
        self.client = MoonrakerClient(host, int(options.get("port") or 7125), options.get("api_key"))

    async def close(self) -> None:
        await self.client.close()

    async def status(self) -> PrinterStatus:
        try:
            res = await self.client.get(
                "/printer/objects/query",
                print_stats="state,filename,print_duration,total_duration,message",
                extruder="temperature",
                heater_bed="temperature",
                display_status="progress",
            )
        except (httpx.HTTPError, ValueError):
            return PrinterStatus()
        st = res["status"]
        ps = st.get("print_stats", {})
        progress = st.get("display_status", {}).get("progress")
        duration = ps.get("print_duration") or 0
        remaining = int(duration / progress - duration) if progress and progress > 0.01 else None
        return PrinterStatus(
            state=_STATES.get(ps.get("state", ""), PrinterState.BUSY),
            nozzle_temp=st.get("extruder", {}).get("temperature"),
            bed_temp=st.get("heater_bed", {}).get("temperature"),
            progress=progress,
            file=ps.get("filename") or None,
            remaining_s=remaining,
            elapsed_s=int(ps["total_duration"]) if ps.get("total_duration") else None,
            message=ps.get("message") or "",
        )

    async def upload(self, path: Path, remote_name: str) -> None:
        await self.client.upload(path, remote_name)

    async def spool_tracking(self) -> SpoolTracking | None:
        return await self.client.spoolman_status()

    async def set_active_spool(self, spool_id: int | None) -> bool:
        return await self.client.set_active_spool(spool_id)

    async def start(self, remote_name: str) -> None:
        await self.client.post("/printer/print/start", filename=remote_name)

    async def pause(self) -> None:
        await self.client.post("/printer/print/pause")

    async def resume(self) -> None:
        await self.client.post("/printer/print/resume")

    async def cancel(self) -> None:
        await self.client.post("/printer/print/cancel")

    async def snapshot(self) -> bytes | None:
        try:
            cams = await self.client.get("/server/webcams/list")
            cam = next((c for c in cams.get("webcams", []) if c.get("enabled", True)), None)
            if not cam or not cam.get("snapshot_url"):
                return None
            url = urljoin(f"http://{self.host}/", cam["snapshot_url"])
            async with httpx.AsyncClient(timeout=10) as http:
                r = await http.get(url)
                r.raise_for_status()
                return r.content
        except (httpx.HTTPError, ValueError, AttributeError):
            return None
