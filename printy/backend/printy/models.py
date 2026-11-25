from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, UTCDateTime, utcnow


class Model(Base):
    """Something to print, e.g. "Vague — abat-jour"."""

    __tablename__ = "models"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    # production (V3)
    stock_alert: Mapped[int | None] = mapped_column(Integer, nullable=True)  # warn under this total
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    sliced: Mapped[list[SlicedFile]] = relationship(
        back_populates="model", cascade="all, delete-orphan", order_by="SlicedFile.created_at"
    )
    sources: Mapped[list[SourceFile]] = relationship(
        back_populates="model", cascade="all, delete-orphan", order_by="SourceFile.created_at"
    )


class SlicedFile(Base):
    """A ready-to-print file for one printer / profile."""

    __tablename__ = "sliced_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(String(500))  # relative to library dir
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    thumbnail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    printer_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    filament_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    filament_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    material: Mapped[str | None] = mapped_column(String(40), nullable=True)
    color: Mapped[str | None] = mapped_column(String(9), nullable=True)
    layer_height: Mapped[float | None] = mapped_column(Float, nullable=True)
    nozzle: Mapped[float | None] = mapped_column(Float, nullable=True)
    label: Mapped[str] = mapped_column(String(120), default="")
    pieces: Mapped[int] = mapped_column(Integer, default=1, server_default="1")  # pieces per print
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    model: Mapped[Model] = relationship(back_populates="sliced")


class SourceFile(Base):
    """Optional unsliced source (STL, 3MF project)."""

    __tablename__ = "source_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(String(500))
    size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    model: Mapped[Model] = relationship(back_populates="sources")


class Printer(Base):
    __tablename__ = "printers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    driver: Mapped[str] = mapped_column(String(40))
    host: Mapped[str] = mapped_column(String(255), default="")
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    switch_entity: Mapped[str | None] = mapped_column(String(255), nullable=True)
    power_entity: Mapped[str | None] = mapped_column(String(255), nullable=True)
    camera_source: Mapped[str] = mapped_column(String(20), default="printer")  # printer | ha | none
    camera_entity: Mapped[str | None] = mapped_column(String(255), nullable=True)
    power_on_lead_min: Mapped[int] = mapped_column(Integer, default=10)
    cooldown_temp: Mapped[float] = mapped_column(Float, default=45.0)
    bed_cleared_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    loaded_spool_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Spoolman id
    last_finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # A print launched by Printy has ended: cut the plug once the printer has cooled.
    # Stored so that a restart of Printy does not forget it.
    power_off_pending: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # last "piece still on the bed" reminder, one per finished print
    bed_reminded_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    @property
    def bed_clear(self) -> bool:
        """The bed was declared empty after the last print ended."""
        if self.bed_cleared_at is None:
            return False
        return self.last_finished_at is None or self.bed_cleared_at >= self.last_finished_at


class JobStatus(StrEnum):
    SCHEDULED = "scheduled"
    POWERING_ON = "powering_on"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    READY = "ready"
    STARTING = "starting"
    PRINTING = "printing"
    PAUSED = "paused"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NEEDS_ATTENTION = "needs_attention"


ACTIVE_STATUSES = {
    JobStatus.POWERING_ON,
    JobStatus.AWAITING_CONFIRMATION,
    JobStatus.READY,
    JobStatus.STARTING,
    JobStatus.PRINTING,
    JobStatus.PAUSED,
}
OPEN_STATUSES = ACTIVE_STATUSES | {JobStatus.SCHEDULED, JobStatus.NEEDS_ATTENTION}
CLOSED_STATUSES = {JobStatus.FINISHED, JobStatus.FAILED, JobStatus.CANCELLED}


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    sliced_file_id: Mapped[int] = mapped_column(ForeignKey("sliced_files.id", ondelete="RESTRICT"))
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id", ondelete="RESTRICT"))
    start_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    duration_s: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default=JobStatus.SCHEDULED, index=True)
    confirm_required: Mapped[bool] = mapped_column(Boolean, default=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    powered_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    message: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    spool_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Spoolman id
    filament_used_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    spool_reported: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # production (V3)
    energy_wh: Mapped[float | None] = mapped_column(Float, nullable=True)
    stock_added: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # pieces put in stock
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    sliced_file: Mapped[SlicedFile] = relationship()
    printer: Mapped[Printer] = relationship()
    events: Mapped[list[JobEvent]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobEvent.at"
    )

    @property
    def end_at(self) -> datetime:
        base = self.started_at or self.start_at
        return base + timedelta(seconds=self.duration_s)


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    kind: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text, default="")

    job: Mapped[Job] = relationship(back_populates="events")


class StockItem(Base):
    """Pieces in stock for one product and one colour."""

    __tablename__ = "stock_items"
    __table_args__ = (UniqueConstraint("model_id", "variant"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id", ondelete="CASCADE"), index=True)
    variant: Mapped[str] = mapped_column(String(120))  # "Blanc mat", or "Sans couleur"
    color: Mapped[str | None] = mapped_column(String(9), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)


class StockMove(Base):
    __tablename__ = "stock_moves"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id", ondelete="CASCADE"), index=True)
    variant: Mapped[str] = mapped_column(String(120))
    delta: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(20))  # print | sale | adjust | defect
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
