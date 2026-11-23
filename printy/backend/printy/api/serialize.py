"""Plain-dict views of the models, as sent to the interface."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import object_session

from ..drivers import DRIVERS
from ..models import Job, JobStatus, Model, Printer, SlicedFile, SourceFile
from ..printers import manager


def _running_job_id(p: Printer) -> int | None:
    """The Printy job printing on this printer, if the current print is one of ours."""
    s = object_session(p)
    if s is None:
        return None
    return s.scalar(
        select(Job.id)
        .where(Job.printer_id == p.id)
        .where(Job.status.in_([JobStatus.PRINTING.value, JobStatus.PAUSED.value, JobStatus.STARTING.value]))
        .limit(1)
    )


def sliced(f: SlicedFile) -> dict[str, Any]:
    return {
        "id": f.id,
        "model_id": f.model_id,
        "filename": f.filename,
        "size": f.size,
        "has_thumbnail": bool(f.thumbnail),
        "printer_model": f.printer_model,
        "duration_s": f.duration_s,
        "filament_g": f.filament_g,
        "filament_m": f.filament_m,
        "material": f.material,
        "color": f.color,
        "layer_height": f.layer_height,
        "nozzle": f.nozzle,
        "pieces": f.pieces or 1,
        "created_at": f.created_at.isoformat(),
    }


def source(f: SourceFile) -> dict[str, Any]:
    return {"id": f.id, "filename": f.filename, "size": f.size, "created_at": f.created_at.isoformat()}


def model(m: Model, *, full: bool = False) -> dict[str, Any]:
    cover = next((f for f in m.sliced if f.thumbnail), None)
    data: dict[str, Any] = {
        "id": m.id,
        "name": m.name,
        "description": m.description,
        "tags": m.tags or [],
        "cover_id": cover.id if cover else None,
        "sliced_count": len(m.sliced),
        "source_count": len(m.sources),
        "duration_s": min((f.duration_s for f in m.sliced if f.duration_s), default=None),
        "filament_g": min((f.filament_g for f in m.sliced if f.filament_g), default=None),
        "colors": sorted({f.color for f in m.sliced if f.color}),
        "stock_alert": m.stock_alert,
        "materials": sorted({f.material for f in m.sliced if f.material}),
        "updated_at": m.updated_at.isoformat(),
    }
    if full:
        data["sliced"] = [sliced(f) for f in m.sliced]
        data["sources"] = [source(f) for f in m.sources]
    return data


def printer(p: Printer) -> dict[str, Any]:
    live = manager.live.get(p.id)
    driver = DRIVERS.get(p.driver)
    return {
        "id": p.id,
        "name": p.name,
        "driver": p.driver,
        "driver_label": driver.info.label if driver else p.driver,
        "host": p.host,
        "options": {k: ("" if "key" in k else v) for k, v in (p.options or {}).items()},
        "switch_entity": p.switch_entity,
        "power_entity": p.power_entity,
        "camera_source": p.camera_source,
        "camera_entity": p.camera_entity,
        "power_on_lead_min": p.power_on_lead_min,
        "cooldown_temp": p.cooldown_temp,
        "bed_clear": p.bed_clear,
        # a finished print has not been declared removed yet
        "piece_on_bed": p.last_finished_at is not None and not p.bed_clear,
        "loaded_spool_id": p.loaded_spool_id,
        "sort_order": p.sort_order,
        "live": {
            **(live.status.to_dict() if live else {"state": "offline"}),
            "power_on": live.power_on if live else None,
            "power_w": live.power_w if live else None,
            "spool_tracked": bool(live and live.tracking is not None),
            **_window(p.id),
            "job_id": _running_job_id(p),
        },
    }


def _window(printer_id: int) -> dict[str, Any]:
    w = manager.print_window(printer_id)
    return {
        "print_started_at": w[0].isoformat() if w else None,
        "print_ends_at": w[1].isoformat() if w else None,
    }


def job(j: Job, *, events: bool = False, filament: Any = None) -> dict[str, Any]:
    f = j.sliced_file
    data: dict[str, Any] = {
        "id": j.id,
        "status": j.status,
        "printer_id": j.printer_id,
        "sliced_file_id": j.sliced_file_id,
        "model_id": f.model_id,
        "model_name": f.model.name,
        "has_thumbnail": bool(f.thumbnail),
        "color": f.color,
        "material": f.material,
        "filament_g": f.filament_g,
        "start_at": j.start_at.isoformat(),
        "end_at": j.end_at.isoformat(),
        "duration_s": j.duration_s,
        "confirm_required": j.confirm_required,
        "confirmed_at": j.confirmed_at.isoformat() if j.confirmed_at else None,
        "started_at": j.started_at.isoformat() if j.started_at else None,
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
        "message": j.message,
        "note": j.note,
        "spool_id": j.spool_id,
        "filament_used_g": j.filament_used_g,
        "energy_wh": j.energy_wh,
        "stock_added": j.stock_added or 0,
    }
    if j.status in ("printing", "paused"):
        st = manager.status(j.printer_id)
        data["progress"] = st.progress
        data["remaining_s"] = st.remaining_s
        if (w := manager.print_window(j.printer_id)) and st.remaining_s is not None:
            data["end_at"] = w[1].isoformat()  # live estimate from the printer
    if filament is not None:
        data["spool_change"] = filament.spool_change
        data["previous_spool_id"] = filament.previous_spool_id
        data["low_filament"] = filament.low_filament
        data["spool_remaining_g"] = filament.remaining_before_g
    if events:
        data["events"] = [{"at": e.at.isoformat(), "kind": e.kind, "message": e.message} for e in j.events]
    return data
