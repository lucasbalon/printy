"""Stock per product and energy per piece (V3)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Job, JobStatus, Model, SlicedFile, StockItem, StockMove
from .spoolman import Spool

NO_COLOR = "Sans couleur"
ENERGY_HISTORY = 5  # finished prints averaged for the energy of a piece


# -- stock ----------------------------------------------------------------------------


def variant_of(job: Job, spool: Spool | None) -> tuple[str, str | None]:
    """The colour a print produced: the spool when known, else the file's colour."""
    if spool is not None:
        return spool.name, spool.color
    color = job.sliced_file.color
    return (color, color) if color else (NO_COLOR, None)


def move(
    s: Session,
    model_id: int,
    variant: str,
    delta: int,
    reason: str,
    *,
    color: str | None = None,
    job_id: int | None = None,
    note: str = "",
) -> StockItem:
    item = s.scalar(select(StockItem).where(StockItem.model_id == model_id, StockItem.variant == variant))
    if item is None:
        item = StockItem(model_id=model_id, variant=variant, color=color, quantity=0)
        s.add(item)
    elif color and not item.color:
        item.color = color
    item.quantity = max(0, item.quantity + delta)
    s.add(StockMove(model_id=model_id, variant=variant, delta=delta, reason=reason, job_id=job_id, note=note))
    return item


def stock_of(s: Session, model_id: int) -> list[StockItem]:
    return list(
        s.scalars(select(StockItem).where(StockItem.model_id == model_id).order_by(StockItem.variant))
    )


# -- energy ---------------------------------------------------------------------------


def model_energy(s: Session, model: Model) -> dict[str, Any]:
    """Average kWh per piece over the last finished prints measured by the plug."""
    jobs = s.scalars(
        select(Job)
        .join(SlicedFile)
        .where(
            SlicedFile.model_id == model.id,
            Job.status == JobStatus.FINISHED.value,
            Job.energy_wh.is_not(None),
        )
        .order_by(Job.finished_at.desc())
        .limit(ENERGY_HISTORY)
    ).all()
    per_piece = [j.energy_wh / 1000 / max(1, j.sliced_file.pieces or 1) for j in jobs]
    return {
        "kwh_per_piece": round(sum(per_piece) / len(per_piece), 3) if per_piece else None,
        "measured_on": len(per_piece),
    }
