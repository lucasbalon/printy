"""User settings stored in the database, with sensible defaults."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, fields
from typing import Any

from sqlalchemy.orm import Session

from .models import Setting

DEFAULTS: dict[str, Any] = {
    # Notifications: list of notify services, e.g. "notify.mobile_app_pixel_8"
    "notify_targets": [],
    # Ask "is the bed empty?" before each print (can be turned off per job)
    "confirm_by_default": True,
    # The "is the bed empty?" notification goes out this long before the start
    # (before the power on if larger than the printer's power-on lead: photo only if
    # the camera works with the printer off)
    "confirm_lead_min": 10,
    # Without an answer after this delay, the job needs attention and power is cut
    "confirm_timeout_min": 15,  # counted from the start time at the earliest
    # Filament needed by a print: slicer estimate + this margin; and what stays on the core
    "filament_margin_pct": 5,
    "spool_reserve_g": 0,
    # Suggestions for finishing a spool: [{"tag", "material", "color"}], a kind of spool → a tag
    "tag_rules": [],
    # Reminder when a finished piece is still on the bed 2 h before the next print
    "bed_reminder": True,
    # Do not cut power if another print starts within this delay
    "keep_on_window_min": 30,
    # Off-peak electricity ranges, local time
    "off_peak": [{"start": "22:00", "end": "06:00"}],
    # Import channels
    "import_octoprint": True,
    "import_watch_folder": True,
    "octoprint_api_key": None,  # generated on first read
    # Spoolman base URL, e.g. http://homeassistant.local:7912 (empty: disabled)
    "spoolman_url": "",
    # Spool change reminders (the launch itself always asks for confirmation)
    "spool_reminder_finish": True,  # in the end-of-print notification
    "spool_reminder_morning": "08:00",  # daily summary time, "" to disable
    "spool_reminder_sent_on": None,  # internal: last day the summary went out
    # Production (V3)
    "stock_auto": True,  # add pieces to stock when a print finishes
    "ha_entities": True,  # publish sensor.printy_* entities in Home Assistant
}


@dataclass
class Tunables:
    """Delays and thresholds that used to be fixed in the code, shown in the settings."""

    snooze_min: int = 60  # "Reporter" in the notification and on the print
    bed_reminder_hours: float = 2.0  # forgotten piece: this long before the next print
    bed_reminder_after_min: int = 30  # ... and at least this long after the end
    bed_photo_threshold_pct: float = 3.0  # share of the picture changed: "occupied"
    offpeak_horizon_days: int = 3  # how far the off-peak slot is looked for
    finish_max_prints: int = 12  # finishing a spool: prints per combination
    finish_stock_slack_g: float = 25.0  # "stock" proposal: extra leftover accepted
    boot_settle_min: float = 2.0  # printer just switched on: time to finish booting
    offline_grace_min: int = 10  # printer silent after the power on (plus the lead time)
    launch_timeout_min: int = 10  # sending the file and the start command
    start_grace_min: int = 10  # start command sent: idle, heating, calibration allowed
    busy_grace_min: int = 30  # ... or this long when the printer says it is busy


tune = Tunables()
for _f in fields(Tunables):
    DEFAULTS[_f.name] = _f.default


def _apply_tunables(values: dict[str, Any]) -> None:
    for f in fields(Tunables):
        try:
            setattr(tune, f.name, type(f.default)(values.get(f.name, f.default)))
        except (TypeError, ValueError):
            setattr(tune, f.name, f.default)


def get_all(session: Session) -> dict[str, Any]:
    from .spoolman import limits, spoolman

    values = dict(DEFAULTS)
    for row in session.query(Setting).all():
        values[row.key] = row.value
    spoolman.configure(values.get("spoolman_url"))
    limits.margin_pct = float(values.get("filament_margin_pct", 5))
    limits.reserve_g = float(values.get("spool_reserve_g", 0))
    _apply_tunables(values)
    if not values.get("octoprint_api_key"):
        values["octoprint_api_key"] = secrets.token_hex(16)
        set_many(session, {"octoprint_api_key": values["octoprint_api_key"]})
    return values


def get(session: Session, key: str) -> Any:
    row = session.get(Setting, key)
    return row.value if row is not None else DEFAULTS.get(key)


def set_many(session: Session, values: dict[str, Any]) -> None:
    for key, value in values.items():
        if key not in DEFAULTS:
            raise KeyError(key)
        row = session.get(Setting, key)
        if row is None:
            session.add(Setting(key=key, value=value))
        else:
            row.value = value
    session.commit()
