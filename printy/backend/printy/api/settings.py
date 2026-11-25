from __future__ import annotations

import re
import secrets
import tempfile
from datetime import date
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import settings as settings_store
from ..backup import snapshot
from ..config import config
from ..db import get_session
from ..ha import ha
from ..ha_entities import publisher
from ..scheduler import scheduler
from ..spoolman import Spoolman, SpoolmanError

router = APIRouter(tags=["settings"])
DB = Annotated[Session, Depends(get_session)]

_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@router.get("/settings")
def get_settings(s: DB):
    values = settings_store.get_all(s)
    return {
        **values,
        "env": {
            "home_assistant": config.in_home_assistant,
            "entities": publisher.status,
            "errors": scheduler.errors,
            "inbox_dir": str(config.inbox_dir),
            "timezone": str(config.timezone),
        },
    }


class OffPeak(BaseModel):
    start: str
    end: str


class TagRule(BaseModel):
    tag: str = Field(min_length=1, max_length=60)
    material: str | None = None
    color: str | None = None


class SettingsPatch(BaseModel):
    notify_targets: list[str] | None = None
    confirm_by_default: bool | None = None
    bed_reminder: bool | None = None
    tag_rules: list[TagRule] | None = None
    filament_margin_pct: float | None = Field(None, ge=0, le=30)
    spool_reserve_g: float | None = Field(None, ge=0, le=200)
    snooze_min: int | None = Field(None, ge=5, le=720)
    bed_reminder_hours: float | None = Field(None, ge=0.5, le=24)
    bed_reminder_after_min: int | None = Field(None, ge=0, le=240)
    bed_photo_threshold_pct: float | None = Field(None, ge=0.5, le=50)
    offpeak_horizon_days: int | None = Field(None, ge=1, le=14)
    finish_max_prints: int | None = Field(None, ge=1, le=30)
    finish_stock_slack_g: float | None = Field(None, ge=0, le=500)
    boot_settle_min: float | None = Field(None, ge=0, le=15)
    offline_grace_min: int | None = Field(None, ge=1, le=60)
    launch_timeout_min: int | None = Field(None, ge=1, le=60)
    start_grace_min: int | None = Field(None, ge=1, le=60)
    busy_grace_min: int | None = Field(None, ge=1, le=180)
    confirm_lead_min: int | None = Field(None, ge=0, le=720)
    confirm_timeout_min: int | None = Field(None, ge=5, le=240)
    keep_on_window_min: int | None = Field(None, ge=0, le=240)
    off_peak: list[OffPeak] | None = None
    import_octoprint: bool | None = None
    import_watch_folder: bool | None = None
    spoolman_url: str | None = None
    spool_reminder_finish: bool | None = None
    spool_reminder_morning: str | None = None
    stock_auto: bool | None = None
    ha_entities: bool | None = None


@router.patch("/settings")
def patch_settings(body: SettingsPatch, s: DB):
    values: dict[str, Any] = body.model_dump(exclude_none=True)
    morning = values.get("spool_reminder_morning")
    if morning and not _TIME.match(morning):
        raise HTTPException(422, "Heure du rappel au format HH:MM attendue.")
    for r in values.get("off_peak", []):
        if not (_TIME.match(r["start"]) and _TIME.match(r["end"])):
            raise HTTPException(422, "Heures au format HH:MM attendues.")
    settings_store.set_many(s, values)
    return get_settings(s)


class SpoolmanTest(BaseModel):
    url: str


@router.post("/spoolman/test")
async def test_spoolman(body: SpoolmanTest):
    probe = Spoolman()
    probe.configure(body.url)
    try:
        info = await probe.info()
        spools = await probe.spools(fresh=True)
    except SpoolmanError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"version": info.get("version"), "spools": len(spools)}


@router.post("/settings/octoprint-key")
def rotate_key(s: DB):
    settings_store.set_many(s, {"octoprint_api_key": secrets.token_hex(16)})
    return get_settings(s)


@router.get("/backup")
def export_backup(tasks: BackgroundTasks):
    """Consistent copy of Printy's database, to download."""
    tmp = Path(tempfile.mkdtemp()) / "printy.db"
    snapshot(config.data_dir / "printy.db", tmp)
    tasks.add_task(lambda: (tmp.unlink(missing_ok=True), tmp.parent.rmdir()))
    return FileResponse(tmp, media_type="application/vnd.sqlite3", filename=f"printy-{date.today().isoformat()}.db")


@router.post("/settings/test-notification")
async def test_notification(s: DB):
    targets = settings_store.get(s, "notify_targets")
    if not targets:
        raise HTTPException(422, "Choisis d’abord au moins un téléphone.")
    await ha.notify(targets, "Printy", "Les notifications fonctionnent. Tu recevras ici la photo du plateau.")
    return {"ok": True}


@router.get("/ha/entities")
async def entities():
    """Entities offered in the printer form and settings pickers."""
    try:
        states = await ha.states()
        notify = await ha.notify_services()
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Home Assistant ne répond pas ({exc}).") from exc

    def pick(pred) -> list[dict[str, str]]:  # noqa: ANN001
        return sorted(
            (
                {"id": e["entity_id"], "name": e.get("attributes", {}).get("friendly_name") or e["entity_id"]}
                for e in states
                if pred(e)
            ),
            key=lambda x: x["name"].lower(),
        )

    return {
        "switches": pick(lambda e: e["entity_id"].split(".")[0] in ("switch", "light", "input_boolean")),
        "cameras": pick(lambda e: e["entity_id"].startswith("camera.")),
        "power_sensors": pick(
            lambda e: e["entity_id"].startswith("sensor.") and e.get("attributes", {}).get("device_class") == "power"
        ),
        "notify": [{"id": n, "name": n.removeprefix("notify.").replace("_", " ")} for n in notify],
    }
