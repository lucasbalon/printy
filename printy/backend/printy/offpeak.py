"""Off-peak electricity ranges: share of a print inside them, best slot for a print."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from .config import config

Interval = tuple[datetime, datetime]

STEP = timedelta(minutes=15)
HORIZON = timedelta(days=3)


def intervals(ranges: list[dict[str, str]], around: datetime, days: int = 5) -> list[Interval]:
    """Off-peak intervals (aware datetimes) from the day before `around` on."""
    tz = config.timezone
    day0 = around.astimezone(tz).date() - timedelta(days=1)
    out: list[Interval] = []
    for i in range(days + 1):
        day = day0 + timedelta(days=i)
        for r in ranges:
            sh, sm = (int(x) for x in r["start"].split(":"))
            eh, em = (int(x) for x in r["end"].split(":"))
            start = datetime.combine(day, time(sh, sm), tz)
            end = datetime.combine(day, time(eh, em), tz)
            if end <= start:
                end += timedelta(days=1)  # 22:00 → 06:00 the next day
            out.append((start, end))
    return out


def share(start: datetime, end: datetime, ivs: list[Interval]) -> float:
    total = (end - start).total_seconds()
    if total <= 0:
        return 0.0
    inside = sum(max(0.0, (min(end, e) - max(start, s)).total_seconds()) for s, e in ivs)
    return min(1.0, inside / total)


def best_slot(
    ranges: list[dict[str, str]], duration: timedelta, busy: list[Interval], now: datetime
) -> tuple[datetime, float] | None:
    """Earliest free start with the largest off-peak share, within the next 3 days."""
    if not ranges:
        return None
    ivs = intervals(ranges, now)
    first = now.replace(second=0, microsecond=0)
    first += timedelta(minutes=-first.minute % 15)
    candidates = {first + STEP * i for i in range(int(HORIZON / STEP))}
    candidates |= {s for s, _ in ivs} | {e - duration for _, e in ivs} | {e for _, e in busy}
    best: tuple[datetime, float] | None = None
    for start in sorted(c for c in candidates if now <= c <= now + HORIZON):
        end = start + duration
        if any(s < end and start < e for s, e in busy):
            continue
        sh = round(share(start, end, ivs), 3)
        if best is None or sh > best[1]:
            best = (start, sh)
    return best
