"""Consistent copies of the SQLite database.

Home Assistant backs up /data while Printy runs (`backup: hot`). Just before, the
Supervisor runs `python3 -m printy.backup` (`backup_pre`), which writes a consistent
copy next to the database. After a restore, a database that does not open cleanly is
replaced by that copy at start-up.

Standard library only: `backup_pre` runs outside the add-on's environment.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def backup_path(db: Path) -> Path:
    return db.with_name(f"{db.stem}-backup{db.suffix}")


def snapshot(db: Path, dest: Path) -> None:
    """Copies the live database through SQLite's backup API (safe while in use)."""
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    src = sqlite3.connect(db)
    try:
        out = sqlite3.connect(tmp)
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    tmp.replace(dest)


def _healthy(db: Path) -> bool:
    try:
        con = sqlite3.connect(db)
        try:
            return con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            con.close()
    except sqlite3.Error:
        return False


def restore_if_broken(db: Path) -> bool:
    """At start-up: a damaged database is replaced by the last consistent copy."""
    copy = backup_path(db)
    if not db.exists() or not copy.exists() or _healthy(db) or not _healthy(copy):
        return False
    log.warning("database damaged, restoring the copy made for the last backup")
    db.rename(db.with_name(f"{db.name}.damaged"))
    for extra in (db.with_name(f"{db.name}-wal"), db.with_name(f"{db.name}-shm")):
        extra.unlink(missing_ok=True)
    shutil.copyfile(copy, db)
    return True


if __name__ == "__main__":
    database = Path(sys.argv[1] if len(sys.argv) > 1 else "/data/printy.db")
    if database.exists():
        snapshot(database, backup_path(database))
