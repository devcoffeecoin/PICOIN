from __future__ import annotations

from pathlib import Path

from picoin_forge_l2.common.hashing import hash_json
from picoin_forge_l2.common.models import EpochSettlement

from .settlement import build_settlement_payload_preview, read_settlement


def verify_settlement(settlement: EpochSettlement) -> dict:
    total_verified_compute = round(sum(item.verified_compute_score for item in settlement.workers), 8)
    reward_sum = round(sum(item.reward_pi for item in settlement.workers), 8)
    expected_result_hash = hash_json(
        {
            "epoch_id": settlement.epoch_id,
            "epoch_reward": settlement.epoch_reward,
            "total_verified_compute": total_verified_compute,
            "workers": [item.model_dump(mode="json") for item in settlement.workers],
        }
    )
    preview = build_settlement_payload_preview(settlement)
    checks = {
        "total_verified_compute_matches": total_verified_compute == settlement.total_verified_compute,
        "result_hash_matches": expected_result_hash == settlement.result_hash,
        "reward_sum_not_above_epoch_reward": reward_sum <= round(settlement.epoch_reward, 8),
        "preview_payload_hash": preview.payload_hash,
        "no_l1_transaction_created": preview.no_l1_transaction_created,
    }
    return {
        "epoch_id": settlement.epoch_id,
        "valid": all(value for key, value in checks.items() if isinstance(value, bool)),
        "checks": checks,
        "expected_result_hash": expected_result_hash,
        "actual_result_hash": settlement.result_hash,
        "reward_sum": reward_sum,
        "epoch_reward": settlement.epoch_reward,
    }


def verify_settlement_file(state_dir: str | Path, epoch_id: int) -> dict:
    return verify_settlement(read_settlement(state_dir, epoch_id))
