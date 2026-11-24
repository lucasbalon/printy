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
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from . import settings as settings_store
from .config import config
from .db import SessionLocal, utcnow
from .drivers import DriverError, PrinterState
from .ha import NotifyAction, ha
from .models import ACTIVE_STATUSES, OPEN_STATUSES, Job, JobEvent, JobStatus, Printer
from . import production
from .ha_entities import publisher
from .printers import manager
from .spoolman import SpoolmanError, describe, spoolman
from .spoolman import plan as spoolman_plan

log = logging.getLogger(__name__)

TICK_S = 5
OFFLINE_GRACE = timedelta(minutes=10)
# After the start command, how long the printer may stay idle (heating, calibration)
START_GRACE = timedelta(minutes=10)
# … or report a busy state it does not name (long calibration on a K2)
BUSY_GRACE = timedelta(minutes=30)
# A printer that just came back online (plug switched on) is still booting: a K2 needs ~2 min
BOOT_SETTLE_S = 120
# Upload and start command together
LAUNCH_TIMEOUT_S = 600
SNAPSHOT_DIR = config.data_dir / "snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def add_event(job: Job, kind: str, message: str = "") -> None:
    job.events.append(JobEvent(kind=kind, message=message))
    job.message = message if kind in ("attention", "error") else ""


def remote_name(job: Job) -> str:
    return f"printy-{job.id}-{Path(job.sliced_file.filename).name}".replace(" ", "_")


class Scheduler:
    def __init__(self) -> None:
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._background: set[asyncio.Task[None]] = set()
        self._powered_by_us: set[int] = set()
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
                await self._guard(s, "coupure", self._maybe_power_off(s, printer, opts))
            await self._guard(s, "spoolman", self._report_usage(s))
            await self._guard(s, "rappel du matin", self._morning_reminder(s, opts))
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
        return at is not None and time.monotonic() - at < BOOT_SETTLE_S

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
                    self._powered_by_us.add(printer.id)
                add_event(job, "power_on", "Imprimante allumée.")
            else:
                add_event(job, "power_on", "Préparation de l’imprimante.")
            return

        if st == JobStatus.POWERING_ON:
            if status.state == PrinterState.OFFLINE:
                if now - (job.powered_at or now) > OFFLINE_GRACE + timedelta(minutes=printer.power_on_lead_min):
                    job.status = JobStatus.NEEDS_ATTENTION
                    add_event(job, "attention", "L’imprimante ne répond pas après l’allumage.")
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
            timeout = timedelta(minutes=int(opts["confirm_timeout_min"]))
            if job.notified_at and now - job.notified_at > timeout and now >= job.start_at:
                job.status = JobStatus.NEEDS_ATTENTION
                add_event(job, "attention", "Pas de confirmation reçue : impression non lancée.")
            return

        if st == JobStatus.READY:
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

    async def _ask_confirmation(self, job: Job, opts: dict) -> None:
        printer = job.printer
        # A spool change always needs a human, whatever the confirmation settings.
        swap = (
            job.spool_id is not None
            and printer.loaded_spool_id is not None
            and job.spool_id != printer.loaded_spool_id
            and job.confirmed_at is None
        )
        if not swap:
            if not job.confirm_required or job.confirmed_at:
                job.status = JobStatus.READY
                return
            if printer.bed_clear:
                job.confirmed_at = utcnow()
                job.status = JobStatus.READY
                add_event(job, "confirmed", "Plateau déclaré vidé après l’impression précédente.")
                return

        image = await self.capture(job)
        job.status = JobStatus.AWAITING_CONFIRMATION
        job.notified_at = utcnow()
        local_start = job.start_at.astimezone(config.timezone).strftime("%H:%M")
        model = job.sliced_file.model.name
        if swap:
            spools = {sp.id: sp for sp in await spoolman.spools_or_empty()}
            new = spools.get(job.spool_id)
            new_label = describe(new) if new else f"la bobine n° {job.spool_id}"
            add_event(job, "confirmation_requested", f"Changement de bobine demandé : {new_label}.")
            title = f"{printer.name} : change la bobine"
            message = f"Mets {new_label} pour {model} ({local_start}), puis vérifie que le plateau est vide."
            start_label = "Bobine changée, lancer"
        else:
            add_event(job, "confirmation_requested", "Confirmation du plateau vide demandée.")
            title = f"{printer.name} : le plateau est-il vide ?"
            message = f"{model} doit démarrer à {local_start}."
            start_label = "Lancer"
        await ha.notify(
            opts["notify_targets"],
            title,
            message,
            image=image,
            tag=f"printy-job-{job.id}",
            actions=[
                NotifyAction(f"PRINTY_START_{job.id}", start_label),
                NotifyAction(f"PRINTY_SNOOZE_{job.id}", "Reporter d'1 h"),
                NotifyAction(f"PRINTY_CANCEL_{job.id}", "Annuler", destructive=True),
            ],
        )

    async def capture(self, job: Job) -> str | None:
        """Takes a bed photo; returns the image reference for the notification."""
        printer = job.printer
        data: bytes | None = None
        if printer.camera_source == "ha" and printer.camera_entity:
            data = await ha.camera_snapshot(printer.camera_entity)
        elif printer.camera_source == "printer" and (driver := manager.driver(printer.id)):
            data = await driver.snapshot()
        if not data:
            return f"/api/camera_proxy/{printer.camera_entity}" if printer.camera_entity else None
        (SNAPSHOT_DIR / f"job-{job.id}.jpg").write_bytes(data)
        if config.ha_www_dir:
            (config.ha_www_dir / f"job-{job.id}.jpg").write_bytes(data)
            return f"/local/printy/job-{job.id}.jpg?v={int(time.time())}"
        return None

    # -- launch and follow ----------------------------------------------------

    async def _launch(self, job_id: int) -> None:
        try:
            await self._send_file(job_id)
        finally:
            self._launching.discard(job_id)
            self.poke()

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
                await asyncio.wait_for(send(), timeout=LAUNCH_TIMEOUT_S)
            except Exception as exc:  # noqa: BLE001 - whatever happens, the job must not stay stuck
                log.warning("launch of job %s failed: %s", job_id, exc)
                reason = "l’imprimante ne répond pas" if isinstance(exc, TimeoutError) else (str(exc) or exc.__class__.__name__)
                s.refresh(job)
                if job.status == JobStatus.STARTING:
                    job.status = JobStatus.NEEDS_ATTENTION
                    add_event(job, "error", f"Lancement impossible : {reason}")
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
            return
        if job.started_at is None:
            # the upload never finished (Printy restarted while sending)
            job.status = JobStatus.NEEDS_ATTENTION
            add_event(job, "attention", "L’envoi du fichier a été interrompu : relance l’impression.")
            return
        ours = bool(status.file) and status.file.rsplit("/", 1)[-1] == remote_name(job)
        # a "finished" with our file name is this very print (a short one), not the previous one
        if status.state in (PrinterState.PRINTING, PrinterState.PAUSED) or (ours and status.state == PrinterState.FINISHED):
            job.status = JobStatus.PRINTING
            add_event(job, "started", "Impression lancée.")
            return
        waited = utcnow() - job.started_at
        if waited < (BUSY_GRACE if status.state == PrinterState.BUSY else START_GRACE):
            job.message = "L’imprimante prépare l’impression (chauffe, calibrage)."
            return
        # never seen printing: the start command was ignored, nothing was printed
        job.status = JobStatus.NEEDS_ATTENTION
        job.started_at = None
        add_event(job, "attention", "L’imprimante n’a pas démarré l’impression.")
        await ha.notify(
            opts["notify_targets"],
            f"{job.printer.name} : impression non démarrée",
            f"{job.sliced_file.model.name} a été envoyée mais l’imprimante ne l’a pas lancée.",
            tag=f"printy-job-{job.id}",
        )

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
        """For a good print, the pieces go to stock, in the spool's colour."""
        self._metered_at.pop(job.id, None)
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
            self._record_usage(job, self._last_progress.pop(job.id, 0))
            add_event(job, "error", status.message or "L’imprimante signale une erreur.")
            await self._close_print(s, job, opts, success=False)
        elif status.state in (PrinterState.FINISHED, PrinterState.IDLE):
            # The print was seen running; a K2 still flickers through several states
            # after the start (idle, heating, calibration): nothing is concluded too early.
            if now - (job.started_at or now) < START_GRACE and job.id not in self._stopped:
                return
            self._stopped.discard(job.id)
            progress = self._last_progress.pop(job.id, 0)
            done = status.state == PrinterState.FINISHED or progress > 0.98
            job.finished_at = now
            job.printer.last_finished_at = now
            self._record_usage(job, 1.0 if done else progress)
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
        image = await self.capture(job)
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

    # -- morning spool reminder ----------------------------------------------

    async def _morning_reminder(self, s: Session, opts: dict) -> None:
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
            .where(Job.status.in_([st.value for st in OPEN_STATUSES if st != JobStatus.NEEDS_ATTENTION]))
            .options(joinedload(Job.sliced_file), joinedload(Job.printer))
        ).unique().all()
        spools = await spoolman.spools_or_empty()
        by_id = {sp.id: sp for sp in spools}
        loaded = {p.id: p.loaded_spool_id for p in s.query(Printer).all()}
        fil = spoolman_plan(list(open_jobs), loaded, spools)
        lines: list[str] = []
        swaps: list[Job] = []
        for job in sorted(open_jobs, key=lambda j: j.start_at):
            info = fil.get(job.id)
            if info is None or job.status != JobStatus.SCHEDULED or job.start_at > horizon:
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

    # -- filament consumption -----------------------------------------------

    @staticmethod
    def _record_usage(job: Job, share: float) -> None:
        grams = job.sliced_file.filament_g
        if grams and job.spool_id is not None and share > 0:
            job.filament_used_g = round(grams * min(1.0, share), 2)
            if manager.tracked(job.printer_id):
                # Moonraker already reported the real extruded length to Spoolman
                job.spool_reported = True
                add_event(job, "spool", "Consommation déduite par Klipper (Moonraker) dans Spoolman.")

    async def _report_usage(self, s: Session) -> None:
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

    # -- power off ----------------------------------------------------------

    async def _maybe_power_off(self, s: Session, printer: Printer, opts: dict) -> None:
        if not printer.switch_entity or printer.id not in self._powered_by_us:
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
        status = manager.status(printer.id)
        if status.state in (PrinterState.PRINTING, PrinterState.PAUSED, PrinterState.BUSY):
            return
        temps = [t for t in (status.nozzle_temp, status.bed_temp) if t is not None]
        if any(t >= printer.cooldown_temp for t in temps):
            return
        await ha.set_power(printer.switch_entity, False)
        self._powered_by_us.discard(printer.id)
        log.info("%s cooled down, power off", printer.name)

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
                {"START": confirm, "SNOOZE": snooze, "CANCEL": cancel}[verb](job)
            s.commit()
        self.poke()


# -- job operations shared by the API and the notification handler -------------


def confirm(job: Job) -> None:
    job.confirmed_at = utcnow()
    if job.spool_id is not None and job.printer.loaded_spool_id != job.spool_id:
        job.printer.loaded_spool_id = job.spool_id  # the user swapped the spool
        manager.want_spool(job.printer_id, job.spool_id)
    if job.status == JobStatus.AWAITING_CONFIRMATION:
        job.status = JobStatus.READY
    add_event(job, "confirmed", "Plateau vide confirmé.")


def spool_swapped(job: Job) -> None:
    """The user put the job's spool on the printer ahead of time."""
    if job.spool_id is not None:
        job.printer.loaded_spool_id = job.spool_id
        manager.want_spool(job.printer_id, job.spool_id)
        add_event(job, "spool", "Changement de bobine signalé.")


def snooze(job: Job, minutes: int = 60) -> None:
    job.start_at = max(job.start_at, utcnow()) + timedelta(minutes=minutes)
    job.status = JobStatus.SCHEDULED
    job.notified_at = None
    add_event(job, "snoozed", f"Reportée de {minutes} min.")


def cancel(job: Job) -> None:
    """Cancels in Printy. A print the printer is really running must be stopped on it first."""
    if job.status in (JobStatus.FINISHED, JobStatus.FAILED, JobStatus.CANCELLED):
        return
    if job.status in (JobStatus.PRINTING, JobStatus.PAUSED, JobStatus.STARTING):
        if scheduler.launching(job.id):
            raise ValueError("Envoi du fichier en cours : réessaie dans un instant.")
        if manager.status(job.printer_id).active:
            raise ValueError("L’imprimante imprime : arrête d’abord l’impression.")
        if job.started_at is not None:
            job.finished_at = utcnow()
    job.status = JobStatus.CANCELLED
    add_event(job, "cancelled", "Annulée.")


scheduler = Scheduler()
