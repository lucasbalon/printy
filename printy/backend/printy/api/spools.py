from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import offpeak, production
from .. import settings as settings_store
from ..db import get_session, utcnow
from ..models import OPEN_STATUSES, Job, Model, Printer, SlicedFile
from ..printers import manager
from ..scheduler import add_event, scheduler
from ..spoolfinish import Candidate, proposals, rule_tags
from ..spoolman import GOOD_MATCH, Spool, SpoolmanError, color_distance, limits, rank_spools, spoolman, suggest
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


# -- finishing a spool --------------------------------------------------------------


def _candidates(s: Session, tag: str, spool: Spool) -> list[Candidate]:
    """Printable versions of the tag's models that suit the spool's material."""
    out: list[Candidate] = []
    models = s.scalars(select(Model).options(selectinload(Model.sliced))).all()
    for m in models:
        if tag not in (m.tags or []):
            continue
        # stock in the spool's colour (the variant a print on this spool would produce)
        items = production.stock_of(s, m.id)
        same = [
            i for i in items
            if i.variant == spool.name or (i.color and spool.color and color_distance(i.color, spool.color) <= GOOD_MATCH)
        ]
        stock = sum(i.quantity for i in same)
        versions = [
            v for v in m.active_sliced
            if v.filament_g and (not v.material or not spool.material or v.material.lower() == spool.material.lower())
        ]
        for v in versions:
            label = ", ".join(x for x in (v.printer_model, f"{v.layer_height} mm" if v.layer_height else None) if x)
            out.append(Candidate(
                sliced_id=v.id, model_id=m.id, model_name=m.name, label=label if len(versions) > 1 else "",
                filament_g=v.filament_g, pieces=max(1, v.pieces or 1), duration_s=v.duration_s or 3600,
                stock=stock, alert=m.stock_alert,
            ))
    return out


async def _spool_and_available(s: Session, spool_id: int) -> tuple[Spool, float, float]:
    settings_store.get_all(s)
    if not spoolman.enabled:
        raise HTTPException(409, "Spoolman n’est pas configuré.")
    try:
        spool = next((sp for sp in await spoolman.spools() if sp.id == spool_id), None)
    except SpoolmanError as exc:
        raise HTTPException(502, str(exc)) from exc
    if spool is None:
        raise HTTPException(404, "Bobine introuvable dans Spoolman.")
    if spool.remaining_g is None:
        raise HTTPException(409, "Spoolman ne connaît pas le poids restant de cette bobine.")
    planned = sum(
        (j.sliced_file.filament_g or 0)
        for j in s.scalars(
            select(Job).where(Job.spool_id == spool_id, Job.status.in_([st.value for st in OPEN_STATUSES]))
        )
    )
    return spool, limits.usable(spool.remaining_g) - planned, planned


@router.get("/spools/{spool_id}/finish")
async def finish_spool(spool_id: int, s: DB, tag: str | None = None):
    """Best combinations of prints to use up a spool (suggestions only)."""
    spool, available, planned = await _spool_and_available(s, spool_id)
    suggested = rule_tags(settings_store.get(s, "tag_rules") or [], spool)
    tag = tag or (suggested[0] if suggested else None)
    cands = _candidates(s, tag, spool) if tag else []
    return {
        "spool": spool.to_dict(),
        "remaining_g": spool.remaining_g,
        "reserve_g": limits.reserve_g,
        "margin_pct": limits.margin_pct,
        "planned_g": round(planned, 1),
        "available_g": round(available, 1),
        "tag": tag,
        "suggested_tags": suggested,
        "candidates": len(cands),
        "proposals": proposals(cands, available),
    }


class FinishItem(BaseModel):
    sliced_id: int
    count: int = Field(ge=1, le=20)


class FinishPlan(BaseModel):
    printer_id: int
    items: list[FinishItem] = Field(min_length=1)


@router.post("/spools/{spool_id}/finish/plan", status_code=201)
async def plan_finish(spool_id: int, body: FinishPlan, s: DB):
    """Plans the chosen prints one after another on this spool, in off-peak hours when
    possible; each still asks for the bed confirmation as usual."""
    spool, _, _ = await _spool_and_available(s, spool_id)
    if s.get(Printer, body.printer_id) is None:
        raise HTTPException(404, "Imprimante introuvable.")
    files = []
    for it in body.items:
        f = s.get(SlicedFile, it.sliced_id)
        if f is None:
            raise HTTPException(404, "Fichier introuvable.")
        files += [f] * it.count
    files.sort(key=lambda f: -(f.filament_g or 0))  # the largest first
    now = utcnow()
    busy = [
        (j.start_at, j.end_at)
        for j in s.scalars(
            select(Job).where(Job.printer_id == body.printer_id, Job.status.in_([st.value for st in OPEN_STATUSES]))
        )
    ]
    if w := manager.print_window(body.printer_id):
        busy.append((w[0], w[1] or now + timedelta(hours=1)))
    ranges = settings_store.get(s, "off_peak") or []
    confirm = bool(settings_store.get(s, "confirm_by_default"))
    created = []
    for f in files:
        duration = timedelta(seconds=f.duration_s or 3600)
        slot = offpeak.best_slot(ranges, duration, busy, now)
        start = slot[0] if slot else offpeak.first_free(duration, busy, now)
        job = Job(
            sliced_file=f, printer_id=body.printer_id, start_at=start, duration_s=int(duration.total_seconds()),
            confirm_required=confirm, spool_id=spool.id, note="Fin de bobine",
        )
        add_event(job, "created", f"Planifiée pour finir {spool.label}.")
        s.add(job)
        busy.append((start, start + duration))
        created.append(job)
    s.commit()
    scheduler.poke()
    return [serialize.job(j) for j in created]
