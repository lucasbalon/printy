from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import settings as settings_store
from ..db import get_session
from ..models import OPEN_STATUSES, Job, Printer
from ..printers import manager
from ..scheduler import scheduler
from ..spoolman import GOOD_MATCH, SpoolmanError, rank_spools, spoolman, suggest
from . import serialize

router = APIRouter(tags=["spools"])
DB = Annotated[Session, Depends(get_session)]


@router.get("/spools")
async def list_spools(
    s: DB,
    color: str | None = None,
    material: str | None = None,
    printer_id: int | None = None,
    need_g: float | None = None,
):
    """Spools from Spoolman; with a colour, sorted from best to worst match."""
    settings_store.get_all(s)  # applies the configured URL
    if not spoolman.enabled:
        return {"enabled": False, "error": None, "spools": [], "suggested_id": None, "url": ""}
    try:
        spools = await spoolman.spools()
    except SpoolmanError as exc:
        return {"enabled": True, "error": str(exc), "spools": [], "suggested_id": None, "url": spoolman.url}

    printers = s.scalars(select(Printer)).all()
    loaded_on = {p.loaded_spool_id: p.name for p in printers if p.loaded_spool_id is not None}
    planned: dict[int, float] = {}
    for job in s.scalars(
        select(Job).where(Job.spool_id.is_not(None), Job.status.in_([st.value for st in OPEN_STATUSES]))
    ):
        planned[job.spool_id] = planned.get(job.spool_id, 0) + (job.sliced_file.filament_g or 0)

    loaded = next((p.loaded_spool_id for p in printers if p.id == printer_id), None)
    suggested = None
    if color or material:
        ranked = rank_spools(spools, color, material, loaded)
        pairs = [(sp, score <= GOOD_MATCH) for sp, score in ranked]
        suggested = suggest(spools, color, material, loaded, need_g)
    else:
        pairs = [(sp, False) for sp in sorted(spools, key=lambda x: ((x.material or ""), x.name.lower(), x.id))]
    items = [
        {**sp.to_dict(), "match": match, "loaded_on": loaded_on.get(sp.id), "planned_g": round(planned.get(sp.id, 0), 1)}
        for sp, match in pairs
    ]
    return {
        "enabled": True,
        "error": None,
        "spools": items,
        "suggested_id": suggested.id if suggested else None,
        "url": spoolman.url,
    }


@router.get("/spoolman/filaments")
async def list_filaments(s: DB):
    settings_store.get_all(s)
    try:
        return await spoolman.filaments()
    except SpoolmanError as exc:
        raise HTTPException(502, str(exc)) from exc


class NewFilament(BaseModel):
    vendor: str = ""
    name: str = Field(min_length=1, max_length=80)  # the colour name, e.g. "Blanc mat"
    material: str = Field("PLA", min_length=2, max_length=20)
    color: str | None = None
    weight: float = Field(1000, gt=0, le=10000)
    diameter: float = Field(1.75, gt=0, le=5)


class NewSpools(BaseModel):
    filament_id: int | None = None
    filament: NewFilament | None = None
    count: int = Field(1, ge=1, le=20)
    location: str = ""
    load_on_printer_id: int | None = None


@router.post("/spoolman/spools", status_code=201)
async def create_spools(body: NewSpools, s: DB):
    """New spool(s) in Spoolman, from an existing filament or a new one."""
    settings_store.get_all(s)
    if body.filament_id is None and body.filament is None:
        raise HTTPException(422, "Choisis un filament ou décris-en un nouveau.")
    try:
        filament_id = body.filament_id
        if filament_id is None and body.filament is not None:
            f = body.filament
            filament_id = await spoolman.create_filament(
                vendor=f.vendor, name=f.name, material=f.material, color=f.color, weight=f.weight, diameter=f.diameter
            )
        created = [await spoolman.create_spool(filament_id, location=body.location.strip() or None) for _ in range(body.count)]
    except SpoolmanError as exc:
        raise HTTPException(502, str(exc)) from exc
    if body.load_on_printer_id is not None and (p := s.get(Printer, body.load_on_printer_id)):
        p.loaded_spool_id = created[0].id
        s.commit()
        manager.want_spool(p.id, created[0].id)
        scheduler.poke()
    return [sp.to_dict() for sp in created]


@router.post("/spoolman/spools/{spool_id}/archive")
async def archive_spool(spool_id: int, s: DB):
    """An empty spool leaves the lists (archived in Spoolman, not deleted)."""
    settings_store.get_all(s)
    try:
        await spoolman.archive(spool_id)
    except SpoolmanError as exc:
        raise HTTPException(502, str(exc)) from exc
    for p in s.scalars(select(Printer).where(Printer.loaded_spool_id == spool_id)):
        p.loaded_spool_id = None
    s.commit()
    return {"ok": True}


class LoadedSpool(BaseModel):
    spool_id: int | None


@router.post("/printers/{printer_id}/spool")
def set_loaded_spool(printer_id: int, body: LoadedSpool, s: DB):
    p = s.get(Printer, printer_id)
    if p is None:
        raise HTTPException(404, "Imprimante introuvable.")
    p.loaded_spool_id = body.spool_id
    s.commit()
    manager.want_spool(p.id, body.spool_id)
    scheduler.poke()
    return serialize.printer(p)
