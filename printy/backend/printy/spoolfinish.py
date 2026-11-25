"""Finishing a spool: which prints use what is left with the least waste.

A spool cannot be changed in the middle of a print, so the end of a spool is lost
unless the last prints fit it closely. Given the filament left (minus what is already
planned on the spool), the models of a tag (e.g. "abat-jour") and their stock, this
looks for combinations of prints that leave the least on the spool.

Two proposals come out:
- "reste": the smallest leftover;
- "stock": the most useful for the stock (models under their alert level, or the
  least stocked) with a leftover hardly larger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .settings import tune
from .spoolman import GOOD_MATCH, Spool, color_distance, limits

NODE_LIMIT = 300_000
# prints per combination and the "stock" proposal's extra leftover: settings.Tunables


@dataclass
class Candidate:
    sliced_id: int
    model_id: int
    model_name: str
    label: str  # version, when a model has several
    filament_g: float  # slicer estimate
    pieces: int
    duration_s: int
    stock: int
    alert: int | None

    @property
    def need_g(self) -> float:
        return limits.need(self.filament_g)


@dataclass
class Combo:
    counts: dict[int, int] = field(default_factory=dict)  # index in candidates -> prints


def rule_tags(rules: list[dict[str, Any]], spool: Spool) -> list[str]:
    """Tags suggested for a spool by the rules "this kind of spool → this tag"."""
    out: list[str] = []
    for r in rules:
        mat, col = r.get("material"), r.get("color")
        if mat and (spool.material or "").lower() != mat.lower():
            continue
        if col and color_distance(spool.color, col) > GOOD_MATCH:
            continue
        if r.get("tag") and r["tag"] not in out:
            out.append(r["tag"])
    return out


def _combos(cands: list[Candidate], available: float) -> list[dict[int, int]]:
    """Every combination that cannot take one more print (maximal), within limits."""
    order = sorted(range(len(cands)), key=lambda i: -cands[i].need_g)
    smallest = min(c.need_g for c in cands)
    max_prints = tune.finish_max_prints
    out: list[dict[int, int]] = []
    counts: dict[int, int] = {}
    nodes = 0

    def walk(pos: int, left: float, prints: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > NODE_LIMIT:
            return
        if left < smallest or prints >= max_prints or pos == len(order):
            if counts and (left < smallest or prints >= max_prints or not any(cands[i].need_g <= left for i in order)):
                out.append(dict(counts))
            return
        i = order[pos]
        need = cands[i].need_g
        most = min(int(left // need), max_prints - prints)
        for n in range(most, -1, -1):
            if n:
                counts[i] = n
            else:
                counts.pop(i, None)
            walk(pos + 1, left - n * need, prints + n)
        counts.pop(i, None)

    walk(0, available, 0)
    return out


def _stock_value(cands: list[Candidate], combo: dict[int, int]) -> float:
    """Pieces under the alert level count most, then pieces of little-stocked models."""
    added: dict[int, int] = {}
    value = 0.0
    for i, n in combo.items():
        c = cands[i]
        for _ in range(n * max(1, c.pieces)):
            have = c.stock + added.get(c.model_id, 0)
            value += 2.0 if c.alert is not None and have < c.alert else 1.0 / (1 + have)
            added[c.model_id] = added.get(c.model_id, 0) + 1
    return value


def _describe(cands: list[Candidate], combo: dict[int, int], available: float, kind: str) -> dict[str, Any]:
    items = []
    for i, n in sorted(combo.items(), key=lambda kv: -cands[kv[0]].filament_g):
        c = cands[i]
        refill = c.alert is not None and c.stock < c.alert
        items.append({
            "sliced_id": c.sliced_id, "model_id": c.model_id, "model_name": c.model_name, "label": c.label,
            "count": n, "filament_g": round(c.filament_g, 1), "pieces": c.pieces * n,
            "stock": c.stock, "alert": c.alert, "under_alert": refill,
        })
    used = sum(cands[i].filament_g * n for i, n in combo.items())
    reserved = sum(cands[i].need_g * n for i, n in combo.items())
    return {
        "kind": kind,
        "items": items,
        "prints": sum(combo.values()),
        "used_g": round(used, 1),
        # the 5 % margin is kept on the spool: what is expected to really remain
        "leftover_g": round(available - used, 1),
        "margin_g": round(reserved - used, 1),
        "duration_s": sum(cands[i].duration_s * n for i, n in combo.items()),
    }


def proposals(cands: list[Candidate], available: float) -> list[dict[str, Any]]:
    if not cands or available <= 0:
        return []
    combos = _combos(cands, available)
    if not combos:
        return []

    def leftover(c: dict[int, int]) -> float:
        return available - sum(cands[i].need_g * n for i, n in c.items())

    best = min(combos, key=lambda c: (round(leftover(c), 1), -_stock_value(cands, c), sum(c.values())))
    slack = tune.finish_stock_slack_g
    near = [c for c in combos if leftover(c) <= leftover(best) + slack]
    useful = max(near, key=lambda c: (round(_stock_value(cands, c), 3), -leftover(c), -sum(c.values())))
    out = [_describe(cands, best, available, "reste")]
    if useful != best and _stock_value(cands, useful) > _stock_value(cands, best) + 1e-6:
        out.append(_describe(cands, useful, available, "stock"))
    else:
        out[0]["also_stock"] = True  # the closest fit is also the most useful for the stock
    return out
