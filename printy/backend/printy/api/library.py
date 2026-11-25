from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from .. import library
from ..config import config
from ..db import get_session, utcnow
from ..models import OPEN_STATUSES, Job, Model, PendingFile, SlicedFile, SourceFile
from . import serialize

router = APIRouter(tags=["library"])
DB = Annotated[Session, Depends(get_session)]


@router.get("/models")
def list_models(s: DB, q: str = "", tag: str = ""):
    stmt = select(Model).options(selectinload(Model.sliced), selectinload(Model.sources))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(Model.name.ilike(like), Model.description.ilike(like)))
    models = s.scalars(stmt.order_by(Model.updated_at.desc())).all()
    if tag:
        models = [m for m in models if tag in (m.tags or [])]
    return [serialize.model(m) for m in models]


@router.get("/tags")
def list_tags(s: DB):
    counts: dict[str, int] = {}
    for tags in s.scalars(select(Model.tags)):
        for t in tags or []:
            counts[t] = counts.get(t, 0) + 1
    return [{"name": k, "count": v} for k, v in sorted(counts.items())]


@router.get("/models/{model_id}")
def get_model(model_id: int, s: DB):
    m = s.get(Model, model_id)
    if m is None:
        raise HTTPException(404, "Modèle introuvable.")
    return serialize.model(m, full=True)


class ModelPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    tags: list[str] | None = None
    stock_alert: int | None = Field(None, ge=0, le=100000)
    clear_stock_alert: bool = False


@router.patch("/models/{model_id}")
def patch_model(model_id: int, body: ModelPatch, s: DB):
    m = s.get(Model, model_id)
    if m is None:
        raise HTTPException(404, "Modèle introuvable.")
    if body.name is not None:
        m.name = body.name.strip()
    if body.description is not None:
        m.description = body.description
    if body.tags is not None:
        m.tags = sorted({t.strip().lower() for t in body.tags if t.strip()})
    if body.stock_alert is not None or body.clear_stock_alert:
        m.stock_alert = None if body.clear_stock_alert else body.stock_alert
    s.commit()
    return serialize.model(m, full=True)


class SlicedPatch(BaseModel):
    pieces: int = Field(ge=1, le=500)


@router.patch("/sliced/{file_id}")
def patch_sliced(file_id: int, body: SlicedPatch, s: DB):
    f = s.get(SlicedFile, file_id)
    if f is None:
        raise HTTPException(404, "Fichier introuvable.")
    f.pieces = body.pieces
    s.commit()
    return serialize.sliced(f)


def _has_open_jobs(s: Session, sliced_ids: list[int]) -> bool:
    if not sliced_ids:
        return False
    stmt = select(Job.id).where(
        Job.sliced_file_id.in_(sliced_ids), Job.status.in_([st.value for st in OPEN_STATUSES])
    )
    return s.scalar(stmt.limit(1)) is not None


def _drop_closed_jobs(s: Session, sliced_ids: list[int]) -> None:
    for job in s.scalars(select(Job).where(Job.sliced_file_id.in_(sliced_ids))):
        s.delete(job)


@router.delete("/models/{model_id}", status_code=204)
def delete_model(model_id: int, s: DB):
    m = s.get(Model, model_id)
    if m is None:
        return
    ids = [f.id for f in m.sliced]
    if _has_open_jobs(s, ids):
        raise HTTPException(409, "Ce modèle a des impressions planifiées. Annule-les d’abord.")
    _drop_closed_jobs(s, ids)
    s.delete(m)
    s.commit()
    library.delete_model_dir(model_id)


def _result(filename: str | None, r: library.ImportResult) -> dict:
    return {
        "filename": filename,
        "model_id": r.model.id if r.model else None,
        "model_name": r.model.name if r.model else None,
        "kind": r.kind,  # sliced | source | pending (waits to be filed)
        "duplicate": r.duplicate,
        "pending_id": r.pending.id if r.pending else None,
    }


@router.post("/upload")
def upload(s: DB, files: list[UploadFile] = File(...), model_id: int | None = Form(None)):
    results, errors = [], []
    for f in files:
        try:
            r = library.import_stream(s, f.file, f.filename or "fichier", model_id=model_id)
            results.append(_result(f.filename, r))
        except (library.UnsupportedFile, LookupError) as exc:
            s.rollback()
            errors.append({"filename": f.filename, "error": str(exc)})
    return {"imported": results, "errors": errors}


CHUNK_DIR = config.library_dir / ".tmp" / "chunks"
MAX_CHUNK = 8 * 1024 * 1024
_UPLOAD_ID = re.compile(r"^[a-f0-9]{32}$")


@router.post("/upload/chunk")
async def upload_chunk(
    request: Request,
    s: DB,
    upload_id: str,
    index: int,
    total: int,
    filename: str,
    model_id: int | None = None,
):
    """Receives a file in pieces, in order. Home Assistant's Ingress proxy refuses
    large request bodies, so the interface sends big files (STL...) in chunks."""
    if not _UPLOAD_ID.match(upload_id) or not (0 <= index < total):
        raise HTTPException(422, "Envoi invalide.")
    body = await request.body()
    if len(body) > MAX_CHUNK:
        raise HTTPException(413, "Morceau trop gros.")
    CHUNK_DIR.mkdir(parents=True, exist_ok=True)
    part = CHUNK_DIR / upload_id
    if index == 0:
        part.unlink(missing_ok=True)
    elif not part.exists():
        raise HTTPException(409, "Envoi interrompu, recommence l’import.")
    with part.open("ab") as fh:
        fh.write(body)
    if index < total - 1:
        return {"received": index + 1, "total": total}
    try:
        with part.open("rb") as fh:
            r = library.import_stream(s, fh, filename, model_id=model_id)
    except (library.UnsupportedFile, LookupError) as exc:
        s.rollback()
        return {"imported": [], "errors": [{"filename": filename, "error": str(exc)}]}
    finally:
        part.unlink(missing_ok=True)
    return {"imported": [_result(filename, r)], "errors": []}


@router.delete("/sliced/{file_id}", status_code=204)
def delete_sliced(file_id: int, s: DB):
    f = s.get(SlicedFile, file_id)
    if f is None:
        return
    if _has_open_jobs(s, [f.id]):
        raise HTTPException(409, "Ce fichier est utilisé par une impression planifiée.")
    _drop_closed_jobs(s, [f.id])
    for other in s.scalars(select(SlicedFile).where(SlicedFile.replaces_id == f.id)):
        other.replaces_id = None
    library.delete_file(f.path)
    if f.thumbnail:
        library.delete_file(f.thumbnail)
    s.delete(f)
    s.commit()


@router.delete("/sources/{file_id}", status_code=204)
def delete_source(file_id: int, s: DB):
    f = s.get(SourceFile, file_id)
    if f is None:
        return
    library.delete_file(f.path)
    s.delete(f)
    s.commit()


# -- files waiting to be filed ------------------------------------------------------


@router.get("/pending")
def list_pending(s: DB):
    return [serialize.pending(p) for p in s.scalars(select(PendingFile).order_by(PendingFile.created_at))]


@router.get("/pending/{pending_id}/thumbnail")
def pending_thumbnail(pending_id: int, s: DB):
    p = s.get(PendingFile, pending_id)
    if p is None or not p.thumbnail:
        raise HTTPException(404)
    return FileResponse(config.library_dir / p.thumbnail)


class FileIn(BaseModel):
    model_id: int | None = None  # an existing model…
    new_name: str | None = Field(None, max_length=200)  # … or a new one
    replace: bool = False  # replace the version for the same printer and material


@router.post("/pending/{pending_id}/file")
def file_pending(pending_id: int, body: FileIn, s: DB):
    p = s.get(PendingFile, pending_id)
    if p is None:
        raise HTTPException(404, "Fichier introuvable : il a peut-être déjà été rangé.")
    try:
        r = library.file_pending(s, p, model_id=body.model_id, new_name=body.new_name, replace=body.replace)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return _result(p.filename, r)


@router.delete("/pending/{pending_id}", status_code=204)
def discard_pending(pending_id: int, s: DB):
    p = s.get(PendingFile, pending_id)
    if p is not None:
        library.discard_pending(s, p)


# -- versions ---------------------------------------------------------------------


def _sliced(s: Session, file_id: int) -> SlicedFile:
    f = s.get(SlicedFile, file_id)
    if f is None:
        raise HTTPException(404, "Fichier introuvable.")
    return f


@router.post("/sliced/{file_id}/replace")
def replace_version(file_id: int, s: DB):
    """The new version replaces the older one for the same printer and material."""
    f = _sliced(s, file_id)
    if f.replaces_id is None:
        raise HTTPException(409, "Aucune version à remplacer.")
    library.replace_version(s, f)
    s.commit()
    return serialize.model(f.model, full=True)


@router.post("/sliced/{file_id}/keep-both")
def keep_both(file_id: int, s: DB):
    f = _sliced(s, file_id)
    f.replaces_id = None
    s.commit()
    return serialize.model(f.model, full=True)


@router.post("/sliced/{file_id}/archive")
def archive_version(file_id: int, s: DB):
    """Hides a version from planning, keeping its history."""
    f = _sliced(s, file_id)
    if _has_open_jobs(s, [f.id]):
        raise HTTPException(409, "Ce fichier est utilisé par une impression planifiée.")
    f.archived_at = utcnow()
    f.replaces_id = None
    s.commit()
    return serialize.model(f.model, full=True)


@router.post("/sliced/{file_id}/restore")
def restore_version(file_id: int, s: DB):
    f = _sliced(s, file_id)
    f.archived_at = None
    s.commit()
    return serialize.model(f.model, full=True)


@router.get("/sliced/{file_id}/thumbnail")
def thumbnail(file_id: int, s: DB):
    f = s.get(SlicedFile, file_id)
    if f is None or not f.thumbnail:
        raise HTTPException(404)
    return FileResponse(config.library_dir / f.thumbnail, headers={"Cache-Control": "max-age=86400"})


@router.get("/sliced/{file_id}/download")
def download_sliced(file_id: int, s: DB):
    f = s.get(SlicedFile, file_id)
    if f is None:
        raise HTTPException(404)
    return FileResponse(config.library_dir / f.path, filename=f.filename)


@router.get("/sources/{file_id}/download")
def download_source(file_id: int, s: DB):
    f = s.get(SourceFile, file_id)
    if f is None:
        raise HTTPException(404)
    return FileResponse(config.library_dir / f.path, filename=f.filename)
