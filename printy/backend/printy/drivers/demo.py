"""Simulated printer, to try Printy without hardware."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..gcode import read_meta
from .base import DriverError, DriverInfo, OptionField, PrinterDriver, PrinterState, PrinterStatus

ROOM = 21.0


class DemoDriver(PrinterDriver):
    info = DriverInfo(
        key="demo",
        label="Imprimante de démonstration",
        host_label="Adresse",
        host_required=False,
        options=[
            OptionField("speed", "Accélération", "number", 60, help="60 : une heure d’impression dure une minute."),
        ],
        note="Simule une impression sans matériel.",
    )

    def __init__(self, host: str, options: dict[str, Any]) -> None:
        super().__init__(host, options)
        self.speed = float(options.get("speed") or 60)
        self.files: dict[str, int] = {}
        self.file: str | None = None
        self.duration = 0
        self.elapsed = 0.0  # simulated seconds
        self.last_tick = time.monotonic()
        self.state = PrinterState.IDLE
        self.cooling_since: float | None = time.monotonic() - 3600

    def _tick(self) -> None:
        now = time.monotonic()
        dt = now - self.last_tick
        self.last_tick = now
        if self.state == PrinterState.PRINTING:
            self.elapsed += dt * self.speed
            if self.elapsed >= self.duration:
                self.state = PrinterState.FINISHED
                self.cooling_since = now

    def _temps(self) -> tuple[float, float]:
        if self.state in (PrinterState.PRINTING, PrinterState.PAUSED):
            return 220.0, 60.0
        if self.cooling_since is None:
            return ROOM, ROOM
        cool = (time.monotonic() - self.cooling_since) * self.speed / 60  # simulated minutes
        factor = 0.5 ** (cool / 4)  # half-life of 4 simulated minutes
        return ROOM + (220 - ROOM) * factor, ROOM + (60 - ROOM) * factor

    async def status(self) -> PrinterStatus:
        self._tick()
        nozzle, bed = self._temps()
        active = self.state in (PrinterState.PRINTING, PrinterState.PAUSED, PrinterState.FINISHED)
        progress = min(1.0, self.elapsed / self.duration) if active and self.duration else None
        return PrinterStatus(
            state=self.state,
            nozzle_temp=round(nozzle, 1),
            bed_temp=round(bed, 1),
            progress=progress,
            file=self.file if active else None,
            remaining_s=max(0, int(self.duration - self.elapsed)) if active else None,
            elapsed_s=int(self.elapsed) if active else None,
        )

    async def upload(self, path: Path, remote_name: str) -> None:
        self.files[remote_name] = read_meta(path).duration_s or 3600

    async def start(self, remote_name: str) -> None:
        self._tick()
        if self.state in (PrinterState.PRINTING, PrinterState.PAUSED):
            raise DriverError("Une impression est déjà en cours.")
        if remote_name not in self.files:
            raise DriverError("Fichier absent de l’imprimante.")
        self.file, self.duration, self.elapsed = remote_name, self.files[remote_name], 0.0
        self.state = PrinterState.PRINTING
        self.cooling_since = None

    async def pause(self) -> None:
        self._tick()
        if self.state == PrinterState.PRINTING:
            self.state = PrinterState.PAUSED

    async def resume(self) -> None:
        self._tick()
        if self.state == PrinterState.PAUSED:
            self.state = PrinterState.PRINTING

    async def cancel(self) -> None:
        self._tick()
        self.state = PrinterState.IDLE
        self.file = None
        self.cooling_since = time.monotonic()
