from __future__ import annotations

from picoin_forge_l2.common.models import EpochReward
from picoin_forge_l2.coordinator.settlement import build_settlement, write_settlement
from picoin_forge_l2.coordinator.verifier import verify_settlement_file


def test_verify_settlement_file_recomputes_hashes(tmp_path):
    settlement = build_settlement(
        1,
        100.0,
        [
            EpochReward(worker_id="worker_a", wallet="PIA", verified_compute_score=75.0, reward_pi=75.0),
            EpochReward(worker_id="worker_b", wallet="PIB", verified_compute_score=25.0, reward_pi=25.0),
        ],
    )
    write_settlement(tmp_path, settlement)

    result = verify_settlement_file(tmp_path, 1)

    assert result["valid"] is True
    assert result["actual_result_hash"] == settlement.result_hash
    assert result["reward_sum"] == 100.0
    assert result["checks"]["preview_payload_hash"]
    assert result["checks"]["no_l1_transaction_created"] is True
