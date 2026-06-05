import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pool_server import PoolDatabase


def test_pool_database_uses_busy_timeout(tmp_path):
    db = PoolDatabase(tmp_path / "pool.sqlite3")

    with db.connect() as connection:
        timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]

    assert timeout == 60000


def test_pool_database_event_does_not_raise_when_locked(tmp_path):
    db = PoolDatabase(tmp_path / "pool.sqlite3")

    blocker = sqlite3.connect(db.path)
    try:
        blocker.execute("BEGIN EXCLUSIVE")
        blocker.execute("CREATE TABLE IF NOT EXISTS lock_test (id INTEGER)")

        db.event("error", "test while locked", {"ok": True})
    finally:
        blocker.rollback()
        blocker.close()

