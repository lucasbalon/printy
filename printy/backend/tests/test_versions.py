"""Files waiting to be filed, and new versions replacing old ones."""

import io
from datetime import timedelta

from fastapi.testclient import TestClient

from printy.db import SessionLocal, init_db, utcnow
from printy.main import app
from printy.models import Job, JobStatus, SlicedFile

from .test_gcode import ORCA


def _send(client, name, tag):
    r = client.post("/api/v1/upload", files=[("files", (name, io.BytesIO(ORCA.encode() + f"; {tag}\n".encode())))])
    assert r.status_code == 200, r.text
    return r.json()["imported"][0]


def test_unknown_name_waits_then_new_model():
    init_db()
    client = TestClient(app)
    item = _send(client, "Abat-jour Nuage_K2_PLA.gcode", "nuage")
    assert item["kind"] == "pending" and item["model_id"] is None
    waiting = [p for p in client.get("/api/v1/pending").json() if p["id"] == item["pending_id"]]
    assert waiting and waiting[0]["suggested_name"] == "Abat-jour Nuage" and waiting[0]["duration_s"]
    # the same file again: recognised, not waiting twice
    assert _send(client, "Abat-jour Nuage_K2_PLA.gcode", "nuage")["duplicate"] is True
    filed = client.post(f"/api/v1/pending/{item['pending_id']}/file", json={"new_name": "Nuage"}).json()
    assert filed["model_name"] == "Nuage" and filed["kind"] == "sliced"
    assert not [p for p in client.get("/api/v1/pending").json() if p["id"] == item["pending_id"]]


def test_discard_waiting_file():
    init_db()
    client = TestClient(app)
    item = _send(client, "brouillon_x.gcode", "brouillon")
    assert client.delete(f"/api/v1/pending/{item['pending_id']}").status_code == 204
    assert not [p for p in client.get("/api/v1/pending").json() if p["id"] == item["pending_id"]]


def test_new_version_replaces_the_old_one_and_keeps_history():
    init_db()
    client = TestClient(app)
    first = _send(client, "Lampe Version_K2_PLA.gcode", "v1")
    model_id = client.post(f"/api/v1/pending/{first['pending_id']}/file", json={}).json()["model_id"]
    old_id = client.get(f"/api/v1/models/{model_id}").json()["sliced"][0]["id"]
    client.patch(f"/api/v1/sliced/{old_id}", json={"pieces": 3})
    pid = client.post("/api/v1/printers", json={"name": "versions", "driver": "demo", "camera_source": "none"}).json()["id"]
    planned = client.post("/api/v1/jobs", json={
        "sliced_file_id": old_id, "printer_id": pid, "start_at": (utcnow() + timedelta(days=2)).isoformat(),
    }).json()["id"]
    with SessionLocal() as s:  # an old print of the old version, with its energy
        old = s.get(SlicedFile, old_id)
        s.add(Job(sliced_file=old, printer_id=pid, start_at=utcnow() - timedelta(days=3), duration_s=3600,
                  status=JobStatus.FINISHED.value, finished_at=utcnow() - timedelta(days=3), energy_wh=120))
        s.commit()

    # same name, same printer and material: filed at once, offered as a replacement
    second = _send(client, "Lampe Version_K2_PLA.gcode", "v2")
    assert second["kind"] == "sliced" and second["model_id"] == model_id
    m = client.get(f"/api/v1/models/{model_id}").json()
    new = next(v for v in m["sliced"] if v["id"] != old_id)
    assert new["replaces_id"] == old_id and m["version_offer"] is True

    m = client.post(f"/api/v1/sliced/{new['id']}/replace").json()
    assert [v["id"] for v in m["sliced"]] == [new["id"]] and m["sliced"][0]["pieces"] == 3
    assert [v["id"] for v in m["archived"]] == [old_id] and m["version_offer"] is False
    with SessionLocal() as s:
        assert s.get(Job, planned).sliced_file_id == new["id"]  # planned print follows
        assert s.query(Job).filter(Job.sliced_file_id == old_id).count() == 1  # history kept
    stock = next(p for p in client.get("/api/v1/stock").json() if p["id"] == model_id)
    assert stock["energy"]["measured_on"] == 1

    # back to the old one if needed
    m = client.post(f"/api/v1/sliced/{old_id}/restore").json()
    assert {v["id"] for v in m["sliced"]} == {old_id, new["id"]}
    client.delete(f"/api/v1/jobs/{planned}")


def test_waiting_file_into_an_existing_model_replacing():
    init_db()
    client = TestClient(app)
    first = _send(client, "Suspension_K2_PLA.gcode", "s1")
    model_id = client.post(f"/api/v1/pending/{first['pending_id']}/file", json={}).json()["model_id"]
    other = _send(client, "suspension_v2_tweaked.gcode", "s2")  # a different name: waits
    assert other["kind"] == "pending"
    client.post(f"/api/v1/pending/{other['pending_id']}/file", json={"model_id": model_id, "replace": True})
    m = client.get(f"/api/v1/models/{model_id}").json()
    assert len(m["sliced"]) == 1 and len(m["archived"]) == 1 and m["sliced"][0]["filename"].startswith("suspension_v2")


def test_keep_both():
    init_db()
    client = TestClient(app)
    first = _send(client, "Applique_K2_PLA.gcode", "a1")
    model_id = client.post(f"/api/v1/pending/{first['pending_id']}/file", json={}).json()["model_id"]
    second = _send(client, "Applique_K2_PLA.gcode", "a2")
    m = client.get(f"/api/v1/models/{second['model_id']}").json()
    new = next(v for v in m["sliced"] if v["replaces_id"])
    m = client.post(f"/api/v1/sliced/{new['id']}/keep-both").json()
    assert len(m["sliced"]) == 2 and not m["archived"] and m["version_offer"] is False
    assert model_id == second["model_id"]
