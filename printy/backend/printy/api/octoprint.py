"""Minimal OctoPrint-compatible endpoints, so Orca Slicer can "send" to Printy.

In Orca: printer settings → physical printer → host type OctoPrint, URL
http://<home-assistant>:<port>, API key from Printy’s settings. Files land in the
library; nothing is sent to a printer.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import library
from .. import settings as settings_store
from ..db import get_session

router = APIRouter(tags=["octoprint"])
DB = Annotated[Session, Depends(get_session)]


def _check(s: Session, key: str | None) -> None:
    if not settings_store.get(s, "import_octoprint"):
        raise HTTPException(403, "L’envoi depuis le trancheur est désactivé dans Printy.")
    expected = settings_store.get_all(s)["octoprint_api_key"]
    if not key or key != expected:
        raise HTTPException(401, "Clé API invalide.")


@router.get("/api/version")
def version(s: DB, x_api_key: Annotated[str | None, Header()] = None):
    _check(s, x_api_key)
    return {"api": "0.1", "server": "1.10.0", "text": "OctoPrint 1.10.0 (Printy)"}


@router.post("/api/files/local", status_code=201)
def upload(s: DB, file: UploadFile = File(...), x_api_key: Annotated[str | None, Header()] = None):
    _check(s, x_api_key)
    try:
        r = library.import_stream(s, file.file, file.filename or "fichier.gcode")
    except library.UnsupportedFile as exc:
        raise HTTPException(415, str(exc)) from exc
    return {
        "done": True,
        "files": {"local": {"name": file.filename, "origin": "local"}},
        "printy": {
            "model_id": r.model.id if r.model else None,
            "model_name": r.model.name if r.model else None,
            "to_file": r.kind == "pending",  # name matches no model: waits in the library
            "duplicate": r.duplicate,
        },
    }
