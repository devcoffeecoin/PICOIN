from __future__ import annotations

import json
from pathlib import Path

from picoin_forge_l2.common.hashing import hash_json
from picoin_forge_l2.common.models import EpochReward, EpochSettlement


def build_settlement(epoch_id: int, epoch_reward: float, rewards: list[EpochReward]) -> EpochSettlement:
    total_verified_compute = round(sum(item.verified_compute_score for item in rewards), 8)
    payload = {
        "epoch_id": epoch_id,
        "epoch_reward": epoch_reward,
        "total_verified_compute": total_verified_compute,
        "workers": [item.model_dump(mode="json") for item in rewards],
    }
    result_hash = hash_json(payload)
    return EpochSettlement(
        epoch_id=epoch_id,
        epoch_reward=epoch_reward,
        total_verified_compute=total_verified_compute,
        workers=rewards,
        result_hash=result_hash,
    )


def write_settlement(state_dir: str | Path, settlement: EpochSettlement) -> Path:
    path = Path(state_dir) / f"settlement_epoch_{settlement.epoch_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settlement.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
