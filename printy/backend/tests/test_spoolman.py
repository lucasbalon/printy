"""Spool matching, spool-change confirmation and consumption reporting."""

import asyncio
import io
from datetime import timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from printy import settings as settings_store
from printy.db import SessionLocal, init_db, utcnow
from printy.ha import ha
from printy.main import app
from printy.models import Job, Printer
from printy.printers import manager
from printy.scheduler import scheduler
from printy.spoolman import Spool, color_distance, plan, spoolman, suggest

from .conftest import upload_new
from .test_gcode import ORCA  # beige PLA, 37.25 g

WHITE = Spool(1, "Blanc", "Elegoo", "PLA", "#FFFFFF", 500.0, None)
BLACK = Spool(2, "Noir", "Elegoo", "PLA", "#1A1A1A", 50.0, None)
WHITE_PETG = Spool(3, "Blanc", "Sunlu", "PETG", "#FFFFFF", 900.0, None)


def test_colour_matching():
    assert color_distance("#F5F5DC", "#FFFFFF") < color_distance("#F5F5DC", "#1A1A1A")
    # same colour but wrong material loses to the right material
    assert suggest([WHITE_PETG, WHITE, BLACK], "#F5F5DC", "PLA", None) is WHITE
    # nothing close enough: no suggestion rather than a wrong one
    assert suggest([BLACK], "#FFFFFF", "PLA", None) is None


def test_plan_changes_and_shortage():
    def job(i, printer, spool, grams, hours):
        return SimpleNamespace(
            id=i, printer_id=printer, spool_id=spool, start_at=utcnow() + timedelta(hours=hours),
            sliced_file=SimpleNamespace(filament_g=grams),
        )

    jobs = [job(1, 1, 2, 30, 1), job(2, 1, 2, 30, 3), job(3, 1, 1, 100, 5)]
    out = plan(jobs, {1: 2}, [WHITE, BLACK])
    assert not out[1].spool_change and not out[1].low_filament
    assert out[2].low_filament  # 50 g - 30 g = 20 g left, 30 g needed
    assert out[3].spool_change and out[3].previous_spool_id == 2


def test_plan_running_job_counts_only_what_is_left():
    # 60 g print, half done: Moonraker already took 30 g off the spool in Spoolman
    running = SimpleNamespace(
        id=1, printer_id=1, spool_id=2, start_at=utcnow() - timedelta(hours=1),
        sliced_file=SimpleNamespace(filament_g=60),
    )
    # Spoolman shows 50 g left; 30 g of the 60 g are still to print
    assert plan([running], {1: 2}, [BLACK])[1].low_filament
    out = plan([running], {1: 2}, [BLACK], {1: 0.5})
    assert not out[1].low_filament
    assert out[1].need_g == 30


def test_spool_change_flow(monkeypatch):
    init_db()
    sent, used = [], []

    async def fake_notify(targets, title, message, **kw):
        sent.append((title, message, [a.action for a in kw.get("actions") or []]))

    async def fake_spools(fresh=False):
        return [WHITE, BLACK]

    async def fake_use(spool_id, grams):
        used.append((spool_id, grams))

    monkeypatch.setattr(ha, "notify", fake_notify)
    monkeypatch.setattr(spoolman, "spools", fake_spools)
    monkeypatch.setattr(spoolman, "use", fake_use)
    client = TestClient(app)
    assert client.patch("/api/v1/settings", json={"spoolman_url": "http://spoolman.test:7912"}).status_code == 200

    r = upload_new(client, files=[("files", ("cube_spool.gcode", io.BytesIO(ORCA.encode() + b"; spool test\n")))])
    sliced_id = client.get(f"/api/v1/models/{r.json()['imported'][0]['model_id']}").json()["sliced"][0]["id"]
    pid = client.post("/api/v1/printers", json={
        "name": "Démo bobines", "driver": "demo", "options": {"speed": 1_000_000}, "camera_source": "none",
    }).json()["id"]
    assert client.post(f"/api/v1/printers/{pid}/spool", json={"spool_id": BLACK.id}).json()["loaded_spool_id"] == 2

    # confirmation disabled, but the spool must change: Printy still asks
    job = client.post("/api/v1/jobs", json={
        "sliced_file_id": sliced_id, "printer_id": pid, "start_at": utcnow().isoformat(), "confirm_required": False,
    }).json()
    assert job["spool_id"] == WHITE.id  # suggested from the file colour
    listed = next(j for j in client.get("/api/v1/jobs").json() if j["id"] == job["id"])
    assert listed["spool_change"] and listed["previous_spool_id"] == BLACK.id

    async def step():
        await manager.sync()
        await manager.refresh_all()
        await scheduler.tick()

    loop = asyncio.new_event_loop()
    loop.run_until_complete(step())
    loop.run_until_complete(step())
    with SessionLocal() as s:
        assert s.get(Job, job["id"]).status == "awaiting_confirmation"
    title, message, actions = sent[-1]
    assert "change la bobine" in title and "Elegoo Blanc" in message

    loop.run_until_complete(scheduler.on_notification_action(f"PRINTY_START_{job['id']}"))
    with SessionLocal() as s:
        assert s.get(Printer, pid).loaded_spool_id == WHITE.id
    loop.run_until_complete(step())
    loop.run_until_complete(asyncio.sleep(0.05))  # file sent
    loop.run_until_complete(step())  # seen printing
    with SessionLocal() as s:
        j = s.get(Job, job["id"])
        assert j.status == "printing"
        j.started_at = utcnow() - timedelta(minutes=11)  # past the start grace
        s.commit()
    loop.run_until_complete(step())
    with SessionLocal() as s:
        j = s.get(Job, job["id"])
        assert j.status == "finished" and j.filament_used_g == 37.25 and j.spool_reported
    assert used == [(WHITE.id, 37.25)]
    loop.close()
    client.patch("/api/v1/settings", json={"spoolman_url": ""})


def test_morning_reminder(monkeypatch):
    init_db()
    sent = []

    async def fake_notify(targets, title, message, **kw):
        sent.append((title, message, [a.action for a in kw.get("actions") or []]))

    async def fake_spools(fresh=False):
        return [WHITE, BLACK]

    monkeypatch.setattr(ha, "notify", fake_notify)
    monkeypatch.setattr(spoolman, "spools", fake_spools)
    client = TestClient(app)
    client.patch("/api/v1/settings", json={"spoolman_url": "http://spoolman.test:7912", "spool_reminder_morning": "00:00"})

    r = upload_new(client, files=[("files", ("matin.gcode", io.BytesIO(ORCA.encode() + b"; morning\n")))])
    sliced_id = client.get(f"/api/v1/models/{r.json()['imported'][0]['model_id']}").json()["sliced"][0]["id"]
    pid = client.post("/api/v1/printers", json={"name": "Matin", "driver": "demo", "camera_source": "none"}).json()["id"]
    client.post(f"/api/v1/printers/{pid}/spool", json={"spool_id": BLACK.id})
    job = client.post("/api/v1/jobs", json={
        "sliced_file_id": sliced_id, "printer_id": pid, "start_at": (utcnow() + timedelta(hours=3)).isoformat(),
    }).json()
    assert job["spool_id"] == WHITE.id

    with SessionLocal() as s:  # earlier tests may already have sent today's summary
        settings_store.set_many(s, {"spool_reminder_sent_on": None})
    loop = asyncio.new_event_loop()
    loop.run_until_complete(scheduler.tick())
    reminders = [m for m in sent if m[0] == "Printy : bobines à préparer"]
    assert len(reminders) == 1 and "Elegoo Blanc" in reminders[0][1]
    assert reminders[0][2] == [f"PRINTY_SPOOL_{job['id']}"]
    loop.run_until_complete(scheduler.tick())  # not twice the same day
    assert len([m for m in sent if m[0] == "Printy : bobines à préparer"]) == 1

    loop.run_until_complete(scheduler.on_notification_action(f"PRINTY_SPOOL_{job['id']}"))
    with SessionLocal() as s:
        assert s.get(Printer, pid).loaded_spool_id == WHITE.id
    listed = next(j for j in client.get("/api/v1/jobs").json() if j["id"] == job["id"])
    assert not listed["spool_change"]
    loop.close()
    client.patch("/api/v1/settings", json={"spoolman_url": "", "spool_reminder_morning": "08:00"})


def test_klipper_tracks_the_spool(monkeypatch):
    """With Moonraker's Spoolman module, the printer is the reference and reports usage."""
    from printy.drivers.base import SpoolTracking
    from printy.drivers.demo import DemoDriver

    init_db()
    active = {"id": BLACK.id}
    used = []

    async def tracking():
        return SpoolTracking(connected=True, spool_id=active["id"])

    async def set_active(spool_id):
        active["id"] = spool_id
        return True

    async def fake_notify(*a, **kw):
        pass

    async def fake_spools(fresh=False):
        return [WHITE, BLACK]

    async def fake_use(spool_id, grams):
        used.append((spool_id, grams))

    monkeypatch.setattr(ha, "notify", fake_notify)
    monkeypatch.setattr(spoolman, "spools", fake_spools)
    monkeypatch.setattr(spoolman, "use", fake_use)
    client = TestClient(app)
    client.patch("/api/v1/settings", json={"spoolman_url": "http://spoolman.test:7912"})

    pid = client.post("/api/v1/printers", json={
        "name": "Klipper", "driver": "demo", "options": {"speed": 1_000_000}, "camera_source": "none",
    }).json()["id"]
    loop = asyncio.new_event_loop()
    loop.run_until_complete(manager.sync())
    driver = manager.live[pid].driver  # only this printer runs Moonraker's Spoolman module
    assert isinstance(driver, DemoDriver)
    monkeypatch.setattr(driver, "spool_tracking", tracking)
    monkeypatch.setattr(driver, "set_active_spool", set_active)

    async def refresh():
        await manager.sync()
        await manager.refresh_all()

    # the spool set on the printer (Fluidd, screen) shows up in Printy
    loop.run_until_complete(refresh())
    with SessionLocal() as s:
        assert s.get(Printer, pid).loaded_spool_id == BLACK.id
    assert client.get("/api/v1/printers").json()[-1]["live"]["spool_tracked"] is True

    # a spool chosen in Printy is pushed to the printer
    client.post(f"/api/v1/printers/{pid}/spool", json={"spool_id": WHITE.id})
    loop.run_until_complete(refresh())
    assert active["id"] == WHITE.id

    # a finished print is not deducted twice
    r = upload_new(client, files=[("files", ("klipper.gcode", io.BytesIO(ORCA.encode() + b"; klipper\n")))])
    sliced_id = client.get(f"/api/v1/models/{r.json()['imported'][0]['model_id']}").json()["sliced"][0]["id"]
    job = client.post("/api/v1/jobs", json={
        "sliced_file_id": sliced_id, "printer_id": pid, "start_at": utcnow().isoformat(), "confirm_required": False,
    }).json()
    for _ in range(3):
        loop.run_until_complete(refresh())
        loop.run_until_complete(scheduler.tick())
        loop.run_until_complete(asyncio.sleep(0.05))
    with SessionLocal() as s:
        j = s.get(Job, job["id"])
        j.started_at = utcnow() - timedelta(minutes=11)  # past the start grace
        s.commit()
    loop.run_until_complete(refresh())
    loop.run_until_complete(scheduler.tick())
    with SessionLocal() as s:
        j = s.get(Job, job["id"])
        assert j.status == "finished" and j.spool_reported
    assert used == []
    loop.close()
    client.patch("/api/v1/settings", json={"spoolman_url": ""})


def test_choice_among_spools_of_the_same_colour():
    opened = Spool(10, "Blanc", "Elegoo", "PLA", "#FFFFFF", 180.0, None)
    almost_empty = Spool(11, "Blanc", "Elegoo", "PLA", "#FFFFFF", 30.0, None)
    new = Spool(12, "Blanc", "Elegoo", "PLA", "#FFFFFF", 1000.0, None)
    spools = [new, almost_empty, opened]
    # the opened spool with just enough is used up first, the new one stays sealed
    assert suggest(spools, "#FFFFFF", "PLA", None, need_g=150) is opened
    # too little everywhere but on the new spool
    assert suggest(spools, "#FFFFFF", "PLA", None, need_g=400) is new
    # the spool already on the printer wins when it has enough
    assert suggest(spools, "#FFFFFF", "PLA", loaded_id=12, need_g=150) is new
    assert suggest(spools, "#FFFFFF", "PLA", loaded_id=11, need_g=150) is opened


def test_describe_spool():
    from printy.spoolman import describe

    sp = Spool(12, "Blanc mat", "Elegoo", "PLA", "#F4F1EA", 820.4, "Étagère A")
    assert describe(sp) == "la bobine n° 12 Elegoo Blanc mat (PLA, 820 g, Étagère A)"
