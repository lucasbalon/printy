"""Spoolman integration: spools, colour matching, consumption and the filament plan.

Spoolman API: https://donkie.github.io/Spoolman/ (GET /api/v1/spool, PUT /api/v1/spool/{id}/use).
Printy only reads spools and reports consumption; it never edits them otherwise.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import asdict, dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

CACHE_S = 30
# g/cm3, needed by Spoolman to turn lengths into weights
DENSITY = {"PLA": 1.24, "PETG": 1.27, "ABS": 1.04, "ASA": 1.07, "TPU": 1.21, "PC": 1.20, "PA": 1.14}
LOW_MARGIN = 1.05  # need 5 % more than the slicer estimate
GOOD_MATCH = 18.0  # CIE76 distance under which two colours read as "the same"


class SpoolmanError(Exception):
    pass


@dataclass
class Spool:
    id: int
    name: str  # "Blanc mat"
    vendor: str | None
    material: str | None
    color: str | None  # "#RRGGBB"
    remaining_g: float | None
    location: str | None
    filament_id: int | None = None
    initial_g: float | None = None

    @property
    def label(self) -> str:
        parts = [self.vendor, self.name] if self.vendor and self.vendor not in self.name else [self.name]
        return " ".join(p for p in parts if p)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "label": self.label}


def _parse(raw: dict[str, Any]) -> Spool:
    fil = raw.get("filament") or {}
    hexes = fil.get("color_hex") or (fil.get("multi_color_hexes") or "").split(",")[0]
    color = f"#{hexes[:6].upper()}" if hexes else None
    return Spool(
        id=int(raw["id"]),
        name=fil.get("name") or f"Bobine {raw['id']}",
        vendor=(fil.get("vendor") or {}).get("name"),
        material=fil.get("material"),
        color=color,
        remaining_g=raw.get("remaining_weight"),
        location=raw.get("location") or None,
        filament_id=fil.get("id"),
        initial_g=raw.get("initial_weight") or fil.get("weight"),
    )


class Spoolman:
    def __init__(self) -> None:
        self.url = ""
        self._cache: tuple[float, list[Spool]] | None = None

    def configure(self, url: str | None) -> None:
        url = (url or "").strip().rstrip("/")
        if url and not url.startswith("http"):
            url = f"http://{url}"
        if url != self.url:
            self.url = url
            self._cache = None

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    async def _request(self, method: str, path: str, **kw: Any) -> Any:
        if not self.enabled:
            raise SpoolmanError("Spoolman n’est pas configuré.")
        try:
            async with httpx.AsyncClient(base_url=f"{self.url}/api/v1", timeout=8) as http:
                r = await http.request(method, path, **kw)
                r.raise_for_status()
                return r.json()
        except httpx.HTTPError as exc:
            raise SpoolmanError(f"Spoolman ne répond pas ({exc.__class__.__name__}).") from exc

    async def info(self) -> dict[str, Any]:
        return await self._request("GET", "/info")

    async def spools(self, *, fresh: bool = False) -> list[Spool]:
        now = time.monotonic()
        if not fresh and self._cache and now - self._cache[0] < CACHE_S:
            return self._cache[1]
        raw = await self._request("GET", "/spool", params={"allow_archived": "false"})
        spools = [_parse(r) for r in raw]
        self._cache = (now, spools)
        return spools

    async def spools_or_empty(self) -> list[Spool]:
        if not self.enabled:
            return []
        try:
            return await self.spools()
        except SpoolmanError as exc:
            log.warning("%s", exc)
            return self._cache[1] if self._cache else []

    async def filaments(self) -> list[dict[str, Any]]:
        raw = await self._request("GET", "/filament")
        out = []
        for f in raw:
            hexes = f.get("color_hex") or (f.get("multi_color_hexes") or "").split(",")[0]
            out.append({
                "id": f["id"],
                "name": f.get("name") or f"Filament {f['id']}",
                "vendor": (f.get("vendor") or {}).get("name"),
                "material": f.get("material"),
                "color": f"#{hexes[:6].upper()}" if hexes else None,
                "weight": f.get("weight"),
            })
        return sorted(out, key=lambda f: ((f["vendor"] or "").lower(), (f["material"] or ""), f["name"].lower()))

    async def vendor_id(self, name: str) -> int:
        """Existing vendor with this name, or a new one."""
        for v in await self._request("GET", "/vendor"):
            if (v.get("name") or "").strip().lower() == name.strip().lower():
                return int(v["id"])
        created = await self._request("POST", "/vendor", json={"name": name.strip()})
        return int(created["id"])

    async def create_filament(
        self, *, vendor: str | None, name: str, material: str, color: str | None, weight: float, diameter: float
    ) -> int:
        body: dict[str, Any] = {
            "name": name.strip(),
            "material": material.strip().upper(),
            "density": DENSITY.get(material.strip().upper(), 1.24),
            "diameter": diameter,
            "weight": weight,
        }
        if color:
            body["color_hex"] = color.lstrip("#").upper()[:6]
        if vendor and vendor.strip():
            body["vendor_id"] = await self.vendor_id(vendor)
        created = await self._request("POST", "/filament", json=body)
        return int(created["id"])

    async def create_spool(self, filament_id: int, *, location: str | None = None) -> Spool:
        body: dict[str, Any] = {"filament_id": filament_id}
        if location:
            body["location"] = location
        created = await self._request("POST", "/spool", json=body)
        self._cache = None
        return _parse(created)

    async def archive(self, spool_id: int) -> None:
        await self._request("PATCH", f"/spool/{spool_id}", json={"archived": True})
        self._cache = None

    async def use(self, spool_id: int, grams: float) -> None:
        await self._request("PUT", f"/spool/{spool_id}/use", json={"use_weight": round(grams, 2)})
        self._cache = None


spoolman = Spoolman()


# -- colour matching ---------------------------------------------------------------


def _lab(hex_color: str) -> tuple[float, float, float]:
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5))

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = lin(r), lin(g), lin(b)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def color_distance(a: str | None, b: str | None) -> float:
    if not a or not b:
        return math.inf
    try:
        return math.dist(_lab(a), _lab(b))
    except ValueError:
        return math.inf


def rank_spools(
    spools: list[Spool], color: str | None, material: str | None, loaded_id: int | None = None
) -> list[tuple[Spool, float]]:
    """Spools sorted from best to worst match; lower score is better."""

    def score(s: Spool) -> float:
        d = color_distance(color, s.color) if color else 50.0
        if material and s.material and s.material.upper() != material.upper():
            d += 60  # wrong material: only if nothing else fits
        if s.remaining_g is not None and s.remaining_g <= 0:
            d += 200
        if s.id == loaded_id:
            d -= 4  # prefer not to swap when the loaded spool is as good
        return d

    return sorted(((s, score(s)) for s in spools), key=lambda x: x[1])


def suggest(
    spools: list[Spool],
    color: str | None,
    material: str | None,
    loaded_id: int | None,
    need_g: float | None = None,
) -> Spool | None:
    """The spool to use for a print.

    Among the spools of the right colour and material:
    1. the one already on the printer, if it has enough filament;
    2. otherwise the opened spool with the least filament that is still enough,
       so started spools get used up before new ones are opened;
    3. otherwise the fullest one (Printy will warn that it runs short).
    """
    ranked = rank_spools(spools, color, material, None)
    if not ranked or ranked[0][1] > GOOD_MATCH:
        return None
    best = ranked[0][1]
    same = [sp for sp, score in ranked if score <= GOOD_MATCH and score - best < 8]
    need = (need_g or 0) * LOW_MARGIN

    def enough(sp: Spool) -> bool:
        return sp.remaining_g is None or sp.remaining_g >= need

    loaded = next((sp for sp in same if sp.id == loaded_id), None)
    if loaded and enough(loaded):
        return loaded
    sufficient = [sp for sp in same if enough(sp)]
    if sufficient:
        return min(sufficient, key=lambda sp: sp.remaining_g if sp.remaining_g is not None else float("inf"))
    return max(same, key=lambda sp: sp.remaining_g or 0)


def describe(sp: Spool) -> str:
    """"la bobine n° 12 Elegoo Blanc mat (PLA, 820 g, étagère A)": enough to pick the right one."""
    details = [d for d in (sp.material, f"{round(sp.remaining_g)} g" if sp.remaining_g is not None else None, sp.location) if d]
    return f"la bobine n° {sp.id} {sp.label}" + (f" ({', '.join(details)})" if details else "")


# -- filament plan -------------------------------------------------------------------


@dataclass
class JobFilament:
    spool_change: bool = False  # another spool than the one loaded before this job
    previous_spool_id: int | None = None
    low_filament: bool = False  # the spool will not have enough left
    remaining_before_g: float | None = None


def plan(jobs: list[Any], loaded: dict[int, int | None], spools: list[Spool]) -> dict[int, JobFilament]:
    """Walks open jobs in time order: which ones need a spool change, which ones run dry.

    `jobs`: open jobs (objects with id, printer_id, spool_id, start_at, sliced_file.filament_g).
    `loaded`: spool currently loaded per printer id.
    """
    remaining = {s.id: s.remaining_g for s in spools}
    current = dict(loaded)
    out: dict[int, JobFilament] = {}
    for job in sorted(jobs, key=lambda j: j.start_at):
        info = JobFilament()
        if job.spool_id is not None:
            before = current.get(job.printer_id)
            if before is not None and before != job.spool_id:
                info.spool_change = True
                info.previous_spool_id = before
            current[job.printer_id] = job.spool_id
            need = job.sliced_file.filament_g
            left = remaining.get(job.spool_id)
            if left is not None:
                info.remaining_before_g = round(left, 1)
                if need:
                    info.low_filament = left < need * LOW_MARGIN
                    remaining[job.spool_id] = left - need
        out[job.id] = info
    return out
