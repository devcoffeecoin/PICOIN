import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pool_server import PoolCoordinator, PoolDatabase


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


def test_public_pool_requires_valid_worker_payout(tmp_path):
    db = PoolDatabase(tmp_path / "pool.sqlite3")
    coordinator = PoolCoordinator(
        db=db,
        server_url="https://api.picoin.science",
        identity={"miner_id": "miner_pool"},
        chunk_size=1,
        poll_seconds=1,
        chunk_timeout_seconds=30,
        verify_chunks=False,
        require_worker_payout=True,
        pool_fee_percent=0,
    )

    with pytest.raises(ValueError, match="payout_address is required"):
        coordinator.register_worker("worker-1", "Worker 1", None)

    with pytest.raises(ValueError, match="valid PI wallet"):
        coordinator.register_worker("worker-1", "Worker 1", "not-a-wallet")

    result = coordinator.register_worker(
        "worker-1",
        "Worker 1",
        "PIE1EE818AA165EECC3F0CCF058F4FF7BC04517F8CD07385",
    )

    assert result["status"] == "ok"
    assert result["worker_id"] == "worker-1"

    coordinator.register_worker(
        "worker-1",
        "Worker 1 renamed",
        "PI251078EE911B17EDC747DB5BDF505649ECAF60F787AA23",
    )

    with db.connect() as connection:
        row = connection.execute(
            "SELECT name, payout_address FROM pool_workers WHERE worker_id = ?",
            ("worker-1",),
        ).fetchone()

    assert row["name"] == "Worker 1 renamed"
    assert row["payout_address"] == "PIE1EE818AA165EECC3F0CCF058F4FF7BC04517F8CD07385"
