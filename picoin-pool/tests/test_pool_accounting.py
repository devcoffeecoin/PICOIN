import pytest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pool_accounting import (
    assemble_segment,
    filter_shares_by_window,
    split_range,
    summarize_payouts,
    summarize_round_window_payouts,
    summarize_shares,
)


def test_split_range_uses_inclusive_chunks():
    chunks = split_range(10, 17, 3)

    assert [(chunk.range_start, chunk.range_end, chunk.units) for chunk in chunks] == [
        (10, 12, 3),
        (13, 15, 3),
        (16, 17, 2),
    ]


def test_assemble_segment_orders_chunks_and_validates_coverage():
    segment = assemble_segment(
        10,
        15,
        [
            {"range_start": 13, "range_end": 15, "segment": "DEF"},
            {"range_start": 10, "range_end": 12, "segment": "ABC"},
        ],
    )

    assert segment == "ABCDEF"


def test_assemble_segment_rejects_missing_chunk():
    with pytest.raises(ValueError, match="missing segment data"):
        assemble_segment(
            10,
            15,
            [{"range_start": 10, "range_end": 12, "segment": "ABC"}],
        )


def test_summarize_shares_returns_units_and_fraction():
    shares = summarize_shares(
        [
            {"worker_id": "alice", "units": 3},
            {"worker_id": "bob", "units": 1},
            {"worker_id": "alice", "units": 2},
        ]
    )

    assert shares["alice"]["units"] == 5
    assert shares["alice"]["fraction"] == pytest.approx(5 / 6)
    assert shares["bob"]["units"] == 1
    assert shares["bob"]["fraction"] == pytest.approx(1 / 6)


def test_summarize_payouts_splits_accepted_rewards_by_units():
    payouts = summarize_payouts(
        task_rewards=[{"pool_task_id": "task-1", "reward": 2.5}],
        share_rows=[
            {"pool_task_id": "task-1", "worker_id": "alice", "units": 3},
            {"pool_task_id": "task-1", "worker_id": "bob", "units": 1},
            {"pool_task_id": "pending-task", "worker_id": "alice", "units": 20},
        ],
        worker_rows=[
            {"worker_id": "alice", "name": "Alice", "payout_address": "PIA"},
            {"worker_id": "bob", "name": "Bob", "payout_address": "PIB"},
        ],
        pool_fee_percent=10,
    )

    assert payouts["gross_total"] == pytest.approx(2.5)
    assert payouts["pool_fee_total"] == pytest.approx(0.25)
    assert payouts["pending_total"] == pytest.approx(2.25)

    by_worker = {worker["worker_id"]: worker for worker in payouts["workers"]}
    assert by_worker["alice"]["units"] == 3
    assert by_worker["alice"]["gross_amount"] == pytest.approx(1.875)
    assert by_worker["alice"]["pool_fee_amount"] == pytest.approx(0.1875)
    assert by_worker["alice"]["pending_amount"] == pytest.approx(1.6875)
    assert by_worker["bob"]["pending_amount"] == pytest.approx(0.5625)


def test_summarize_payouts_subtracts_submitted_payments():
    payouts = summarize_payouts(
        task_rewards=[{"pool_task_id": "task-1", "reward": 1.0}],
        share_rows=[{"pool_task_id": "task-1", "worker_id": "alice", "units": 1}],
        worker_rows=[{"worker_id": "alice", "name": "Alice", "payout_address": "PIA"}],
        payout_rows=[{"worker_id": "alice", "payout_address": "PIA", "amount": 0.25}],
        pool_fee_percent=1,
        min_payout_amount=0.1,
    )

    by_worker = {worker["worker_id"]: worker for worker in payouts["workers"]}
    assert payouts["gross_total"] == pytest.approx(1.0)
    assert payouts["pool_fee_total"] == pytest.approx(0.01)
    assert payouts["paid_total"] == pytest.approx(0.25)
    assert payouts["pending_total"] == pytest.approx(0.74)
    assert by_worker["alice"]["paid_amount"] == pytest.approx(0.25)
    assert by_worker["alice"]["pending_amount"] == pytest.approx(0.74)
    assert by_worker["alice"]["payable"] is True


def test_summarize_round_window_payouts_includes_historical_work_before_winning_block():
    payouts = summarize_round_window_payouts(
        task_rewards=[
            {
                "pool_task_id": "pooltask_win",
                "mainnet_task_id": "task_win",
                "reward": 2.0,
                "completed_at": "2026-06-05T00:10:00+00:00",
            }
        ],
        share_rows=[
            {"pool_task_id": "pooltask_stale_a", "worker_id": "alice", "units": 1, "created_at": "2026-06-05T00:01:00+00:00"},
            {"pool_task_id": "pooltask_stale_b", "worker_id": "bob", "units": 3, "created_at": "2026-06-05T00:02:00+00:00"},
            {"pool_task_id": "pooltask_win", "worker_id": "carol", "units": 2, "created_at": "2026-06-05T00:09:00+00:00"},
        ],
        worker_rows=[
            {"worker_id": "alice", "name": "Alice", "payout_address": "PIA"},
            {"worker_id": "bob", "name": "Bob", "payout_address": "PIB"},
            {"worker_id": "carol", "name": "Carol", "payout_address": "PIC"},
        ],
        pool_fee_percent=1,
    )

    by_worker = {worker["worker_id"]: worker for worker in payouts["workers"]}

    assert payouts["accounting_mode"] == "round_window"
    assert payouts["gross_total"] == pytest.approx(2.0)
    assert payouts["pool_fee_total"] == pytest.approx(0.02)
    assert payouts["pending_total"] == pytest.approx(1.98)
    assert payouts["rounds"][0]["total_units"] == 6
    assert by_worker["alice"]["pending_amount"] == pytest.approx(0.33)
    assert by_worker["bob"]["pending_amount"] == pytest.approx(0.99)
    assert by_worker["carol"]["pending_amount"] == pytest.approx(0.66)


def test_summarize_round_window_payouts_resets_each_winning_block_window():
    payouts = summarize_round_window_payouts(
        task_rewards=[
            {
                "pool_task_id": "pooltask_win_2",
                "mainnet_task_id": "task_win_2",
                "reward": 1.0,
                "completed_at": "2026-06-05T00:20:00+00:00",
            },
            {
                "pool_task_id": "pooltask_win_1",
                "mainnet_task_id": "task_win_1",
                "reward": 1.0,
                "completed_at": "2026-06-05T00:10:00+00:00",
            },
        ],
        share_rows=[
            {"pool_task_id": "pooltask_stale_1", "worker_id": "alice", "units": 2, "created_at": "2026-06-05T00:03:00+00:00"},
            {"pool_task_id": "pooltask_win_1", "worker_id": "bob", "units": 2, "created_at": "2026-06-05T00:09:00+00:00"},
            {"pool_task_id": "pooltask_stale_2", "worker_id": "carol", "units": 1, "created_at": "2026-06-05T00:15:00+00:00"},
            {"pool_task_id": "pooltask_win_2", "worker_id": "dave", "units": 3, "created_at": "2026-06-05T00:19:00+00:00"},
        ],
        worker_rows=[
            {"worker_id": "alice", "name": "Alice", "payout_address": "PIA"},
            {"worker_id": "bob", "name": "Bob", "payout_address": "PIB"},
            {"worker_id": "carol", "name": "Carol", "payout_address": "PIC"},
            {"worker_id": "dave", "name": "Dave", "payout_address": "PID"},
        ],
        pool_fee_percent=0,
    )

    by_worker = {worker["worker_id"]: worker for worker in payouts["workers"]}

    assert payouts["gross_total"] == pytest.approx(2.0)
    assert by_worker["alice"]["pending_amount"] == pytest.approx(0.5)
    assert by_worker["bob"]["pending_amount"] == pytest.approx(0.5)
    assert by_worker["carol"]["pending_amount"] == pytest.approx(0.25)
    assert by_worker["dave"]["pending_amount"] == pytest.approx(0.75)
    assert [round_row["total_units"] for round_row in payouts["rounds"]] == [4, 4]


def test_summarize_round_window_payouts_reopens_underpaid_historical_shares():
    payouts = summarize_round_window_payouts(
        task_rewards=[
            {
                "pool_task_id": "pooltask_win",
                "mainnet_task_id": "task_win",
                "reward": 2.0,
                "completed_at": "2026-06-05T00:10:00+00:00",
            }
        ],
        share_rows=[
            {"pool_task_id": "pooltask_stale", "worker_id": "alice", "units": 1, "created_at": "2026-06-05T00:01:00+00:00"},
            {"pool_task_id": "pooltask_win", "worker_id": "bob", "units": 1, "created_at": "2026-06-05T00:09:00+00:00"},
        ],
        worker_rows=[
            {"worker_id": "alice", "name": "Alice", "payout_address": "PIA"},
            {"worker_id": "bob", "name": "Bob", "payout_address": "PIB"},
        ],
        payout_rows=[{"worker_id": "bob", "payout_address": "PIB", "amount": 2.0}],
        pool_fee_percent=0,
        min_payout_amount=0.1,
    )

    by_worker = {worker["worker_id"]: worker for worker in payouts["workers"]}

    assert payouts["paid_total"] == pytest.approx(2.0)
    assert payouts["pending_total"] == pytest.approx(1.0)
    assert payouts["operator_top_up_total"] == pytest.approx(1.0)
    assert by_worker["alice"]["pending_amount"] == pytest.approx(1.0)
    assert "bob" not in by_worker


def test_filter_shares_by_window_returns_current_round_only():
    rows = [
        {"worker_id": "old", "units": 1, "created_at": "2026-06-05T00:01:00+00:00"},
        {"worker_id": "current", "units": 1, "created_at": "2026-06-05T00:12:00+00:00"},
    ]

    assert [row["worker_id"] for row in filter_shares_by_window(rows, start_at="2026-06-05T00:10:00+00:00")] == [
        "current"
    ]
