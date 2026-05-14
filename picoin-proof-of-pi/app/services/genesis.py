from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.crypto import canonical_json, sha256_text


GENESIS_ALLOCATION_VERSION = 1


def load_genesis_allocations(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    allocation_path = Path(path)
    if not allocation_path.exists():
        raise FileNotFoundError(f"genesis allocations file not found: {allocation_path}")
    document = json.loads(allocation_path.read_text(encoding="utf-8"))
    return normalize_genesis_allocations(document)


def normalize_genesis_allocations(document: dict[str, Any]) -> dict[str, Any]:
    version = int(document.get("version", GENESIS_ALLOCATION_VERSION))
    allocations = []
    for allocation in document.get("allocations", []):
        account_id = str(allocation["account_id"]).strip()
        account_type = str(allocation.get("account_type") or "wallet").strip()
        amount = round(float(allocation["amount"]), 8)
        if not account_id:
            raise ValueError("genesis allocation account_id is required")
        if amount <= 0:
            raise ValueError("genesis allocation amount must be positive")
        allocations.append(
            {
                "account_id": account_id,
                "account_type": account_type,
                "amount": amount,
                "description": str(allocation.get("description") or "genesis allocation funding"),
            }
        )
    allocations.sort(key=lambda item: (item["account_id"], item["account_type"], item["amount"]))
    return {
        "version": version,
        "network_id": str(document.get("network_id") or "").strip(),
        "chain_id": str(document.get("chain_id") or "").strip(),
        "created_at": str(document.get("created_at") or "1970-01-01T00:00:00+00:00"),
        "allocations": allocations,
    }


def genesis_allocations_hash(document: dict[str, Any] | None) -> str:
    if not document:
        return "0" * 64
    normalized = normalize_genesis_allocations(document)
    return sha256_text(canonical_json(normalized))
