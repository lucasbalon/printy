"""Drives each planned print through its life cycle.

scheduled → powering_on → awaiting_confirmation → ready → starting → printing
          → finished (then cooling and power off)

"starting" lasts from the upload until the printer is really seen printing: a start
command the printer ignores never shows up as a running print.

Every transition is logged as a JobEvent. The loop runs every few seconds and can be
woken early with `scheduler.poke()` after a change made from the API.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from . import bedcheck, photos, power, production, reminders, usage
from . import settings as settings_store
from .config import config
from .db import SessionLocal, utcnow
from .drivers import DriverError, PrinterState
from .ha import NotifyAction, ha
from .ha_entities import publisher
from .job_ops import (
    add_event,
    arm_power_off,
    cancel,
    confirm,
    minutes_label,
    remote_name,
    remove,
    snooze,
    spool_swapped,
)
from .models import ACTIVE_STATUSES, NOT_STARTED_STATUSES, OPEN_STATUSES, Job, JobStatus, Printer
from .printers import manager
from .settings import tune
from .spoolman import describe, spoolman

log = logging.getLogger(__name__)

TICK_S = 5  # delays and thresholds: settings.Tunables (shown in the settings)


class Scheduler:
    def __init__(self) -> None:
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._background: set[asyncio.Task[None]] = set()
        self._last_progress: dict[int, float] = {}
        self._launching: set[int] = set()  # jobs whose file is being sent right now
        self._stopped: set[int] = set()  # jobs the user stopped from Printy: no start grace
        self._offline: dict[int, bool] = {}  # printer id -> offline at the last tick
        self._online_at: dict[int, float] = {}  # printer id -> back online since (monotonic)
        self._metered_at: dict[int, float] = {}  # job id -> last energy sample (monotonic)
        self.errors: dict[str, dict[str, str]] = {}  # last error per step, for the diagnostics

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="scheduler")
        ha.listen_actions(self.on_notification_action)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    def poke(self) -> None:
        self._wake.set()

    async def _loop(self) -> None:
        while True:
            try:
                await manager.sync()
                await manager.refresh_all()
                await self.tick()
                self.errors.pop("boucle", None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                if "boucle" not in self.errors:
                    log.exception("scheduler tick failed")
                self.errors["boucle"] = {"error": f"{exc.__class__.__name__} : {exc}", "at": utcnow().isoformat()}
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=TICK_S)
            except TimeoutError:
                pass
            self._wake.clear()

    # -- main step ------------------------------------------------------------

    async def tick(self) -> None:
        with SessionLocal() as s:
            opts = settings_store.get_all(s)
            jobs = s.scalars(
                select(Job)
                .where(Job.status.in_([st.value for st in OPEN_STATUSES]))
                .options(joinedload(Job.printer), joinedload(Job.sliced_file))
                .order_by(Job.start_at)
            ).unique().all()
            self._track_boot(s)
            # each part is isolated: one failure must not stop the others
            for job in jobs:
                await self._guard(s, f"impression {job.id}", self._step(s, job, opts))
            for printer in s.query(Printer).all():
                await self._guard(s, "coupure", power.maybe_power_off(s, printer, opts))
            await self._guard(s, "spoolman", usage.report(s))
            await self._guard(s, "rappel du matin", reminders.morning(s, opts))
            await self._guard(s, "rappel du plateau", reminders.forgotten_piece(s, opts))
            await self._guard(s, "entités", publisher.run(s, opts))

    async def _guard(self, s: Session, name: str, step) -> None:  # noqa: ANN001
        try:
            await step
            s.commit()
            self.errors.pop(name, None)
        except Exception as exc:  # noqa: BLE001
            s.rollback()
            if name not in self.errors:
                log.exception("scheduler step %s failed", name)
            self.errors[name] = {"error": f"{exc.__class__.__name__} : {exc}", "at": utcnow().isoformat()}

    def _track_boot(self, s: Session) -> None:
        """Notes when a printer comes back online, to let it finish booting."""
        for printer_id in s.scalars(select(Printer.id)):
            offline = manager.status(printer_id).state == PrinterState.OFFLINE
            if self._offline.get(printer_id) and not offline:
                self._online_at[printer_id] = time.monotonic()
            self._offline[printer_id] = offline

    def booting(self, printer_id: int) -> bool:
        at = self._online_at.get(printer_id)
        return at is not None and time.monotonic() - at < tune.boot_settle_min * 60

    def launching(self, job_id: int) -> bool:
        return job_id in self._launching

    def stopped_by_user(self, job_id: int) -> None:
        """The user stopped the print from Printy: an idle printer now means "cancelled"."""
        self._stopped.add(job_id)
        self.poke()

    async def _step(self, s: Session, job: Job, opts: dict) -> None:
        # chain the immediate transitions (scheduled → powering on → ready → starting)
        # instead of waiting for the next tick between each of them
        for _ in range(4):
            before = job.status
            await self._advance(s, job, opts)
            if job.status == before or job.status in (JobStatus.STARTING, JobStatus.NEEDS_ATTENTION):
                return

    async def _advance(self, s: Session, job: Job, opts: dict) -> None:
        now = utcnow()
        printer = job.printer
        status = manager.status(printer.id)
        st = JobStatus(job.status)

        if st == JobStatus.SCHEDULED:
            if now < job.start_at - timedelta(minutes=printer.power_on_lead_min):
                await self._ask_early(s, job, status, opts)
                return
            if self._printer_taken(s, job):
                if now >= job.start_at and "attend" not in job.message:
                    job.message = "En attente de la fin de l’impression précédente."
                return
            job.status = JobStatus.POWERING_ON
            job.powered_at = now
            if printer.switch_entity:
                if not await ha.is_on(printer.switch_entity):
                    await ha.set_power(printer.switch_entity, True)
                add_event(job, "power_on", "Imprimante allumée.")
            else:
                add_event(job, "power_on", "Préparation de l’imprimante.")
            return

        if st == JobStatus.POWERING_ON:
            if status.state == PrinterState.OFFLINE:
                grace = timedelta(minutes=tune.offline_grace_min + printer.power_on_lead_min)
                if now - (job.powered_at or now) > grace:
                    await self._needs_attention(job, opts, "L’imprimante ne répond pas après l’allumage.")
                return
            if status.state in (PrinterState.PRINTING, PrinterState.PAUSED, PrinterState.BUSY):
                job.message = "L’imprimante est occupée."
                return
            if self.booting(printer.id):
                job.message = "L’imprimante termine son démarrage."
                return
            job.message = ""
            await self._ask_confirmation(job, opts)
            return

        if st == JobStatus.AWAITING_CONFIRMATION:
            if job.powered_at is None:
                # asked before the power on: the printer is switched on at its usual time,
                # the answer is still awaited
                if now >= job.start_at - timedelta(minutes=printer.power_on_lead_min):
                    job.status = JobStatus.SCHEDULED
                return
            timeout = timedelta(minutes=int(opts["confirm_timeout_min"]))
            if job.notified_at is None:
                return
            if now > max(job.notified_at, job.start_at) + timeout:
                await self._needs_attention(job, opts, "Pas de confirmation reçue : impression non lancée.")
                arm_power_off(job)  # switched on for this print: cut once cool
            elif not job.confirm_resent and now >= max(job.start_at, job.notified_at + timeout / 2):
                # the phone may have been offline: one more try (it replaces the first one)
                job.confirm_resent = True
                await self._notify_confirmation(job, opts, again=True)
            return

        if st == JobStatus.READY:
            if now >= job.start_at and self._swap(job):
                # the printer (Spoolman via Moonraker) still has another spool: ask, wait
                job.confirmed_at = None
                await self._notify_confirmation(job, opts)
                return
            if now >= job.start_at:
                job.status = JobStatus.STARTING
                job.started_at = None
                add_event(job, "starting", "Envoi du fichier à l’imprimante.")
                self._launching.add(job.id)
                task = asyncio.create_task(self._launch(job.id))
                self._background.add(task)
                task.add_done_callback(self._background.discard)
            return

        if st == JobStatus.STARTING:
            if job.started_at is not None:
                self._meter(job, opts)  # heating and calibration count too
            await self._follow_start(s, job, status, opts)
            return

        if st in (JobStatus.PRINTING, JobStatus.PAUSED):
            self._meter(job, opts)
            await self._follow_print(s, job, status, opts)

    def _printer_taken(self, s: Session, job: Job) -> bool:
        return (
            s.scalar(
                select(Job.id)
                .where(Job.printer_id == job.printer_id, Job.id != job.id)
                .where(Job.status.in_([st.value for st in ACTIVE_STATUSES]))
                .limit(1)
            )
            is not None
        )

    # -- confirmation -------------------------------------------------------

    @staticmethod
    def _swap(job: Job) -> bool:
        """A spool change always needs a human, whatever the confirmation settings."""
        loaded = job.printer.loaded_spool_id
        return job.spool_id is not None and loaded is not None and job.spool_id != loaded

    @staticmethod
    def _confirm_time_reached(job: Job, opts: dict) -> bool:
        return utcnow() >= job.start_at - timedelta(minutes=int(opts["confirm_lead_min"]))

    async def _ask_early(self, s: Session, job: Job, status, opts: dict) -> None:  # noqa: ANN001
        """Confirmation asked before the printer is switched on (setting "confirm_lead_min")."""
        if job.notified_at is not None or not self._confirm_time_reached(job, opts):
            return
        if not self._swap(job) and (not job.confirm_required or job.confirmed_at or job.printer.bed_clear):
            return  # nothing to ask, or settled at the power on
        if status.active or self._printer_taken(s, job):
            return  # another print is on the bed: asking now makes no sense
        job.powered_at = None
        await self._notify_confirmation(job, opts)

    async def _ask_confirmation(self, job: Job, opts: dict) -> None:
        """The printer is on and ready: launch, or wait for the user's confirmation."""
        printer = job.printer
        if not self._swap(job):
            if not job.confirm_required or job.confirmed_at:
                job.status = JobStatus.READY
                return
            if printer.bed_clear:
                job.confirmed_at = utcnow()
                job.status = JobStatus.READY
                add_event(job, "confirmed", "Plateau déclaré vidé après l’impression précédente.")
                return
        elif job.confirmed_at is not None:
            # confirmed, but the printer still has another spool: ask again, about the spool
            job.confirmed_at = None
            job.notified_at = None
        if job.notified_at is not None:
            job.status = JobStatus.AWAITING_CONFIRMATION  # already asked before the power on
            return
        if self._confirm_time_reached(job, opts):
            await self._notify_confirmation(job, opts)

    async def _notify_confirmation(self, job: Job, opts: dict, *, again: bool = False) -> None:
        """Asks to confirm the bed (and the spool). `again`: the reminder of an unanswered
        request, which keeps the first request's time (and so its deadline)."""
        printer = job.printer
        swap = self._swap(job)
        data = await photos.photo(printer)
        image = await photos.capture(job, data)
        hint = bedcheck.sentence(bedcheck.verdict(printer.id, data))
        job.status = JobStatus.AWAITING_CONFIRMATION
        if not again:
            job.notified_at = utcnow()
            job.confirm_resent = False
        local_start = job.start_at.astimezone(config.timezone).strftime("%H:%M")
        model = job.sliced_file.model.name
        if swap:
            spools = {sp.id: sp for sp in await spoolman.spools_or_empty()}
            new = spools.get(job.spool_id)
            new_label = describe(new) if new else f"la bobine n° {job.spool_id}"
            add_event(job, "confirmation_requested", f"{'Rappel : c' if again else 'C'}hangement de bobine demandé : {new_label}.")
            title = f"{printer.name} : change la bobine"
            message = f"Mets {new_label} pour {model} ({local_start}), puis vérifie que le plateau est vide."
            start_label = "Bobine changée, lancer"
        else:
            add_event(job, "confirmation_requested", f"Confirmation du plateau vide {'redemandée' if again else 'demandée'}. {hint}".strip())
            title = f"{printer.name} : le plateau est-il vide ?"
            message = f"{model} doit démarrer à {local_start}."
            start_label = "Lancer"
        if hint:
            message += f" {hint}"
        if again:
            title = f"Rappel : {title}"
            message += " Sans réponse, elle ne sera pas lancée."
        await ha.notify(
            opts["notify_targets"],
            title,
            message,
            image=image,
            tag=f"printy-job-{job.id}",
            urgent=True,
            actions=[
                NotifyAction(f"PRINTY_START_{job.id}", start_label),
                NotifyAction(f"PRINTY_SNOOZE_{job.id}", f"Reporter de {minutes_label(tune.snooze_min)}"),
                NotifyAction(f"PRINTY_CANCEL_{job.id}", "Annuler", destructive=True),
            ],
        )

    async def _needs_attention(self, job: Job, opts: dict, reason: str, *, kind: str = "attention") -> None:
        """The print will not go on by itself: the user is told, on the phone too (the
        notification replaces a pending request for the same print)."""
        job.status = JobStatus.NEEDS_ATTENTION
        add_event(job, kind, reason)
        await ha.notify(
            opts["notify_targets"],
            f"{job.printer.name} : impression à revoir",
            f"{job.sliced_file.model.name} : {reason[0].lower()}{reason[1:]} Ouvre Printy pour la relancer ou l’annuler.",
            tag=f"printy-job-{job.id}",
        )

    # -- launch and follow ----------------------------------------------------

    async def _launch(self, job_id: int) -> None:
        try:
            await self._keep_empty_bed(job_id)
            await self._send_file(job_id)
        finally:
            self._launching.discard(job_id)
            self.poke()

    async def _keep_empty_bed(self, job_id: int) -> None:
        """Right before a confirmed print the bed is empty: its photo is the reference."""
        try:
            with SessionLocal() as s:
                job = s.get(Job, job_id)
                if job is None or job.confirmed_at is None:
                    return
                printer = job.printer
                data = await photos.photo(printer)
            if data:
                bedcheck.save_reference(printer.id, data)
        except Exception as exc:  # noqa: BLE001 - never blocks the launch
            log.debug("empty bed photo: %s", exc)

    async def _send_file(self, job_id: int) -> None:
        with SessionLocal() as s:
            job = s.get(Job, job_id)
            if job is None:
                return
            driver = manager.driver(job.printer_id)
            path = config.library_dir / job.sliced_file.path
            name = remote_name(job)
            spool_id, printer_id = job.spool_id, job.printer_id

            async def send() -> None:
                if driver is None:
                    raise DriverError("Pilote d’imprimante indisponible.")
                await driver.upload(path, name)
                if spool_id is not None and await driver.set_active_spool(spool_id):
                    manager.spool_pushed(printer_id)
                await driver.start(name)

            try:
                await asyncio.wait_for(send(), timeout=tune.launch_timeout_min * 60)
            except Exception as exc:  # noqa: BLE001 - whatever happens, the job must not stay stuck
                log.warning("launch of job %s failed: %s", job_id, exc)
                reason = "l’imprimante ne répond pas" if isinstance(exc, TimeoutError) else (str(exc) or exc.__class__.__name__)
                s.refresh(job)
                if job.status == JobStatus.STARTING:
                    opts = settings_store.get_all(s)
                    await self._needs_attention(job, opts, f"Lancement impossible : {reason}.", kind="error")
                    s.commit()
                return
            s.refresh(job)
            if job.status != JobStatus.STARTING:
                return  # cancelled meanwhile
            job.started_at = utcnow()
            if job.spool_id is not None:
                job.printer.loaded_spool_id = job.spool_id
            add_event(job, "sent", "Fichier envoyé, démarrage demandé à l’imprimante.")
            s.commit()

    async def _follow_start(self, s: Session, job: Job, status, opts: dict) -> None:  # noqa: ANN001
        """Between the start command and the first sign of a running print."""
        if job.id in self._launching:
            return
        # the upload may have ended meanwhile, committed by its own session
        s.refresh(job)
        if job.status != JobStatus.STARTING:
            return
        if job.id in self._stopped and not status.active:
            self._stopped.discard(job.id)
            job.status = JobStatus.CANCELLED
            job.finished_at = utcnow()
            add_event(job, "cancelled", "Lancement arrêté depuis Printy.")
            if job.started_at is not None:
                arm_power_off(job)
            return
        if job.started_at is None:
            # the upload never finished (Printy restarted while sending)
            await self._needs_attention(job, opts, "L’envoi du fichier a été interrompu.")
            return
        ours = bool(status.file) and status.file.rsplit("/", 1)[-1] == remote_name(job)
        # a "finished" with our file name is this very print (a short one), not the previous one
        if status.state in (PrinterState.PRINTING, PrinterState.PAUSED) or (ours and status.state == PrinterState.FINISHED):
            job.status = JobStatus.PRINTING
            add_event(job, "started", "Impression lancée.")
            return
        waited = utcnow() - job.started_at
        grace = tune.busy_grace_min if status.state == PrinterState.BUSY else tune.start_grace_min
        if waited < timedelta(minutes=grace):
            job.message = "L’imprimante prépare l’impression (chauffe, calibrage)."
            return
        # never seen printing: the start command was ignored, nothing was printed
        job.started_at = None
        await self._needs_attention(job, opts, "Le fichier a été envoyé mais l’imprimante n’a pas démarré l’impression.")

    def _meter(self, job: Job, opts: dict) -> None:
        """Integrates the plug's power over the print (kWh per piece)."""
        live = manager.live.get(job.printer_id)
        now = time.monotonic()
        last = self._metered_at.get(job.id)
        self._metered_at[job.id] = now
        if live is None or live.power_w is None or last is None or now - last > 120:
            return
        wh = live.power_w * (now - last) / 3600
        job.energy_wh = round((job.energy_wh or 0) + wh, 3)

    async def _close_print(self, s: Session, job: Job, opts: dict, *, success: bool) -> None:
        """For a good print, the pieces go to stock, in the spool's colour.
        The print was launched by Printy: the plug is cut once the printer has cooled."""
        self._metered_at.pop(job.id, None)
        arm_power_off(job)
        spools = {sp.id: sp for sp in await spoolman.spools_or_empty()} if job.spool_id is not None else {}
        spool = spools.get(job.spool_id) if job.spool_id is not None else None
        if success and opts.get("stock_auto", True):
            pieces = max(1, job.sliced_file.pieces or 1)
            variant, color = production.variant_of(job, spool)
            production.move(s, job.sliced_file.model_id, variant, pieces, "print", color=color, job_id=job.id)
            job.stock_added = pieces
            add_event(job, "stock", f"{pieces} pièce{'s' if pieces > 1 else ''} ajoutée{'s' if pieces > 1 else ''} au stock ({variant}).")

    async def _follow_print(self, s: Session, job: Job, status, opts: dict) -> None:  # noqa: ANN001
        now = utcnow()
        running = status.state in (PrinterState.PRINTING, PrinterState.PAUSED)
        if running:
            # outside of a running print, the progress may be the previous print's 100 %
            if status.progress is not None:
                self._last_progress[job.id] = status.progress
        if status.state == PrinterState.PAUSED and job.status != JobStatus.PAUSED:
            job.status = JobStatus.PAUSED
            add_event(job, "paused", "Impression en pause.")
        elif status.state == PrinterState.PRINTING and job.status == JobStatus.PAUSED:
            job.status = JobStatus.PRINTING
            add_event(job, "resumed", "Impression reprise.")
        elif status.state == PrinterState.ERROR:
            job.status = JobStatus.FAILED
            job.finished_at = now
            usage.record(job, self._last_progress.pop(job.id, 0))
            add_event(job, "error", status.message or "L’imprimante signale une erreur.")
            await self._close_print(s, job, opts, success=False)
        elif status.state in (PrinterState.FINISHED, PrinterState.IDLE):
            # The print was seen running; a K2 still flickers through several states
            # after the start (idle, heating, calibration): nothing is concluded too early.
            if now - (job.started_at or now) < timedelta(minutes=tune.start_grace_min) and job.id not in self._stopped:
                return
            self._stopped.discard(job.id)
            progress = self._last_progress.pop(job.id, 0)
            done = status.state == PrinterState.FINISHED or progress > 0.98
            job.finished_at = now
            job.printer.last_finished_at = now
            usage.record(job, 1.0 if done else progress)
            if done:
                job.status = JobStatus.FINISHED
                add_event(job, "finished", "Impression terminée.")
                await self._close_print(s, job, opts, success=True)
                await self._notify_finished(s, job)
            else:
                job.status = JobStatus.CANCELLED
                add_event(job, "cancelled", "Impression interrompue sur l’imprimante.")
                await self._close_print(s, job, opts, success=False)

    async def _notify_finished(self, s: Session, job: Job) -> None:
        opts = settings_store.get_all(s)
        image = await photos.capture(job)
        message = f"{job.sliced_file.model.name} est prête. Touche « Plateau vidé » une fois la pièce retirée."
        nxt = s.scalar(
            select(Job)
            .where(Job.printer_id == job.printer_id, Job.status == JobStatus.SCHEDULED.value)
            .order_by(Job.start_at)
            .limit(1)
        )
        actions = [NotifyAction(f"PRINTY_BEDCLEAR_{job.printer_id}", "Plateau vidé")]
        if nxt is not None:
            when = nxt.start_at.astimezone(config.timezone).strftime("%H:%M le %d/%m")
            message += f" Prochaine : {nxt.sliced_file.model.name} à {when}."
            loaded = job.printer.loaded_spool_id
            swap = nxt.spool_id is not None and loaded is not None and nxt.spool_id != loaded
            if swap and opts.get("spool_reminder_finish", True):
                spools = {sp.id: sp for sp in await spoolman.spools_or_empty()}
                sp = spools.get(nxt.spool_id)
                message += f" Elle demande {describe(sp) if sp else f'la bobine n° {nxt.spool_id}'}."
                actions.append(NotifyAction(f"PRINTY_SPOOL_{nxt.id}", "Bobine changée"))
        await ha.notify(
            opts["notify_targets"],
            f"{job.printer.name} : impression terminée",
            message,
            image=image,
            tag=f"printy-printer-{job.printer_id}",
            actions=actions,
        )

    # -- user actions (API and notifications) ---------------------------------

    async def on_notification_action(self, action: str) -> None:
        _, verb, ident = action.split("_", 2)
        with SessionLocal() as s:
            if verb == "BEDCLEAR":
                printer = s.get(Printer, int(ident))
                if printer:
                    printer.bed_cleared_at = utcnow()
            elif verb == "SPOOL":
                job = s.get(Job, int(ident))
                if job is not None:
                    spool_swapped(job)
            else:
                job = s.get(Job, int(ident))
                if job is None:
                    return
                if verb == "CANCEL" and job.status in NOT_STARTED_STATUSES:
                    remove(s, job)  # not started: nothing to keep in the history
                elif verb == "CANCEL":
                    cancel(job, launching=self.launching(job.id))
                else:
                    {"START": confirm, "SNOOZE": snooze}[verb](job)
            s.commit()
        self.poke()


scheduler = Scheduler()
