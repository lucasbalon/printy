"""End-to-end life cycle of a planned print on the demo printer."""

import asyncio
import io
from datetime import timedelta

from fastapi.testclient import TestClient

from printy.db import SessionLocal, init_db, utcnow
from printy.ha import ha
from printy.main import app
from printy.models import Job
from printy.printers import manager
from printy.scheduler import scheduler

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

    r = client.post("/api/v1/upload", files=[("files", ("vague_base_K2_PLA.gcode", io.BytesIO(ORCA.encode())))])
    assert r.status_code == 200, r.text
    imported = r.json()["imported"][0]
    assert imported["model_name"] == "Vague base"
    model = client.get(f"/api/v1/models/{imported['model_id']}").json()
    sliced_id = model["sliced"][0]["id"]
    assert client.get(f"/api/v1/sliced/{sliced_id}/thumbnail").status_code == 200

    p = client.post("/api/v1/printers", json={
        "name": "Démo", "driver": "demo", "options": {"speed": 1_000_000},
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
    loop.run_until_complete(step())
    assert status() == "powering_on"
    assert loop.run_until_complete(ha.is_on("switch.prise_k2")) is True
    loop.run_until_complete(step())
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
    assert status() == "printing"

    with SessionLocal() as s:
        s.get(Job, jid).started_at = utcnow() - timedelta(minutes=5)
        s.commit()
    loop.run_until_complete(step())
    assert status() == "finished"
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
        assert s.get(Job, j2["id"]).status == "ready"
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
    assert body["imported"][0]["kind"] == "source"
    model = client.get(f"/api/v1/models/{body['imported'][0]['model_id']}").json()
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
    r = client.post("/api/v1/upload", files=[("files", ("externe.gcode", io.BytesIO(ORCA.encode() + b"; ext\n")))])
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
    r = client.post("/api/v1/upload", files=[("files", ("muette.gcode", io.BytesIO(ORCA.encode() + b"; mute\n")))])
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
    assert job().status == "printing"

    # two minutes later, still idle: heating or calibrating, keep waiting
    with SessionLocal() as s:
        s.get(Job, jid).started_at = utcnow() - timedelta(minutes=2)
        s.commit()
    loop.run_until_complete(step())
    assert job().status == "printing"

    # past the grace period: flagged, not counted as an interrupted print
    with SessionLocal() as s:
        s.get(Job, jid).started_at = utcnow() - timedelta(minutes=11)
        s.commit()
    loop.run_until_complete(step())
    assert job().status == "needs_attention"
    assert "non démarrée" in sent[-1]
    loop.close()
