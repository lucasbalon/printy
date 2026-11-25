"""Automatic power off: only after a print launched by Printy, on live data."""

import asyncio
import io
from datetime import timedelta

from fastapi.testclient import TestClient

from printy.db import SessionLocal, init_db, utcnow
from printy.drivers import PrinterStatus
from printy.ha import ha
from printy.main import app
from printy.models import Job, Printer
from printy.printers import manager
from printy.scheduler import scheduler

from .conftest import upload_new
from .test_gcode import ORCA


def _setup(client, name):
    switch = f"switch.prise_{name}"
    pid = client.post("/api/v1/printers", json={
        "name": name, "driver": "demo", "options": {"speed": 1}, "camera_source": "none",
        "switch_entity": switch,
    }).json()["id"]
    r = upload_new(client, files=[("files", (f"{name}.gcode", io.BytesIO(ORCA.encode() + f"; {name}\n".encode())))])
    sliced_id = client.get(f"/api/v1/models/{r.json()['imported'][0]['model_id']}").json()["sliced"][0]["id"]
    return pid, sliced_id, switch


async def _step():
    await manager.sync()
    await manager.refresh_all()
    await scheduler.tick()


def _plug(loop, switch):
    return loop.run_until_complete(ha.is_on(switch))


def _pending(pid):
    with SessionLocal() as s:
        return s.get(Printer, pid).power_off_pending


def _print_from_printy(client, loop, pid, sliced_id):
    """Launches a print from Printy and lets it end; the printer is then cool."""
    jid = client.post("/api/v1/jobs", json={
        "sliced_file_id": sliced_id, "printer_id": pid, "start_at": utcnow().isoformat(), "start_now": True,
    }).json()["id"]
    for _ in range(3):
        loop.run_until_complete(_step())
        loop.run_until_complete(asyncio.sleep(0.05))
    with SessionLocal() as s:
        assert s.get(Job, jid).status == "printing"
        s.get(Job, jid).started_at = utcnow() - timedelta(minutes=11)
        s.commit()
    manager.live[pid].driver.speed = 1_000_000  # ends and cools at once
    loop.run_until_complete(_step())
    with SessionLocal() as s:
        assert s.get(Job, jid).status == "finished"
    return jid


def test_plug_already_on_is_cut_after_a_printy_print():
    """The printer was switched on by hand: Printy still cuts it after its own print."""
    init_db()
    client = TestClient(app)
    pid, sliced_id, switch = _setup(client, "deja_allumee")
    loop = asyncio.new_event_loop()
    loop.run_until_complete(ha.set_power(switch, True))
    _print_from_printy(client, loop, pid, sliced_id)
    assert _pending(pid) is True  # stored: survives a restart of Printy
    loop.run_until_complete(asyncio.sleep(0.01))
    loop.run_until_complete(_step())
    assert _plug(loop, switch) is False
    assert _pending(pid) is False
    loop.close()


def test_print_started_elsewhere_never_cuts():
    init_db()
    client = TestClient(app)
    pid, _, switch = _setup(client, "externe_prise")
    loop = asyncio.new_event_loop()
    loop.run_until_complete(ha.set_power(switch, True))
    loop.run_until_complete(manager.sync())
    driver = manager.live[pid].driver
    driver.files["vase.gcode"] = 60
    driver.speed = 1_000_000
    loop.run_until_complete(driver.start("vase.gcode"))
    for _ in range(3):
        loop.run_until_complete(_step())
        loop.run_until_complete(asyncio.sleep(0.01))
    assert _plug(loop, switch) is True
    loop.close()


def test_print_started_elsewhere_after_a_printy_print_is_left_alone():
    """Printy's print ended, then the user started one from the screen before the cut:
    the end of that one does not cut the plug."""
    init_db()
    client = TestClient(app)
    pid, sliced_id, switch = _setup(client, "reprise")
    loop = asyncio.new_event_loop()
    loop.run_until_complete(ha.set_power(switch, True))
    loop.run_until_complete(manager.sync())
    driver = manager.live[pid].driver
    real_status = driver.status
    hot = {"v": True}

    async def status():
        st = await real_status()
        if hot["v"]:
            st.nozzle_temp = 200.0
        return st

    driver.status = status
    _print_from_printy(client, loop, pid, sliced_id)
    loop.run_until_complete(_step())
    assert _plug(loop, switch) is True  # still hot
    driver.files["vase.gcode"] = 60
    driver.speed = 1
    loop.run_until_complete(driver.start("vase.gcode"))
    loop.run_until_complete(_step())
    assert _pending(pid) is False
    driver.speed = 1_000_000  # it ends and cools
    hot["v"] = False
    for _ in range(3):
        loop.run_until_complete(_step())
        loop.run_until_complete(asyncio.sleep(0.01))
    assert _plug(loop, switch) is True
    loop.close()


def test_no_cut_without_a_live_answer(monkeypatch):
    """Cool according to the last status, but the printer does not answer now, or the
    plug's state is unknown in Home Assistant: nothing is cut."""
    init_db()
    client = TestClient(app)
    pid, sliced_id, switch = _setup(client, "injoignable")
    loop = asyncio.new_event_loop()
    loop.run_until_complete(ha.set_power(switch, True))
    loop.run_until_complete(manager.sync())
    driver = manager.live[pid].driver

    async def silent():
        return PrinterStatus()  # offline

    monkeypatch.setattr(driver, "fresh_status", silent)
    _print_from_printy(client, loop, pid, sliced_id)
    loop.run_until_complete(asyncio.sleep(0.01))
    loop.run_until_complete(_step())
    assert _plug(loop, switch) is True and _pending(pid) is True

    monkeypatch.undo()
    real = ha.switch_state

    async def unknown(entity_id, *, refresh=False):
        return None if entity_id == switch else await real(entity_id, refresh=refresh)

    monkeypatch.setattr(ha, "switch_state", unknown)
    loop.run_until_complete(_step())
    assert _plug(loop, switch) is True and _pending(pid) is True

    monkeypatch.undo()
    loop.run_until_complete(_step())
    assert _plug(loop, switch) is False
    loop.close()
