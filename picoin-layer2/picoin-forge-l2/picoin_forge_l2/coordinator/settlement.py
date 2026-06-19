from __future__ import annotations

import json
from pathlib import Path

from picoin_forge_l2.common.hashing import hash_json
from picoin_forge_l2.common.models import EpochReward, EpochSettlement, SettlementPayloadPreview


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


def settlement_path(state_dir: str | Path, epoch_id: int) -> Path:
    return Path(state_dir) / f"settlement_epoch_{int(epoch_id)}.json"


def read_settlement(state_dir: str | Path, epoch_id: int) -> EpochSettlement:
    path = settlement_path(state_dir, epoch_id)
    if not path.exists():
        raise KeyError(f"settlement not found for epoch {epoch_id}")
    return EpochSettlement.model_validate_json(path.read_text(encoding="utf-8"))


def list_settlements(state_dir: str | Path, limit: int = 100) -> list[EpochSettlement]:
    safe_limit = max(1, min(int(limit), 1000))
    paths = sorted(
        Path(state_dir).glob("settlement_epoch_*.json"),
        key=lambda item: int(item.stem.rsplit("_", 1)[-1]),
        reverse=True,
    )
    return [EpochSettlement.model_validate_json(path.read_text(encoding="utf-8")) for path in paths[:safe_limit]]


def summarize_settlement(settlement: EpochSettlement) -> dict:
    return {
        "epoch_id": settlement.epoch_id,
        "epoch_reward": settlement.epoch_reward,
        "total_verified_compute": settlement.total_verified_compute,
        "worker_count": len(settlement.workers),
        "result_hash": settlement.result_hash,
        "timestamp": settlement.timestamp.isoformat(),
        "l1_settlement_ready": settlement.l1_settlement_ready,
    }


def build_settlement_payload_preview(settlement: EpochSettlement) -> SettlementPayloadPreview:
    canonical_payload = {
        "schema_version": "picoin-forge-l2-settlement-preview-v1",
        "payload_type": "l2_epoch_settlement_preview",
        "epoch_id": settlement.epoch_id,
        "epoch_reward": settlement.epoch_reward,
        "total_verified_compute": settlement.total_verified_compute,
        "worker_count": len(settlement.workers),
        "settlement_result_hash": settlement.result_hash,
        "worker_rewards": [reward.model_dump(mode="json") for reward in settlement.workers],
        "signatures": [],
        "no_l1_transaction_created": True,
    }
    return SettlementPayloadPreview(
        epoch_id=settlement.epoch_id,
        epoch_reward=settlement.epoch_reward,
        total_verified_compute=settlement.total_verified_compute,
        worker_count=len(settlement.workers),
        settlement_result_hash=settlement.result_hash,
        worker_rewards=settlement.workers,
        payload_hash=hash_json(canonical_payload),
    )
