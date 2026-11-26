"""Common interface for printer drivers.

Adding a brand means adding one subclass of PrinterDriver and registering it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar


class PrinterState(StrEnum):
    OFFLINE = "offline"  # unreachable (often: powered off)
    IDLE = "idle"
    PRINTING = "printing"
    PAUSED = "paused"
    FINISHED = "finished"  # a print just ended, bed not cleared yet
    ERROR = "error"
    BUSY = "busy"  # homing, heating, loading...


@dataclass
class PrinterStatus:
    state: PrinterState = PrinterState.OFFLINE
    nozzle_temp: float | None = None
    bed_temp: float | None = None
    progress: float | None = None  # 0..1
    file: str | None = None
    remaining_s: int | None = None
    elapsed_s: int | None = None  # time since the print started, pauses included
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def active(self) -> bool:
        return self.state in (PrinterState.PRINTING, PrinterState.PAUSED)

    def estimated_elapsed(self) -> int | None:
        if self.elapsed_s is not None:
            return self.elapsed_s
        p, left = self.progress, self.remaining_s
        if p is not None and left is not None and 0.01 < p < 1:
            return int(left * p / (1 - p))
        return None

    @property
    def is_cool(self) -> bool:
        return all(t is None or t < 45 for t in (self.nozzle_temp, self.bed_temp))


@dataclass
class OptionField:
    """A driver-specific setting shown in the printer form."""

    key: str
    label: str
    kind: str = "text"  # text | password | number
    default: Any = None
    help: str = ""
    required: bool = False


@dataclass
class DriverInfo:
    key: str
    label: str
    host_label: str = "Adresse IP"
    host_required: bool = True
    has_camera: bool = False
    options: list[OptionField] = field(default_factory=list)
    note: str = ""


@dataclass
class SpoolTracking:
    """Filament tracking done by the printer itself (Moonraker's Spoolman module)."""

    connected: bool
    spool_id: int | None


class DriverError(Exception):
    """Raised with a message meant for the user."""


class PrinterDriver(ABC):
    info: ClassVar[DriverInfo]

    def __init__(self, host: str, options: dict[str, Any]) -> None:
        self.host = host.strip()
        self.options = options

    async def connect(self) -> None:  # noqa: B027
        """Start background connections. Must never raise."""

    async def close(self) -> None:  # noqa: B027
        """Stop background connections."""

    @abstractmethod
    async def status(self) -> PrinterStatus: ...

    async def fresh_status(self) -> PrinterStatus:
        """Status just asked to the printer, never a cached one (used before cutting power).
        OFFLINE when the printer does not answer now."""
        return await self.status()

    @abstractmethod
    async def upload(self, path: Path, remote_name: str) -> None: ...

    @abstractmethod
    async def start(self, remote_name: str) -> None: ...

    @abstractmethod
    async def pause(self) -> None: ...

    @abstractmethod
    async def resume(self) -> None: ...

    @abstractmethod
    async def cancel(self) -> None: ...

    async def snapshot(self) -> bytes | None:
        return None

    def raw_state(self) -> str:
        """What the printer itself last reported, for the diagnosis of a failed start."""
        return ""

    async def spool_tracking(self) -> SpoolTracking | None:
        """None when the printer does not report filament use to Spoolman itself."""
        return None

    async def set_active_spool(self, spool_id: int | None) -> bool:
        """Tells the printer which spool is loaded; False when not supported."""
        return False
