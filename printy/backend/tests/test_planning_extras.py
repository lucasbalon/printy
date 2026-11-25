"""Off-peak slot, spool check before launch, forgotten piece, bed photo, backup."""

import asyncio
import io
import sqlite3
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from printy import bedcheck, offpeak
from printy.backup import backup_path, restore_if_broken, snapshot
from printy.config import config
from printy.db import SessionLocal, init_db, utcnow
from printy.ha import ha
from printy.main import app
from printy.models import Job, Printer
from printy.printers import manager
from printy.scheduler import scheduler

from .test_gcode import ORCA

TZ = config.timezone


def _setup(client, name):
    pid = client.post("/api/v1/printers", json={
        "name": name, "driver": "demo", "options": {"speed": 1}, "camera_source": "none",
    }).json()["id"]
    r = client.post("/api/v1/upload", files=[("files", (f"{name}.gcode", io.BytesIO(ORCA.encode() + f"; {name}\n".encode())))])
    sliced_id = client.get(f"/api/v1/models/{r.json()['imported'][0]['model_id']}").json()["sliced"][0]["id"]
    return pid, sliced_id


async def _step():
    await manager.sync()
    await manager.refresh_all()
    await scheduler.tick()


def _status(jid):
    with SessionLocal() as s:
        return s.get(Job, jid).status


def _notifier(monkeypatch):
    sent = []

    async def fake_notify(targets, title, message, **kw):
        sent.append((title, message, [a.action for a in kw.get("actions") or []]))

    monkeypatch.setattr(ha, "notify", fake_notify)
    return sent


# -- off-peak --------------------------------------------------------------------


def test_best_off_peak_slot():
    ranges = [{"start": "22:00", "end": "06:00"}]
    now = datetime(2026, 11, 25, 15, 0, tzinfo=TZ)
    start, share = offpeak.best_slot(ranges, timedelta(hours=4), [], now)
    assert start == datetime(2026, 11, 25, 22, 0, tzinfo=TZ) and share == 1.0
    # tonight's beginning is taken: right after the other print
    busy = [(datetime(2026, 11, 25, 22, 0, tzinfo=TZ), datetime(2026, 11, 26, 1, 0, tzinfo=TZ))]
    start, share = offpeak.best_slot(ranges, timedelta(hours=4), busy, now)
    assert start == datetime(2026, 11, 26, 1, 0, tzinfo=TZ) and share == 1.0
    # longer than the night: as much of it as possible, as early as possible
    start, share = offpeak.best_slot(ranges, timedelta(hours=10), [], now)
    assert share == 0.8 and start <= datetime(2026, 11, 25, 22, 0, tzinfo=TZ)


# -- spool ------------------------------------------------------------------------


def test_preview_sees_the_spool_of_the_print_before():
    init_db()
    client = TestClient(app)
    pid, sliced_id = _setup(client, "apercu")
    with SessionLocal() as s:
        s.get(Printer, pid).loaded_spool_id = 1
        s.commit()
    first = (utcnow() + timedelta(hours=2)).isoformat()
    jid = client.post("/api/v1/jobs", json={"sliced_file_id": sliced_id, "printer_id": pid, "start_at": first, "spool_id": 2}).json()["id"]
    later = (utcnow() + timedelta(hours=6)).isoformat()
    p = client.post("/api/v1/jobs/preview", json={"sliced_file_id": sliced_id, "printer_id": pid, "start_at": later, "spool_id": 2}).json()
    assert p["spool_change"] is False  # spool 2 will already be on after the first print
    p = client.post("/api/v1/jobs/preview", json={"sliced_file_id": sliced_id, "printer_id": pid, "start_at": later, "spool_id": 1}).json()
    assert p["spool_change"] is True and p["previous_spool_id"] == 2
    client.delete(f"/api/v1/jobs/{jid}")  # leaves the planning as it was for the other tests


def test_launch_waits_when_the_printer_has_another_spool(monkeypatch):
    """Launched at once (bed vouched for), but the printer still has another spool:
    Printy asks and waits."""
    init_db()
    sent = _notifier(monkeypatch)
    client = TestClient(app)
    pid, sliced_id = _setup(client, "bobine")
    with SessionLocal() as s:
        s.get(Printer, pid).loaded_spool_id = 1
        s.commit()
    jid = client.post("/api/v1/jobs", json={
        "sliced_file_id": sliced_id, "printer_id": pid, "start_at": utcnow().isoformat(), "start_now": True, "spool_id": 2,
    }).json()["id"]
    loop = asyncio.new_event_loop()
    loop.run_until_complete(_step())
    assert _status(jid) == "awaiting_confirmation"
    assert sent[-1][2][0] == f"PRINTY_START_{jid}" and "bobine" in sent[-1][0]
    loop.run_until_complete(scheduler.on_notification_action(f"PRINTY_START_{jid}"))
    for _ in range(2):
        loop.run_until_complete(_step())
        loop.run_until_complete(asyncio.sleep(0.05))
    assert _status(jid) in ("starting", "printing")
    loop.run_until_complete(manager.live[pid].driver.cancel())
    loop.close()


# -- forgotten piece --------------------------------------------------------------


def test_forgotten_piece_reminder(monkeypatch):
    init_db()
    sent = _notifier(monkeypatch)
    client = TestClient(app)
    pid, sliced_id = _setup(client, "oubli")
    with SessionLocal() as s:
        s.get(Printer, pid).last_finished_at = utcnow() - timedelta(hours=1)
        s.commit()
    client.post("/api/v1/jobs", json={
        "sliced_file_id": sliced_id, "printer_id": pid, "start_at": (utcnow() + timedelta(hours=3)).isoformat(),
    })
    loop = asyncio.new_event_loop()
    loop.run_until_complete(scheduler.tick())
    assert not [m for m in sent if "oubli" in m[0]]  # 3 h ahead: not yet
    with SessionLocal() as s:
        s.query(Job).filter(Job.printer_id == pid).one().start_at = utcnow() + timedelta(hours=1)
        s.commit()
    loop.run_until_complete(scheduler.tick())
    loop.run_until_complete(scheduler.tick())
    mine = [m for m in sent if m[0].startswith("oubli")]
    assert len(mine) == 1 and mine[0][2] == [f"PRINTY_BEDCLEAR_{pid}"]
    loop.close()


def test_forgotten_piece_reminder_can_be_turned_off(monkeypatch):
    init_db()
    sent = _notifier(monkeypatch)
    client = TestClient(app)
    pid, sliced_id = _setup(client, "sans_rappel")
    client.patch("/api/v1/settings", json={"bed_reminder": False})
    try:
        with SessionLocal() as s:
            s.get(Printer, pid).last_finished_at = utcnow() - timedelta(hours=1)
            s.commit()
        client.post("/api/v1/jobs", json={
            "sliced_file_id": sliced_id, "printer_id": pid, "start_at": (utcnow() + timedelta(hours=1)).isoformat(),
        })
        asyncio.new_event_loop().run_until_complete(scheduler.tick())
        assert not [m for m in sent if m[0].startswith("sans_rappel")]
    finally:
        client.patch("/api/v1/settings", json={"bed_reminder": True})


# -- bed photo --------------------------------------------------------------------


def _bed(piece: bool, light: int = 0) -> bytes:
    img = Image.new("RGB", (640, 480), (90 + light, 90 + light, 95 + light))
    d = ImageDraw.Draw(img)
    for x in range(0, 640, 40):  # textured plate
        d.line([(x, 0), (x, 480)], fill=(70 + light, 70 + light, 75 + light), width=2)
    if piece:
        d.rectangle([250, 180, 390, 300], fill=(230, 60, 40))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def test_bed_photo_comparison():
    bedcheck.save_reference(9999, _bed(False))
    assert bedcheck.verdict(9999, _bed(False, light=25)) == "empty"  # brighter room
    assert bedcheck.verdict(9999, _bed(True)) == "occupied"
    assert bedcheck.verdict(12345, _bed(True)) is None  # no reference yet


# -- backup -----------------------------------------------------------------------


def test_backup_copy_and_restore(tmp_path):
    db = tmp_path / "printy.db"
    con = sqlite3.connect(db)
    con.execute("create table t (v int)")
    con.execute("insert into t values (42)")
    con.commit()
    con.close()
    snapshot(db, backup_path(db))
    assert not restore_if_broken(db)  # healthy: kept
    db.write_bytes(b"not a database at all" * 100)
    assert restore_if_broken(db)
    assert sqlite3.connect(db).execute("select v from t").fetchone() == (42,)


def test_export_endpoint():
    init_db()
    r = TestClient(app).get("/api/v1/backup")
    assert r.status_code == 200 and r.content[:16] == b"SQLite format 3\x00"
