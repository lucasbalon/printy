"""Is the bed empty? A hint from the camera, never a decision.

A reference photo of the empty bed is kept per printer, taken right before a print
that the user confirmed (or launched at once): the bed is known to be empty then.
A later photo is compared with it; a piece left on the bed shows up as a patch of
pixels that changed. Brightness and contrast are normalised first, so the light of
the room matters little; a moved camera or a lit / unlit chamber can still fool it,
which is why the result is only ever shown as "seems empty / seems occupied".
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

from .config import config

log = logging.getLogger(__name__)

REF_DIR = config.data_dir / "snapshots"
REF_DIR.mkdir(parents=True, exist_ok=True)

SIZE = (96, 72)
PIXEL_DELTA = 1.2  # a pixel "changed" beyond this many standard deviations
OCCUPIED_SHARE = 0.03  # more than 3 % of changed pixels: something is on the bed


def _ref_path(printer_id: int) -> Path:
    return REF_DIR / f"printer-{printer_id}-empty.jpg"


def save_reference(printer_id: int, data: bytes) -> None:
    _ref_path(printer_id).write_bytes(data)


def has_reference(printer_id: int) -> bool:
    return _ref_path(printer_id).exists()


def _normalised(data: bytes) -> list[float] | None:
    from PIL import Image, ImageFilter

    try:
        img = Image.open(io.BytesIO(data)).convert("L").resize(SIZE).filter(ImageFilter.GaussianBlur(1.5))
    except Exception:  # noqa: BLE001 - not an image
        return None
    px = list(img.tobytes())  # one byte per pixel in L mode
    mean = sum(px) / len(px)
    std = (sum((p - mean) ** 2 for p in px) / len(px)) ** 0.5 or 1.0
    return [(p - mean) / std for p in px]


def changed_share(printer_id: int, data: bytes) -> float | None:
    """Share of the picture that differs from the empty bed (None: no reference)."""
    path = _ref_path(printer_id)
    if not path.exists():
        return None
    ref, now = _normalised(path.read_bytes()), _normalised(data)
    if ref is None or now is None:
        return None
    changed = sum(1 for a, b in zip(ref, now, strict=True) if abs(a - b) > PIXEL_DELTA)
    return changed / len(ref)


def verdict(printer_id: int, data: bytes | None) -> str | None:
    """"empty", "occupied" or None (no photo or no reference)."""
    if not data:
        return None
    share = changed_share(printer_id, data)
    if share is None:
        return None
    log.info("bed check printer %s: %.1f %% changed", printer_id, share * 100)
    return "occupied" if share > OCCUPIED_SHARE else "empty"


def sentence(v: str | None) -> str:
    if v == "empty":
        return "D’après la photo, le plateau semble vide."
    if v == "occupied":
        return "⚠️ D’après la photo, le plateau semble encore occupé."
    return ""
