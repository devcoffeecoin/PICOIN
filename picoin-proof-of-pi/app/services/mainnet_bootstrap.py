from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.crypto import canonical_json, sha256_text
from app.core.money import canonical_amount, to_units, units_from_db, units_to_float
from app.core.settings import CHAIN_ID, MIN_VALIDATOR_STAKE, NETWORK_ID, NETWORK_PROFILE
from app.db.database import get_connection, init_db
from app.services.wallet import is_valid_address


class MainnetBootstrapError(RuntimeError):
    pass


def apply_bootstrap_validator_stakes(stakes_file: Path, *, dry_run: bool = False) -> dict[str, Any]:
    if NETWORK_PROFILE.name != "mainnet":
        raise MainnetBootstrapError("bootstrap validator stake can only run on mainnet")

    document = _load_stakes_document(stakes_file)
    init_db()
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        _require_prelaunch_state(connection)
        applied = []
        total_units = 0
        timestamp = datetime.now(timezone.utc).isoformat()

        for stake in document["stakes"]:
            applied.append(_apply_validator_stake(connection, stake, timestamp))
            total_units += to_units(stake["amount"])

        if dry_run:
            connection.rollback()
        else:
            connection.commit()

        return {
            "status": "dry_run" if dry_run else "applied",
            "network_id": NETWORK_ID,
            "chain_id": CHAIN_ID,
            "document_hash": sha256_text(canonical_json(document)),
            "applied_count": len(applied),
            "total_amount": units_to_float(total_units),
            "total_units": total_units,
            "stakes": applied,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _load_stakes_document(stakes_file: Path) -> dict[str, Any]:
    if not stakes_file.exists():
        raise MainnetBootstrapError(f"bootstrap validator stake file not found: {stakes_file}")
    try:
        raw_document = json.loads(stakes_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MainnetBootstrapError(f"invalid bootstrap validator stake JSON: {exc}") from exc

    network_id = str(raw_document.get("network_id") or "").strip()
    chain_id = _normalize_chain_id(raw_document.get("chain_id"))
    if network_id and network_id != NETWORK_ID:
        raise MainnetBootstrapError(f"bootstrap stake network_id mismatch: {network_id} != {NETWORK_ID}")
    if chain_id not in {"", None} and str(chain_id) != str(CHAIN_ID):
        raise MainnetBootstrapError(f"bootstrap stake chain_id mismatch: {chain_id} != {CHAIN_ID}")

    raw_stakes = raw_document.get("stakes")
    if not isinstance(raw_stakes, list) or not raw_stakes:
        raise MainnetBootstrapError("bootstrap validator stake file requires a non-empty stakes list")

    seen_validators: set[str] = set()
    stakes = []
    for raw_stake in raw_stakes:
        if not isinstance(raw_stake, dict):
            raise MainnetBootstrapError("each bootstrap validator stake must be an object")
        validator_id = str(raw_stake.get("validator_id") or "").strip()
        owner = str(raw_stake.get("stake_owner_address") or raw_stake.get("owner_address") or "").strip().upper()
        amount_units = to_units(raw_stake.get("amount", MIN_VALIDATOR_STAKE))
        if not validator_id.startswith("validator_"):
            raise MainnetBootstrapError(f"invalid validator_id in bootstrap stake file: {validator_id}")
        if validator_id in seen_validators:
            raise MainnetBootstrapError(f"duplicate bootstrap validator stake for {validator_id}")
        seen_validators.add(validator_id)
        if not _is_canonical_wallet_address(owner):
            raise MainnetBootstrapError(f"stake_owner_address must be a canonical Picoin wallet address: {owner}")
        if amount_units < to_units(MIN_VALIDATOR_STAKE):
            raise MainnetBootstrapError(
                f"bootstrap validator stake for {validator_id} must be at least {canonical_amount(to_units(MIN_VALIDATOR_STAKE))} PI"
            )
        stakes.append(
            {
                "validator_id": validator_id,
                "stake_owner_address": owner,
                "amount": units_to_float(amount_units),
                "amount_units": amount_units,
            }
        )

    stakes.sort(key=lambda item: item["validator_id"])
    return {
        "version": int(raw_document.get("version", 1)),
        "network_id": network_id or NETWORK_ID,
        "chain_id": chain_id if chain_id not in {"", None} else CHAIN_ID,
        "stakes": stakes,
    }


def _require_prelaunch_state(connection: Any) -> None:
    height_row = connection.execute("SELECT COALESCE(MAX(height), 0) AS height FROM blocks").fetchone()
    height = 0 if height_row is None else int(height_row["height"] or 0)
    if height != 0:
        raise MainnetBootstrapError(f"bootstrap validator stake must run before block 1; current height is {height}")

    mempool_row = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM mempool_transactions
        WHERE status IN ('pending', 'selected', 'propagated')
        """
    ).fetchone()
    if mempool_row is not None and int(mempool_row["count"] or 0) > 0:
        raise MainnetBootstrapError("bootstrap validator stake requires an empty pending mempool")


def _apply_validator_stake(connection: Any, stake: dict[str, Any], timestamp: str) -> dict[str, Any]:
    validator_id = stake["validator_id"]
    owner = stake["stake_owner_address"]
    amount_units = int(stake["amount_units"])
    amount = units_to_float(amount_units)
    related_id = f"mainnet_bootstrap_validator_stake:{validator_id}"

    existing = connection.execute(
        "SELECT 1 FROM ledger_entries WHERE related_id = ? LIMIT 1",
        (related_id,),
    ).fetchone()
    if existing is not None:
        raise MainnetBootstrapError(f"bootstrap validator stake already applied for {validator_id}")

    validator = connection.execute(
        """
        SELECT validator_id, enabled, is_banned, stake_locked, wallet_stake_locked, stake_owner_address
        FROM validators
        WHERE validator_id = ?
        """,
        (validator_id,),
    ).fetchone()
    if validator is None:
        raise MainnetBootstrapError(f"validator must be registered before bootstrap stake: {validator_id}")
    if int(validator["enabled"] or 0) != 1:
        raise MainnetBootstrapError(f"validator is disabled: {validator_id}")
    if int(validator["is_banned"] or 0) != 0:
        raise MainnetBootstrapError(f"validator is banned: {validator_id}")

    stake_locked_units = to_units(validator["stake_locked"] or 0)
    wallet_stake_units = to_units(validator["wallet_stake_locked"] or 0)
    if stake_locked_units != wallet_stake_units:
        raise MainnetBootstrapError(f"validator has legacy/unbacked stake before bootstrap: {validator_id}")
    existing_owner = str(validator["stake_owner_address"] or "").strip().upper()
    if wallet_stake_units > 0 or existing_owner:
        raise MainnetBootstrapError(f"validator already has wallet-backed stake: {validator_id}")

    owner_row = connection.execute(
        "SELECT balance, balance_units FROM balances WHERE account_id = ?",
        (owner,),
    ).fetchone()
    owner_units = _balance_units(owner_row)
    if owner_units < amount_units:
        raise MainnetBootstrapError(
            f"stake owner balance is insufficient for {validator_id}: {canonical_amount(owner_units)} PI available"
        )

    _apply_account_delta(
        connection,
        owner,
        "wallet",
        -amount_units,
        "validator_stake_debit",
        related_id,
        f"mainnet bootstrap validator stake for {validator_id}",
        timestamp,
    )
    _apply_account_delta(
        connection,
        validator_id,
        "validator",
        amount_units,
        "validator_stake_lock",
        related_id,
        f"mainnet bootstrap validator stake from {owner}",
        timestamp,
    )
    connection.execute(
        """
        UPDATE validators
        SET stake_locked = ?,
            wallet_stake_locked = ?,
            stake_owner_address = ?,
            last_seen_at = ?
        WHERE validator_id = ?
        """,
        (amount, amount, owner, timestamp, validator_id),
    )
    return {
        "validator_id": validator_id,
        "stake_owner_address": owner,
        "amount": amount,
        "amount_units": amount_units,
        "related_id": related_id,
    }


def _apply_account_delta(
    connection: Any,
    account_id: str,
    account_type: str,
    amount_units: int,
    entry_type: str,
    related_id: str,
    description: str,
    timestamp: str,
) -> None:
    current = connection.execute(
        "SELECT balance, balance_units FROM balances WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    previous_units = _balance_units(current)
    balance_after_units = previous_units + amount_units
    if balance_after_units < 0:
        raise MainnetBootstrapError(f"negative balance for {account_id}")
    balance_after = units_to_float(balance_after_units)
    connection.execute(
        """
        INSERT INTO balances (account_id, account_type, balance, balance_units, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(account_id) DO UPDATE SET
            account_type = excluded.account_type,
            balance = excluded.balance,
            balance_units = excluded.balance_units,
            updated_at = excluded.updated_at
        """,
        (account_id, account_type, balance_after, balance_after_units, timestamp),
    )
    connection.execute(
        """
        INSERT INTO ledger_entries (
            account_id, account_type, amount, amount_units, balance_after, balance_after_units, entry_type,
            block_height, related_id, description, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
        """,
        (
            account_id,
            account_type,
            units_to_float(amount_units),
            amount_units,
            balance_after,
            balance_after_units,
            entry_type,
            related_id,
            description,
            timestamp,
        ),
    )


def _balance_units(row: Any) -> int:
    if row is None:
        return 0
    return units_from_db(row["balance"], row["balance_units"])


def _is_canonical_wallet_address(address: str) -> bool:
    return is_valid_address(address) and len(str(address or "").strip()) == 48


def _normalize_chain_id(value: Any) -> str | int:
    if value is None:
        return ""
    if isinstance(value, int):
        return value
    cleaned = str(value).strip()
    return int(cleaned) if cleaned.isdigit() else cleaned
