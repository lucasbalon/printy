from __future__ import annotations

from .base import DriverError, DriverInfo, PrinterDriver, PrinterState, PrinterStatus
from .creality import CrealityDriver
from .demo import DemoDriver
from .moonraker import MoonrakerDriver

DRIVERS: dict[str, type[PrinterDriver]] = {
    cls.info.key: cls for cls in (CrealityDriver, MoonrakerDriver, DemoDriver)
}

__all__ = [
    "DRIVERS",
    "DriverError",
    "DriverInfo",
    "PrinterDriver",
    "PrinterState",
    "PrinterStatus",
]
