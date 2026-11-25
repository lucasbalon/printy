"""The bed confirmation is asked at its own time, before or after the power on."""

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


def _setup(client, name):
    switch = f"switch.prise_{name}"
    pid = client.post("/api/v1/printers", json={
        "name": name, "driver": "demo", "options": {"speed": 1}, "camera_source": "none",
        "switch_entity": switch, "power_on_lead_min": 5,
    }).json()["id"]
    r = client.post("/api/v1/upload", files=[("files", (f"{name}.gcode", io.BytesIO(ORCA.encode() + f"; {name}\n".encode())))])
    sliced_id = client.get(f"/api/v1/models/{r.json()['imported'][0]['model_id']}").json()["sliced"][0]["id"]
    return pid, sliced_id, switch


async def _step():
    await manager.sync()
    await manager.refresh_all()
    await scheduler.tick()


def _status(jid):
    with SessionLocal() as s:
        return s.get(Job, jid).status


def _start_in(jid, minutes):
    with SessionLocal() as s:
        s.get(Job, jid).start_at = utcnow() + timedelta(minutes=minutes)
        s.commit()


def _env(monkeypatch, client, name, lead):
    sent = []

    async def fake_notify(targets, title, message, **kw):
        sent.append(title)

    monkeypatch.setattr(ha, "notify", fake_notify)
    assert client.patch("/api/v1/settings", json={"confirm_lead_min": lead}).status_code == 200
    pid, sliced_id, switch = _setup(client, name)
    jid = client.post("/api/v1/jobs", json={
        "sliced_file_id": sliced_id, "printer_id": pid, "start_at": (utcnow() + timedelta(hours=3)).isoformat(),
        "confirm_required": True,
    }).json()["id"]
    return sent, jid, switch


def test_confirmation_before_power_on(monkeypatch):
    init_db()
    client = TestClient(app)
    sent, jid, switch = _env(monkeypatch, client, "tot", 60)
    loop = asyncio.new_event_loop()
    try:
        _start_in(jid, 90)
        loop.run_until_complete(_step())
        assert _status(jid) == "scheduled" and not sent
        _start_in(jid, 50)  # within the hour: asked, printer still off
        loop.run_until_complete(_step())
        assert _status(jid) == "awaiting_confirmation" and len(sent) == 1
        assert loop.run_until_complete(ha.is_on(switch)) is False
        # confirmed early: the printer is switched on at its own time only
        loop.run_until_complete(scheduler.on_notification_action(f"PRINTY_START_{jid}"))
        assert _status(jid) == "scheduled"
        assert loop.run_until_complete(ha.is_on(switch)) is False
        _start_in(jid, 4)
        loop.run_until_complete(_step())
        assert _status(jid) == "ready" and len(sent) == 1
        assert loop.run_until_complete(ha.is_on(switch)) is True
    finally:
        client.patch("/api/v1/settings", json={"confirm_lead_min": 10})
        loop.close()


def test_unanswered_early_confirmation_still_powers_on(monkeypatch):
    init_db()
    client = TestClient(app)
    sent, jid, switch = _env(monkeypatch, client, "sans_reponse", 60)
    loop = asyncio.new_event_loop()
    try:
        _start_in(jid, 30)
        loop.run_until_complete(_step())
        assert _status(jid) == "awaiting_confirmation"
        _start_in(jid, 4)
        loop.run_until_complete(_step())
        # switched on, still waiting for the answer, not asked twice
        assert loop.run_until_complete(ha.is_on(switch)) is True
        assert _status(jid) == "awaiting_confirmation" and len(sent) == 1
        loop.run_until_complete(scheduler.on_notification_action(f"PRINTY_START_{jid}"))
        assert _status(jid) == "ready"
    finally:
        client.patch("/api/v1/settings", json={"confirm_lead_min": 10})
        loop.close()


def test_confirmation_after_power_on(monkeypatch):
    init_db()
    client = TestClient(app)
    sent, jid, switch = _env(monkeypatch, client, "tard", 2)
    loop = asyncio.new_event_loop()
    try:
        _start_in(jid, 4)
        loop.run_until_complete(_step())
        assert loop.run_until_complete(ha.is_on(switch)) is True
        assert _status(jid) == "powering_on" and not sent
        _start_in(jid, 1)
        loop.run_until_complete(_step())
        assert _status(jid) == "awaiting_confirmation" and len(sent) == 1
    finally:
        client.patch("/api/v1/settings", json={"confirm_lead_min": 10})
        loop.close()
