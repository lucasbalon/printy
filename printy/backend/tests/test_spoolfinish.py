"""Finishing a spool: least waste, stock-aware alternative, rules, planning."""

import io

from fastapi.testclient import TestClient

from printy.db import SessionLocal, init_db
from printy.main import app
from printy.models import Job, Model, SlicedFile
from printy.spoolfinish import Candidate, proposals, rule_tags
from printy.spoolman import Spool, spoolman

from .test_gcode import ORCA


def _c(i, grams, stock=0, alert=None, name=None):
    return Candidate(i, i, name or f"M{i}", "", grams, 1, 3600, stock, alert)


def test_closest_fit():
    # 300 g left (5 % margin per print): 2 × 140 (294 g) beats 1 × 200 + ... options
    cands = [_c(1, 140), _c(2, 200), _c(3, 90)]
    best = proposals(cands, 300)[0]
    used = sum(i["filament_g"] * i["count"] for i in best["items"])
    assert best["kind"] == "reste"
    # nothing else fits in what is left, and no combination leaves less
    assert 300 - used * 1.05 < 90 * 1.05
    assert best["leftover_g"] == round(300 - used, 1)


def test_stock_proposal_prefers_models_under_alert():
    # M1 fits best but is well stocked; M2 is under its alert level and wastes a little more
    cands = [_c(1, 95, stock=10, name="Vague"), _c(2, 92, stock=0, alert=3, name="Mira")]
    out = proposals(cands, 300)
    kinds = [p["kind"] for p in out]
    assert kinds[0] == "reste"
    stock = out[-1]
    assert any(i["model_name"] == "Mira" and i["under_alert"] for i in stock["items"])


def test_nothing_fits():
    assert proposals([_c(1, 500)], 300) == []


def test_rules_match_kind_not_spool():
    white = Spool(1, "Blanc", "Sunlu", "PLA", "#FFFFFF", 400, None)
    black = Spool(2, "Noir", "Sunlu", "PLA", "#000000", 400, None)
    petg = Spool(3, "Blanc", "Sunlu", "PETG", "#FAFAFA", 400, None)
    rules = [{"tag": "abat-jour", "material": "PLA", "color": "#FDFDFD"}]
    assert rule_tags(rules, white) == ["abat-jour"]
    assert rule_tags(rules, black) == []
    assert rule_tags(rules, petg) == []
    assert rule_tags([{"tag": "tout PLA", "material": "pla", "color": None}], black) == ["tout PLA"]


def test_finish_endpoint_and_plan(monkeypatch):
    init_db()
    client = TestClient(app)
    spool = Spool(77, "Blanc mat", "Sunlu", "PLA", "#FFFFFF", 350.0, None)

    async def fake_spools(*_a, **_k):
        return [spool]

    monkeypatch.setattr(spoolman, "spools", fake_spools)
    monkeypatch.setattr(spoolman, "spools_or_empty", fake_spools)
    monkeypatch.setattr(type(spoolman), "enabled", property(lambda self: True))
    pid = client.post("/api/v1/printers", json={"name": "fin", "driver": "demo", "camera_source": "none"}).json()["id"]
    r = client.post("/api/v1/upload", files=[("files", ("abat_finish.gcode", io.BytesIO(ORCA.encode() + b"; finish\n")))])
    model_id = r.json()["imported"][0]["model_id"]
    with SessionLocal() as s:
        s.get(Model, model_id).tags = ["abat-jour-test"]
        f = s.query(SlicedFile).filter(SlicedFile.model_id == model_id).one()
        f.filament_g, f.material = 100.0, "PLA"
        s.commit()

    res = client.get("/api/v1/spools/77/finish").json()
    assert res["tag"] is None and res["proposals"] == []  # no rule yet: pick a tag
    res = client.get("/api/v1/spools/77/finish?tag=abat-jour-test").json()
    assert res["available_g"] == 350.0
    best = res["proposals"][0]
    assert best["prints"] == 3 and best["leftover_g"] == 50.0  # 3 × 100 g (+5 %) in 350 g

    # remembered for this kind of spool
    client.patch("/api/v1/settings", json={"tag_rules": [{"tag": "abat-jour-test", "material": "PLA", "color": "#FFFFFF"}]})
    try:
        assert client.get("/api/v1/spools/77/finish").json()["tag"] == "abat-jour-test"
        jobs = client.post("/api/v1/spools/77/finish/plan", json={
            "printer_id": pid, "items": [{"sliced_id": best["items"][0]["sliced_id"], "count": 3}],
        })
        assert jobs.status_code == 201, jobs.text
        planned = jobs.json()
        assert len(planned) == 3 and all(j["spool_id"] == 77 for j in planned)
        starts = sorted(j["start_at"] for j in planned)
        assert len(set(starts)) == 3  # one after the other, no overlap
        # what is planned now counts: nothing more fits
        assert client.get("/api/v1/spools/77/finish").json()["proposals"] == []
    finally:
        client.patch("/api/v1/settings", json={"tag_rules": []})
        with SessionLocal() as s:
            for j in s.query(Job).filter(Job.printer_id == pid):
                s.delete(j)
            s.commit()


def test_margin_and_reserve_come_from_the_settings():
    from printy.spoolman import limits

    cands = [_c(1, 100)]
    before = (limits.margin_pct, limits.reserve_g)
    try:
        limits.margin_pct, limits.reserve_g = 0, 50
        assert limits.usable(350) == 300
        assert proposals(cands, limits.usable(350))[0]["prints"] == 3  # 3 × 100 g, no margin
        limits.margin_pct = 10
        assert proposals(cands, limits.usable(350))[0]["prints"] == 2  # 110 g each now
    finally:
        limits.margin_pct, limits.reserve_g = before
