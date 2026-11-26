from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session, utcnow
from ..drivers import DRIVERS, DriverError
from ..ha import ha
from ..models import Job, JobStatus, Printer
from ..printers import manager
from ..scheduler import scheduler
from . import serialize

router = APIRouter(tags=["printers"])
DB = Annotated[Session, Depends(get_session)]


@router.get("/drivers")
def drivers():
    return [asdict(cls.info) for cls in DRIVERS.values()]


@router.get("/printers")
def list_printers(s: DB):
    return [serialize.printer(p) for p in s.scalars(select(Printer).order_by(Printer.sort_order, Printer.id))]


class PrinterIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    driver: str
    host: str = ""
    options: dict[str, Any] = {}
    switch_entity: str | None = None
    power_entity: str | None = None
    camera_source: Literal["printer", "ha", "none"] = "printer"
    camera_entity: str | None = None
    power_on_lead_min: int = Field(10, ge=0, le=120)
    cooldown_temp: float = Field(45, ge=25, le=80)


def _apply(p: Printer, body: PrinterIn) -> None:
    if body.driver not in DRIVERS:
        raise HTTPException(422, "Pilote inconnu.")
    info = DRIVERS[body.driver].info
    if info.host_required and not body.host.strip():
        raise HTTPException(422, "L’adresse de l’imprimante est requise.")
    old = p.options or {}
    # Secrets are never sent back to the interface: an empty value keeps the old one.
    options = {k: (old.get(k) if "key" in k and not v else v) for k, v in body.options.items()}
    p.name = body.name.strip()
    p.driver = body.driver
    p.host = body.host.strip()
    p.options = options
    p.switch_entity = body.switch_entity or None
    p.power_entity = body.power_entity or None
    p.camera_source = body.camera_source
    p.camera_entity = body.camera_entity or None
    p.power_on_lead_min = body.power_on_lead_min
    p.cooldown_temp = body.cooldown_temp


@router.post("/printers", status_code=201)
async def create_printer(body: PrinterIn, s: DB):
    p = Printer()
    _apply(p, body)
    p.sort_order = len(s.scalars(select(Printer.id)).all())
    s.add(p)
    s.commit()
    await manager.sync()
    scheduler.poke()
    return serialize.printer(p)


@router.put("/printers/{printer_id}")
async def update_printer(printer_id: int, body: PrinterIn, s: DB):
    p = s.get(Printer, printer_id)
    if p is None:
        raise HTTPException(404, "Imprimante introuvable.")
    _apply(p, body)
    s.commit()
    await manager.sync()
    scheduler.poke()
    return serialize.printer(p)


@router.delete("/printers/{printer_id}", status_code=204)
async def delete_printer(printer_id: int, s: DB):
    p = s.get(Printer, printer_id)
    if p is None:
        return
    if s.scalar(select(Job.id).where(Job.printer_id == printer_id).limit(1)):
        raise HTTPException(409, "Cette imprimante a un historique d’impressions : elle ne peut pas être supprimée.")
    s.delete(p)
    s.commit()
    await manager.sync()


class PowerIn(BaseModel):
    on: bool


@router.post("/printers/{printer_id}/power")
async def power(printer_id: int, body: PowerIn, s: DB):
    p = s.get(Printer, printer_id)
    if p is None or not p.switch_entity:
        raise HTTPException(404, "Aucune prise associée.")
    if not body.on:
        st = manager.status(p.id)
        if st.state in ("printing", "paused"):
            raise HTTPException(409, "Impossible de couper : une impression est en cours.")
    await ha.set_power(p.switch_entity, body.on)
    p.power_off_pending = False  # the user took the plug in hand
    s.commit()
    await manager.refresh(p)
    return serialize.printer(p)


@router.post("/printers/{printer_id}/keep-on")
def keep_on(printer_id: int, s: DB):
    """Cancels the automatic power off planned after the last print."""
    p = s.get(Printer, printer_id)
    if p is None:
        raise HTTPException(404)
    p.power_off_pending = False
    s.commit()
    return serialize.printer(p)


class ActionIn(BaseModel):
    action: Literal["pause", "resume", "cancel"]


@router.post("/printers/{printer_id}/action")
async def printer_action(printer_id: int, body: ActionIn, s: DB):
    driver = manager.driver(printer_id)
    if driver is None:
        raise HTTPException(404, "Imprimante introuvable.")
    try:
        await getattr(driver, body.action)()
    except DriverError as exc:
        raise HTTPException(409, str(exc)) from exc
    if body.action == "pause":
        for job_id in s.scalars(
            select(Job.id).where(Job.printer_id == printer_id, Job.status == JobStatus.PRINTING.value)
        ):
            scheduler.paused_by_user(job_id)
    if body.action == "cancel":
        running = s.scalars(
            select(Job.id).where(
                Job.printer_id == printer_id,
                Job.status.in_([JobStatus.STARTING.value, JobStatus.PRINTING.value, JobStatus.PAUSED.value]),
            )
        ).all()
        for job_id in running:
            scheduler.stopped_by_user(job_id)
    scheduler.poke()
    p = s.get(Printer, printer_id)
    await manager.refresh(p)
    return serialize.printer(p)


@router.post("/printers/{printer_id}/bed-clear")
def bed_clear(printer_id: int, s: DB):
    p = s.get(Printer, printer_id)
    if p is None:
        raise HTTPException(404)
    p.bed_cleared_at = utcnow()
    s.commit()
    scheduler.poke()
    return serialize.printer(p)


@router.get("/printers/{printer_id}/snapshot")
async def snapshot(printer_id: int, s: DB):
    p = s.get(Printer, printer_id)
    if p is None:
        raise HTTPException(404)
    data = None
    if p.camera_source == "ha" and p.camera_entity:
        data = await ha.camera_snapshot(p.camera_entity)
    elif p.camera_source == "printer" and (driver := manager.driver(p.id)):
        data = await driver.snapshot()
    if not data:
        raise HTTPException(404, "Pas d’image disponible.")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
