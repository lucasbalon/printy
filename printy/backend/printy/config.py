"""Runtime paths and environment.

In the add-on, persistent state lives in /data and files in /share/printy.
Outside Home Assistant (development), everything goes under ./.dev.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Config:
    data_dir: Path
    library_dir: Path
    inbox_dir: Path
    ha_www_dir: Path | None
    supervisor_token: str | None
    ha_url: str
    timezone: ZoneInfo
    dev: bool

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'printy.db'}"

    @property
    def in_home_assistant(self) -> bool:
        return self.supervisor_token is not None


def load_config() -> Config:
    token = os.environ.get("SUPERVISOR_TOKEN")
    dev = token is None
    root = Path(os.environ.get("PRINTY_DEV_DIR", ".dev")).resolve() if dev else None

    data_dir = Path(os.environ.get("PRINTY_DATA_DIR", root / "data" if root else "/data"))
    share_dir = Path(os.environ.get("PRINTY_SHARE_DIR", root / "share" if root else "/share/printy"))
    www = os.environ.get("PRINTY_HA_WWW_DIR", "" if dev else "/homeassistant/www/printy")

    cfg = Config(
        data_dir=data_dir,
        library_dir=share_dir / "library",
        inbox_dir=share_dir / "inbox",
        ha_www_dir=Path(www) if www else None,
        supervisor_token=token,
        ha_url=os.environ.get("PRINTY_HA_URL", "http://supervisor/core"),
        timezone=ZoneInfo(os.environ.get("TZ", "Europe/Paris")),
        dev=dev,
    )
    for d in (cfg.data_dir, cfg.library_dir, cfg.inbox_dir):
        d.mkdir(parents=True, exist_ok=True)
    if cfg.ha_www_dir:
        cfg.ha_www_dir.mkdir(parents=True, exist_ok=True)
    return cfg


config = load_config()
