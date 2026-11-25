"""Automatic power off: the plug is cut once a print launched by Printy has cooled down."""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import utcnow
from .drivers import PrinterState
from .ha import ha
from .job_ops import add_event
from .models import ACTIVE_STATUSES, Job, JobStatus, Printer
from .printers import manager

log = logging.getLogger(__name__)


async def maybe_power_off(s: Session, printer: Printer, opts: dict) -> None:
    """Cuts the plug after a print launched by Printy, never after one started elsewhere.

    The cached status only filters out the obvious cases; the decision itself rests on
    the plug's state and the printer's status read again right before cutting."""
    if not printer.power_off_pending:
        return
    if not printer.switch_entity:
        printer.power_off_pending = False
        return
    now = utcnow()
    busy = s.scalar(
        select(Job.id)
        .where(Job.printer_id == printer.id)
        .where(Job.status.in_([st.value for st in ACTIVE_STATUSES]))
        .limit(1)
    )
    if busy:
        return
    window = timedelta(minutes=int(opts["keep_on_window_min"]))
    upcoming = s.scalars(
        select(Job).where(Job.printer_id == printer.id, Job.status == JobStatus.SCHEDULED.value)
    ).all()
    if any(j.start_at - timedelta(minutes=printer.power_on_lead_min) <= now + window for j in upcoming):
        return
    if not _can_cut(printer, manager.status(printer.id)):
        return

    # confirmation from live data, not from what was last seen
    plug = await ha.switch_state(printer.switch_entity, refresh=True)
    if plug is None:
        return  # plug unavailable in Home Assistant: try again later
    if plug is False:
        printer.power_off_pending = False  # already cut (by hand or by an automation)
        return
    if not _can_cut(printer, await manager.fresh_status(printer.id)):
        return
    await ha.set_power(printer.switch_entity, False)
    printer.power_off_pending = False
    log.info("%s cooled down, power off", printer.name)
    last = s.scalar(
        select(Job).where(Job.printer_id == printer.id, Job.finished_at.is_not(None)).order_by(Job.finished_at.desc()).limit(1)
    )
    if last is not None:
        add_event(last, "power_off", "Imprimante refroidie, prise coupée.")


def _can_cut(printer: Printer, status) -> bool:  # noqa: ANN001
    """The printer answers, does nothing and has cooled down."""
    if status.state in (PrinterState.PRINTING, PrinterState.PAUSED):
        # no Printy print is running (checked before): a print started elsewhere
        # took over the printer, its end is not Printy's business
        printer.power_off_pending = False
        log.info("%s: print started outside Printy, no automatic power off", printer.name)
        return False
    if status.state not in (PrinterState.IDLE, PrinterState.FINISHED, PrinterState.ERROR):
        return False  # unreachable or busy: its real state is unknown
    temps = [t for t in (status.nozzle_temp, status.bed_temp) if t is not None]
    return bool(temps) and all(t < printer.cooldown_temp for t in temps)
