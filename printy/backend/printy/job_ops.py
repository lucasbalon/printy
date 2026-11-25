"""Job operations shared by the scheduler, the API and the notification handler."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from .config import config
from .db import utcnow
from .models import CLOSED_STATUSES, NOT_STARTED_STATUSES, Job, JobEvent, JobStatus
from .printers import manager
from .settings import tune

SNAPSHOT_DIR = config.data_dir / "snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def add_event(job: Job, kind: str, message: str = "") -> None:
    job.events.append(JobEvent(kind=kind, message=message))
    job.message = message if kind in ("attention", "error") else ""


def remote_name(job: Job) -> str:
    """The file name on the printer, which also tells Printy's prints from the others."""
    return f"printy-{job.id}-{Path(job.sliced_file.filename).name}".replace(" ", "_")


def minutes_label(m: int) -> str:
    """60 → "1 h", 90 → "1 h 30", 45 → "45 min"."""
    h, rest = divmod(int(m), 60)
    if not h:
        return f"{rest} min"
    return f"{h} h {rest:02d}" if rest else f"{h} h"


def arm_power_off(job: Job) -> None:
    """A print launched by Printy has ended: its plug will be cut once the printer is cool."""
    if job.printer.switch_entity:
        job.printer.power_off_pending = True


def confirm(job: Job) -> None:
    job.confirmed_at = utcnow()
    if job.spool_id is not None and job.printer.loaded_spool_id != job.spool_id:
        job.printer.loaded_spool_id = job.spool_id  # the user swapped the spool
        manager.want_spool(job.printer_id, job.spool_id)
    if job.status == JobStatus.AWAITING_CONFIRMATION:
        # asked before the power on: the printer still has to be switched on
        job.status = JobStatus.READY if job.powered_at is not None else JobStatus.SCHEDULED
    add_event(job, "confirmed", "Plateau vide confirmé.")


def spool_swapped(job: Job) -> None:
    """The user put the job's spool on the printer ahead of time."""
    if job.spool_id is not None:
        job.printer.loaded_spool_id = job.spool_id
        manager.want_spool(job.printer_id, job.spool_id)
        add_event(job, "spool", "Changement de bobine signalé.")


def snooze(job: Job, minutes: int | None = None) -> None:
    minutes = minutes or tune.snooze_min
    job.start_at = max(job.start_at, utcnow()) + timedelta(minutes=minutes)
    job.status = JobStatus.SCHEDULED
    job.notified_at = None
    add_event(job, "snoozed", f"Reportée de {minutes} min.")


def remove(s: Session, job: Job) -> None:
    """Deletes a print that has not started, or one from the history."""
    if job.status not in NOT_STARTED_STATUSES | CLOSED_STATUSES:
        raise ValueError("Cette impression est lancée : annule-la ou arrête-la d’abord.")
    if job.status in NOT_STARTED_STATUSES and job.powered_at is not None:
        arm_power_off(job)  # switched on for this print: cut once cool
    (SNAPSHOT_DIR / f"job-{job.id}.jpg").unlink(missing_ok=True)
    s.delete(job)


def cancel(job: Job, *, launching: bool = False) -> None:
    """Cancels in Printy. A print the printer is really running must be stopped on it first.
    `launching`: its file is being sent to the printer right now."""
    if job.status in (JobStatus.FINISHED, JobStatus.FAILED, JobStatus.CANCELLED):
        return
    if job.status in (JobStatus.PRINTING, JobStatus.PAUSED, JobStatus.STARTING):
        if launching:
            raise ValueError("Envoi du fichier en cours : réessaie dans un instant.")
        if manager.status(job.printer_id).active:
            raise ValueError("L’imprimante imprime : arrête d’abord l’impression.")
        if job.started_at is not None:
            job.finished_at = utcnow()
            arm_power_off(job)
    job.status = JobStatus.CANCELLED
    add_event(job, "cancelled", "Annulée.")
