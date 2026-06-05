import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pool_server import PoolCoordinator, PoolDatabase, is_lost_competitive_round_error, parse_iso_timestamp, utc_now
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


def test_auto_chunk_size_uses_active_workers(tmp_path, monkeypatch):
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
    for index in range(8):
        coordinator.register_worker(f"worker-{index}", f"Worker {index}", None)

    monkeypatch.setattr(
        "pool_server.get_task_for_identity",
        lambda *_: {
            "status": "assigned",
            "task_id": "task_auto",
            "range_start": 10,
            "range_end": 18,
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
    ]
    assert event is not None
    assert '"chunk_mode":"auto"' in event["payload_json"]
    assert '"active_workers":8' in event["payload_json"]
    assert '"chunk_strategy":"adaptive_work_queue"' in event["payload_json"]


def test_auto_chunk_size_uses_fine_queue_for_mixed_worker_capacity(tmp_path, monkeypatch):
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
            "range_end": 108,
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

    assert len(chunks) == 9
    assert all(row["units"] == 1 for row in chunks)
    assert chunks[0]["range_start"] == 100
    assert chunks[-1]["range_end"] == 108


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
