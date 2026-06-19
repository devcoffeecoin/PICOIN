from __future__ import annotations

from pathlib import Path
from typing import Any

from picoin_forge_l2.common.hashing import hash_json
from picoin_forge_l2.common.models import EpochSettlement

from .demo import run_demo_network
from .settlement import build_settlement_payload_preview


def run_federated_demo(
    state_dir: str | Path,
    *,
    coordinator_count: int = 2,
    workers_per_coordinator: int = 2,
    epoch_reward: float = 100.0,
) -> dict[str, Any]:
    base_dir = Path(state_dir)
    safe_coordinator_count = max(1, coordinator_count)
    safe_workers_per_coordinator = max(1, workers_per_coordinator)
    coordinators: list[dict[str, Any]] = []

    for index in range(safe_coordinator_count):
        coordinator_id = f"coordinator-{index + 1}"
        coordinator_state_dir = base_dir / coordinator_id
        result = run_demo_network(
            coordinator_state_dir,
            worker_count=safe_workers_per_coordinator,
            epoch_reward=epoch_reward,
        )
        preview = build_settlement_payload_preview(result.settlement)
        coordinators.append(
            {
                "coordinator_id": coordinator_id,
                "state_dir": str(coordinator_state_dir),
                "workers_created": result.workers_created,
                "challenges_passed": result.challenges_passed,
                "settlement": result.settlement.model_dump(mode="json"),
                "l1_preview": preview.model_dump(mode="json"),
            }
        )

    federation_payload = {
        "schema_version": "picoin-forge-l2-federation-demo-v1",
        "coordinator_count": safe_coordinator_count,
        "workers_per_coordinator": safe_workers_per_coordinator,
        "epoch_reward_per_coordinator": epoch_reward,
        "coordinators": [
            {
                "coordinator_id": row["coordinator_id"],
                "settlement_result_hash": row["settlement"]["result_hash"],
                "payload_hash": row["l1_preview"]["payload_hash"],
                "workers_created": row["workers_created"],
            }
            for row in coordinators
        ],
    }
    return {
        **federation_payload,
        "federation_result_hash": hash_json(federation_payload),
        "coordinators": coordinators,
        "no_l1_transaction_created": True,
        "note": "Local simulation only. This manifest is not submitted to Picoin L1.",
    }


def verify_federated_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    coordinators = manifest.get("coordinators") or []
    if not isinstance(coordinators, list):
        return {"valid": False, "errors": ["coordinators must be a list"]}

    coordinator_checks = []
    for row in coordinators:
        if not isinstance(row, dict):
            errors.append("coordinator entry must be an object")
            continue
        coordinator_id = str(row.get("coordinator_id", "unknown"))
        try:
            settlement = EpochSettlement.model_validate(row["settlement"])
            expected_preview = build_settlement_payload_preview(settlement).model_dump(mode="json")
            provided_preview = row.get("l1_preview") or {}
            preview_hash_matches = provided_preview.get("payload_hash") == expected_preview["payload_hash"]
            settlement_hash_matches = provided_preview.get("settlement_result_hash") == settlement.result_hash
            if not preview_hash_matches:
                errors.append(f"{coordinator_id}: l1 preview payload hash mismatch")
            if not settlement_hash_matches:
                errors.append(f"{coordinator_id}: settlement result hash mismatch")
            coordinator_checks.append(
                {
                    "coordinator_id": coordinator_id,
                    "preview_hash_matches": preview_hash_matches,
                    "settlement_hash_matches": settlement_hash_matches,
                }
            )
        except (KeyError, ValueError, TypeError) as exc:
            errors.append(f"{coordinator_id}: invalid settlement payload: {exc}")

    expected_root_hash = hash_json(_federation_hash_payload(manifest))
    root_hash_matches = manifest.get("federation_result_hash") == expected_root_hash
    if not root_hash_matches:
        errors.append("federation result hash mismatch")

    expected_count = int(manifest.get("coordinator_count") or 0)
    if expected_count != len(coordinators):
        errors.append("coordinator_count does not match coordinator entries")

    return {
        "valid": not errors,
        "errors": errors,
        "expected_federation_result_hash": expected_root_hash,
        "root_hash_matches": root_hash_matches,
        "coordinator_checks": coordinator_checks,
    }


def _federation_hash_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    coordinators = manifest.get("coordinators") or []
    return {
        "schema_version": manifest.get("schema_version"),
        "coordinator_count": manifest.get("coordinator_count"),
        "workers_per_coordinator": manifest.get("workers_per_coordinator"),
        "epoch_reward_per_coordinator": manifest.get("epoch_reward_per_coordinator"),
        "coordinators": [
            {
                "coordinator_id": row.get("coordinator_id"),
                "settlement_result_hash": _coordinator_settlement_hash(row),
                "payload_hash": _coordinator_payload_hash(row),
                "workers_created": row.get("workers_created"),
            }
            for row in coordinators
            if isinstance(row, dict)
        ],
    }


def _coordinator_settlement_hash(row: dict[str, Any]) -> str | None:
    if "settlement_result_hash" in row:
        return row.get("settlement_result_hash")
    settlement = row.get("settlement") or {}
    if isinstance(settlement, dict):
        return settlement.get("result_hash")
    return None


def _coordinator_payload_hash(row: dict[str, Any]) -> str | None:
    if "payload_hash" in row:
        return row.get("payload_hash")
    preview = row.get("l1_preview") or {}
    if isinstance(preview, dict):
        return preview.get("payload_hash")
    return None
