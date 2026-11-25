"""Filament consumption of a print, deducted from its spool in Spoolman."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from .job_ops import add_event
from .models import Job
from .printers import manager
from .spoolman import SpoolmanError, spoolman

log = logging.getLogger(__name__)


def record(job: Job, share: float) -> None:
    """The print ended after `share` (0..1) of its file: the filament it used."""
    grams = job.sliced_file.filament_g
    if grams and job.spool_id is not None and share > 0:
        job.filament_used_g = round(grams * min(1.0, share), 2)
        if manager.tracked(job.printer_id):
            # Moonraker already reported the real extruded length to Spoolman
            job.spool_reported = True
            add_event(job, "spool", "Consommation déduite par Klipper (Moonraker) dans Spoolman.")


async def report(s: Session) -> None:
    """Deducts used filament in Spoolman; retried on later ticks if Spoolman is down."""
    if not spoolman.enabled:
        return
    pending = s.scalars(
        select(Job).where(
            Job.spool_id.is_not(None),
            Job.filament_used_g.is_not(None),
            Job.spool_reported.is_(False),
        )
    ).all()
    for job in pending:
        try:
            await spoolman.use(job.spool_id, job.filament_used_g)
        except SpoolmanError as exc:
            log.warning("spool usage for job %s not reported: %s", job.id, exc)
            return
        job.spool_reported = True
        add_event(job, "spool", f"{job.filament_used_g:g} g déduits de la bobine dans Spoolman.")
        s.commit()
