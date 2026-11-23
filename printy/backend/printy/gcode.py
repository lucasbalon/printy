"""Metadata extraction from sliced files (.gcode and .gcode.3mf).

Reads the slicer comments (Orca Slicer, Bambu Studio, PrusaSlicer, Creality Print,
Cura) from the head and the tail of the file, and the largest embedded thumbnail.
"""

from __future__ import annotations

import base64
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

HEAD_BYTES = 1_500_000
TAIL_BYTES = 400_000

SLICED_SUFFIXES = (".gcode", ".gco", ".g", ".gcode.3mf")
SOURCE_SUFFIXES = (".stl", ".3mf", ".obj", ".step", ".stp", ".blend")


@dataclass
class SlicedMeta:
    duration_s: int | None = None
    filament_g: float | None = None
    filament_m: float | None = None
    material: str | None = None
    color: str | None = None
    layer_height: float | None = None
    nozzle: float | None = None
    printer_model: str | None = None
    thumbnail_png: bytes | None = None
    thumbnail_ext: str = "png"


def is_sliced(filename: str) -> bool:
    name = filename.lower()
    if name.endswith(".3mf"):
        return name.endswith(".gcode.3mf")
    return name.endswith(SLICED_SUFFIXES)


def is_source(filename: str) -> bool:
    return not is_sliced(filename) and filename.lower().endswith(SOURCE_SUFFIXES)


def read_meta(path: Path) -> SlicedMeta:
    if path.name.lower().endswith(".3mf"):
        return _read_3mf(path)
    with path.open("rb") as fh:
        head = fh.read(HEAD_BYTES)
        size = path.stat().st_size
        tail = b""
        if size > HEAD_BYTES:
            fh.seek(max(HEAD_BYTES, size - TAIL_BYTES))
            tail = fh.read()
    return parse_gcode_text((head + b"\n" + tail).decode("utf-8", errors="replace"))


# --- G-code comments -----------------------------------------------------------

_KV = re.compile(r"^;\s*([^=:\n]+?)\s*[=:]\s*(.+?)\s*$", re.MULTILINE)
_DURATION = re.compile(r"(?:(\d+)d)?\s*(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?")
_THUMB_BEGIN = re.compile(
    r"^;\s*(thumbnail|thumbnail_PNG|thumbnail_JPG|png) begin\s+(\d+)[x*](\d+)", re.MULTILINE
)
_THUMB_END = re.compile(r"^;\s*(?:thumbnail|thumbnail_PNG|thumbnail_JPG|png) end", re.MULTILINE)


def _duration(text: str) -> int | None:
    text = text.strip()
    if text.isdigit():
        return int(text)
    m = _DURATION.fullmatch(text.replace(" ", ""))
    if not m or not any(m.groups()):
        return None
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def _floats(value: str) -> list[float]:
    out = []
    for part in re.split(r"[,;]", value):
        try:
            out.append(float(part.strip()))
        except ValueError:
            pass
    return out


def _first(value: str) -> str:
    return re.split(r"[;,]", value)[0].strip().strip('"')


def parse_gcode_text(text: str) -> SlicedMeta:
    meta = SlicedMeta()
    kv: dict[str, str] = {}
    for key, value in _KV.findall(text):
        kv.setdefault(key.strip().lower(), value)

    for key in (
        "estimated printing time (normal mode)",
        "model printing time",
        "total estimated time",
        "print time",
        "estimated printing time",
        "time",
    ):
        if key in kv and (d := _duration(kv[key])) is not None:
            meta.duration_s = d
            break

    for key in ("total filament used [g]", "filament used [g]", "filament used(g)"):
        if key in kv and (vals := _floats(kv[key])):
            meta.filament_g = round(sum(vals), 2)
            break
    for key in ("filament used [mm]", "total filament used [mm]"):
        if key in kv and (vals := _floats(kv[key])):
            meta.filament_m = round(sum(vals) / 1000, 2)
            break
    if meta.filament_m is None and "filament used" in kv:
        # Cura: ";Filament used: 1.234m"
        if vals := _floats(kv["filament used"].rstrip("m")):
            meta.filament_m = round(sum(vals), 2)

    if v := kv.get("filament_type"):
        meta.material = _first(v)
    for key in ("filament_colour", "filament_color", "extruder_colour"):
        if (v := kv.get(key)) and re.fullmatch(r"#[0-9a-fA-F]{6,8}", _first(v)):
            meta.color = _first(v)[:7].upper()
            break
    if (v := kv.get("layer_height")) and (vals := _floats(v)):
        meta.layer_height = vals[0]
    if (v := kv.get("nozzle_diameter")) and (vals := _floats(v)):
        meta.nozzle = vals[0]
    for key in ("printer_model", "printer_settings_id", "machine_name"):
        if v := kv.get(key):
            meta.printer_model = _first(v)[:120]
            break

    meta.thumbnail_png, meta.thumbnail_ext = _largest_thumbnail(text)
    return meta


def _largest_thumbnail(text: str) -> tuple[bytes | None, str]:
    best: tuple[int, bytes, str] | None = None
    for m in _THUMB_BEGIN.finditer(text):
        kind, w, h = m.group(1), int(m.group(2)), int(m.group(3))
        end_m = _THUMB_END.search(text, m.end())
        if end_m is None:
            continue
        lines = text[m.end() : end_m.start()].splitlines()[1:]  # skip rest of "begin" line
        data = "".join(line.lstrip(";").strip() for line in lines)
        try:
            raw = base64.b64decode(data, validate=False)
        except ValueError:
            continue
        ext = "jpg" if kind.endswith("JPG") or raw[:3] == b"\xff\xd8\xff" else "png"
        if not (raw.startswith(b"\x89PNG") or raw[:3] == b"\xff\xd8\xff"):
            continue
        if best is None or w * h > best[0]:
            best = (w * h, raw, ext)
    return (best[1], best[2]) if best else (None, "png")


# --- 3MF (Bambu Studio / Orca "export plate sliced file") ----------------------


def _read_3mf(path: Path) -> SlicedMeta:
    meta = SlicedMeta()
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        gcode = next((n for n in names if n.lower().endswith(".gcode")), None)
        if gcode:
            with zf.open(gcode) as fh:
                head = fh.read(HEAD_BYTES)
            meta = parse_gcode_text(head.decode("utf-8", errors="replace"))
        for candidate in ("Metadata/plate_1.png", "Metadata/thumbnail.png", "Thumbnails/thumbnail.png"):
            if candidate in names:
                meta.thumbnail_png, meta.thumbnail_ext = zf.read(candidate), "png"
                break
        if "Metadata/slice_info.config" in names:
            _apply_slice_info(meta, zf.read("Metadata/slice_info.config"))
    return meta


def _apply_slice_info(meta: SlicedMeta, xml: bytes) -> None:
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return
    plate = root.find("plate")
    if plate is None:
        return
    values = {m.get("key"): m.get("value") for m in plate.findall("metadata")}
    if meta.duration_s is None and (v := values.get("prediction")) and v.isdigit():
        meta.duration_s = int(v)
    if meta.filament_g is None and (v := values.get("weight")):
        try:
            meta.filament_g = round(float(v), 2)
        except ValueError:
            pass
    fil = plate.find("filament")
    if fil is not None:
        meta.material = meta.material or fil.get("type")
        if not meta.color and (c := fil.get("color")):
            meta.color = c[:7].upper()
