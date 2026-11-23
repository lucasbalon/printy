"""File library: import, grouping into models, storage on disk."""

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
from .gcode import is_sliced, is_source, read_meta
from .models import Model, SlicedFile, SourceFile

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
    model: Model
    kind: str  # sliced | source
    duplicate: bool


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
    """Stores an uploaded file and attaches it to a model (existing or new)."""
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
        model = _resolve_model(s, filename, model_id)
        dest_dir = config.library_dir / str(model.id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = _unique(dest_dir, _safe_name(filename))
        shutil.move(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)

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
    model.sliced.append(sliced)
    s.flush()
    if meta.thumbnail_png:
        thumb = dest_dir / ".thumbs" / f"{sliced.id}.{meta.thumbnail_ext}"
        thumb.parent.mkdir(exist_ok=True)
        thumb.write_bytes(meta.thumbnail_png)
        sliced.thumbnail = thumb.relative_to(config.library_dir).as_posix()
    s.commit()
    log.info("imported %s into model %s", dest.name, model.name)
    return ImportResult(model, "sliced", duplicate=False)


def import_path(s: Session, path: Path) -> ImportResult:
    with path.open("rb") as fh:
        return import_stream(s, fh, path.name)


def _resolve_model(s: Session, filename: str, model_id: int | None) -> Model:
    if model_id is not None:
        model = s.get(Model, model_id)
        if model is None:
            raise LookupError("Modèle introuvable.")
        return model
    name = model_name_from(filename)
    model = s.scalar(select(Model).where(func.lower(Model.name) == name.lower()))
    if model is None:
        model = Model(name=name, tags=[])
        s.add(model)
        s.flush()
    return model


def delete_file(path: str) -> None:
    (config.library_dir / path).unlink(missing_ok=True)


def delete_model_dir(model_id: int) -> None:
    shutil.rmtree(config.library_dir / str(model_id), ignore_errors=True)
