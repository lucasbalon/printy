from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import production
from ..db import get_session
from ..models import Job, JobStatus, Model, StockItem, StockMove
from ..scheduler import add_event
from . import serialize

router = APIRouter(tags=["stock"])
DB = Annotated[Session, Depends(get_session)]


def product(s: Session, m: Model) -> dict:
    items = production.stock_of(s, m.id)
    total = sum(i.quantity for i in items)
    return {
        **serialize.model(m),
        "stock": [{"variant": i.variant, "color": i.color, "quantity": i.quantity} for i in items],
        "stock_total": total,
        "stock_low": m.stock_alert is not None and total < m.stock_alert,
        "energy": production.model_energy(s, m),
    }


@router.get("/stock")
def list_stock(s: DB):
    """Products: every model with something printable or something in stock."""
    in_stock = set(s.scalars(select(StockItem.model_id)))
    models = s.scalars(select(Model).options(selectinload(Model.sliced), selectinload(Model.sources))).all()
    out = [product(s, m) for m in models if m.sliced or m.id in in_stock]
    out.sort(key=lambda p: (not p["stock_low"], p["stock_total"] == 0, p["name"].lower()))
    return out


class MoveIn(BaseModel):
    model_id: int
    variant: str = Field(min_length=1, max_length=120)
    color: str | None = None
    delta: int = Field(ge=-1000, le=1000)
    reason: Literal["sale", "adjust", "defect", "print"] = "adjust"
    note: str = ""


@router.post("/stock/move")
def stock_move(body: MoveIn, s: DB):
    m = s.get(Model, body.model_id)
    if m is None:
        raise HTTPException(404, "Produit introuvable.")
    if body.delta == 0:
        raise HTTPException(422, "Quantité nulle.")
    production.move(s, m.id, body.variant.strip(), body.delta, body.reason, color=body.color, note=body.note)
    s.commit()
    return product(s, m)


@router.get("/stock/moves")
def stock_moves(s: DB, model_id: int | None = None, limit: int = 40):
    stmt = select(StockMove, Model.name).join(Model, Model.id == StockMove.model_id)
    if model_id is not None:
        stmt = stmt.where(StockMove.model_id == model_id)
    rows = s.execute(stmt.order_by(StockMove.at.desc()).limit(min(limit, 200))).all()
    return [
        {
            "id": mv.id,
            "model_id": mv.model_id,
            "model_name": name,
            "variant": mv.variant,
            "delta": mv.delta,
            "reason": mv.reason,
            "job_id": mv.job_id,
            "note": mv.note,
            "at": mv.at.isoformat(),
        }
        for mv, name in rows
    ]


@router.post("/jobs/{job_id}/defect")
def mark_defect(job_id: int, s: DB):
    """A print that looked finished but is not sellable: its pieces leave the stock."""
    job = s.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Impression introuvable.")
    if job.status != JobStatus.FINISHED:
        raise HTTPException(409, "Seule une impression terminée peut être marquée comme ratée.")
    if job.stock_added:
        last = s.scalar(
            select(StockMove).where(StockMove.job_id == job.id, StockMove.reason == "print").limit(1)
        )
        variant = last.variant if last else production.NO_COLOR
        production.move(s, job.sliced_file.model_id, variant, -job.stock_added, "defect", job_id=job.id)
        job.stock_added = 0
    job.status = JobStatus.FAILED
    add_event(job, "defect", "Marquée comme ratée : pièces retirées du stock.")
    s.commit()
    return serialize.job(job, events=True)
