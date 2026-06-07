import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pool_server
from pool_server import PoolCoordinator, PoolDatabase, is_lost_competitive_round_error, parse_iso_timestamp, utc_now
from miner.client import TaskUnavailable
from app.services.wallet import create_wallet


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


def test_pool_treats_mainnet_task_429_as_idle_info(tmp_path, monkeypatch):
    db = PoolDatabase(tmp_path / "pool.sqlite3")
    coordinator = PoolCoordinator(
        db=db,
        server_url="https://api.picoin.science",
        identity={"miner_id": "miner_pool"},
        chunk_size=1,
        poll_seconds=1,
        chunk_timeout_seconds=30,
        verify_chunks=False,
        require_worker_payout=False,
        pool_fee_percent=0,
    )

    def unavailable(*args, **kwargs):
        raise TaskUnavailable("competitive round is waiting for validation; retry after next block", 7)

    monkeypatch.setattr(pool_server, "get_task_for_identity", unavailable)

    coordinator.ensure_active_task()

    with db.connect() as connection:
        task_count = connection.execute("SELECT COUNT(*) FROM pool_tasks").fetchone()[0]
        event = connection.execute(
            "SELECT level, message, payload_json FROM pool_events ORDER BY rowid DESC LIMIT 1"
        ).fetchone()

    assert task_count == 0
    assert event["level"] == "info"
    assert event["message"] == "mainnet did not assign pool work"
    payload = json.loads(event["payload_json"])
    assert payload["status"] == "unavailable"
    assert "waiting for validation" in payload["detail"]
    assert payload["retry_after_seconds"] == 7


def test_stats_reports_lost_competitive_rounds_without_error_status(tmp_path):
    assert is_lost_competitive_round_error("commit rejected: competitive round won by task_abc at block 123")
    assert not is_lost_competitive_round_error("commit rejected: invalid signature")

    db = PoolDatabase(tmp_path / "pool.sqlite3")
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO pool_tasks (
                pool_task_id, mainnet_task_id, status, range_start, range_end,
                algorithm, raw_task_json, error, created_at
            )
            VALUES (?, ?, 'error', 1, 1, 'bbp_hex_v1', '{}', ?, '2026-06-05T00:00:00+00:00')
            """,
            (
                "pooltask_lost",
                "task_lost",
                "commit rejected: competitive round won by task_winner at block 6674",
            ),
        )

    coordinator = PoolCoordinator(
        db=db,
        server_url="https://api.picoin.science",
        identity={"miner_id": "miner_pool"},
        chunk_size=1,
        poll_seconds=1,
        chunk_timeout_seconds=30,
        verify_chunks=False,
        require_worker_payout=False,
        pool_fee_percent=0,
    )

    assert coordinator.stats()["tasks"] == [{"status": "lost", "count": 1}]


def test_stats_reports_pool_performance_and_won_blocks(tmp_path):
    worker_wallet = create_wallet("worker")
    db = PoolDatabase(tmp_path / "pool.sqlite3")
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO pool_workers (worker_id, name, payout_address, registered_at, last_seen_at)
            VALUES ('worker-1', 'Worker 1', ?, ?, ?)
            """,
            (worker_wallet["address"], utc_now(), utc_now()),
        )
        connection.execute(
            """
            INSERT INTO pool_tasks (
                pool_task_id, mainnet_task_id, status, range_start, range_end,
                algorithm, raw_task_json, raw_reveal_json, created_at, completed_at
            )
            VALUES (
                'pooltask_won', 'task_won', 'accepted', 1, 1,
                'bbp_hex_v1', '{}', ?, '2026-06-05T00:00:00+00:00', '2026-06-05T00:01:00+00:00'
            )
            """,
            ('{"block":{"height":77,"block_hash":"abc123","reward":1.5},"status":"accepted"}',),
        )
        connection.execute(
            """
            INSERT INTO pool_tasks (
                pool_task_id, mainnet_task_id, status, range_start, range_end,
                algorithm, raw_task_json, error, created_at, completed_at
            )
            VALUES (
                'pooltask_lost', 'task_lost', 'lost', 1, 1,
                'bbp_hex_v1', '{}', 'commit rejected: competitive round won by task_other at block 78',
                '2026-06-05T00:02:00+00:00', '2026-06-05T00:03:00+00:00'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO pool_chunks (chunk_id, pool_task_id, worker_id, status, range_start, range_end, units, assigned_at, submitted_at)
            VALUES ('chunk_1', 'pooltask_won', 'worker-1', 'completed', 1, 1, 1, ?, ?)
            """,
            (utc_now(), utc_now()),
        )
        connection.execute(
            """
            INSERT INTO pool_shares (share_id, worker_id, pool_task_id, chunk_id, units, credited, created_at)
            VALUES ('share_1', 'worker-1', 'pooltask_won', 'chunk_1', 1, 1, ?)
            """,
            (utc_now(),),
        )

    coordinator = PoolCoordinator(
        db=db,
        server_url="https://api.picoin.science",
        identity={"miner_id": "miner_pool"},
        chunk_size=1,
        poll_seconds=1,
        chunk_timeout_seconds=30,
        verify_chunks=False,
        require_worker_payout=False,
        pool_fee_percent=1,
    )

    stats = coordinator.stats()

    assert stats["active_workers"] == 1
    assert stats["won_blocks"][0]["height"] == 77
    assert stats["won_blocks"][0]["reward"] == pytest.approx(1.5)
    assert stats["performance"]["blocks_won"] == 1
    assert stats["performance"]["lost_rounds"] == 1
    assert stats["performance"]["win_rate_percent"] == pytest.approx(50.0)
    assert stats["performance"]["active_tasks"] == 0
    assert stats["performance"]["completed_tasks"] == 2


def test_validation_pending_reveal_stays_pending_without_final_block(tmp_path, monkeypatch):
    db = PoolDatabase(tmp_path / "pool.sqlite3")
    task = {
        "task_id": "task_pending",
        "range_start": 1,
        "range_end": 3,
        "algorithm": "bbp_hex_v1",
    }
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO pool_workers (worker_id, name, payout_address, registered_at, last_seen_at)
            VALUES ('worker-1', 'Worker 1', NULL, ?, ?)
            """,
            (utc_now(), utc_now()),
        )
        connection.execute(
            """
            INSERT INTO pool_tasks (
                pool_task_id, mainnet_task_id, status, range_start, range_end,
                algorithm, raw_task_json, created_at
            )
            VALUES ('pooltask_pending', 'task_pending', 'gathering', 1, 3, 'bbp_hex_v1', ?, ?)
            """,
            (json.dumps(task), utc_now()),
        )
        connection.executemany(
            """
            INSERT INTO pool_chunks (
                chunk_id, pool_task_id, worker_id, status, range_start, range_end,
                segment, units, compute_ms, assigned_at, submitted_at
            )
            VALUES (?, 'pooltask_pending', 'worker-1', 'completed', ?, ?, ?, 1, 100, ?, ?)
            """,
            [
                ("chunk_1", 1, 1, "1", utc_now(), utc_now()),
                ("chunk_2", 2, 2, "2", utc_now(), utc_now()),
                ("chunk_3", 3, 3, "3", utc_now(), utc_now()),
            ],
        )
        connection.execute(
            """
            INSERT INTO pool_shares (share_id, worker_id, pool_task_id, chunk_id, units, credited, created_at)
            VALUES ('share_1', 'worker-1', 'pooltask_pending', 'chunk_1', 1, 0, ?)
            """,
            (utc_now(),),
        )

    coordinator = PoolCoordinator(
        db=db,
        server_url="https://api.picoin.science",
        identity={"miner_id": "miner_pool"},
        chunk_size=1,
        poll_seconds=1,
        chunk_timeout_seconds=30,
        verify_chunks=False,
        require_worker_payout=False,
        pool_fee_percent=0,
    )

    monkeypatch.setattr("pool_server.commit_result", lambda *_: {"accepted": True, "challenge": "ok"})
    monkeypatch.setattr(
        "pool_server.reveal_samples",
        lambda *_: {
            "accepted": True,
            "status": "validation_pending",
            "block": None,
            "validation": {"job_id": "job_1"},
        },
    )

    with db.connect() as connection:
        pool_task = dict(connection.execute("SELECT * FROM pool_tasks WHERE pool_task_id = 'pooltask_pending'").fetchone())

    coordinator.finalize_task(pool_task)

    with db.connect() as connection:
        row = connection.execute(
            "SELECT status, raw_reveal_json FROM pool_tasks WHERE pool_task_id = 'pooltask_pending'"
        ).fetchone()
        share = connection.execute("SELECT credited FROM pool_shares WHERE share_id = 'share_1'").fetchone()

    reveal = json.loads(row["raw_reveal_json"])
    assert row["status"] == "validation_pending"
    assert reveal["status"] == "validation_pending"
    assert reveal["block"] is None
    assert share["credited"] == 1


def test_reconcile_won_blocks_settles_validation_pending_task(tmp_path, monkeypatch):
    worker_wallet = create_wallet("worker")
    db = PoolDatabase(tmp_path / "pool.sqlite3")
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO pool_workers (worker_id, name, payout_address, registered_at, last_seen_at)
            VALUES ('worker-1', 'Worker 1', ?, ?, ?)
            """,
            (worker_wallet["address"], utc_now(), utc_now()),
        )
        connection.execute(
            """
            INSERT INTO pool_tasks (
                pool_task_id, mainnet_task_id, status, range_start, range_end,
                algorithm, raw_task_json, raw_reveal_json, created_at, completed_at
            )
            VALUES (
                'pooltask_pending', 'task_pending', 'validation_pending', 1, 1,
                'bbp_hex_v1', '{}', ?, '2026-06-05T00:00:00+00:00', '2026-06-05T00:01:00+00:00'
            )
            """,
            ('{"accepted":true,"status":"validation_pending","block":null}',),
        )
        connection.execute(
            """
            INSERT INTO pool_shares (share_id, worker_id, pool_task_id, chunk_id, units, credited, created_at)
            VALUES ('share_1', 'worker-1', 'pooltask_pending', 'chunk_1', 1, 1, ?)
            """,
            ("2026-06-05T00:00:30+00:00",),
        )

    coordinator = PoolCoordinator(
        db=db,
        server_url="https://api.picoin.science",
        identity={"miner_id": "miner_pool"},
        chunk_size=1,
        poll_seconds=1,
        chunk_timeout_seconds=30,
        verify_chunks=False,
        require_worker_payout=False,
        pool_fee_percent=0,
        settlement_block_limit=100,
    )
    monkeypatch.setattr(
        coordinator,
        "_fetch_recent_mainnet_blocks",
        lambda: [
            {
                "height": 77,
                "hash": "abc123",
                "task_id": "task_pending",
                "miner_id": "miner_pool",
                "reward": 1.5,
            }
        ],
    )

    result = coordinator.reconcile_won_blocks()

    with db.connect() as connection:
        row = connection.execute(
            "SELECT status, raw_reveal_json FROM pool_tasks WHERE pool_task_id = 'pooltask_pending'"
        ).fetchone()

    reveal = json.loads(row["raw_reveal_json"])
    stats = coordinator.stats()

    assert result["settled"] == 1
    assert row["status"] == "accepted"
    assert reveal["status"] == "accepted"
    assert reveal["block"]["block_hash"] == "abc123"
    assert stats["won_blocks"][0]["height"] == 77
    assert stats["payouts"]["pending_total"] == pytest.approx(1.5)


def test_reconcile_won_blocks_marks_pending_task_lost_when_round_has_other_winner(tmp_path, monkeypatch):
    db = PoolDatabase(tmp_path / "pool.sqlite3")
    raw_task = {
        "task_id": "task_pending",
        "range_start": 1,
        "range_end": 1,
        "algorithm": "bbp_hex_v1",
        "competitive_round_height": 77,
    }
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO pool_tasks (
                pool_task_id, mainnet_task_id, status, range_start, range_end,
                algorithm, raw_task_json, raw_reveal_json, created_at, completed_at
            )
            VALUES (
                'pooltask_pending', 'task_pending', 'validation_pending', 1, 1,
                'bbp_hex_v1', ?, ?, '2026-06-05T00:00:00+00:00', '2026-06-05T00:01:00+00:00'
            )
            """,
            (
                json.dumps(raw_task),
                '{"accepted":true,"status":"validation_pending","block":null}',
            ),
        )

    coordinator = PoolCoordinator(
        db=db,
        server_url="https://api.picoin.science",
        identity={"miner_id": "miner_pool"},
        chunk_size=1,
        poll_seconds=1,
        chunk_timeout_seconds=30,
        verify_chunks=False,
        require_worker_payout=False,
        pool_fee_percent=0,
        settlement_block_limit=100,
    )
    monkeypatch.setattr(
        coordinator,
        "_fetch_recent_mainnet_blocks",
        lambda: [
            {
                "height": 77,
                "block_hash": "winner_hash",
                "task_id": "task_other",
                "miner_id": "miner_other",
                "reward": 1.5,
            }
        ],
    )

    result = coordinator.reconcile_won_blocks()

    with db.connect() as connection:
        row = connection.execute(
            "SELECT status, error, raw_reveal_json FROM pool_tasks WHERE pool_task_id = 'pooltask_pending'"
        ).fetchone()

    reveal = json.loads(row["raw_reveal_json"])
    assert result["settled"] == 0
    assert result["lost"] == 1
    assert row["status"] == "lost"
    assert row["error"] == "competitive round won by task_other at block 77"
    assert reveal["accepted"] is False
    assert reveal["status"] == "lost"
    assert reveal["block"] is None


def test_new_pool_task_does_not_close_validation_pending_tasks(tmp_path):
    db = PoolDatabase(tmp_path / "pool.sqlite3")
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO pool_tasks (
                pool_task_id, mainnet_task_id, status, range_start, range_end,
                algorithm, raw_task_json, raw_reveal_json, created_at, completed_at
            )
            VALUES (
                'pooltask_old', 'task_old', 'validation_pending', 1, 1,
                'bbp_hex_v1', '{}', ?, '2026-06-05T00:00:00+00:00', '2026-06-05T00:01:00+00:00'
            )
            """,
            ('{"accepted":true,"status":"validation_pending","block":null}',),
        )
        connection.execute(
            """
            INSERT INTO pool_tasks (
                pool_task_id, mainnet_task_id, status, range_start, range_end,
                algorithm, raw_task_json, raw_reveal_json, created_at, completed_at
            )
            VALUES (
                'pooltask_old_accepted', 'task_old_accepted', 'accepted', 1, 1,
                'bbp_hex_v1', '{}', ?, '2026-06-05T00:00:30+00:00', '2026-06-05T00:01:30+00:00'
            )
            """,
            ('{"accepted":true,"status":"validation_pending","block":null}',),
        )
        connection.execute(
            """
            INSERT INTO pool_tasks (
                pool_task_id, mainnet_task_id, status, range_start, range_end,
                algorithm, raw_task_json, created_at
            )
            VALUES (
                'pooltask_new', 'task_new', 'gathering', 2, 2,
                'bbp_hex_v1', '{}', '2026-06-05T00:02:00+00:00'
            )
            """
        )

    coordinator = PoolCoordinator(
        db=db,
        server_url="https://api.picoin.science",
        identity={"miner_id": "miner_pool"},
        chunk_size=1,
        poll_seconds=1,
        chunk_timeout_seconds=30,
        verify_chunks=False,
        require_worker_payout=False,
        pool_fee_percent=0,
    )

    coordinator.ensure_active_task()

    with db.connect() as connection:
        row = connection.execute(
            "SELECT status, error, raw_reveal_json FROM pool_tasks WHERE pool_task_id = 'pooltask_old'"
        ).fetchone()
        old_accepted = connection.execute(
            "SELECT status, error, raw_reveal_json FROM pool_tasks WHERE pool_task_id = 'pooltask_old_accepted'"
        ).fetchone()

    reveal = json.loads(row["raw_reveal_json"])
    old_accepted_reveal = json.loads(old_accepted["raw_reveal_json"])
    assert row["status"] == "validation_pending"
    assert row["error"] is None
    assert reveal["accepted"] is True
    assert reveal["status"] == "validation_pending"
    assert reveal["block"] is None
    assert old_accepted["status"] == "accepted"
    assert old_accepted["error"] is None
    assert old_accepted_reveal["accepted"] is True
    assert old_accepted_reveal["status"] == "validation_pending"
    assert old_accepted_reveal["block"] is None


def test_stats_separates_unsettled_from_active_validation_pending(tmp_path):
    db = PoolDatabase(tmp_path / "pool.sqlite3")
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO pool_tasks (
                pool_task_id, mainnet_task_id, status, range_start, range_end,
                algorithm, raw_task_json, raw_reveal_json, created_at, completed_at
            )
            VALUES (
                'pooltask_unsettled', 'task_unsettled', 'unsettled', 1, 1,
                'bbp_hex_v1', ?, ?, '2026-06-05T00:00:30+00:00', '2026-06-05T00:01:30+00:00'
            )
            """,
            ("{}", '{"accepted":true,"status":"validation_pending","block":null}'),
        )
        connection.execute(
            """
            INSERT INTO pool_tasks (
                pool_task_id, mainnet_task_id, status, range_start, range_end,
                algorithm, raw_task_json, raw_reveal_json, created_at, completed_at
            )
            VALUES (
                'pooltask_pending', 'task_pending', 'validation_pending', 1, 1,
                'bbp_hex_v1', ?, ?, '2026-06-05T00:02:30+00:00', '2026-06-05T00:03:30+00:00'
            )
            """,
            ("{}", '{"accepted":true,"status":"validation_pending","block":null}'),
        )

    coordinator = PoolCoordinator(
        db=db,
        server_url="https://api.picoin.science",
        identity={"miner_id": "miner_pool"},
        chunk_size=1,
        poll_seconds=1,
        chunk_timeout_seconds=30,
        verify_chunks=False,
        require_worker_payout=False,
        pool_fee_percent=0,
    )

    stats = coordinator.stats()

    assert stats["tasks"] == [
        {"status": "unsettled", "count": 1},
        {"status": "validation_pending", "count": 1},
    ]
    assert stats["performance"]["validation_pending_tasks"] == 1
    assert stats["performance"]["unsettled_tasks"] == 1
    assert stats["performance"]["active_tasks"] == 0
    assert stats["performance"]["completed_tasks"] == 1


def test_reconcile_mainnet_task_statuses_uses_task_status_endpoint(tmp_path, monkeypatch):
    db = PoolDatabase(tmp_path / "pool.sqlite3")
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO pool_tasks (
                pool_task_id, mainnet_task_id, status, range_start, range_end,
                algorithm, raw_task_json, raw_reveal_json, created_at, completed_at
            )
            VALUES (
                'pooltask_pending', 'task_pending', 'validation_pending', 1, 1,
                'bbp_hex_v1', '{}', ?, '2026-06-05T00:00:00+00:00', '2026-06-05T00:01:00+00:00'
            )
            """,
            ('{"accepted":true,"status":"validation_pending","block":null}',),
        )
        connection.execute(
            """
            INSERT INTO pool_tasks (
                pool_task_id, mainnet_task_id, status, range_start, range_end,
                algorithm, raw_task_json, raw_reveal_json, created_at, completed_at
            )
            VALUES (
                'pooltask_winner', 'task_winner', 'validation_pending', 2, 2,
                'bbp_hex_v1', '{}', ?, '2026-06-05T00:02:00+00:00', '2026-06-05T00:03:00+00:00'
            )
            """,
            ('{"accepted":true,"status":"validation_pending","block":null}',),
        )
        connection.execute(
            """
            INSERT INTO pool_tasks (
                pool_task_id, mainnet_task_id, status, range_start, range_end,
                algorithm, raw_task_json, raw_reveal_json, created_at, completed_at
            )
            VALUES (
                'pooltask_unsettled', 'task_unsettled', 'unsettled', 3, 3,
                'bbp_hex_v1', '{}', ?, '2026-06-05T00:04:00+00:00', '2026-06-05T00:05:00+00:00'
            )
            """,
            ('{"accepted":true,"status":"validation_pending","block":null}',),
        )

    coordinator = PoolCoordinator(
        db=db,
        server_url="https://api.picoin.science",
        identity={"miner_id": "miner_pool"},
        chunk_size=1,
        poll_seconds=1,
        chunk_timeout_seconds=30,
        verify_chunks=False,
        require_worker_payout=False,
        pool_fee_percent=0,
    )

    def fake_status(task_id):
        if task_id == "task_winner":
            return {
                "status": "accepted",
                "task_status": "accepted",
                "message": "block accepted",
                "block": {"height": 88, "block_hash": "winner", "reward": 1.25},
                "validation": {"status": "approved"},
            }
        if task_id == "task_unsettled":
            return {
                "status": "expired",
                "task_status": "expired",
                "message": "task expired on mainnet",
                "block": None,
                "validation": None,
            }
        return {
            "status": "stale",
            "task_status": "stale",
            "message": "competitive round won by task_other at block 88",
            "block": None,
            "validation": {"status": "rejected"},
        }

    monkeypatch.setattr(coordinator, "_fetch_mainnet_task_status", fake_status)

    result = coordinator.reconcile_mainnet_task_statuses()

    with db.connect() as connection:
        pending = connection.execute(
            "SELECT status, error, raw_reveal_json FROM pool_tasks WHERE pool_task_id = 'pooltask_pending'"
        ).fetchone()
        winner = connection.execute(
            "SELECT status, error, raw_reveal_json FROM pool_tasks WHERE pool_task_id = 'pooltask_winner'"
        ).fetchone()
        unsettled = connection.execute(
            "SELECT status, error, raw_reveal_json FROM pool_tasks WHERE pool_task_id = 'pooltask_unsettled'"
        ).fetchone()

    pending_reveal = json.loads(pending["raw_reveal_json"])
    winner_reveal = json.loads(winner["raw_reveal_json"])
    unsettled_reveal = json.loads(unsettled["raw_reveal_json"])
    stats = coordinator.stats()

    assert result["checked"] == 3
    assert result["updated"] == 3
    assert pending["status"] == "stale"
    assert pending["error"] == "competitive round won by task_other at block 88"
    assert pending_reveal["status"] == "stale"
    assert pending_reveal["validation"]["status"] == "rejected"
    assert winner["status"] == "accepted"
    assert winner["error"] is None
    assert winner_reveal["status"] == "accepted"
    assert winner_reveal["block"]["height"] == 88
    assert unsettled["status"] == "expired"
    assert unsettled["error"] == "task expired on mainnet"
    assert unsettled_reveal["status"] == "expired"
    assert stats["performance"]["blocks_won"] == 1
    assert stats["performance"]["non_winning_rounds"] == 2
    assert stats["performance"]["completed_tasks"] == 3
    assert stats["active_tasks"] == 0
    assert stats["completed_tasks"] == 3


def test_auto_chunk_size_uses_one_unit_chunks_for_small_tasks_with_many_workers(tmp_path, monkeypatch):
    db = PoolDatabase(tmp_path / "pool.sqlite3")
    coordinator = PoolCoordinator(
        db=db,
        server_url="https://api.picoin.science",
        identity={"miner_id": "miner_pool"},
        chunk_size="auto",
        poll_seconds=1,
        chunk_timeout_seconds=30,
        verify_chunks=False,
        require_worker_payout=False,
        pool_fee_percent=0,
    )
    for index in range(10):
        coordinator.register_worker(f"worker-{index}", f"Worker {index}", None)

    monkeypatch.setattr(
        "pool_server.get_task_for_identity",
        lambda *_: {
            "status": "assigned",
            "task_id": "task_auto",
            "range_start": 10,
            "range_end": 19,
            "algorithm": "bbp_hex_v1",
        },
    )

    coordinator.ensure_active_task()

    with db.connect() as connection:
        chunks = connection.execute(
            """
            SELECT range_start, range_end, units
            FROM pool_chunks
            ORDER BY range_start
            """
        ).fetchall()
        event = connection.execute(
            """
            SELECT payload_json
            FROM pool_events
            WHERE message = 'pool task created'
            ORDER BY event_id DESC
            LIMIT 1
            """
        ).fetchone()

    assert [(row["range_start"], row["range_end"], row["units"]) for row in chunks] == [
        (10, 10, 1),
        (11, 11, 1),
        (12, 12, 1),
        (13, 13, 1),
        (14, 14, 1),
        (15, 15, 1),
        (16, 16, 1),
        (17, 17, 1),
        (18, 18, 1),
        (19, 19, 1),
    ]
    assert event is not None
    assert '"chunk_mode":"auto"' in event["payload_json"]
    assert '"active_workers":10' in event["payload_json"]
    assert '"chunk_strategy":"one_unit_speculative_queue"' in event["payload_json"]
    assert '"target_chunks":10' in event["payload_json"]


def test_auto_chunk_size_creates_one_chunk_per_unit_regardless_worker_count(tmp_path, monkeypatch):
    db = PoolDatabase(tmp_path / "pool.sqlite3")
    coordinator = PoolCoordinator(
        db=db,
        server_url="https://api.picoin.science",
        identity={"miner_id": "miner_pool"},
        chunk_size="auto",
        poll_seconds=1,
        chunk_timeout_seconds=30,
        verify_chunks=False,
        require_worker_payout=False,
        pool_fee_percent=0,
    )
    for index in range(3):
        coordinator.register_worker(f"worker-{index}", f"Worker {index}", None)

    monkeypatch.setattr(
        "pool_server.get_task_for_identity",
        lambda *_: {
            "status": "assigned",
            "task_id": "task_auto",
            "range_start": 100,
            "range_end": 199,
            "algorithm": "bbp_hex_v1",
        },
    )

    coordinator.ensure_active_task()

    with db.connect() as connection:
        chunks = connection.execute(
            """
            SELECT range_start, range_end, units
            FROM pool_chunks
            ORDER BY range_start
            """
        ).fetchall()

    assert len(chunks) == 100
    assert max(row["units"] for row in chunks) == 1
    assert min(row["units"] for row in chunks) == 1
    assert chunks[0]["range_start"] == 100
    assert chunks[-1]["range_end"] == 199


def test_hybrid_race_uses_full_task_when_units_exceed_workers(tmp_path, monkeypatch):
    db = PoolDatabase(tmp_path / "pool.sqlite3")
    coordinator = PoolCoordinator(
        db=db,
        server_url="https://api.picoin.science",
        identity={"miner_id": "miner_pool"},
        chunk_size="hybrid-race",
        poll_seconds=1,
        chunk_timeout_seconds=30,
        verify_chunks=False,
        require_worker_payout=False,
        pool_fee_percent=0,
    )
    for index in range(3):
        coordinator.register_worker(f"worker-{index}", f"Worker {index}", None)

    monkeypatch.setattr(
        "pool_server.get_task_for_identity",
        lambda *_: {
            "status": "assigned",
            "task_id": "task_hybrid_full",
            "range_start": 100,
            "range_end": 109,
            "algorithm": "bbp_hex_v1",
        },
    )

    coordinator.ensure_active_task()

    with db.connect() as connection:
        chunks = connection.execute(
            """
            SELECT range_start, range_end, units
            FROM pool_chunks
            ORDER BY range_start
            """
        ).fetchall()
        event = connection.execute(
            """
            SELECT payload_json
            FROM pool_events
            WHERE message = 'pool task created'
            ORDER BY event_id DESC
            LIMIT 1
            """
        ).fetchone()

    assert [(row["range_start"], row["range_end"], row["units"]) for row in chunks] == [(100, 109, 10)]
    assert event is not None
    assert '"chunk_mode":"hybrid-race"' in event["payload_json"]
    assert '"chunk_strategy":"full_task_race"' in event["payload_json"]
    assert '"active_workers":3' in event["payload_json"]
    assert '"target_chunks":1' in event["payload_json"]


def test_hybrid_race_uses_one_unit_chunks_when_workers_cover_units(tmp_path, monkeypatch):
    db = PoolDatabase(tmp_path / "pool.sqlite3")
    coordinator = PoolCoordinator(
        db=db,
        server_url="https://api.picoin.science",
        identity={"miner_id": "miner_pool"},
        chunk_size="hybrid-race",
        poll_seconds=1,
        chunk_timeout_seconds=30,
        verify_chunks=False,
        require_worker_payout=False,
        pool_fee_percent=0,
    )
    for index in range(10):
        coordinator.register_worker(f"worker-{index}", f"Worker {index}", None)

    monkeypatch.setattr(
        "pool_server.get_task_for_identity",
        lambda *_: {
            "status": "assigned",
            "task_id": "task_hybrid_units",
            "range_start": 200,
            "range_end": 209,
            "algorithm": "bbp_hex_v1",
        },
    )

    coordinator.ensure_active_task()

    with db.connect() as connection:
        chunks = connection.execute(
            """
            SELECT range_start, range_end, units
            FROM pool_chunks
            ORDER BY range_start
            """
        ).fetchall()
        event = connection.execute(
            """
            SELECT payload_json
            FROM pool_events
            WHERE message = 'pool task created'
            ORDER BY event_id DESC
            LIMIT 1
            """
        ).fetchone()

    assert len(chunks) == 10
    assert [(row["range_start"], row["range_end"], row["units"]) for row in chunks] == [
        (position, position, 1) for position in range(200, 210)
    ]
    assert event is not None
    assert '"chunk_mode":"hybrid-race"' in event["payload_json"]
    assert '"chunk_strategy":"one_unit_speculative_queue"' in event["payload_json"]
    assert '"active_workers":10' in event["payload_json"]
    assert '"target_chunks":10' in event["payload_json"]


def test_submit_last_chunk_schedules_immediate_finalize(tmp_path, monkeypatch):
    db = PoolDatabase(tmp_path / "pool.sqlite3")
    coordinator = PoolCoordinator(
        db=db,
        server_url="https://api.picoin.science",
        identity={"miner_id": "miner_pool"},
        chunk_size=1,
        poll_seconds=15,
        chunk_timeout_seconds=30,
        verify_chunks=False,
        require_worker_payout=False,
        pool_fee_percent=0,
    )
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO pool_workers (worker_id, name, payout_address, registered_at, last_seen_at)
            VALUES ('worker-1', 'Worker 1', NULL, ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO pool_tasks (
                pool_task_id, mainnet_task_id, status, range_start, range_end,
                algorithm, raw_task_json, created_at
            )
            VALUES ('pooltask_ready', 'task_ready', 'gathering', 1, 2, 'bbp_hex_v1', '{}', ?)
            """,
            (now,),
        )
        connection.execute(
            """
            INSERT INTO pool_chunks (
                chunk_id, pool_task_id, worker_id, status, range_start, range_end,
                segment, units, compute_ms, assigned_at, submitted_at
            )
            VALUES ('chunk_done', 'pooltask_ready', 'worker-1', 'completed', 1, 1, '1', 1, 100, ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO pool_chunks (
                chunk_id, pool_task_id, worker_id, status, range_start, range_end,
                units, assigned_at
            )
            VALUES ('chunk_last', 'pooltask_ready', 'worker-1', 'assigned', 2, 2, 1, ?)
            """,
            (now,),
        )

    scheduled: list[str] = []
    monkeypatch.setattr(coordinator, "_schedule_finalize_task", scheduled.append)

    result = coordinator.submit_work("worker-1", "chunk_last", "2", 25)

    assert result["status"] == "accepted"
    assert scheduled == ["pooltask_ready"]


def test_speculative_chunk_assignment_first_valid_submit_wins(tmp_path):
    db = PoolDatabase(tmp_path / "pool.sqlite3")
    coordinator = PoolCoordinator(
        db=db,
        server_url="https://api.picoin.science",
        identity={"miner_id": "miner_pool"},
        chunk_size=1,
        poll_seconds=1,
        chunk_timeout_seconds=30,
        verify_chunks=False,
        require_worker_payout=False,
        pool_fee_percent=0,
    )
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO pool_workers (worker_id, name, payout_address, registered_at, last_seen_at)
            VALUES ('slow-worker', 'Slow', NULL, ?, ?),
                   ('fast-worker', 'Fast', NULL, ?, ?)
            """,
            (now, now, now, now),
        )
        connection.execute(
            """
            INSERT INTO pool_tasks (
                pool_task_id, mainnet_task_id, status, range_start, range_end,
                algorithm, raw_task_json, created_at
            )
            VALUES ('pooltask_race', 'task_race', 'gathering', 7, 7, 'bbp_hex_v1', '{}', ?)
            """,
            (now,),
        )
        connection.execute(
            """
            INSERT INTO pool_chunks (chunk_id, pool_task_id, status, range_start, range_end, units)
            VALUES ('chunk_race', 'pooltask_race', 'pending', 7, 7, 1)
            """
        )

    slow_work = coordinator.claim_work("slow-worker")
    fast_work = coordinator.claim_work("fast-worker")

    assert slow_work["status"] == "work"
    assert slow_work["assignment_mode"] == "primary"
    assert fast_work["status"] == "work"
    assert fast_work["assignment_mode"] == "speculative"
    assert fast_work["chunk_id"] == slow_work["chunk_id"]

    fast_result = coordinator.submit_work("fast-worker", "chunk_race", "A", 10)
    slow_result = coordinator.submit_work("slow-worker", "chunk_race", "A", 100)

    assert fast_result["status"] == "accepted"
    assert slow_result["status"] == "stale"
    with db.connect() as connection:
        chunk = connection.execute(
            "SELECT status, worker_id FROM pool_chunks WHERE chunk_id = 'chunk_race'"
        ).fetchone()
        shares = connection.execute(
            "SELECT worker_id, units FROM pool_shares WHERE chunk_id = 'chunk_race'"
        ).fetchall()
        assignment_events = connection.execute(
            """
            SELECT payload_json
            FROM pool_events
            WHERE message = 'pool chunk assigned'
            ORDER BY event_id
            """
        ).fetchall()

    assert dict(chunk) == {"status": "completed", "worker_id": "fast-worker"}
    assert [(row["worker_id"], row["units"]) for row in shares] == [("fast-worker", 1)]
    assignment_payloads = [json.loads(row["payload_json"]) for row in assignment_events]
    assert [
        (payload["assignment_mode"], payload["worker_id"], payload["previous_worker_id"])
        for payload in assignment_payloads
    ] == [
        ("primary", "slow-worker", None),
        ("speculative", "fast-worker", "slow-worker"),
    ]


def test_hybrid_full_task_race_late_submit_gets_time_weighted_shares(tmp_path, monkeypatch):
    db = PoolDatabase(tmp_path / "pool.sqlite3")
    coordinator = PoolCoordinator(
        db=db,
        server_url="https://api.picoin.science",
        identity={"miner_id": "miner_pool"},
        chunk_size="hybrid-race",
        poll_seconds=1,
        chunk_timeout_seconds=30,
        verify_chunks=False,
        require_worker_payout=False,
        pool_fee_percent=0,
    )
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO pool_workers (worker_id, name, payout_address, registered_at, last_seen_at)
            VALUES ('winner-worker', 'Winner', NULL, ?, ?),
                   ('late-worker', 'Late', NULL, ?, ?)
            """,
            (now, now, now, now),
        )
        connection.execute(
            """
            INSERT INTO pool_tasks (
                pool_task_id, mainnet_task_id, status, range_start, range_end,
                algorithm, raw_task_json, created_at
            )
            VALUES ('pooltask_full_race', 'task_full_race', 'gathering', 50, 59, 'bbp_hex_v1', '{}', ?)
            """,
            (now,),
        )
        connection.execute(
            """
            INSERT INTO pool_chunks (chunk_id, pool_task_id, status, range_start, range_end, units)
            VALUES ('chunk_full_race', 'pooltask_full_race', 'pending', 50, 59, 10)
            """
        )

    scheduled: list[str] = []
    monkeypatch.setattr(coordinator, "_schedule_finalize_task", scheduled.append)

    winner_work = coordinator.claim_work("winner-worker")
    late_work = coordinator.claim_work("late-worker")

    assert winner_work["assignment_mode"] == "primary"
    assert late_work["assignment_mode"] == "speculative"
    assert late_work["chunk_id"] == winner_work["chunk_id"]

    winner_result = coordinator.submit_work("winner-worker", "chunk_full_race", "1234567890", 1000)
    late_result = coordinator.submit_work("late-worker", "chunk_full_race", "1234567890", 2500)
    duplicate_result = coordinator.submit_work("late-worker", "chunk_full_race", "1234567890", 2500)

    assert winner_result["status"] == "accepted"
    assert winner_result["units"] == 10
    assert late_result["status"] == "accepted"
    assert late_result["units"] == 4
    assert duplicate_result["status"] == "stale"
    assert scheduled == ["pooltask_full_race"]
    with db.connect() as connection:
        shares = connection.execute(
            """
            SELECT worker_id, units
            FROM pool_shares
            WHERE chunk_id = 'chunk_full_race'
            ORDER BY created_at
            """
        ).fetchall()
        event = connection.execute(
            """
            SELECT payload_json
            FROM pool_events
            WHERE message = 'pool full task race share credited'
            ORDER BY event_id DESC
            LIMIT 1
            """
        ).fetchone()

    assert [(row["worker_id"], row["units"]) for row in shares] == [
        ("winner-worker", 10),
        ("late-worker", 4),
    ]
    assert event is not None
    payload = json.loads(event["payload_json"])
    assert payload["credit_units"] == 4
    assert payload["task_units"] == 10
    assert payload["winner_compute_ms"] == 1000
    assert payload["worker_compute_ms"] == 2500


def test_idle_worker_claim_updates_last_seen_for_auto_chunking(tmp_path):
    db = PoolDatabase(tmp_path / "pool.sqlite3")
    coordinator = PoolCoordinator(
        db=db,
        server_url="https://api.picoin.science",
        identity={"miner_id": "miner_pool"},
        chunk_size="auto",
        poll_seconds=1,
        chunk_timeout_seconds=30,
        verify_chunks=False,
        require_worker_payout=False,
        pool_fee_percent=0,
    )

    coordinator.register_worker("worker-1", "Worker 1", None)
    with db.connect() as connection:
        connection.execute(
            "UPDATE pool_workers SET last_seen_at = '2026-01-01T00:00:00+00:00' WHERE worker_id = 'worker-1'"
        )

    result = coordinator.claim_work("worker-1")

    assert result["status"] == "idle"
    with db.connect() as connection:
        worker = connection.execute(
            "SELECT last_seen_at FROM pool_workers WHERE worker_id = 'worker-1'"
        ).fetchone()
        assert coordinator._active_worker_count(connection) == 1

    assert parse_iso_timestamp(worker["last_seen_at"]) > parse_iso_timestamp("2026-01-01T00:00:00+00:00")


def test_stats_reports_pool_hashrate_from_recent_worker_chunks(tmp_path):
    db = PoolDatabase(tmp_path / "pool.sqlite3")
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO pool_workers (worker_id, name, payout_address, registered_at, last_seen_at)
            VALUES ('worker-1', 'Worker 1', NULL, ?, ?),
                   ('worker-2', 'Worker 2', NULL, ?, ?)
            """,
            (now, now, now, now),
        )
        connection.execute(
            """
            INSERT INTO pool_tasks (
                pool_task_id, mainnet_task_id, status, range_start, range_end,
                algorithm, raw_task_json, created_at
            )
            VALUES ('pooltask_rate', 'task_rate', 'gathering', 1, 3, 'bbp_hex_v1', '{}', ?)
            """,
            (now,),
        )
        connection.execute(
            """
            INSERT INTO pool_chunks (
                chunk_id, pool_task_id, worker_id, status, range_start, range_end,
                units, compute_ms, assigned_at, submitted_at
            )
            VALUES ('chunk_1', 'pooltask_rate', 'worker-1', 'completed', 1, 2, 2, 1000, ?, ?),
                   ('chunk_2', 'pooltask_rate', 'worker-2', 'completed', 3, 3, 1, 500, ?, ?)
            """,
            (now, now, now, now),
        )

    coordinator = PoolCoordinator(
        db=db,
        server_url="https://api.picoin.science",
        identity={"miner_id": "miner_pool"},
        chunk_size="auto",
        poll_seconds=1,
        chunk_timeout_seconds=30,
        verify_chunks=False,
        require_worker_payout=False,
        pool_fee_percent=0,
    )

    stats = coordinator.stats()

    assert stats["hashrate"]["pool_hashrate_hps"] == pytest.approx(4.0)
    assert stats["hashrate"]["active_hashrate_workers"] == 2
    assert stats["performance"]["pool_hashrate_hps"] == pytest.approx(4.0)
    assert stats["performance"]["active_hashrate_workers"] == 2


def test_auto_payout_submits_transfer_once_and_subtracts_pending(tmp_path, monkeypatch):
    wallet = create_wallet("pool-payout")
    worker_wallet = create_wallet("worker")
    db = PoolDatabase(tmp_path / "pool.sqlite3")
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO pool_workers (worker_id, name, payout_address, registered_at, last_seen_at)
            VALUES ('worker-1', 'Worker 1', ?, '2026-06-05T00:00:00+00:00', '2026-06-05T00:00:00+00:00')
            """,
            (worker_wallet["address"],),
        )
        connection.execute(
            """
            INSERT INTO pool_tasks (
                pool_task_id, mainnet_task_id, status, range_start, range_end,
                algorithm, raw_task_json, raw_reveal_json, created_at, completed_at
            )
            VALUES (
                'pooltask_1', 'task_1', 'accepted', 1, 1,
                'bbp_hex_v1', '{}', ?, '2026-06-05T00:00:00+00:00', '2026-06-05T00:01:00+00:00'
            )
            """,
            ('{"block":{"reward":1.0}}',),
        )
        connection.execute(
            """
            INSERT INTO pool_shares (share_id, worker_id, pool_task_id, chunk_id, units, credited, created_at)
            VALUES ('share_1', 'worker-1', 'pooltask_1', 'chunk_1', 1, 1, '2026-06-05T00:00:30+00:00')
            """
        )

    coordinator = PoolCoordinator(
        db=db,
        server_url="https://api.picoin.science",
        identity={"miner_id": "miner_pool"},
        chunk_size=1,
        poll_seconds=1,
        chunk_timeout_seconds=30,
        verify_chunks=False,
        require_worker_payout=True,
        pool_fee_percent=1,
        payout_wallet=wallet,
        payout_interval_seconds=7200,
        payout_min_amount=0.1,
    )

    submitted = []
    monkeypatch.setattr(coordinator, "_fetch_wallet_nonce", lambda: 12)

    def fake_submit(tx):
        submitted.append(tx)
        return {"tx_hash": tx["tx_hash"], "status": "pending"}

    monkeypatch.setattr(coordinator, "_submit_payout_transaction", fake_submit)

    result = coordinator.run_payouts()

    assert result["submitted"] == 1
    assert result["errors"] == 0
    assert submitted[0]["tx_type"] == "transfer"
    assert submitted[0]["sender"] == wallet["address"]
    assert submitted[0]["recipient"] == worker_wallet["address"]
    assert submitted[0]["amount"] == "0.990000"
    assert submitted[0]["nonce"] == 12

    stats = coordinator.stats()
    assert stats["payouts"]["paid_total"] == pytest.approx(0.99)
    assert stats["payouts"]["pending_total"] == pytest.approx(0.0)
    assert stats["payouts"]["workers"] == []

    second = coordinator.run_payouts()
    assert second["submitted"] == 0
    assert len(submitted) == 1


def test_auto_payout_submits_multiple_worker_transfers_with_incrementing_nonces(tmp_path, monkeypatch):
    wallet = create_wallet("pool-payout")
    alice_wallet = create_wallet("alice")
    bob_wallet = create_wallet("bob")
    db = PoolDatabase(tmp_path / "pool.sqlite3")
    with db.connect() as connection:
        connection.executemany(
            """
            INSERT INTO pool_workers (worker_id, name, payout_address, registered_at, last_seen_at)
            VALUES (?, ?, ?, '2026-06-05T00:00:00+00:00', '2026-06-05T00:00:00+00:00')
            """,
            [
                ("alice", "Alice", alice_wallet["address"]),
                ("bob", "Bob", bob_wallet["address"]),
            ],
        )
        connection.execute(
            """
            INSERT INTO pool_tasks (
                pool_task_id, mainnet_task_id, status, range_start, range_end,
                algorithm, raw_task_json, raw_reveal_json, created_at, completed_at
            )
            VALUES (
                'pooltask_1', 'task_1', 'accepted', 1, 2,
                'bbp_hex_v1', '{}', ?, '2026-06-05T00:00:00+00:00', '2026-06-05T00:01:00+00:00'
            )
            """,
            ('{"block":{"reward":2.0}}',),
        )
        connection.executemany(
            """
            INSERT INTO pool_shares (share_id, worker_id, pool_task_id, chunk_id, units, credited, created_at)
            VALUES (?, ?, 'pooltask_1', ?, 1, 1, ?)
            """,
            [
                ("share_alice", "alice", "chunk_alice", "2026-06-05T00:00:20+00:00"),
                ("share_bob", "bob", "chunk_bob", "2026-06-05T00:00:30+00:00"),
            ],
        )

    coordinator = PoolCoordinator(
        db=db,
        server_url="https://api.picoin.science",
        identity={"miner_id": "miner_pool"},
        chunk_size=1,
        poll_seconds=1,
        chunk_timeout_seconds=30,
        verify_chunks=False,
        require_worker_payout=True,
        pool_fee_percent=1,
        payout_wallet=wallet,
        payout_interval_seconds=7200,
        payout_min_amount=0.1,
    )

    submitted = []
    monkeypatch.setattr(coordinator, "_fetch_wallet_nonce", lambda: 12)
    monkeypatch.setattr(
        coordinator,
        "_submit_payout_transaction",
        lambda tx: submitted.append(tx) or {"tx_hash": tx["tx_hash"], "status": "pending"},
    )

    result = coordinator.run_payouts()

    assert result["submitted"] == 2
    assert result["errors"] == 0
    assert [tx["nonce"] for tx in submitted] == [12, 13]
    assert {tx["recipient"] for tx in submitted} == {alice_wallet["address"], bob_wallet["address"]}
    assert [tx["amount"] for tx in submitted] == ["0.990000", "0.990000"]
    stats = coordinator.stats()
    assert stats["payouts"]["paid_total"] == pytest.approx(1.98)
    assert stats["payouts"]["pending_total"] == pytest.approx(0.0)


def test_stats_pays_previous_window_and_resets_current_round_shares(tmp_path):
    alice_wallet = create_wallet("alice")
    bob_wallet = create_wallet("bob")
    carol_wallet = create_wallet("carol")
    db = PoolDatabase(tmp_path / "pool.sqlite3")
    with db.connect() as connection:
        connection.executemany(
            """
            INSERT INTO pool_workers (worker_id, name, payout_address, registered_at, last_seen_at)
            VALUES (?, ?, ?, '2026-06-05T00:00:00+00:00', '2026-06-05T00:00:00+00:00')
            """,
            [
                ("alice", "Alice", alice_wallet["address"]),
                ("bob", "Bob", bob_wallet["address"]),
                ("carol", "Carol", carol_wallet["address"]),
            ],
        )
        connection.executemany(
            """
            INSERT INTO pool_tasks (
                pool_task_id, mainnet_task_id, status, range_start, range_end,
                algorithm, raw_task_json, raw_reveal_json, error, created_at, completed_at
            )
            VALUES (?, ?, ?, 1, 1, 'bbp_hex_v1', '{}', ?, ?, ?, ?)
            """,
            [
                (
                    "pooltask_before_win",
                    "task_before_win",
                    "lost",
                    '{"accepted":true,"status":"validation_pending","block":null}',
                    "commit rejected: competitive round won by task_other at block 10",
                    "2026-06-05T00:00:00+00:00",
                    "2026-06-05T00:05:00+00:00",
                ),
                (
                    "pooltask_win",
                    "task_win",
                    "accepted",
                    '{"block":{"height":77,"block_hash":"abc123","reward":2.0},"status":"accepted"}',
                    None,
                    "2026-06-05T00:08:00+00:00",
                    "2026-06-05T00:10:00+00:00",
                ),
                (
                    "pooltask_after_win",
                    "task_after_win",
                    "lost",
                    '{"accepted":true,"status":"validation_pending","block":null}',
                    "commit rejected: competitive round won by task_other at block 11",
                    "2026-06-05T00:11:00+00:00",
                    "2026-06-05T00:12:00+00:00",
                ),
            ],
        )
        connection.executemany(
            """
            INSERT INTO pool_shares (share_id, worker_id, pool_task_id, chunk_id, units, credited, created_at)
            VALUES (?, ?, ?, ?, 1, 1, ?)
            """,
            [
                ("share_alice", "alice", "pooltask_before_win", "chunk_alice", "2026-06-05T00:04:00+00:00"),
                ("share_bob", "bob", "pooltask_win", "chunk_bob", "2026-06-05T00:09:00+00:00"),
                ("share_carol", "carol", "pooltask_after_win", "chunk_carol", "2026-06-05T00:12:00+00:00"),
            ],
        )

    coordinator = PoolCoordinator(
        db=db,
        server_url="https://api.picoin.science",
        identity={"miner_id": "miner_pool"},
        chunk_size=1,
        poll_seconds=1,
        chunk_timeout_seconds=30,
        verify_chunks=False,
        require_worker_payout=True,
        pool_fee_percent=1,
        payout_min_amount=0.1,
    )

    stats = coordinator.stats()
    payout_workers = {worker["worker_id"]: worker for worker in stats["payouts"]["workers"]}

    assert stats["payouts"]["accounting_mode"] == "round_window"
    assert stats["payouts"]["pending_total"] == pytest.approx(1.98)
    assert payout_workers["alice"]["pending_amount"] == pytest.approx(0.99)
    assert payout_workers["bob"]["pending_amount"] == pytest.approx(0.99)
    assert "carol" not in payout_workers
    assert stats["credited_shares"] == stats["current_round_shares"]
    assert stats["current_round_shares"]["carol"]["units"] == 1
    assert set(stats["last_round_shares"]) == {"alice", "bob"}
    assert set(stats["lifetime_shares"]) == {"alice", "bob", "carol"}


def test_reconcile_payout_releases_not_found_submitted_payment(tmp_path, monkeypatch):
    wallet = create_wallet("pool-payout")
    worker_wallet = create_wallet("worker")
    db = PoolDatabase(tmp_path / "pool.sqlite3")
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO pool_workers (worker_id, name, payout_address, registered_at, last_seen_at)
            VALUES ('worker-1', 'Worker 1', ?, '2026-06-05T00:00:00+00:00', '2026-06-05T00:00:00+00:00')
            """,
            (worker_wallet["address"],),
        )
        connection.execute(
            """
            INSERT INTO pool_tasks (
                pool_task_id, mainnet_task_id, status, range_start, range_end,
                algorithm, raw_task_json, raw_reveal_json, created_at, completed_at
            )
            VALUES (
                'pooltask_1', 'task_1', 'accepted', 1, 1,
                'bbp_hex_v1', '{}', ?, '2026-06-05T00:00:00+00:00', '2026-06-05T00:01:00+00:00'
            )
            """,
            ('{"block":{"reward":1.0}}',),
        )
        connection.execute(
            """
            INSERT INTO pool_shares (share_id, worker_id, pool_task_id, chunk_id, units, credited, created_at)
            VALUES ('share_1', 'worker-1', 'pooltask_1', 'chunk_1', 1, 1, '2026-06-05T00:00:30+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO pool_payouts (
                payout_id, worker_id, payout_address, amount, amount_units,
                fee, fee_units, tx_hash, status, raw_tx_json, created_at, updated_at
            )
            VALUES (
                'payout_missing', 'worker-1', ?, 0.99, 990000,
                0, 0, 'missing_tx', 'submitted', '{}',
                '2026-06-05T00:00:00+00:00', '2026-06-05T00:00:00+00:00'
            )
            """,
            (worker_wallet["address"],),
        )

    coordinator = PoolCoordinator(
        db=db,
        server_url="https://api.picoin.science",
        identity={"miner_id": "miner_pool"},
        chunk_size=1,
        poll_seconds=1,
        chunk_timeout_seconds=30,
        verify_chunks=False,
        require_worker_payout=True,
        pool_fee_percent=1,
        payout_wallet=wallet,
        payout_interval_seconds=7200,
        payout_min_amount=0.1,
        payout_confirmation_grace_seconds=1,
    )
    monkeypatch.setattr(coordinator, "_fetch_payout_transaction", lambda _tx_hash: {"detail": "transaction not found"})

    result = coordinator.reconcile_payout_statuses()

    assert result["expired"] == 1
    with db.connect() as connection:
        row = connection.execute("SELECT status, error FROM pool_payouts WHERE payout_id = 'payout_missing'").fetchone()
    assert row["status"] == "error"
    assert "not found" in row["error"]
    stats = coordinator.stats()
    assert stats["payouts"]["paid_total"] == pytest.approx(0.0)
    assert stats["payouts"]["pending_total"] == pytest.approx(0.99)


def test_reconcile_payout_marks_confirmed_payment(tmp_path, monkeypatch):
    wallet = create_wallet("pool-payout")
    worker_wallet = create_wallet("worker")
    db = PoolDatabase(tmp_path / "pool.sqlite3")
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO pool_payouts (
                payout_id, worker_id, payout_address, amount, amount_units,
                fee, fee_units, tx_hash, status, raw_tx_json, created_at, updated_at
            )
            VALUES (
                'payout_confirmed', 'worker-1', ?, 0.99, 990000,
                0, 0, 'confirmed_tx', 'submitted', '{}',
                '2026-06-05T00:00:00+00:00', '2026-06-05T00:00:00+00:00'
            )
            """,
            (worker_wallet["address"],),
        )

    coordinator = PoolCoordinator(
        db=db,
        server_url="https://api.picoin.science",
        identity={"miner_id": "miner_pool"},
        chunk_size=1,
        poll_seconds=1,
        chunk_timeout_seconds=30,
        verify_chunks=False,
        require_worker_payout=True,
        pool_fee_percent=1,
        payout_wallet=wallet,
        payout_interval_seconds=7200,
        payout_min_amount=0.1,
    )
    monkeypatch.setattr(
        coordinator,
        "_fetch_payout_transaction",
        lambda _tx_hash: {"status": "confirmed", "block_height": 123},
    )

    result = coordinator.reconcile_payout_statuses()

    assert result["confirmed"] == 1
    with db.connect() as connection:
        row = connection.execute("SELECT status, raw_response_json FROM pool_payouts WHERE payout_id = 'payout_confirmed'").fetchone()
    assert row["status"] == "confirmed"
    assert json.loads(row["raw_response_json"])["block_height"] == 123


def test_reconcile_payout_releases_empty_transaction_lookup_after_grace(tmp_path, monkeypatch):
    wallet = create_wallet("pool-payout")
    worker_wallet = create_wallet("worker")
    db = PoolDatabase(tmp_path / "pool.sqlite3")
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO pool_payouts (
                payout_id, worker_id, payout_address, amount, amount_units,
                fee, fee_units, tx_hash, status, raw_tx_json, created_at, updated_at
            )
            VALUES (
                'payout_empty', 'worker-1', ?, 0.25, 250000,
                0, 0, 'empty_tx', 'submitted', '{}',
                '2026-06-05T00:00:00+00:00', '2026-06-05T00:00:00+00:00'
            )
            """,
            (worker_wallet["address"],),
        )

    coordinator = PoolCoordinator(
        db=db,
        server_url="https://api.picoin.science",
        identity={"miner_id": "miner_pool"},
        chunk_size=1,
        poll_seconds=1,
        chunk_timeout_seconds=30,
        verify_chunks=False,
        require_worker_payout=True,
        pool_fee_percent=1,
        payout_wallet=wallet,
        payout_interval_seconds=7200,
        payout_min_amount=0.1,
        payout_confirmation_grace_seconds=1,
    )
    monkeypatch.setattr(
        coordinator,
        "_fetch_payout_transaction",
        lambda _tx_hash: {
            "status": None,
            "block_height": None,
            "sender": None,
            "recipient": None,
            "amount": None,
            "fee": None,
        },
    )

    result = coordinator.reconcile_payout_statuses()

    assert result["expired"] == 1
    with db.connect() as connection:
        row = connection.execute("SELECT status, error FROM pool_payouts WHERE payout_id = 'payout_empty'").fetchone()
    assert row["status"] == "error"
    assert "not found" in row["error"]
