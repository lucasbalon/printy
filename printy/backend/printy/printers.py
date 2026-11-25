"""Live printer runtime: one driver instance per printer and a status cache."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .db import SessionLocal, utcnow
from .drivers import DRIVERS, PrinterDriver, PrinterState, PrinterStatus
from .drivers.base import SpoolTracking
from .ha import ha
from .models import Printer

log = logging.getLogger(__name__)


@dataclass
class Live:
    driver: PrinterDriver
    signature: tuple
    status: PrinterStatus = field(default_factory=PrinterStatus)
    power_on: bool | None = None
    power_w: float | None = None
    tracking: SpoolTracking | None = None  # filament tracked by the printer (Moonraker + Spoolman)
    updated_at: float = 0


def _signature(p: Printer) -> tuple:
    return (p.driver, p.host, tuple(sorted((p.options or {}).items())))


class PrinterManager:
    def __init__(self) -> None:
        self.live: dict[int, Live] = {}
        self._lock = asyncio.Lock()
        # spool chosen in Printy, to push to the printer when it is reachable
        self._pending_spool: dict[int, int | None] = {}

    def want_spool(self, printer_id: int, spool_id: int | None) -> None:
        self._pending_spool[printer_id] = spool_id

    def spool_pushed(self, printer_id: int) -> None:
        self._pending_spool.pop(printer_id, None)

    def print_window(self, printer_id: int) -> tuple[datetime, datetime | None] | None:
        """Start and predicted end of the print running now, from the printer's own figures.
        The end is None while the printer does not know it yet (heating, calibration)."""
        st = self.status(printer_id)
        if not st.active:
            return None
        now = utcnow()
        elapsed = st.estimated_elapsed()
        start = now - timedelta(seconds=elapsed) if elapsed is not None else now
        end = now + timedelta(seconds=st.remaining_s) if st.remaining_s is not None else None
        return start, end

    def tracked(self, printer_id: int) -> bool:
        live = self.live.get(printer_id)
        return live is not None and live.tracking is not None

    async def sync(self) -> None:
        """Create, replace or drop drivers to match the database."""
        async with self._lock:
            with SessionLocal() as s:
                printers = {p.id: p for p in s.query(Printer).all()}
            for pid in list(self.live):
                if pid not in printers or self.live[pid].signature != _signature(printers[pid]):
                    await self.live.pop(pid).driver.close()
            for pid, p in printers.items():
                if pid in self.live or p.driver not in DRIVERS:
                    continue
                driver = DRIVERS[p.driver](p.host, p.options or {})
                await driver.connect()
                self.live[pid] = Live(driver=driver, signature=_signature(p))

    def driver(self, printer_id: int) -> PrinterDriver | None:
        live = self.live.get(printer_id)
        return live.driver if live else None

    def status(self, printer_id: int) -> PrinterStatus:
        live = self.live.get(printer_id)
        return live.status if live else PrinterStatus()

    async def refresh(self, printer: Printer) -> Live | None:
        live = self.live.get(printer.id)
        if live is None:
            return None
        try:
            live.status = await asyncio.wait_for(live.driver.status(), timeout=12)
        except Exception as exc:  # noqa: BLE001
            log.debug("status %s: %s", printer.name, exc)
            live.status = PrinterStatus(state=PrinterState.OFFLINE)
        if live.status.state != PrinterState.OFFLINE:
            await self._sync_spool(printer, live)
        if printer.switch_entity:
            live.power_on = await ha.is_on(printer.switch_entity)
        if printer.power_entity:
            try:
                live.power_w = float(await ha.state(printer.power_entity) or "nan")
            except ValueError:
                live.power_w = None
        live.updated_at = utcnow().timestamp()
        return live

    async def fresh_status(self, printer_id: int) -> PrinterStatus:
        """Status asked to the printer right now (OFFLINE if it does not answer)."""
        live = self.live.get(printer_id)
        if live is None:
            return PrinterStatus()
        try:
            status = await asyncio.wait_for(live.driver.fresh_status(), timeout=12)
        except Exception as exc:  # noqa: BLE001
            log.debug("fresh status %s: %s", printer_id, exc)
            status = PrinterStatus(state=PrinterState.OFFLINE)
        live.status = status
        return status

    async def _sync_spool(self, printer: Printer, live: Live) -> None:
        """Keeps Printy's loaded spool and the printer's active spool (Moonraker) in step.

        A change made in Printy is pushed; otherwise the printer is the reference
        (the spool may have been changed from Fluidd/Mainsail or the touch screen)."""
        try:
            live.tracking = await live.driver.spool_tracking()
        except Exception:  # noqa: BLE001
            live.tracking = None
        if live.tracking is None:
            return
        if printer.id in self._pending_spool:
            wanted = self._pending_spool[printer.id]
            if await live.driver.set_active_spool(wanted):
                self._pending_spool.pop(printer.id, None)
                live.tracking.spool_id = wanted
            return
        if live.tracking.spool_id != printer.loaded_spool_id:
            with SessionLocal() as s:
                p = s.get(Printer, printer.id)
                if p is not None:
                    p.loaded_spool_id = live.tracking.spool_id
                    s.commit()
            printer.loaded_spool_id = live.tracking.spool_id
            log.info("%s: active spool from the printer: %s", printer.name, live.tracking.spool_id)

    async def refresh_all(self) -> None:
        with SessionLocal() as s:
            printers = s.query(Printer).all()
        await asyncio.gather(*(self.refresh(p) for p in printers))

    async def close(self) -> None:
        for live in self.live.values():
            await live.driver.close()
        self.live.clear()


manager = PrinterManager()
