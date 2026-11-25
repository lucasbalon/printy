from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import AwareDatetime, BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .. import job_ops, offpeak
from .. import settings as settings_store
from ..config import config
from ..db import get_session, utcnow
from ..ha import ha
from ..models import CLOSED_STATUSES, OPEN_STATUSES, Job, JobStatus, Printer, SlicedFile
from ..printers import manager
from ..job_ops import SNAPSHOT_DIR, add_event
from ..scheduler import scheduler
from ..spoolman import plan as filament_plan
from ..spoolman import spoolman, suggest
from . import serialize

router = APIRouter(tags=["jobs"])
DB = Annotated[Session, Depends(get_session)]

MOVABLE = {JobStatus.SCHEDULED, JobStatus.NEEDS_ATTENTION, JobStatus.AWAITING_CONFIRMATION, JobStatus.POWERING_ON}


def _get(s: Session, job_id: int) -> Job:
    job = s.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Impression introuvable.")
    return job


def _overlap(s: Session, printer_id: int, start: datetime, duration_s: int, exclude: int | None = None) -> Job | None:
    end = start + timedelta(seconds=duration_s)
    for other in s.scalars(
        select(Job).where(Job.printer_id == printer_id, Job.status.in_([st.value for st in OPEN_STATUSES]))
    ):
        other_end = other.end_at
        if other.status in (JobStatus.PRINTING, JobStatus.PAUSED) and (w := manager.print_window(printer_id)) and w[1]:
            other_end = max(other_end, w[1])  # the printer knows better than the slicer
        if other.id != exclude and other.start_at < end and start < other_end:
            return other
    return None


def _check_slot(s: Session, printer_id: int, start: datetime, duration_s: int, exclude: int | None = None) -> None:
    window = manager.print_window(printer_id)
    running_ours = s.scalar(
        select(Job.id).where(
            Job.printer_id == printer_id,
            Job.status.in_([JobStatus.PRINTING.value, JobStatus.PAUSED.value, JobStatus.STARTING.value]),
        )
    )
    # end not known yet (the printer is heating): at least the next hour is taken
    if window and not running_ours and start < (window[1] or utcnow() + timedelta(hours=1)):
        st = manager.status(printer_id)
        name = (st.file or "une impression").rsplit("/", 1)[-1]
        if window[1] is None:
            raise HTTPException(409, f"L’imprimante démarre « {name} » : sa fin n’est pas encore connue.")
        end = window[1].astimezone(config.timezone).strftime("%H:%M")
        raise HTTPException(409, f"L’imprimante est occupée par « {name} » jusque vers {end}.")
    if other := _overlap(s, printer_id, start, duration_s, exclude):
        raise HTTPException(
            409, f"Ce créneau chevauche « {other.sliced_file.model.name} » sur cette imprimante."
        )


async def _filament(s: Session, extra: list | None = None) -> dict:
    """Spool changes and filament shortages over all open jobs (plus `extra`, not saved)."""
    settings_store.get_all(s)
    open_jobs = s.scalars(
        select(Job)
        # jobs waiting for the user will not run on their own: they do not load a spool
        .where(Job.status.in_([st.value for st in OPEN_STATUSES if st != JobStatus.NEEDS_ATTENTION]))
        .options(joinedload(Job.sliced_file))
    ).unique().all()
    loaded = {p.id: p.loaded_spool_id for p in s.scalars(select(Printer))}
    jobs = list(open_jobs) + (extra or [])
    return filament_plan(jobs, loaded, await spoolman.spools_or_empty(), manager.deducted(jobs))


class PreviewIn(BaseModel):
    sliced_file_id: int
    printer_id: int
    start_at: AwareDatetime | None = None  # empty: now
    spool_id: int | None = None
    exclude_job_id: int | None = None  # the job being moved


@router.post("/jobs/preview")
async def preview(body: PreviewIn, s: DB):
    """What planning this print would mean: best off-peak slot, spool change, filament."""
    f = s.get(SlicedFile, body.sliced_file_id)
    if f is None or s.get(Printer, body.printer_id) is None:
        raise HTTPException(404, "Fichier ou imprimante introuvable.")
    now = utcnow()
    duration = timedelta(seconds=f.duration_s or 3600)
    busy = [
        (j.start_at, j.end_at)
        for j in s.scalars(
            select(Job).where(Job.printer_id == body.printer_id, Job.status.in_([st.value for st in OPEN_STATUSES]))
        )
        if j.id != body.exclude_job_id
    ]
    if w := manager.print_window(body.printer_id):
        busy.append((w[0], w[1] or now + timedelta(hours=1)))
    slot = offpeak.best_slot(settings_store.get(s, "off_peak") or [], duration, busy, now)
    out: dict = {"off_peak": {"start_at": slot[0].isoformat(), "share": slot[1]} if slot else None}
    if body.spool_id is not None:
        virtual = SimpleNamespace(
            id=-1, printer_id=body.printer_id, spool_id=body.spool_id,
            start_at=max(body.start_at or now, now), sliced_file=SimpleNamespace(filament_g=f.filament_g),
        )
        info = (await _filament(s, [virtual]))[-1]  # also applies the margin settings
        out |= {
            "spool_change": info.spool_change,
            "previous_spool_id": info.previous_spool_id,
            "low_filament": info.low_filament,
            "spool_remaining_g": info.remaining_before_g,
        }
    return out


@router.get("/jobs")
async def list_jobs(s: DB, start: AwareDatetime | None = None, end: AwareDatetime | None = None):
    stmt = select(Job).options(joinedload(Job.sliced_file).joinedload(SlicedFile.model))
    jobs = s.scalars(stmt.order_by(Job.start_at)).unique().all()
    if start and end:
        jobs = [
            j for j in jobs
            if (j.start_at < end and start < j.end_at) or j.status in (JobStatus.NEEDS_ATTENTION,)
        ]
    fil = await _filament(s)
    out = [serialize.job(j, filament=fil.get(j.id)) for j in jobs]
    # planned prints that would start while the printer is still busy
    busy_until: dict[int, datetime] = {}
    for j in jobs:
        if j.printer_id not in busy_until and (w := manager.print_window(j.printer_id)) and w[1]:
            busy_until[j.printer_id] = w[1]
    for data, j in zip(out, jobs, strict=True):
        until = busy_until.get(j.printer_id)
        data["waits_for_print"] = bool(until and j.status == JobStatus.SCHEDULED and j.start_at < until)
    return out


@router.get("/jobs/history")
def history(s: DB, limit: int = 50):
    stmt = (
        select(Job)
        .where(Job.status.in_([st.value for st in CLOSED_STATUSES]))
        .order_by(Job.finished_at.desc().nulls_last(), Job.start_at.desc())
        .limit(limit)
    )
    return [serialize.job(j) for j in s.scalars(stmt)]


@router.get("/jobs/{job_id}")
async def get_job(job_id: int, s: DB):
    job = _get(s, job_id)
    fil = await _filament(s)
    return serialize.job(job, events=True, filament=fil.get(job.id))


class JobIn(BaseModel):
    sliced_file_id: int
    printer_id: int
    start_at: AwareDatetime
    confirm_required: bool | None = None
    note: str = ""
    spool_id: int | None = None
    auto_spool: bool = True  # pick the best matching spool when spool_id is empty
    start_now: bool = False  # print right away; the user vouches for an empty bed


@router.post("/jobs", status_code=201)
async def create_job(body: JobIn, s: DB):
    f = s.get(SlicedFile, body.sliced_file_id)
    printer = s.get(Printer, body.printer_id)
    if f is None or printer is None:
        raise HTTPException(404, "Fichier ou imprimante introuvable.")
    start = utcnow() if body.start_now else max(body.start_at.astimezone(UTC), utcnow())
    duration = f.duration_s or 3600
    _check_slot(s, printer.id, start, duration)
    confirm = body.confirm_required
    if confirm is None:
        confirm = bool(settings_store.get(s, "confirm_by_default"))
    job = Job(
        sliced_file=f,
        printer=printer,
        start_at=start,
        duration_s=duration,
        confirm_required=confirm,
        note=body.note,
        spool_id=body.spool_id,
    )
    if job.spool_id is None and body.auto_spool:
        settings_store.get_all(s)
        best = suggest(await spoolman.spools_or_empty(), f.color, f.material, printer.loaded_spool_id, f.filament_g)
        job.spool_id = best.id if best else None
    s.add(job)
    if body.start_now:
        job.confirmed_at = start
        add_event(job, "created", "Lancement immédiat demandé, plateau vide confirmé.")
    else:
        add_event(job, "created", "Planifiée.")
    s.commit()
    scheduler.poke()
    return serialize.job(job, events=True)


class JobPatch(BaseModel):
    start_at: AwareDatetime | None = None
    printer_id: int | None = None
    confirm_required: bool | None = None
    note: str | None = None
    spool_id: int | None = None
    clear_spool: bool = False


@router.patch("/jobs/{job_id}")
def patch_job(job_id: int, body: JobPatch, s: DB):
    job = _get(s, job_id)
    if body.start_at is not None or body.printer_id is not None:
        if JobStatus(job.status) not in MOVABLE:
            raise HTTPException(409, "Cette impression ne peut plus être déplacée.")
        start = max((body.start_at or job.start_at).astimezone(UTC), utcnow())
        printer_id = body.printer_id or job.printer_id
        if s.get(Printer, printer_id) is None:
            raise HTTPException(404, "Imprimante introuvable.")
        _check_slot(s, printer_id, start, job.duration_s, exclude=job.id)
        moved_printer = printer_id != job.printer_id
        job.start_at, job.printer_id = start, printer_id
        if job.status != JobStatus.SCHEDULED or moved_printer:
            job.status = JobStatus.SCHEDULED
            job.notified_at = None
            if moved_printer:
                job.confirmed_at = None
        local = start.astimezone(config.timezone).strftime("%d/%m à %H:%M")
        add_event(job, "moved", f"Déplacée au {local}.")
    if body.confirm_required is not None:
        job.confirm_required = body.confirm_required
    if body.note is not None:
        job.note = body.note
    if body.spool_id is not None or body.clear_spool:
        if JobStatus(job.status) in CLOSED_STATUSES:
            raise HTTPException(409, "Cette impression est terminée.")
        job.spool_id = None if body.clear_spool else body.spool_id
    s.commit()
    scheduler.poke()
    return serialize.job(job, events=True)


@router.post("/jobs/{job_id}/confirm")
def confirm_job(job_id: int, s: DB):
    job = _get(s, job_id)
    if JobStatus(job.status) in CLOSED_STATUSES:
        raise HTTPException(409, "Cette impression est terminée.")
    job_ops.confirm(job)
    s.commit()
    scheduler.poke()
    return serialize.job(job, events=True)


@router.post("/jobs/{job_id}/spool-swapped")
def spool_swapped(job_id: int, s: DB):
    """The user put this job's spool on the printer ahead of time."""
    job = _get(s, job_id)
    if job.spool_id is None:
        raise HTTPException(409, "Aucune bobine n’est associée à cette impression.")
    job_ops.spool_swapped(job)
    s.commit()
    scheduler.poke()
    return serialize.job(job, events=True)


@router.post("/jobs/{job_id}/start-now")
def start_now(job_id: int, s: DB):
    """Starts as soon as possible; the user confirmed the bed from the interface."""
    job = _get(s, job_id)
    if JobStatus(job.status) not in MOVABLE | {JobStatus.READY}:
        raise HTTPException(409, "Cette impression ne peut pas être lancée maintenant.")
    now = utcnow()
    _check_slot(s, job.printer_id, now, job.duration_s, exclude=job.id)
    job.start_at = now
    job.confirmed_at = now
    if job.status == JobStatus.AWAITING_CONFIRMATION and job.spool_id is not None and job.printer.loaded_spool_id != job.spool_id:
        job_ops.spool_swapped(job)  # answered the spool change request from the app
    if JobStatus(job.status) in (JobStatus.NEEDS_ATTENTION, JobStatus.AWAITING_CONFIRMATION):
        job.status = JobStatus.READY if job.status == JobStatus.AWAITING_CONFIRMATION else JobStatus.SCHEDULED
    add_event(job, "confirmed", "Lancement immédiat demandé, plateau vide confirmé.")
    s.commit()
    scheduler.poke()
    return serialize.job(job, events=True)


@router.post("/jobs/{job_id}/snooze")
def snooze_job(job_id: int, s: DB, minutes: int | None = None):
    job = _get(s, job_id)
    if JobStatus(job.status) not in MOVABLE | {JobStatus.READY}:
        raise HTTPException(409, "Cette impression ne peut plus être reportée.")
    job_ops.snooze(job, minutes)
    s.commit()
    scheduler.poke()
    return serialize.job(job, events=True)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: int, s: DB):
    job = _get(s, job_id)
    sent = job.status in (JobStatus.STARTING, JobStatus.PRINTING, JobStatus.PAUSED) and job.started_at is not None
    try:
        job_ops.cancel(job, launching=scheduler.launching(job.id))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    s.commit()
    if sent and (driver := manager.driver(job.printer_id)):
        # the file is on the printer: make sure it does not start later on its own
        try:
            await driver.cancel()
        except Exception:  # noqa: BLE001
            pass
    scheduler.poke()
    return serialize.job(job, events=True)


@router.delete("/jobs/{job_id}", status_code=204)
async def delete_job(job_id: int, s: DB):
    """A print not started yet is deleted (no trace); a closed one leaves the history."""
    job = s.get(Job, job_id)
    if job is None:
        return
    try:
        job_ops.remove(s, job)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    s.commit()
    # a pending confirmation request would keep its buttons on the phone
    await ha.clear(settings_store.get(s, "notify_targets") or [], f"printy-job-{job_id}")
    scheduler.poke()


@router.get("/jobs/{job_id}/snapshot")
def job_snapshot(job_id: int):
    path = SNAPSHOT_DIR / f"job-{job_id}.jpg"
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
