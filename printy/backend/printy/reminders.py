"""Reminders sent on the phone: the spools to prepare in the morning, and a finished
piece still on the bed when the next print comes soon."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from . import bedcheck, photos
from . import settings as settings_store
from .config import config
from .db import utcnow
from .ha import NotifyAction, ha
from .job_ops import filament_forecast
from .models import Job, JobStatus, Printer
from .printers import manager
from .settings import tune
from .spoolman import describe, spoolman


async def morning(s: Session, opts: dict) -> None:
    """Once a day at the chosen time: spools to prepare for the day's prints."""
    at = opts.get("spool_reminder_morning")
    if not at or not spoolman.enabled:
        return
    now = utcnow().astimezone(config.timezone)
    today = now.date().isoformat()
    hour, minute = (int(x) for x in at.split(":"))
    if (now.hour, now.minute) < (hour, minute) or opts.get("spool_reminder_sent_on") == today:
        return
    settings_store.set_many(s, {"spool_reminder_sent_on": today})

    horizon = now + timedelta(days=1)
    open_jobs = s.scalars(
        select(Job)
        .where(Job.status == JobStatus.SCHEDULED.value, Job.start_at <= horizon)
        .options(joinedload(Job.sliced_file), joinedload(Job.printer))
    ).unique().all()
    by_id = {sp.id: sp for sp in await spoolman.spools_or_empty()}
    fil = await filament_forecast(s)
    lines: list[str] = []
    swaps: list[Job] = []
    for job in sorted(open_jobs, key=lambda j: j.start_at):
        info = fil.get(job.id)
        if info is None:
            continue
        when = job.start_at.astimezone(config.timezone).strftime("%H:%M")
        sp = by_id.get(job.spool_id) if job.spool_id is not None else None
        name = describe(sp) if sp else "la bonne bobine"
        if info.spool_change:
            lines.append(f"{job.printer.name} : mets {name} pour {job.sliced_file.model.name} ({when}).")
            swaps.append(job)
        if info.low_filament:
            lines.append(f"Pas assez de filament sur {name} pour {job.sliced_file.model.name} ({when}).")
    if not lines:
        return
    actions = [NotifyAction(f"PRINTY_SPOOL_{swaps[0].id}", "Bobine changée")] if len(swaps) == 1 else None
    await ha.notify(
        opts["notify_targets"],
        "Printy : bobines à préparer",
        " ".join(lines),
        actions=actions,
        tag="printy-spools",
    )


async def forgotten_piece(s: Session, opts: dict) -> None:
    """A finished piece was never declared removed and the next print comes soon."""
    if not opts.get("bed_reminder", True):
        return
    now = utcnow()
    for printer in s.query(Printer).all():
        finished = printer.last_finished_at
        if finished is None or printer.bed_clear or now - finished < timedelta(minutes=tune.bed_reminder_after_min):
            continue
        if printer.bed_reminded_at is not None and printer.bed_reminded_at >= finished:
            continue  # one reminder per finished print
        nxt = s.scalar(
            select(Job)
            .where(Job.printer_id == printer.id, Job.status == JobStatus.SCHEDULED.value)
            .order_by(Job.start_at)
            .limit(1)
        )
        before = timedelta(hours=tune.bed_reminder_hours)
        if nxt is None or nxt.start_at - now > before or manager.status(printer.id).active:
            continue
        printer.bed_reminded_at = now
        data = await photos.photo(printer)
        verdict = bedcheck.verdict(printer.id, data)
        local_start = nxt.start_at.astimezone(config.timezone).strftime("%H:%M")
        message = f"{nxt.sliced_file.model.name} est prévue à {local_start}. "
        if verdict == "empty":
            message += "La photo montre un plateau vide : touche « Plateau vidé » si la pièce a bien été retirée."
        else:
            message += "Retire la pièce, puis touche « Plateau vidé ». " + bedcheck.sentence(verdict)
        await ha.notify(
            opts["notify_targets"],
            f"{printer.name} : pièce encore sur le plateau ?",
            message.strip(),
            image=await photos.image_url(printer, data, f"printer-{printer.id}"),
            tag=f"printy-printer-{printer.id}",
            actions=[NotifyAction(f"PRINTY_BEDCLEAR_{printer.id}", "Plateau vidé")],
        )
