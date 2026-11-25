"""File library: import, grouping into models, storage on disk.

An imported file goes to the model of the same name. A file whose name matches no
model waits "to be filed" (PendingFile) until the user creates a model or picks one.
A sliced file for the same printer and material as an existing version is offered as
its replacement: the old version is then archived, never deleted, so its print
history (and energy measures) stay.
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import config
from .db import utcnow
from .gcode import is_sliced, is_source, read_meta
from .models import NOT_STARTED_STATUSES, Job, Model, PendingFile, SlicedFile, SourceFile

PENDING_DIR = ".pending"

log = logging.getLogger(__name__)

_STRIP = re.compile(
    r"(\.gcode\.3mf|\.gcode|\.gco|\.g|\.3mf|\.stl|\.obj|\.step|\.stp|\.blend)$"
    r"|[_\- ]?(plate[_\- ]?\d+)$"
    r"|[_\- ]?(\d+h\d+m|\d+m\d+s)$"
    r"|[_\- ]?(pla|petg|abs|asa|tpu)$"
    r"|[_\- ]?(\d+(\.\d+)?mm)$"
    r"|[_\- ]?(k1|k1c|k2|k2plus|k2_plus|k2pro|x1c|x1|p1s|p1p|a1|a1mini)$",
    re.IGNORECASE,
)


class UnsupportedFile(ValueError):
    pass


@dataclass
class ImportResult:
    model: Model | None  # None: waiting to be filed
    kind: str  # sliced | source | pending
    duplicate: bool
    pending: PendingFile | None = None


def model_name_from(filename: str) -> str:
    name = Path(filename).name
    previous = None
    while previous != name:  # strip suffixes repeatedly ("lampe_K2_PLA_0.2mm_1h30m.gcode")
        previous = name
        name = _STRIP.sub("", name)
    name = re.sub(r"[_]+", " ", name).strip(" -_.")
    return (name[:1].upper() + name[1:]) if name else "Sans nom"


def _safe_name(filename: str) -> str:
    name = Path(filename).name
    return re.sub(r"[^\w.\-+ ]", "_", name).strip() or "fichier"


def _unique(dest_dir: Path, filename: str) -> Path:
    dest = dest_dir / filename
    stem, suffix = filename, ""
    for ext in (".gcode.3mf", ".gcode", ".3mf", ".stl"):
        if filename.lower().endswith(ext):
            stem, suffix = filename[: -len(ext)], filename[-len(ext) :]
            break
    n = 2
    while dest.exists():
        dest = dest_dir / f"{stem} ({n}){suffix}"
        n += 1
    return dest


def import_stream(
    s: Session, stream: BinaryIO, filename: str, *, model_id: int | None = None
) -> ImportResult:
    """Stores an uploaded file: into the given model, the model of the same name, or
    "to be filed" when no model has that name."""
    if not (is_sliced(filename) or is_source(filename)):
        raise UnsupportedFile(f"Format non pris en charge : {Path(filename).name}")

    tmp_dir = config.library_dir / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    tmp = tmp_dir / f"upload-{uuid.uuid4().hex}"
    digest = hashlib.sha256()
    size = 0
    with tmp.open("wb") as out:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
            out.write(chunk)
    sha = digest.hexdigest()

    try:
        if is_sliced(filename):
            existing = s.scalar(select(SlicedFile).where(SlicedFile.sha256 == sha))
            if existing:
                return ImportResult(existing.model, "sliced", duplicate=True)
        waiting = s.scalar(select(PendingFile).where(PendingFile.sha256 == sha))
        if waiting:
            return ImportResult(None, "pending", duplicate=True, pending=waiting)
        if model_id is not None:
            model = s.get(Model, model_id)
            if model is None:
                raise LookupError("Modèle introuvable.")
        else:
            name = model_name_from(filename)
            model = s.scalar(select(Model).where(func.lower(Model.name) == name.lower()))
            if model is None:
                return ImportResult(None, "pending", duplicate=False, pending=_park(s, tmp, filename, size, sha))
        return _attach(s, model, tmp, filename, size, sha)
    finally:
        tmp.unlink(missing_ok=True)


def import_path(s: Session, path: Path) -> ImportResult:
    with path.open("rb") as fh:
        return import_stream(s, fh, path.name)


def _attach(s: Session, model: Model, src: Path, filename: str, size: int, sha: str) -> ImportResult:
    """Moves a stored file into a model's folder and records it."""
    dest_dir = config.library_dir / str(model.id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = _unique(dest_dir, _safe_name(filename))
    shutil.move(src, dest)
    rel = dest.relative_to(config.library_dir).as_posix()
    if is_source(filename):
        model.sources.append(SourceFile(filename=dest.name, path=rel, size=size))
        s.commit()
        return ImportResult(model, "source", duplicate=False)

    meta = read_meta(dest)
    sliced = SlicedFile(
        filename=dest.name,
        path=rel,
        size=size,
        sha256=sha,
        printer_model=meta.printer_model,
        duration_s=meta.duration_s,
        filament_g=meta.filament_g,
        filament_m=meta.filament_m,
        material=meta.material,
        color=meta.color,
        layer_height=meta.layer_height,
        nozzle=meta.nozzle,
    )
    older = same_kind(model, sliced)
    model.sliced.append(sliced)
    s.flush()
    if older is not None:
        sliced.replaces_id = older.id  # the user decides: replace, or keep both
    if meta.thumbnail_png:
        thumb = dest_dir / ".thumbs" / f"{sliced.id}.{meta.thumbnail_ext}"
        thumb.parent.mkdir(exist_ok=True)
        thumb.write_bytes(meta.thumbnail_png)
        sliced.thumbnail = thumb.relative_to(config.library_dir).as_posix()
    s.commit()
    log.info("imported %s into model %s", dest.name, model.name)
    return ImportResult(model, "sliced", duplicate=False)


def same_kind(model: Model, f: SlicedFile) -> SlicedFile | None:
    """The current version for the same printer and material, if any (the newest)."""

    def key(x: SlicedFile) -> tuple[str, str]:
        return ((x.printer_model or "").lower(), (x.material or "").lower())

    same = [v for v in model.active_sliced if v is not f and v.id != f.id and key(v) == key(f)]
    return same[-1] if same else None


def _park(s: Session, src: Path, filename: str, size: int, sha: str) -> PendingFile:
    """Keeps a file aside until the user files it."""
    pend_dir = config.library_dir / PENDING_DIR
    pend_dir.mkdir(parents=True, exist_ok=True)
    dest = _unique(pend_dir, _safe_name(filename))
    shutil.move(src, dest)
    pending = PendingFile(
        filename=dest.name,
        path=dest.relative_to(config.library_dir).as_posix(),
        size=size,
        sha256=sha,
        kind="sliced" if is_sliced(filename) else "source",
        suggested_name=model_name_from(filename),
        meta={},
    )
    s.add(pending)
    s.flush()
    if pending.kind == "sliced":
        meta = read_meta(dest)
        pending.meta = {
            "printer_model": meta.printer_model, "duration_s": meta.duration_s, "filament_g": meta.filament_g,
            "material": meta.material, "color": meta.color, "layer_height": meta.layer_height,
        }
        if meta.thumbnail_png:
            thumb = pend_dir / ".thumbs" / f"{pending.id}.{meta.thumbnail_ext}"
            thumb.parent.mkdir(exist_ok=True)
            thumb.write_bytes(meta.thumbnail_png)
            pending.thumbnail = thumb.relative_to(config.library_dir).as_posix()
    s.commit()
    log.info("%s waits to be filed", dest.name)
    return pending


def file_pending(s: Session, pending: PendingFile, *, model_id: int | None, new_name: str | None,
                 replace: bool) -> ImportResult:
    """Files a waiting file into an existing model or a new one."""
    if model_id is not None:
        model = s.get(Model, model_id)
        if model is None:
            raise LookupError("Modèle introuvable.")
    else:
        name = (new_name or pending.suggested_name).strip() or pending.suggested_name
        model = s.scalar(select(Model).where(func.lower(Model.name) == name.lower()))
        if model is None:
            model = Model(name=name, tags=[])
            s.add(model)
            s.flush()
    src = config.library_dir / pending.path
    thumb = pending.thumbnail
    s.delete(pending)
    s.flush()
    result = _attach(s, model, src, pending.filename, pending.size, pending.sha256)
    if thumb:
        delete_file(thumb)
    if replace and result.kind == "sliced":
        new = model.sliced[-1]
        if new.replaces_id is not None:
            replace_version(s, new)
            s.commit()
    return result


def discard_pending(s: Session, pending: PendingFile) -> None:
    delete_file(pending.path)
    if pending.thumbnail:
        delete_file(pending.thumbnail)
    s.delete(pending)
    s.commit()


def replace_version(s: Session, new: SlicedFile) -> SlicedFile | None:
    """The new version takes the old one's place: planned prints that have not started
    switch to it, the old one is archived with its history."""
    old = s.get(SlicedFile, new.replaces_id) if new.replaces_id else None
    new.replaces_id = None
    if old is None or old.model_id != new.model_id:
        return None
    old.archived_at = utcnow()
    if (new.pieces or 1) == 1 and (old.pieces or 1) > 1:
        new.pieces = old.pieces  # pieces per print, set by hand on the old version
    for job in s.scalars(
        select(Job).where(Job.sliced_file_id == old.id, Job.status.in_([st.value for st in NOT_STARTED_STATUSES]))
    ):
        job.sliced_file = new
        job.duration_s = new.duration_s or job.duration_s
    return old


def delete_file(path: str) -> None:
    (config.library_dir / path).unlink(missing_ok=True)


def delete_model_dir(model_id: int) -> None:
    shutil.rmtree(config.library_dir / str(model_id), ignore_errors=True)
