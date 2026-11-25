"""End-to-end life cycle of a planned print on the demo printer."""

import asyncio
import io
from datetime import timedelta

from fastapi.testclient import TestClient

from printy.drivers import PrinterState
from printy.db import SessionLocal, init_db, utcnow
from printy.ha import ha
from printy.main import app
from printy.models import Job
from printy.printers import manager
from printy.scheduler import scheduler

from .conftest import upload_new
from .test_gcode import ORCA


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_full_cycle(monkeypatch):
    init_db()
    sent = []

    async def fake_notify(targets, title, message, **kw):
        sent.append((title, [a.action for a in kw.get("actions") or []]))

    monkeypatch.setattr(ha, "notify", fake_notify)
    client = TestClient(app)  # no lifespan: we drive the scheduler by hand

    r = upload_new(client, files=[("files", ("vague_base_K2_PLA.gcode", io.BytesIO(ORCA.encode())))])
    assert r.status_code == 200, r.text
    imported = r.json()["imported"][0]
    assert imported["model_name"] == "Vague base"
    model = client.get(f"/api/v1/models/{imported['model_id']}").json()
    sliced_id = model["sliced"][0]["id"]
    assert client.get(f"/api/v1/sliced/{sliced_id}/thumbnail").status_code == 200

    p = client.post("/api/v1/printers", json={
        "name": "Démo", "driver": "demo", "options": {"speed": 1},
        "switch_entity": "switch.prise_k2", "camera_source": "none", "power_on_lead_min": 5,
    })
    assert p.status_code == 201, p.text
    pid = p.json()["id"]

    start = utcnow() + timedelta(minutes=2)
    j = client.post("/api/v1/jobs", json={"sliced_file_id": sliced_id, "printer_id": pid, "start_at": start.isoformat()})
    assert j.status_code == 201, j.text
    jid = j.json()["id"]
    assert j.json()["confirm_required"] is True

    # overlapping slot is refused
    clash = client.post("/api/v1/jobs", json={"sliced_file_id": sliced_id, "printer_id": pid, "start_at": start.isoformat()})
    assert clash.status_code == 409

    async def step():
        await manager.sync()
        await manager.refresh_all()
        await scheduler.tick()

    def status():
        with SessionLocal() as s:
            return s.get(Job, jid).status

    loop = asyncio.new_event_loop()
    # power on and confirmation request in the same tick: the demo printer answers at once
    loop.run_until_complete(step())
    assert loop.run_until_complete(ha.is_on("switch.prise_k2")) is True
    assert status() == "awaiting_confirmation"
    assert sent[-1][1][0] == f"PRINTY_START_{jid}"

    # the user taps "Lancer" on the phone
    loop.run_until_complete(scheduler.on_notification_action(f"PRINTY_START_{jid}"))
    assert status() == "ready"
    # time passes until the start
    with SessionLocal() as s:
        s.get(Job, jid).start_at = utcnow() - timedelta(seconds=1)
        s.commit()
    loop.run_until_complete(step())
    loop.run_until_complete(asyncio.sleep(0.05))  # background launch
    assert status() == "starting"  # sent, not yet seen printing
    loop.run_until_complete(step())  # seen printing
    assert status() == "printing"

    # the print ends within the first minutes: not concluded yet (the K2 flickers at start)
    driver = manager.live[pid].driver
    driver.speed = 1_000_000
    loop.run_until_complete(step())
    assert status() == "printing"

    with SessionLocal() as s:
        s.get(Job, jid).started_at = utcnow() - timedelta(minutes=11)
        s.commit()
    loop.run_until_complete(step())
    assert status() == "finished"
    assert next(p for p in client.get("/api/v1/printers").json() if p["id"] == pid)["piece_on_bed"] is True
    assert sent[-1][1] == [f"PRINTY_BEDCLEAR_{pid}"]

    # still hot: power stays on; once cool it goes off
    loop.run_until_complete(asyncio.sleep(0.01))
    loop.run_until_complete(step())
    loop.run_until_complete(step())
    assert loop.run_until_complete(ha.is_on("switch.prise_k2")) is False

    # bed cleared from the notification: next print skips the confirmation
    loop.run_until_complete(scheduler.on_notification_action(f"PRINTY_BEDCLEAR_{pid}"))
    j2 = client.post("/api/v1/jobs", json={"sliced_file_id": sliced_id, "printer_id": pid,
                                           "start_at": utcnow().isoformat()}).json()
    loop.run_until_complete(step())
    loop.run_until_complete(step())
    with SessionLocal() as s:
        assert s.get(Job, j2["id"]).status in ("starting", "printing")  # no confirmation asked
    loop.close()


def test_chunked_upload():
    init_db()
    client = TestClient(app)
    data = b"solid lamp\n" + b"x" * (9 * 1024 * 1024) + b"\nendsolid lamp\n"
    chunk = 4 * 1024 * 1024
    parts = [data[i:i + chunk] for i in range(0, len(data), chunk)]
    uid = "a" * 32
    for i, part in enumerate(parts):
        r = client.post(
            f"/api/v1/upload/chunk?upload_id={uid}&index={i}&total={len(parts)}&filename=Lampe%20test.stl",
            content=part,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 200, r.text
    body = r.json()
    assert body["imported"][0]["kind"] == "pending"  # a new name: waits to be filed
    filed = client.post(f"/api/v1/pending/{body['imported'][0]['pending_id']}/file", json={}).json()
    assert filed["kind"] == "source" and filed["model_name"] == "Lampe test"
    model = client.get(f"/api/v1/models/{filed['model_id']}").json()
    assert model["sources"][0]["size"] == len(data)
    # a chunk out of sequence is refused
    r = client.post(f"/api/v1/upload/chunk?upload_id={'b' * 32}&index=1&total=2&filename=x.stl", content=b"x")
    assert r.status_code == 409


def test_print_started_outside_printy():
    """A print launched from the slicer or the screen shows up with its live end time."""
    init_db()
    client = TestClient(app)
    pid = client.post("/api/v1/printers", json={
        "name": "Externe", "driver": "demo", "options": {"speed": 1}, "camera_source": "none",
    }).json()["id"]
    r = upload_new(client, files=[("files", ("externe.gcode", io.BytesIO(ORCA.encode() + b"; ext\n")))])
    sliced_id = client.get(f"/api/v1/models/{r.json()['imported'][0]['model_id']}").json()["sliced"][0]["id"]

    loop = asyncio.new_event_loop()
    loop.run_until_complete(manager.sync())
    driver = manager.live[pid].driver
    loop.run_until_complete(driver.upload(__import__("pathlib").Path(__file__), "vase.gcode"))
    driver.files["vase.gcode"] = 7200  # a 2 h print, started from the screen
    loop.run_until_complete(driver.start("vase.gcode"))
    loop.run_until_complete(manager.refresh_all())

    live = next(p for p in client.get("/api/v1/printers").json() if p["id"] == pid)["live"]
    assert live["state"] == "printing" and live["job_id"] is None
    assert live["print_started_at"] and live["print_ends_at"]

    # planning on top of it is refused with a clear message
    now = client.post("/api/v1/jobs", json={"sliced_file_id": sliced_id, "printer_id": pid, "start_at": utcnow().isoformat()})
    assert now.status_code == 409 and "vase.gcode" in now.json()["detail"]
    later = client.post("/api/v1/jobs", json={
        "sliced_file_id": sliced_id, "printer_id": pid, "start_at": (utcnow() + timedelta(hours=3)).isoformat(),
    })
    assert later.status_code == 201
    loop.run_until_complete(driver.cancel())
    loop.close()


def test_ignored_start_is_not_taken_for_a_cancel(monkeypatch):
    """The printer accepts the file but never starts: wait, then flag it (no false cancel)."""
    init_db()
    sent = []

    async def fake_notify(targets, title, message, **kw):
        sent.append(title)

    monkeypatch.setattr(ha, "notify", fake_notify)
    client = TestClient(app)
    pid = client.post("/api/v1/printers", json={
        "name": "Muette", "driver": "demo", "options": {"speed": 1}, "camera_source": "none",
    }).json()["id"]
    r = upload_new(client, files=[("files", ("muette.gcode", io.BytesIO(ORCA.encode() + b"; mute\n")))])
    sliced_id = client.get(f"/api/v1/models/{r.json()['imported'][0]['model_id']}").json()["sliced"][0]["id"]
    jid = client.post("/api/v1/jobs", json={
        "sliced_file_id": sliced_id, "printer_id": pid, "start_at": utcnow().isoformat(), "confirm_required": False,
    }).json()["id"]

    async def step():
        await manager.sync()
        await manager.refresh_all()
        await scheduler.tick()

    def job():
        with SessionLocal() as s:
            return s.get(Job, jid)

    loop = asyncio.new_event_loop()
    loop.run_until_complete(manager.sync())

    async def ignored(name):
        return None

    monkeypatch.setattr(manager.live[pid].driver, "start", ignored)
    for _ in range(4):
        loop.run_until_complete(step())
        loop.run_until_complete(asyncio.sleep(0.05))
    assert job().status == "starting"

    # two minutes later, still idle: heating or calibrating, keep waiting
    with SessionLocal() as s:
        s.get(Job, jid).started_at = utcnow() - timedelta(minutes=2)
        s.commit()
    loop.run_until_complete(step())
    assert job().status == "starting"

    # a stale "finished" from the previous print is not taken for the end of this one
    manager.live[pid].driver.state = PrinterState.FINISHED
    loop.run_until_complete(step())
    assert job().status == "starting"

    # past the grace period: flagged, not counted as a finished or interrupted print
    with SessionLocal() as s:
        s.get(Job, jid).started_at = utcnow() - timedelta(minutes=11)
        s.commit()
    loop.run_until_complete(step())
    assert job().status == "needs_attention"
    assert "à revoir" in sent[-1]  # the phone is told
    loop.close()


def _setup(client, name):
    pid = client.post("/api/v1/printers", json={
        "name": name, "driver": "demo", "options": {"speed": 1}, "camera_source": "none",
    }).json()["id"]
    r = upload_new(client, files=[("files", (f"{name}.gcode", io.BytesIO(ORCA.encode() + f"; {name}\n".encode())))])
    sliced_id = client.get(f"/api/v1/models/{r.json()['imported'][0]['model_id']}").json()["sliced"][0]["id"]
    return pid, sliced_id


async def _step():
    await manager.sync()
    await manager.refresh_all()
    await scheduler.tick()


def _status(jid):
    with SessionLocal() as s:
        return s.get(Job, jid).status


def test_print_now_skips_waiting_and_confirmation(monkeypatch):
    """"Print now" starts at once, without a bed notification, whatever the planned time."""
    init_db()
    sent = []

    async def fake_notify(targets, title, message, **kw):
        sent.append(title)

    monkeypatch.setattr(ha, "notify", fake_notify)
    client = TestClient(app)
    pid, sliced_id = _setup(client, "direct")
    later = (utcnow() + timedelta(minutes=30)).isoformat()
    r = client.post("/api/v1/jobs", json={
        "sliced_file_id": sliced_id, "printer_id": pid, "start_at": later, "confirm_required": True, "start_now": True,
    })
    assert r.status_code == 201, r.text
    jid = r.json()["id"]
    loop = asyncio.new_event_loop()
    for _ in range(3):
        loop.run_until_complete(_step())
        loop.run_until_complete(asyncio.sleep(0.05))
    assert _status(jid) == "printing"
    assert sent == []
    loop.run_until_complete(manager.live[pid].driver.cancel())
    loop.close()


def test_stuck_start_can_be_cancelled_and_removed(monkeypatch):
    """A start the printer ignores never blocks the planning: cancel, then remove."""
    init_db()
    client = TestClient(app)
    pid, sliced_id = _setup(client, "bloquee")
    jid = client.post("/api/v1/jobs", json={
        "sliced_file_id": sliced_id, "printer_id": pid, "start_at": utcnow().isoformat(), "start_now": True,
    }).json()["id"]
    loop = asyncio.new_event_loop()
    loop.run_until_complete(manager.sync())

    async def ignored(name):
        return None

    monkeypatch.setattr(manager.live[pid].driver, "start", ignored)
    for _ in range(3):
        loop.run_until_complete(_step())
        loop.run_until_complete(asyncio.sleep(0.05))
    assert _status(jid) == "starting"

    r = client.post(f"/api/v1/jobs/{jid}/cancel")
    assert r.status_code == 200, r.text
    assert _status(jid) == "cancelled"
    assert client.delete(f"/api/v1/jobs/{jid}").status_code == 204

    # the printer is free again for the next print
    again = client.post("/api/v1/jobs", json={
        "sliced_file_id": sliced_id, "printer_id": pid, "start_at": utcnow().isoformat(), "start_now": True,
    })
    assert again.status_code == 201, again.text
    loop.close()


def test_running_print_cannot_be_cancelled_from_printy_only():
    init_db()
    client = TestClient(app)
    pid, sliced_id = _setup(client, "encours")
    jid = client.post("/api/v1/jobs", json={
        "sliced_file_id": sliced_id, "printer_id": pid, "start_at": utcnow().isoformat(), "start_now": True,
    }).json()["id"]
    loop = asyncio.new_event_loop()
    for _ in range(3):
        loop.run_until_complete(_step())
        loop.run_until_complete(asyncio.sleep(0.05))
    assert _status(jid) == "printing"
    assert client.post(f"/api/v1/jobs/{jid}/cancel").status_code == 409

    # once stopped on the printer, the job can be closed even before Printy notices
    loop.run_until_complete(manager.live[pid].driver.cancel())
    loop.run_until_complete(manager.refresh_all())
    assert client.post(f"/api/v1/jobs/{jid}/cancel").status_code == 200
    loop.close()


def test_upload_interrupted_by_a_restart_is_flagged():
    init_db()
    client = TestClient(app)
    pid, sliced_id = _setup(client, "redemarrage")
    jid = client.post("/api/v1/jobs", json={
        "sliced_file_id": sliced_id, "printer_id": pid, "start_at": utcnow().isoformat(), "start_now": True,
    }).json()["id"]
    with SessionLocal() as s:  # as left by a Printy stopped in the middle of the upload
        s.get(Job, jid).status = "starting"
        s.commit()
    loop = asyncio.new_event_loop()
    loop.run_until_complete(_step())
    assert _status(jid) == "needs_attention"
    assert client.delete(f"/api/v1/jobs/{jid}").status_code == 204
    loop.close()


def test_printer_just_switched_on_gets_time_to_boot(monkeypatch):
    init_db()
    client = TestClient(app)
    pid, sliced_id = _setup(client, "allumage")
    jid = client.post("/api/v1/jobs", json={
        "sliced_file_id": sliced_id, "printer_id": pid, "start_at": utcnow().isoformat(), "start_now": True,
    }).json()["id"]
    loop = asyncio.new_event_loop()
    loop.run_until_complete(manager.sync())
    driver = manager.live[pid].driver
    real_status = driver.status
    off = {"v": True}

    async def status():
        from printy.drivers import PrinterStatus
        return PrinterStatus() if off["v"] else await real_status()

    monkeypatch.setattr(driver, "status", status)
    loop.run_until_complete(_step())
    assert _status(jid) == "powering_on"
    off["v"] = False  # it answers: still booting
    loop.run_until_complete(_step())
    assert _status(jid) == "powering_on"
    scheduler._online_at[pid] -= 121  # past the boot time
    for _ in range(3):
        loop.run_until_complete(_step())
        loop.run_until_complete(asyncio.sleep(0.05))
    assert _status(jid) == "printing"
    loop.run_until_complete(driver.cancel())
    loop.close()


def test_stop_from_printy_closes_the_job_at_once():
    """Stopping from Printy does not wait for the start grace: the job is closed right away."""
    init_db()
    client = TestClient(app)
    pid, sliced_id = _setup(client, "arret")
    jid = client.post("/api/v1/jobs", json={
        "sliced_file_id": sliced_id, "printer_id": pid, "start_at": utcnow().isoformat(), "start_now": True,
    }).json()["id"]
    loop = asyncio.new_event_loop()
    for _ in range(3):
        loop.run_until_complete(_step())
        loop.run_until_complete(asyncio.sleep(0.05))
    assert _status(jid) == "printing"
    assert client.post(f"/api/v1/printers/{pid}/action", json={"action": "cancel"}).status_code == 200
    loop.run_until_complete(_step())
    assert _status(jid) == "cancelled"
    # the printer is free for the next print right away
    assert client.post("/api/v1/jobs", json={
        "sliced_file_id": sliced_id, "printer_id": pid, "start_at": utcnow().isoformat(), "start_now": True,
    }).status_code == 201
    loop.close()
