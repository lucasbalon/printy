"""Imports files dropped into the inbox folder (/share/printy/inbox)."""

from __future__ import annotations

import asyncio
import logging
import shutil
import time

from . import library
from . import settings as settings_store
from .config import config
from .db import SessionLocal

log = logging.getLogger(__name__)

SETTLE_S = 5  # a file must be unchanged for this long (copy finished)


async def watch_inbox() -> None:
    seen: dict[str, tuple[int, float]] = {}
    rejected = config.inbox_dir / "refusés"
    while True:
        await asyncio.sleep(5)
        try:
            with SessionLocal() as s:
                if not settings_store.get(s, "import_watch_folder"):
                    continue
                for path in sorted(config.inbox_dir.iterdir()):
                    if not path.is_file() or path.name.startswith("."):
                        continue
                    stat = path.stat()
                    prev = seen.get(path.name)
                    if prev is None or prev[0] != stat.st_size:
                        seen[path.name] = (stat.st_size, time.monotonic())
                        continue
                    if time.monotonic() - prev[1] < SETTLE_S:
                        continue
                    seen.pop(path.name, None)
                    try:
                        library.import_path(s, path)
                        path.unlink()
                    except library.UnsupportedFile:
                        rejected.mkdir(exist_ok=True)
                        shutil.move(path, rejected / path.name)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("inbox import failed")
