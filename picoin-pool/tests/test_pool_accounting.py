import pytest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pool_accounting import assemble_segment, split_range, summarize_payouts, summarize_shares


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
