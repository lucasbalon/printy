"""V3: stock per product, energy per piece, Home Assistant entities."""

import asyncio
import io
import time
from datetime import timedelta

from fastapi.testclient import TestClient

from printy.db import SessionLocal, init_db, utcnow
from printy.ha import ha
from printy.ha_entities import publisher
from printy.main import app
from printy.models import Job
from printy.printers import manager
from printy.scheduler import scheduler
from printy.spoolman import Spool, spoolman

from .test_gcode import ORCA  # 37.25 g, beige PLA

WHITE = Spool(1, "Blanc mat", "Elegoo", "PLA", "#F4F1EA", 800.0, None)


def test_print_feeds_stock_and_energy(monkeypatch):
    init_db()

    async def fake_notify(*a, **kw):
        pass

    async def fake_spools(fresh=False):
        return [WHITE]

    async def fake_use(spool_id, grams):
        pass

    monkeypatch.setattr(ha, "notify", fake_notify)
    monkeypatch.setattr(spoolman, "spools", fake_spools)
    monkeypatch.setattr(spoolman, "use", fake_use)
    client = TestClient(app)
    client.patch("/api/v1/settings", json={"spoolman_url": "http://spoolman.test:7912"})

    r = client.post("/api/v1/upload", files=[("files", ("stock_lampe.gcode", io.BytesIO(ORCA.encode() + b"; stock\n")))])
    model_id = r.json()["imported"][0]["model_id"]
    sliced = client.get(f"/api/v1/models/{model_id}").json()["sliced"][0]
    assert client.patch(f"/api/v1/sliced/{sliced['id']}", json={"pieces": 2}).json()["pieces"] == 2
    client.patch(f"/api/v1/models/{model_id}", json={"stock_alert": 5})

    pid = client.post("/api/v1/printers", json={
        "name": "Atelier", "driver": "demo", "options": {"speed": 1_000_000}, "camera_source": "none",
    }).json()["id"]
    job = client.post("/api/v1/jobs", json={
        "sliced_file_id": sliced["id"], "printer_id": pid, "start_at": utcnow().isoformat(), "confirm_required": False,
    }).json()
    assert job["spool_id"] == WHITE.id

    loop = asyncio.new_event_loop()

    async def step():
        await manager.sync()
        await manager.refresh_all()
        manager.live[pid].power_w = 300.0  # the plug measures 300 W
        await scheduler.tick()

    for _ in range(3):
        loop.run_until_complete(step())
        loop.run_until_complete(asyncio.sleep(0.05))
    with SessionLocal() as s:
        j = s.get(Job, job["id"])
        assert j.status == "printing"
        j.started_at = utcnow() - timedelta(minutes=5)
        s.commit()
    scheduler._metered_at[job["id"]] = time.monotonic() - 60  # one minute of printing at 300 W
    loop.run_until_complete(step())
    loop.close()

    with SessionLocal() as s:
        j = s.get(Job, job["id"])
        assert j.status == "finished"
        assert 4.9 < j.energy_wh < 5.2  # 300 W x 1 min = 5 Wh
        assert j.stock_added == 2

    p = next(x for x in client.get("/api/v1/stock").json() if x["id"] == model_id)
    assert p["stock"] == [{"variant": "Blanc mat", "color": "#F4F1EA", "quantity": 2}]
    assert p["stock_low"] is True  # 2 < 5
    assert p["energy"]["measured_on"] == 1
    assert p["energy"]["kwh_per_piece"] == 0.003  # 5 Wh for 2 pieces, in kWh rounded to 3 decimals

    # a sale, then the print turns out to be bad
    p = client.post("/api/v1/stock/move", json={"model_id": model_id, "variant": "Blanc mat", "delta": -1, "reason": "sale"}).json()
    assert p["stock_total"] == 1
    assert client.post(f"/api/v1/jobs/{job['id']}/defect").json()["status"] == "failed"
    p = next(x for x in client.get("/api/v1/stock").json() if x["id"] == model_id)
    assert p["stock_total"] == 0  # never below zero
    moves = client.get(f"/api/v1/stock/moves?model_id={model_id}").json()
    assert [m["reason"] for m in moves] == ["defect", "sale", "print"]

    # Home Assistant entities
    with SessionLocal() as s:
        entities = {e.object_id: e for e in publisher.collect(s)}
    assert entities["printy_atelier_etat"].state in ("Prête", "Terminée")
    assert "Stock lampe" in entities["printy_stock_bas"].attributes["produits"]
    client.patch("/api/v1/settings", json={"spoolman_url": ""})


def test_mqtt_discovery_messages():
    import json

    from printy.ha_entities import Entity, MqttLink

    sent = []

    class FakeClient:
        def publish(self, topic, payload, retain=False):
            sent.append((topic, payload, retain))

    link = MqttLink()
    link.client = FakeClient()
    e = Entity("printy_k2_fin_impression", "printy_printer1_fin_impression", "Fin d’impression", None,
               device_class="timestamp", device=("printy_printer_1", "Printy K2"))
    link.publish(e)
    link.publish(e)  # second time: no new announcement
    topics = [t for t, _, _ in sent]
    assert topics.count("homeassistant/sensor/printy/printy_printer1_fin_impression/config") == 1
    cfg = json.loads(sent[0][1])
    assert cfg["default_entity_id"] == "sensor.printy_k2_fin_impression"
    assert cfg["device"]["name"] == "Printy K2" and cfg["device_class"] == "timestamp"
    assert ("printy/printy_k2_fin_impression/state", "None", True) in sent  # unknown for HA
    assert all(retain for _, _, retain in sent)
