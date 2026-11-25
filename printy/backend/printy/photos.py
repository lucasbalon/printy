"""Bed photos: taken from the printer or a Home Assistant camera, kept per job and
published for the notifications."""

from __future__ import annotations

import asyncio
import logging
import time

from .config import config
from .drivers import PrinterState
from .ha import ha
from .ha_entities import publisher
from .job_ops import SNAPSHOT_DIR
from .models import Job, Printer
from .printers import manager

log = logging.getLogger(__name__)


async def photo(printer: Printer) -> bytes | None:
    """A bed photo now, or None (no camera, printer camera off, no answer)."""
    try:
        if printer.camera_source == "ha" and printer.camera_entity:
            return await asyncio.wait_for(ha.camera_snapshot(printer.camera_entity), timeout=15)
        if printer.camera_source == "printer" and (driver := manager.driver(printer.id)):
            if manager.status(printer.id).state == PrinterState.OFFLINE:
                return None
            return await asyncio.wait_for(driver.snapshot(), timeout=15)
    except Exception as exc:  # noqa: BLE001
        log.debug("photo %s: %s", printer.name, exc)
    return None


async def image_url(printer: Printer, data: bytes | None, name: str) -> str | None:
    """Where a notification finds the photo. Without a photo, no image at all: a link
    that does not load shows an error on the phone."""
    if not data:
        return None
    if url := await publisher.photo_url(printer, data):
        return url  # camera entity published through MQTT
    if config.ha_www_dir:
        (config.ha_www_dir / f"{name}.jpg").write_bytes(data)
        return f"/local/printy/{name}.jpg?v={int(time.time())}"
    return None


async def capture(job: Job, data: bytes | None = None) -> str | None:
    """Takes (or uses) a bed photo for the job; returns the notification image."""
    if data is None:
        data = await photo(job.printer)
    if data:
        (SNAPSHOT_DIR / f"job-{job.id}.jpg").write_bytes(data)
    return await image_url(job.printer, data, f"job-{job.id}")
