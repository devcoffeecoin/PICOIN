from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.crypto import canonical_json, sha256_text
from app.core.money import canonical_amount, to_units, units_from_db, units_to_float
from app.core.settings import CHAIN_ID, CHECKPOINT_INTERVAL_BLOCKS, GENESIS_HASH, NETWORK_ID, PROTOCOL_VERSION
from app.db.database import get_connection, row_to_dict


class StateError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


SNAPSHOT_EXPORT_MAX_ATTEMPTS = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def balance_snapshot(connection: Any, block_height: int, block_timestamp: str | None = None) -> list[dict[str, Any]]:
    return [
        {
            "account_id": item["account_id"],
            "account_type": item["account_type"],
            "balance": canonical_amount(item["balance_units"]),
            "balance_units": item["balance_units"],
        }
        for item in canonical_balance_snapshot(connection, block_height, block_timestamp)
    ]


def account_nonce_snapshot(connection: Any) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT account_id, nonce
        FROM account_nonces
        WHERE nonce > 0
        ORDER BY account_id ASC
        """
    ).fetchall()
    return [{"account_id": str(row["account_id"]), "nonce": int(row["nonce"])} for row in rows]


def validator_snapshot(connection: Any) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            validator_id, name, public_key, reward_address, registered_at,
            accepted_jobs, rejected_jobs, invalid_results, trust_score, cooldown_until,
            total_validation_ms, stake_locked, wallet_stake_locked, stake_owner_address,
            slashed_amount, is_banned, enabled, protocol_version
        FROM validators
        ORDER BY validator_id ASC
        """
    ).fetchall()
    return [_normalize_validator_snapshot_row(row) for row in rows]


def protocol_params_snapshot(connection: Any) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            id, protocol_version, algorithm, validation_mode, required_validator_approvals,
            range_assignment_mode, max_pi_position, range_assignment_max_attempts,
            segment_size, sample_count, task_expiration_seconds, max_active_tasks_per_miner,
            base_reward, difficulty, RETARGET_MAX_PI_POSITION, target_block_time_ms,
            retarget_reason, retarget_source_window, retarget_source_details,
            previous_protocol_params_id, active, created_at
        FROM protocol_params
        ORDER BY id ASC
        """
    ).fetchall()
    return [_normalize_protocol_params_snapshot_row(row) for row in rows]


def retarget_events_snapshot(connection: Any) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            id, previous_protocol_params_id, new_protocol_params_id, epoch_start_height,
            epoch_end_height, epoch_block_count, average_block_ms, target_block_ms,
            old_difficulty, new_difficulty, adjustment_factor, action, reason, created_at
        FROM retarget_events
        ORDER BY id ASC
        """
    ).fetchall()
    return [_normalize_retarget_event_snapshot_row(row) for row in rows]


def pending_rewards_snapshot(connection: Any, block_height: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            id, miner_id, block_height, amount, amount_units, account_id, account_type,
            status, matures_at_height, matured_at, orphaned_at, orphan_reason,
            related_id, reason, created_at
        FROM rewards
        WHERE block_height <= ?
          AND matures_at_height IS NOT NULL
          AND matures_at_height > ?
          AND COALESCE(orphaned_at, '') = ''
        ORDER BY matures_at_height ASC, id ASC
        """,
        (block_height, block_height),
    ).fetchall()
    return [_normalize_pending_reward_snapshot_row(row) for row in rows]


def canonical_balance_snapshot(connection: Any, block_height: int, block_timestamp: str | None = None) -> list[dict[str, Any]]:
    if block_height < 0:
        raise ValueError("block_height must be >= 0")
    rows = connection.execute(
        """
        SELECT account_id, account_type, amount, amount_units
        FROM ledger_entries
        WHERE block_height IS NOT NULL AND block_height <= ?
        ORDER BY block_height ASC, id ASC
        """,
        (block_height,),
    ).fetchall()
    merged: dict[tuple[str, str], int] = {}
    for row in rows:
        account_id = str(row["account_id"])
        account_type = str(row["account_type"] or _infer_account_type(account_id))
        key = (account_id, account_type)
        merged[key] = merged.get(key, 0) + units_from_db(row["amount"], row["amount_units"])
    if block_timestamp is not None:
        loose_rows = connection.execute(
            """
            SELECT account_id, account_type, amount, amount_units
            FROM ledger_entries
            WHERE block_height IS NULL AND created_at <= ?
            ORDER BY created_at ASC, id ASC
            """,
            (block_timestamp,),
        ).fetchall()
        for row in loose_rows:
            account_id = str(row["account_id"])
            account_type = str(row["account_type"] or _infer_account_type(account_id))
            key = (account_id, account_type)
            merged[key] = merged.get(key, 0) + units_from_db(row["amount"], row["amount_units"])
    return [
        {"account_id": account_id, "account_type": account_type, "balance_units": units}
        for (account_id, account_type), units in sorted(merged.items(), key=lambda item: (item[0][0], item[0][1]))
        if units != 0
    ]


def calculate_state_root(connection: Any, block_height: int, block_timestamp: str | None = None) -> str:
    """Return a deterministic root for account balances after a block replay."""
    balances = balance_snapshot(connection, block_height, block_timestamp)
    return sha256_text(canonical_json({"height": int(block_height), "balances": balances}))


def update_block_state_root(connection: Any, block_height: int, block_timestamp: str | None = None) -> str:
    state_root = calculate_state_root(connection, block_height, block_timestamp)
    connection.execute(
        "UPDATE blocks SET state_root = ? WHERE height = ?",
        (state_root, block_height),
    )
    return state_root


def create_canonical_checkpoint(
    height: int | None = None,
    *,
    trusted: bool = True,
    source: str = "local",
) -> dict[str, Any]:
    with get_connection() as connection:
        if height is None:
            latest = connection.execute("SELECT COALESCE(MAX(height), 0) AS height FROM blocks").fetchone()
            height = int(latest["height"] if latest else 0)
        checkpoint = create_canonical_checkpoint_in_connection(
            connection,
            int(height),
            trusted=trusted,
            source=source,
        )
    return checkpoint


def create_canonical_checkpoint_in_connection(
    connection: Any,
    height: int,
    *,
    trusted: bool = True,
    source: str = "local",
) -> dict[str, Any]:
    if height <= 0:
        raise StateError(422, "checkpoint height must be a mined block height")
    block = row_to_dict(
        connection.execute(
            """
            SELECT height, previous_hash, block_hash, state_root, timestamp
            FROM blocks
            WHERE height = ?
            """,
            (height,),
        ).fetchone()
    )
    if block is None:
        raise StateError(404, "block not found for checkpoint")

    state_root = block.get("state_root") or update_block_state_root(connection, height, block.get("timestamp"))
    balances = balance_snapshot(connection, height, block.get("timestamp"))
    balances_hash = sha256_text(canonical_json({"height": height, "balances": balances}))
    nonces = account_nonce_snapshot(connection)
    nonces_hash = sha256_text(canonical_json({"height": height, "nonces": nonces}))
    validators = validator_snapshot(connection)
    validators_hash = sha256_text(canonical_json({"height": height, "validators": validators}))
    protocol_params = protocol_params_snapshot(connection)
    protocol_params_hash = sha256_text(canonical_json({"height": height, "protocol_params": protocol_params}))
    retarget_events = retarget_events_snapshot(connection)
    retarget_events_hash = sha256_text(canonical_json({"height": height, "retarget_events": retarget_events}))
    pending_rewards = pending_rewards_snapshot(connection, height)
    pending_rewards_hash = sha256_text(canonical_json({"height": height, "pending_rewards": pending_rewards}))
    ledger_entries_count = int(
        connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM ledger_entries
            WHERE (block_height IS NOT NULL AND block_height <= ?)
               OR (block_height IS NULL AND created_at <= ?)
            """,
            (height, block["timestamp"]),
        ).fetchone()["count"]
    )
    total_balance_units = sum(int(item["balance_units"]) for item in balances)
    total_balance = units_to_float(total_balance_units)
    payload = {
        "chain_id": CHAIN_ID,
        "network_id": NETWORK_ID,
        "genesis_hash": GENESIS_HASH,
        "protocol_version": PROTOCOL_VERSION,
        "height": height,
        "block_hash": block["block_hash"],
        "previous_hash": block["previous_hash"],
        "state_root": state_root,
        "balances_hash": balances_hash,
        "balances_count": len(balances),
        "nonces_hash": nonces_hash,
        "nonces_count": len(nonces),
        "validators_hash": validators_hash,
        "validators_count": len(validators),
        "protocol_params_hash": protocol_params_hash,
        "protocol_params_count": len(protocol_params),
        "retarget_events_hash": retarget_events_hash,
        "retarget_events_count": len(retarget_events),
        "pending_rewards_hash": pending_rewards_hash,
        "pending_rewards_count": len(pending_rewards),
        "ledger_entries_count": ledger_entries_count,
        "total_balance": total_balance,
        "total_balance_units": total_balance_units,
    }
    snapshot_hash = sha256_text(canonical_json(payload))
    checkpoint_id = sha256_text(f"{CHAIN_ID}:{height}:{block['block_hash']}")[:32]
    timestamp = _now()
    payload["snapshot_hash"] = snapshot_hash

    connection.execute(
        """
        INSERT INTO canonical_checkpoints (
            checkpoint_id, height, block_hash, previous_hash, state_root,
            balances_hash, snapshot_hash, balances_count, ledger_entries_count,
            total_balance, total_balance_units, trusted, source, created_at, verified_at, payload
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(height) DO UPDATE SET
            block_hash = excluded.block_hash,
            previous_hash = excluded.previous_hash,
            state_root = excluded.state_root,
            balances_hash = excluded.balances_hash,
            snapshot_hash = excluded.snapshot_hash,
            balances_count = excluded.balances_count,
            ledger_entries_count = excluded.ledger_entries_count,
            total_balance = excluded.total_balance,
            total_balance_units = excluded.total_balance_units,
            trusted = excluded.trusted,
            source = excluded.source,
            verified_at = excluded.verified_at,
            payload = excluded.payload
        """,
        (
            checkpoint_id,
            height,
            block["block_hash"],
            block["previous_hash"],
            state_root,
            balances_hash,
            snapshot_hash,
            len(balances),
            ledger_entries_count,
            total_balance,
            total_balance_units,
            1 if trusted else 0,
            source,
            timestamp,
            timestamp,
            json.dumps(payload, sort_keys=True),
        ),
    )
    return get_checkpoint_in_connection(connection, height) or {}


def maybe_create_checkpoint_in_connection(connection: Any, height: int) -> dict[str, Any] | None:
    if CHECKPOINT_INTERVAL_BLOCKS <= 0 or height <= 0 or height % CHECKPOINT_INTERVAL_BLOCKS != 0:
        return None
    return create_canonical_checkpoint_in_connection(connection, height, trusted=True, source="auto")


def get_checkpoint(height: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        return get_checkpoint_in_connection(connection, height)


def get_checkpoint_in_connection(connection: Any, height: int) -> dict[str, Any] | None:
    row = row_to_dict(
        connection.execute(
            "SELECT * FROM canonical_checkpoints WHERE height = ?",
            (height,),
        ).fetchone()
    )
    return _decode_checkpoint(row)


def latest_checkpoint() -> dict[str, Any] | None:
    with get_connection() as connection:
        return latest_checkpoint_in_connection(connection)


def latest_checkpoint_in_connection(connection: Any) -> dict[str, Any] | None:
    row = row_to_dict(
        connection.execute(
            "SELECT * FROM canonical_checkpoints ORDER BY height DESC LIMIT 1"
        ).fetchone()
    )
    return _decode_checkpoint(row)


def list_checkpoints(limit: int = 50) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM canonical_checkpoints ORDER BY height DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_decode_checkpoint(row_to_dict(row)) for row in rows]


def verify_checkpoint(height: int) -> dict[str, Any]:
    with get_connection() as connection:
        checkpoint = get_checkpoint_in_connection(connection, height)
        if checkpoint is None:
            raise StateError(404, "checkpoint not found")
        block = row_to_dict(
            connection.execute(
                "SELECT height, block_hash, previous_hash, state_root, timestamp FROM blocks WHERE height = ?",
                (height,),
            ).fetchone()
        )
        if block is None:
            raise StateError(404, "checkpoint block not found")
        balances = balance_snapshot(connection, height, block["timestamp"])
        balances_hash = sha256_text(canonical_json({"height": height, "balances": balances}))
        nonces = account_nonce_snapshot(connection)
        nonces_hash = sha256_text(canonical_json({"height": height, "nonces": nonces}))
        validators = validator_snapshot(connection)
        validators_hash = sha256_text(canonical_json({"height": height, "validators": validators}))
        protocol_params = protocol_params_snapshot(connection)
        protocol_params_hash = sha256_text(canonical_json({"height": height, "protocol_params": protocol_params}))
        retarget_events = retarget_events_snapshot(connection)
        retarget_events_hash = sha256_text(canonical_json({"height": height, "retarget_events": retarget_events}))
        pending_rewards = pending_rewards_snapshot(connection, height)
        pending_rewards_hash = sha256_text(canonical_json({"height": height, "pending_rewards": pending_rewards}))
        state_root = calculate_state_root(connection, height, block["timestamp"])
        ledger_entries_count = int(
            connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM ledger_entries
                WHERE (block_height IS NOT NULL AND block_height <= ?)
                   OR (block_height IS NULL AND created_at <= ?)
                """,
                (height, block["timestamp"]),
            ).fetchone()["count"]
        )
        total_balance_units = sum(int(item["balance_units"]) for item in balances)
        total_balance = units_to_float(total_balance_units)
        payload = {
            "chain_id": CHAIN_ID,
            "network_id": NETWORK_ID,
            "genesis_hash": GENESIS_HASH,
            "protocol_version": PROTOCOL_VERSION,
            "height": height,
            "block_hash": block["block_hash"],
            "previous_hash": block["previous_hash"],
            "state_root": state_root,
            "balances_hash": balances_hash,
            "balances_count": len(balances),
            "ledger_entries_count": ledger_entries_count,
            "total_balance": total_balance,
            "total_balance_units": total_balance_units,
        }
        checkpoint_payload = checkpoint.get("payload") or {}
        if checkpoint_payload.get("nonces_hash"):
            payload["nonces_hash"] = nonces_hash
            payload["nonces_count"] = len(nonces)
        if checkpoint_payload.get("validators_hash"):
            payload["validators_hash"] = validators_hash
            payload["validators_count"] = len(validators)
        if checkpoint_payload.get("protocol_params_hash"):
            payload["protocol_params_hash"] = protocol_params_hash
            payload["protocol_params_count"] = len(protocol_params)
        if checkpoint_payload.get("retarget_events_hash"):
            payload["retarget_events_hash"] = retarget_events_hash
            payload["retarget_events_count"] = len(retarget_events)
        if checkpoint_payload.get("pending_rewards_hash"):
            payload["pending_rewards_hash"] = pending_rewards_hash
            payload["pending_rewards_count"] = len(pending_rewards)
        snapshot_hash = sha256_text(canonical_json(payload))
        issues = []
        if checkpoint["block_hash"] != block["block_hash"]:
            issues.append("block_hash mismatch")
        if checkpoint["previous_hash"] != block["previous_hash"]:
            issues.append("previous_hash mismatch")
        if checkpoint["state_root"] != state_root:
            issues.append("state_root mismatch")
        if checkpoint["balances_hash"] != balances_hash:
            issues.append("balances_hash mismatch")
        if checkpoint["balances_count"] != len(balances):
            issues.append("balances_count mismatch")
        if checkpoint["ledger_entries_count"] != ledger_entries_count:
            issues.append("ledger_entries_count mismatch")
        if int(checkpoint.get("total_balance_units") or to_units(checkpoint["total_balance"])) != total_balance_units:
            issues.append("total_balance mismatch")
        if checkpoint_payload.get("nonces_hash") and checkpoint_payload.get("nonces_hash") != nonces_hash:
            issues.append("nonces_hash mismatch")
        if checkpoint_payload.get("nonces_hash"):
            stored_nonces_count = checkpoint_payload.get("nonces_count")
            if stored_nonces_count is None or int(stored_nonces_count) != len(nonces):
                issues.append("nonces_count mismatch")
        if checkpoint_payload.get("validators_hash") and checkpoint_payload.get("validators_hash") != validators_hash:
            issues.append("validators_hash mismatch")
        if checkpoint_payload.get("validators_hash"):
            stored_validators_count = checkpoint_payload.get("validators_count")
            if stored_validators_count is None or int(stored_validators_count) != len(validators):
                issues.append("validators_count mismatch")
        if checkpoint_payload.get("protocol_params_hash") and checkpoint_payload.get("protocol_params_hash") != protocol_params_hash:
            issues.append("protocol_params_hash mismatch")
        if checkpoint_payload.get("protocol_params_hash"):
            stored_protocol_params_count = checkpoint_payload.get("protocol_params_count")
            if stored_protocol_params_count is None or int(stored_protocol_params_count) != len(protocol_params):
                issues.append("protocol_params_count mismatch")
        if checkpoint_payload.get("retarget_events_hash") and checkpoint_payload.get("retarget_events_hash") != retarget_events_hash:
            issues.append("retarget_events_hash mismatch")
        if checkpoint_payload.get("retarget_events_hash"):
            stored_retarget_events_count = checkpoint_payload.get("retarget_events_count")
            if stored_retarget_events_count is None or int(stored_retarget_events_count) != len(retarget_events):
                issues.append("retarget_events_count mismatch")
        if checkpoint_payload.get("pending_rewards_hash") and checkpoint_payload.get("pending_rewards_hash") != pending_rewards_hash:
            issues.append("pending_rewards_hash mismatch")
        if checkpoint_payload.get("pending_rewards_hash"):
            stored_pending_rewards_count = checkpoint_payload.get("pending_rewards_count")
            if stored_pending_rewards_count is None or int(stored_pending_rewards_count) != len(pending_rewards):
                issues.append("pending_rewards_count mismatch")
        if checkpoint["snapshot_hash"] != snapshot_hash:
            issues.append("snapshot_hash mismatch")
        if not issues:
            connection.execute(
                "UPDATE canonical_checkpoints SET verified_at = ? WHERE height = ?",
                (_now(), height),
            )
    return {
        "valid": not issues,
        "height": height,
        "checkpoint": checkpoint,
        "issues": issues,
        "computed": {
            "state_root": state_root,
            "balances_hash": balances_hash,
            "snapshot_hash": snapshot_hash,
            "nonces_hash": nonces_hash if checkpoint_payload.get("nonces_hash") else None,
            "nonces_count": len(nonces) if checkpoint_payload.get("nonces_hash") else None,
            "protocol_params_hash": protocol_params_hash if checkpoint_payload.get("protocol_params_hash") else None,
            "protocol_params_count": len(protocol_params) if checkpoint_payload.get("protocol_params_hash") else None,
            "retarget_events_hash": retarget_events_hash if checkpoint_payload.get("retarget_events_hash") else None,
            "retarget_events_count": len(retarget_events) if checkpoint_payload.get("retarget_events_hash") else None,
            "pending_rewards_hash": pending_rewards_hash if checkpoint_payload.get("pending_rewards_hash") else None,
            "pending_rewards_count": len(pending_rewards) if checkpoint_payload.get("pending_rewards_hash") else None,
        },
    }


def export_canonical_snapshot(height: int | None = None) -> dict[str, Any]:
    last_document: dict[str, Any] | None = None
    for _attempt in range(SNAPSHOT_EXPORT_MAX_ATTEMPTS):
        document = _export_canonical_snapshot_once(height)
        if document.get("valid") is True:
            return document
        last_document = document
    return last_document or {}


def _export_canonical_snapshot_once(height: int | None = None) -> dict[str, Any]:
    with get_connection() as connection:
        if height is None:
            latest = connection.execute("SELECT COALESCE(MAX(height), 0) AS height FROM blocks").fetchone()
            height = int(latest["height"] if latest else 0)
        block = row_to_dict(
            connection.execute("SELECT timestamp FROM blocks WHERE height = ?", (int(height),)).fetchone()
        )
        if block is None:
            raise StateError(404, "block not found for snapshot export")
        # Validator registry/reputation and account nonces can change after a
        # checkpoint was first created. Rebuild the checkpoint at export time so
        # the signed hashes match the exact state included in this snapshot.
        checkpoint = create_canonical_checkpoint_in_connection(
            connection,
            int(height),
            trusted=True,
            source="export",
        )
        balances = balance_snapshot(connection, int(height), block["timestamp"])
        nonces = account_nonce_snapshot(connection)
        validators = validator_snapshot(connection)
        protocol_params = protocol_params_snapshot(connection)
        retarget_events = retarget_events_snapshot(connection)
        pending_rewards = pending_rewards_snapshot(connection, int(height))
        account_types = {
            row["account_id"]: row["account_type"]
            for row in connection.execute("SELECT account_id, account_type FROM balances").fetchall()
        }
        export_balances = [
            {
                **item,
                "account_type": account_types.get(item["account_id"], _infer_account_type(item["account_id"])),
            }
            for item in balances
        ]
    document = {
        "snapshot_version": 1,
        "type": "picoin_canonical_snapshot",
        "exported_at": _now(),
        "checkpoint": _checkpoint_public_payload(checkpoint),
        "balances": export_balances,
        "nonces": nonces,
        "validators": validators,
        "protocol_params": protocol_params,
        "retarget_events": retarget_events,
        "pending_rewards": pending_rewards,
    }
    validation = validate_snapshot_document(document)
    document["valid"] = validation["valid"]
    document["issues"] = validation["issues"]
    return document


def import_canonical_snapshot(document: dict[str, Any], *, source: str = "import") -> dict[str, Any]:
    validation = validate_snapshot_document(document)
    if not validation["valid"]:
        raise StateError(422, f"invalid canonical snapshot: {', '.join(validation['issues'])}")
    checkpoint = validation["checkpoint"]
    timestamp = _now()
    import_id = sha256_text(f"{checkpoint['snapshot_hash']}:{source}")[:32]
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO canonical_snapshot_imports (
                import_id, height, block_hash, previous_hash, state_root,
                balances_hash, snapshot_hash, balances_count, total_balance, total_balance_units,
                source, active, activated_at, state_applied, state_applied_at,
                imported_at, verified_at, payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, 0, NULL, ?, ?, ?)
            ON CONFLICT(snapshot_hash) DO UPDATE SET
                source = excluded.source,
                imported_at = excluded.imported_at,
                verified_at = excluded.verified_at,
                payload = excluded.payload
            """,
            (
                import_id,
                checkpoint["height"],
                checkpoint["block_hash"],
                checkpoint["previous_hash"],
                checkpoint["state_root"],
                checkpoint["balances_hash"],
                checkpoint["snapshot_hash"],
                checkpoint["balances_count"],
                checkpoint["total_balance"],
                checkpoint["total_balance_units"],
                source,
                timestamp,
                timestamp,
                json.dumps(document, sort_keys=True),
            ),
        )
        imported = get_imported_snapshot_in_connection(connection, checkpoint["snapshot_hash"])
    return {"imported": True, "snapshot": imported, "validation": validation}


def validate_snapshot_document(document: dict[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    if document.get("type") != "picoin_canonical_snapshot":
        issues.append("invalid snapshot type")
    if int(document.get("snapshot_version") or 0) != 1:
        issues.append("unsupported snapshot version")
    checkpoint = dict(document.get("checkpoint") or {})
    balances = document.get("balances") or []
    nonces = document.get("nonces") or []
    validators = document.get("validators") or []
    protocol_params = document.get("protocol_params") or []
    retarget_events = document.get("retarget_events") or []
    pending_rewards = document.get("pending_rewards") or []
    if not isinstance(balances, list):
        balances = []
        issues.append("balances must be a list")
    if not isinstance(nonces, list):
        nonces = []
        issues.append("nonces must be a list")
    if not isinstance(validators, list):
        validators = []
        issues.append("validators must be a list")
    if not isinstance(protocol_params, list):
        protocol_params = []
        issues.append("protocol_params must be a list")
    if not isinstance(retarget_events, list):
        retarget_events = []
        issues.append("retarget_events must be a list")
    if not isinstance(pending_rewards, list):
        pending_rewards = []
        issues.append("pending_rewards must be a list")
    required = {
        "chain_id",
        "network_id",
        "genesis_hash",
        "protocol_version",
        "height",
        "block_hash",
        "previous_hash",
        "state_root",
        "balances_hash",
        "balances_count",
        "total_balance",
        "snapshot_hash",
    }
    missing = sorted(required - set(checkpoint))
    if missing:
        issues.append(f"missing checkpoint fields: {', '.join(missing)}")
    if checkpoint.get("chain_id") != CHAIN_ID:
        issues.append("chain_id mismatch")
    if checkpoint.get("network_id") != NETWORK_ID:
        issues.append("network_id mismatch")
    if checkpoint.get("genesis_hash") != GENESIS_HASH:
        issues.append("genesis_hash mismatch")

    height = int(checkpoint.get("height") or 0)
    try:
        normalized_balances = _normalize_snapshot_balances(balances)
    except ValueError as exc:
        normalized_balances = []
        issues.append(str(exc))
    try:
        normalized_nonces = _normalize_snapshot_nonces(nonces)
    except ValueError as exc:
        normalized_nonces = []
        issues.append(str(exc))
    try:
        normalized_validators = _normalize_snapshot_validators(validators)
    except ValueError as exc:
        normalized_validators = []
        issues.append(str(exc))
    try:
        normalized_protocol_params = _normalize_snapshot_protocol_params(protocol_params)
    except ValueError as exc:
        normalized_protocol_params = []
        issues.append(str(exc))
    try:
        normalized_retarget_events = _normalize_snapshot_retarget_events(retarget_events)
    except ValueError as exc:
        normalized_retarget_events = []
        issues.append(str(exc))
    try:
        normalized_pending_rewards = _normalize_snapshot_pending_rewards(pending_rewards)
    except ValueError as exc:
        normalized_pending_rewards = []
        issues.append(str(exc))
    for reward in normalized_pending_rewards:
        if int(reward["matures_at_height"] or 0) <= height:
            issues.append("pending reward matures_at_height must be after snapshot height")
            break
    balances_hash = sha256_text(canonical_json({"height": height, "balances": normalized_balances}))
    nonces_hash = sha256_text(canonical_json({"height": height, "nonces": normalized_nonces}))
    validators_hash = sha256_text(canonical_json({"height": height, "validators": normalized_validators}))
    raw_validators_hash = _raw_snapshot_validators_hash(height, validators) if validators else None
    checkpoint_validators_hash = checkpoint.get("validators_hash")
    if checkpoint_validators_hash and checkpoint_validators_hash in {validators_hash, raw_validators_hash}:
        payload_validators_hash = checkpoint_validators_hash
    else:
        payload_validators_hash = validators_hash
    protocol_params_hash = sha256_text(canonical_json({"height": height, "protocol_params": normalized_protocol_params}))
    retarget_events_hash = sha256_text(canonical_json({"height": height, "retarget_events": normalized_retarget_events}))
    pending_rewards_hash = sha256_text(canonical_json({"height": height, "pending_rewards": normalized_pending_rewards}))
    total_balance_units = sum(int(item["balance_units"]) for item in normalized_balances)
    total_balance = units_to_float(total_balance_units)
    payload = {
        "chain_id": checkpoint.get("chain_id"),
        "network_id": checkpoint.get("network_id"),
        "genesis_hash": checkpoint.get("genesis_hash"),
        "protocol_version": checkpoint.get("protocol_version"),
        "height": height,
        "block_hash": checkpoint.get("block_hash"),
        "previous_hash": checkpoint.get("previous_hash"),
        "state_root": checkpoint.get("state_root"),
        "balances_hash": balances_hash,
        "balances_count": len(normalized_balances),
        "ledger_entries_count": int(checkpoint.get("ledger_entries_count") or 0),
        "total_balance": total_balance,
        "total_balance_units": total_balance_units,
    }
    if checkpoint.get("nonces_hash") or normalized_nonces:
        payload["nonces_hash"] = nonces_hash
        payload["nonces_count"] = len(normalized_nonces)
    if checkpoint_validators_hash or normalized_validators:
        payload["validators_hash"] = payload_validators_hash
        payload["validators_count"] = len(normalized_validators)
    if checkpoint.get("protocol_params_hash") or normalized_protocol_params:
        payload["protocol_params_hash"] = protocol_params_hash
        payload["protocol_params_count"] = len(normalized_protocol_params)
    if checkpoint.get("retarget_events_hash") or normalized_retarget_events:
        payload["retarget_events_hash"] = retarget_events_hash
        payload["retarget_events_count"] = len(normalized_retarget_events)
    if checkpoint.get("pending_rewards_hash") or normalized_pending_rewards:
        payload["pending_rewards_hash"] = pending_rewards_hash
        payload["pending_rewards_count"] = len(normalized_pending_rewards)
    snapshot_hash = sha256_text(canonical_json(payload))
    if checkpoint.get("balances_hash") != balances_hash:
        issues.append("balances_hash mismatch")
    if checkpoint.get("state_root") != balances_hash:
        issues.append("state_root mismatch")
    if int(checkpoint.get("balances_count") or -1) != len(normalized_balances):
        issues.append("balances_count mismatch")
    if checkpoint.get("nonces_hash") and checkpoint.get("nonces_hash") != nonces_hash:
        issues.append("nonces_hash mismatch")
    if checkpoint.get("nonces_hash"):
        stored_nonces_count = checkpoint.get("nonces_count")
        if stored_nonces_count is None or int(stored_nonces_count) != len(normalized_nonces):
            issues.append("nonces_count mismatch")
    if checkpoint_validators_hash and checkpoint_validators_hash not in {validators_hash, raw_validators_hash}:
        issues.append("validators_hash mismatch")
    if checkpoint_validators_hash:
        stored_validators_count = checkpoint.get("validators_count")
        if stored_validators_count is None or int(stored_validators_count) != len(normalized_validators):
            issues.append("validators_count mismatch")
    if checkpoint.get("protocol_params_hash") and checkpoint.get("protocol_params_hash") != protocol_params_hash:
        issues.append("protocol_params_hash mismatch")
    if checkpoint.get("protocol_params_hash"):
        stored_protocol_params_count = checkpoint.get("protocol_params_count")
        if stored_protocol_params_count is None or int(stored_protocol_params_count) != len(normalized_protocol_params):
            issues.append("protocol_params_count mismatch")
    if checkpoint.get("retarget_events_hash") and checkpoint.get("retarget_events_hash") != retarget_events_hash:
        issues.append("retarget_events_hash mismatch")
    if checkpoint.get("retarget_events_hash"):
        stored_retarget_events_count = checkpoint.get("retarget_events_count")
        if stored_retarget_events_count is None or int(stored_retarget_events_count) != len(normalized_retarget_events):
            issues.append("retarget_events_count mismatch")
    if checkpoint.get("pending_rewards_hash") and checkpoint.get("pending_rewards_hash") != pending_rewards_hash:
        issues.append("pending_rewards_hash mismatch")
    if checkpoint.get("pending_rewards_hash"):
        stored_pending_rewards_count = checkpoint.get("pending_rewards_count")
        if stored_pending_rewards_count is None or int(stored_pending_rewards_count) != len(normalized_pending_rewards):
            issues.append("pending_rewards_count mismatch")
    if int(checkpoint.get("total_balance_units") or to_units(checkpoint.get("total_balance") or 0)) != total_balance_units:
        issues.append("total_balance mismatch")
    if checkpoint.get("snapshot_hash") != snapshot_hash:
        issues.append("snapshot_hash mismatch")
    return {
        "valid": not issues,
        "issues": issues,
        "checkpoint": {
            **checkpoint,
            "height": height,
            "balances_count": len(normalized_balances),
            "total_balance": total_balance,
            "total_balance_units": total_balance_units,
            "nonces_hash": checkpoint.get("nonces_hash") or (nonces_hash if normalized_nonces else None),
            "nonces_count": int(
                checkpoint["nonces_count"] if checkpoint.get("nonces_count") is not None else len(normalized_nonces)
            ),
            "validators_hash": checkpoint_validators_hash or (validators_hash if normalized_validators else None),
            "validators_count": int(
                checkpoint["validators_count"] if checkpoint.get("validators_count") is not None else len(normalized_validators)
            ),
            "protocol_params_hash": checkpoint.get("protocol_params_hash") or (protocol_params_hash if normalized_protocol_params else None),
            "protocol_params_count": int(
                checkpoint["protocol_params_count"]
                if checkpoint.get("protocol_params_count") is not None
                else len(normalized_protocol_params)
            ),
            "retarget_events_hash": checkpoint.get("retarget_events_hash") or (retarget_events_hash if normalized_retarget_events else None),
            "retarget_events_count": int(
                checkpoint["retarget_events_count"]
                if checkpoint.get("retarget_events_count") is not None
                else len(normalized_retarget_events)
            ),
            "pending_rewards_hash": checkpoint.get("pending_rewards_hash") or (pending_rewards_hash if normalized_pending_rewards else None),
            "pending_rewards_count": int(
                checkpoint["pending_rewards_count"]
                if checkpoint.get("pending_rewards_count") is not None
                else len(normalized_pending_rewards)
            ),
        },
        "computed": {
            "balances_hash": balances_hash,
            "state_root": balances_hash,
            "snapshot_hash": snapshot_hash,
            "balances_count": len(normalized_balances),
            "total_balance": total_balance,
            "total_balance_units": total_balance_units,
            "nonces_hash": nonces_hash if normalized_nonces else None,
            "nonces_count": len(normalized_nonces),
            "validators_hash": payload_validators_hash if normalized_validators else None,
            "normalized_validators_hash": validators_hash if normalized_validators else None,
            "raw_validators_hash": raw_validators_hash,
            "validators_count": len(normalized_validators),
            "protocol_params_hash": protocol_params_hash if normalized_protocol_params else None,
            "protocol_params_count": len(normalized_protocol_params),
            "retarget_events_hash": retarget_events_hash if normalized_retarget_events else None,
            "retarget_events_count": len(normalized_retarget_events),
            "pending_rewards_hash": pending_rewards_hash if normalized_pending_rewards else None,
            "pending_rewards_count": len(normalized_pending_rewards),
        },
    }


def activate_imported_snapshot(snapshot_hash: str) -> dict[str, Any]:
    timestamp = _now()
    with get_connection() as connection:
        row = connection.execute(
            "SELECT snapshot_hash FROM canonical_snapshot_imports WHERE snapshot_hash = ?",
            (snapshot_hash,),
        ).fetchone()
        if row is None:
            raise StateError(404, "imported snapshot not found")
        connection.execute("UPDATE canonical_snapshot_imports SET active = 0")
        connection.execute(
            """
            UPDATE canonical_snapshot_imports
            SET active = 1, activated_at = ?
            WHERE snapshot_hash = ?
            """,
            (timestamp, snapshot_hash),
        )
        active = get_imported_snapshot_in_connection(connection, snapshot_hash)
    return active or {}


def apply_imported_snapshot_state(snapshot_hash: str, *, replace_existing: bool = False) -> dict[str, Any]:
    timestamp = _now()
    with get_connection() as connection:
        imported = get_imported_snapshot_in_connection(connection, snapshot_hash)
        if imported is None:
            raise StateError(404, "imported snapshot not found")
        local_blocks = int(
            connection.execute("SELECT COUNT(*) AS count FROM blocks").fetchone()["count"]
        )
        if local_blocks > 0 and not replace_existing:
            raise StateError(409, "cannot apply snapshot state after local blocks exist")
        document = imported["payload"]
        validation = validate_snapshot_document(document)
        if not validation["valid"]:
            raise StateError(422, f"invalid imported snapshot: {', '.join(validation['issues'])}")
        balances = _normalize_snapshot_balances_with_type(document.get("balances") or [])
        nonces = _normalize_snapshot_nonces(document.get("nonces") or [])
        validators = _normalize_snapshot_validators(document.get("validators") or [])
        protocol_params = _normalize_snapshot_protocol_params(document.get("protocol_params") or [])
        retarget_events = _normalize_snapshot_retarget_events(document.get("retarget_events") or [])
        pending_rewards = _normalize_snapshot_pending_rewards(document.get("pending_rewards") or [])
        checkpoint = validation["checkpoint"]

        if pending_rewards:
            connection.execute("PRAGMA foreign_keys = OFF")
        cleared = _clear_local_chain_state_for_snapshot_restore(connection) if replace_existing else {}
        if protocol_params:
            connection.execute("DELETE FROM difficulty_bucket_metrics")
            connection.execute("DELETE FROM retarget_events")
            connection.execute("DELETE FROM protocol_params")
        connection.execute("DELETE FROM rewards")
        connection.execute("DELETE FROM ledger_entries")
        connection.execute("DELETE FROM balances")
        connection.execute("DELETE FROM account_nonces")
        if validators:
            connection.execute("DELETE FROM validators")
        validators_by_id = {validator["validator_id"]: validator for validator in validators}
        for item in balances:
            balance_units = int(item["balance_units"])
            balance = units_to_float(balance_units)
            connection.execute(
                """
                INSERT INTO balances (account_id, account_type, balance, balance_units, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (item["account_id"], item["account_type"], balance, balance_units, timestamp),
            )
            _restore_snapshot_balance_ledger_entries(
                connection,
                item,
                validators_by_id,
                int(checkpoint["height"]),
                snapshot_hash,
                timestamp,
            )
        for item in nonces:
            connection.execute(
                """
                INSERT INTO account_nonces (account_id, nonce, updated_at)
                VALUES (?, ?, ?)
                """,
                (item["account_id"], int(item["nonce"]), timestamp),
            )
        for params in protocol_params:
            _restore_snapshot_protocol_params(connection, params)
        for event in retarget_events:
            _restore_snapshot_retarget_event(connection, event)
        for reward in pending_rewards:
            _restore_snapshot_pending_reward(connection, reward)
        for validator in validators:
            _restore_snapshot_validator(connection, validator, timestamp)
        connection.execute("UPDATE canonical_snapshot_imports SET active = 0")
        connection.execute(
            """
            UPDATE canonical_snapshot_imports
            SET active = 1,
                activated_at = COALESCE(activated_at, ?),
                state_applied = 1,
                state_applied_at = ?
            WHERE snapshot_hash = ?
            """,
            (timestamp, timestamp, snapshot_hash),
        )
        applied = get_imported_snapshot_in_connection(connection, snapshot_hash)
    return {
        "applied": True,
        "replace_existing": replace_existing,
        "height": checkpoint["height"],
        "snapshot_hash": snapshot_hash,
        "balances_applied": len(balances),
        "nonces_applied": len(nonces),
        "validators_applied": len(validators),
        "protocol_params_applied": len(protocol_params),
        "retarget_events_applied": len(retarget_events),
        "pending_rewards_applied": len(pending_rewards),
        "cleared": cleared,
        "snapshot": applied,
    }


def restore_imported_snapshot_state(snapshot_hash: str) -> dict[str, Any]:
    return apply_imported_snapshot_state(snapshot_hash, replace_existing=True)


def active_snapshot_base() -> dict[str, Any] | None:
    with get_connection() as connection:
        return active_snapshot_base_in_connection(connection)


def active_snapshot_base_in_connection(connection: Any) -> dict[str, Any] | None:
    row = row_to_dict(
        connection.execute(
            """
            SELECT *
            FROM canonical_snapshot_imports
            WHERE active = 1
            ORDER BY height DESC, activated_at DESC
            LIMIT 1
            """
        ).fetchone()
    )
    return _decode_snapshot_import(row)


def list_imported_snapshots(limit: int = 50) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM canonical_snapshot_imports ORDER BY height DESC, imported_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_decode_snapshot_import(row_to_dict(row)) for row in rows]


def get_imported_snapshot(snapshot_hash: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        return get_imported_snapshot_in_connection(connection, snapshot_hash)


def get_imported_snapshot_in_connection(connection: Any, snapshot_hash: str) -> dict[str, Any] | None:
    row = row_to_dict(
        connection.execute(
            "SELECT * FROM canonical_snapshot_imports WHERE snapshot_hash = ?",
            (snapshot_hash,),
        ).fetchone()
    )
    return _decode_snapshot_import(row)


def _clear_local_chain_state_for_snapshot_restore(connection: Any) -> dict[str, int]:
    foreign_keys_enabled = bool(connection.execute("PRAGMA foreign_keys").fetchone()[0])
    if foreign_keys_enabled:
        connection.execute("PRAGMA foreign_keys = OFF")
    try:
        tables = [
            "consensus_finalizations",
            "consensus_votes",
            "consensus_block_proposals",
            "network_block_headers",
            "canonical_checkpoints",
            "mempool_transactions",
            "account_nonces",
            "retroactive_audits",
            "validation_votes",
            "validation_jobs",
            "commitments",
            "submissions",
            "rejected_submissions",
            "penalties",
            "rewards",
            "blocks",
            "tasks",
            "science_jobs",
            "science_stake_accounts",
            "science_reward_reserve",
            "scientific_development_treasury_claims",
            "scientific_development_treasury_epochs",
            "scientific_development_treasury",
            "science_events",
        ]
        cleared: dict[str, int] = {}
        failures: list[str] = []
        for table in tables:
            try:
                cursor = connection.execute(f"DELETE FROM {table}")
                cleared[table] = int(cursor.rowcount if cursor.rowcount is not None else 0)
            except Exception:
                cleared[table] = -1
                failures.append(table)
        if failures:
            raise StateError(500, f"snapshot restore cleanup failed for: {', '.join(failures)}")
        return cleared
    finally:
        if foreign_keys_enabled:
            connection.execute("PRAGMA foreign_keys = ON")


def _checkpoint_public_payload(checkpoint: dict[str, Any]) -> dict[str, Any]:
    payload = dict(checkpoint.get("payload") or {})
    payload["snapshot_hash"] = checkpoint["snapshot_hash"]
    payload["ledger_entries_count"] = int(checkpoint.get("ledger_entries_count") or payload.get("ledger_entries_count") or 0)
    return payload


def _normalize_snapshot_balances(balances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], int] = {}
    for item in balances:
        account_id = str(item.get("account_id") or "").strip()
        if not account_id:
            continue
        account_type = str(item.get("account_type") or _infer_account_type(account_id)).strip() or "wallet"
        balance_units = units_from_db(item.get("balance") or 0, item.get("balance_units"))
        key = (account_id, account_type)
        merged[key] = merged.get(key, 0) + balance_units
    return [
        {
            "account_id": account_id,
            "account_type": account_type,
            "balance": canonical_amount(balance_units),
            "balance_units": balance_units,
        }
        for (account_id, account_type), balance_units in sorted(merged.items(), key=lambda item: (item[0][0], item[0][1]))
        if balance_units != 0
    ]


def _normalize_snapshot_nonces(nonces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, int] = {}
    for item in nonces:
        account_id = str(item.get("account_id") or "").strip()
        if not account_id:
            continue
        try:
            nonce = int(item.get("nonce") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid nonce for {account_id}") from exc
        if nonce < 0:
            raise ValueError(f"negative nonce for {account_id}")
        if nonce == 0:
            continue
        merged[account_id] = max(merged.get(account_id, 0), nonce)
    return [{"account_id": account_id, "nonce": nonce} for account_id, nonce in sorted(merged.items())]


def _normalize_snapshot_balances_with_type(balances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in balances:
        account_id = str(item.get("account_id") or "").strip()
        if not account_id:
            continue
        balance_units = units_from_db(item.get("balance") or 0, item.get("balance_units"))
        account_type = str(item.get("account_type") or _infer_account_type(account_id)).strip() or "wallet"
        existing = merged.get(account_id)
        if existing is None:
            merged[account_id] = {
                "account_id": account_id,
                "account_type": account_type,
                "balance_units": balance_units,
            }
        else:
            existing["balance_units"] = int(existing["balance_units"]) + balance_units
    return [
        {
            **item,
            "balance": canonical_amount(int(item["balance_units"])),
        }
        for _, item in sorted(merged.items(), key=lambda item: (item[1]["account_id"], item[1]["account_type"]))
        if int(item["balance_units"]) != 0
    ]


def _normalize_snapshot_validators(validators: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for item in validators:
        validator = _normalize_validator_snapshot_row(item)
        if validator["validator_id"]:
            normalized[validator["validator_id"]] = validator
    return [normalized[key] for key in sorted(normalized)]


def _raw_snapshot_validators_hash(height: int, validators: list[dict[str, Any]]) -> str | None:
    raw: dict[str, dict[str, Any]] = {}
    for item in validators:
        if not isinstance(item, dict):
            continue
        validator_id = str(item.get("validator_id") or "").strip()
        if validator_id:
            raw[validator_id] = dict(item)
    if not raw:
        return None
    raw_validators = [raw[key] for key in sorted(raw)]
    return sha256_text(canonical_json({"height": height, "validators": raw_validators}))


def _normalize_snapshot_protocol_params(protocol_params: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: dict[int, dict[str, Any]] = {}
    active_count = 0
    for item in protocol_params:
        params = _normalize_protocol_params_snapshot_row(item)
        params_id = int(params["id"])
        if params_id in normalized:
            raise ValueError(f"protocol params snapshot has duplicate id {params_id}")
        normalized[params_id] = params
        active_count += int(params["active"])
    if normalized and active_count != 1:
        raise ValueError("protocol params snapshot must contain exactly one active row")
    return [normalized[key] for key in sorted(normalized)]


def _normalize_snapshot_retarget_events(retarget_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: dict[int, dict[str, Any]] = {}
    for item in retarget_events:
        event = _normalize_retarget_event_snapshot_row(item)
        event_id = int(event["id"])
        if event_id in normalized:
            raise ValueError(f"retarget event snapshot has duplicate id {event_id}")
        normalized[event_id] = event
    return [normalized[key] for key in sorted(normalized)]


def _normalize_snapshot_pending_rewards(pending_rewards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: dict[int, dict[str, Any]] = {}
    for item in pending_rewards:
        reward = _normalize_pending_reward_snapshot_row(item)
        reward_id = int(reward["id"])
        if reward_id in normalized:
            raise ValueError(f"pending reward snapshot has duplicate id {reward_id}")
        normalized[reward_id] = reward
    return [normalized[key] for key in sorted(normalized)]


def _normalize_validator_snapshot_row(row: Any) -> dict[str, Any]:
    item = dict(row)
    validator_id = str(item.get("validator_id") or "").strip()
    if not validator_id:
        raise ValueError("validator snapshot entry missing validator_id")
    stake_units = units_from_db(item.get("stake_locked") or 0, item.get("stake_locked_units"))
    wallet_stake_units = units_from_db(item.get("wallet_stake_locked") or 0, item.get("wallet_stake_locked_units"))
    slashed_units = units_from_db(item.get("slashed_amount") or 0, item.get("slashed_amount_units"))
    return {
        "validator_id": validator_id,
        "name": str(item.get("name") or validator_id).strip() or validator_id,
        "public_key": str(item.get("public_key") or "").strip(),
        "reward_address": str(item.get("reward_address") or "").strip() or None,
        "registered_at": str(item.get("registered_at") or "").strip(),
        "accepted_jobs": max(0, int(item.get("accepted_jobs") or 0)),
        "rejected_jobs": max(0, int(item.get("rejected_jobs") or 0)),
        "invalid_results": max(0, int(item.get("invalid_results") or 0)),
        "trust_score": round(float(item.get("trust_score") if item.get("trust_score") is not None else 1.0), 8),
        "cooldown_until": str(item.get("cooldown_until") or "").strip() or None,
        "total_validation_ms": max(0, int(item.get("total_validation_ms") or 0)),
        "stake_locked": canonical_amount(stake_units),
        "stake_locked_units": stake_units,
        "wallet_stake_locked": canonical_amount(wallet_stake_units),
        "wallet_stake_locked_units": wallet_stake_units,
        "stake_owner_address": str(item.get("stake_owner_address") or "").strip() or None,
        "slashed_amount": canonical_amount(slashed_units),
        "slashed_amount_units": slashed_units,
        "is_banned": 1 if bool(item.get("is_banned")) else 0,
        "enabled": 1 if bool(item.get("enabled", 1)) else 0,
        "protocol_version": str(item.get("protocol_version") or PROTOCOL_VERSION).strip() or PROTOCOL_VERSION,
    }


def _normalize_protocol_params_snapshot_row(row: Any) -> dict[str, Any]:
    item = row_to_dict(row) if not isinstance(row, dict) else dict(row)
    params_id = _snapshot_required_int(item, "id", "protocol params snapshot entry missing id")
    if params_id <= 0:
        raise ValueError("protocol params snapshot entry missing id")
    return {
        "id": params_id,
        "protocol_version": str(item.get("protocol_version") or PROTOCOL_VERSION).strip() or PROTOCOL_VERSION,
        "algorithm": str(item.get("algorithm") or "").strip(),
        "validation_mode": str(item.get("validation_mode") or "").strip(),
        "required_validator_approvals": _snapshot_required_int(item, "required_validator_approvals"),
        "range_assignment_mode": str(item.get("range_assignment_mode") or "").strip(),
        "max_pi_position": _snapshot_required_int(item, "max_pi_position"),
        "range_assignment_max_attempts": _snapshot_required_int(item, "range_assignment_max_attempts"),
        "segment_size": _snapshot_required_int(item, "segment_size"),
        "sample_count": _snapshot_required_int(item, "sample_count"),
        "task_expiration_seconds": _snapshot_required_int(item, "task_expiration_seconds"),
        "max_active_tasks_per_miner": _snapshot_required_int(item, "max_active_tasks_per_miner"),
        "base_reward": _snapshot_required_float(item, "base_reward"),
        "difficulty": _snapshot_optional_float(item.get("difficulty")),
        "RETARGET_MAX_PI_POSITION": _snapshot_optional_int(item.get("RETARGET_MAX_PI_POSITION")),
        "target_block_time_ms": _snapshot_optional_int(item.get("target_block_time_ms")),
        "retarget_reason": str(item.get("retarget_reason") or "").strip() or None,
        "retarget_source_window": _snapshot_optional_int(item.get("retarget_source_window")),
        "retarget_source_details": str(item.get("retarget_source_details") or "").strip() or None,
        "previous_protocol_params_id": _snapshot_optional_int(item.get("previous_protocol_params_id")),
        "active": 1 if _snapshot_bool(item.get("active", 1)) else 0,
        "created_at": str(item.get("created_at") or "").strip(),
    }


def _normalize_retarget_event_snapshot_row(row: Any) -> dict[str, Any]:
    item = row_to_dict(row) if not isinstance(row, dict) else dict(row)
    event_id = _snapshot_required_int(item, "id", "retarget event snapshot entry missing id")
    if event_id <= 0:
        raise ValueError("retarget event snapshot entry missing id")
    return {
        "id": event_id,
        "previous_protocol_params_id": _snapshot_optional_int(item.get("previous_protocol_params_id")),
        "new_protocol_params_id": _snapshot_optional_int(item.get("new_protocol_params_id")),
        "epoch_start_height": _snapshot_required_int(item, "epoch_start_height"),
        "epoch_end_height": _snapshot_required_int(item, "epoch_end_height"),
        "epoch_block_count": _snapshot_required_int(item, "epoch_block_count"),
        "average_block_ms": _snapshot_required_float(item, "average_block_ms"),
        "target_block_ms": _snapshot_required_int(item, "target_block_ms"),
        "old_difficulty": _snapshot_required_float(item, "old_difficulty"),
        "new_difficulty": _snapshot_required_float(item, "new_difficulty"),
        "adjustment_factor": _snapshot_required_float(item, "adjustment_factor"),
        "action": str(item.get("action") or "").strip(),
        "reason": str(item.get("reason") or "").strip(),
        "created_at": str(item.get("created_at") or "").strip(),
    }


def _normalize_pending_reward_snapshot_row(row: Any) -> dict[str, Any]:
    item = row_to_dict(row) if not isinstance(row, dict) else dict(row)
    reward_id = _snapshot_required_int(item, "id", "pending reward snapshot entry missing id")
    if reward_id <= 0:
        raise ValueError("pending reward snapshot entry missing id")
    miner_id = str(item.get("miner_id") or "").strip()
    if not miner_id:
        raise ValueError("pending reward snapshot entry missing miner_id")
    block_height = _snapshot_required_int(item, "block_height")
    amount_units = units_from_db(item.get("amount") or 0, item.get("amount_units"))
    account_id = str(item.get("account_id") or "").strip() or None
    account_type = str(item.get("account_type") or "").strip() or None
    related_id = str(item.get("related_id") or "").strip() or None
    return {
        "id": reward_id,
        "miner_id": miner_id,
        "block_height": block_height,
        "amount": canonical_amount(amount_units),
        "amount_units": amount_units,
        "account_id": account_id,
        "account_type": account_type,
        "status": "immature",
        "matures_at_height": _snapshot_required_int(item, "matures_at_height"),
        "matured_at": None,
        "orphaned_at": str(item.get("orphaned_at") or "").strip() or None,
        "orphan_reason": str(item.get("orphan_reason") or "").strip() or None,
        "related_id": related_id,
        "reason": str(item.get("reason") or "block reward").strip() or "block reward",
        "created_at": str(item.get("created_at") or "").strip(),
    }


def _snapshot_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _snapshot_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _snapshot_required_int(item: dict[str, Any], key: str, message: str | None = None) -> int:
    value = item.get(key)
    if value is None or value == "":
        raise ValueError(message or f"protocol snapshot entry missing {key}")
    return int(value)


def _snapshot_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return round(float(value), 8)


def _snapshot_required_float(item: dict[str, Any], key: str) -> float:
    value = item.get(key)
    if value is None or value == "":
        raise ValueError(f"protocol snapshot entry missing {key}")
    return round(float(value), 8)


def _restore_snapshot_protocol_params(connection: Any, params: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO protocol_params (
            id, protocol_version, algorithm, validation_mode, required_validator_approvals,
            range_assignment_mode, max_pi_position, range_assignment_max_attempts,
            segment_size, sample_count, task_expiration_seconds, max_active_tasks_per_miner,
            base_reward, difficulty, RETARGET_MAX_PI_POSITION, target_block_time_ms,
            retarget_reason, retarget_source_window, retarget_source_details,
            previous_protocol_params_id, active, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            params["id"],
            params["protocol_version"],
            params["algorithm"],
            params["validation_mode"],
            params["required_validator_approvals"],
            params["range_assignment_mode"],
            params["max_pi_position"],
            params["range_assignment_max_attempts"],
            params["segment_size"],
            params["sample_count"],
            params["task_expiration_seconds"],
            params["max_active_tasks_per_miner"],
            params["base_reward"],
            params["difficulty"],
            params["RETARGET_MAX_PI_POSITION"],
            params["target_block_time_ms"],
            params["retarget_reason"],
            params["retarget_source_window"],
            params["retarget_source_details"],
            params["previous_protocol_params_id"],
            params["active"],
            params["created_at"] or _now(),
        ),
    )


def _restore_snapshot_retarget_event(connection: Any, event: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO retarget_events (
            id, previous_protocol_params_id, new_protocol_params_id,
            epoch_start_height, epoch_end_height, epoch_block_count,
            average_block_ms, target_block_ms, old_difficulty, new_difficulty,
            adjustment_factor, action, reason, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event["id"],
            event["previous_protocol_params_id"],
            event["new_protocol_params_id"],
            event["epoch_start_height"],
            event["epoch_end_height"],
            event["epoch_block_count"],
            event["average_block_ms"],
            event["target_block_ms"],
            event["old_difficulty"],
            event["new_difficulty"],
            event["adjustment_factor"],
            event["action"],
            event["reason"],
            event["created_at"] or _now(),
        ),
    )


def _restore_snapshot_pending_reward(connection: Any, reward: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO rewards (
            id, miner_id, block_height, amount, amount_units, account_id, account_type,
            status, matures_at_height, matured_at, orphaned_at, orphan_reason,
            related_id, reason, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'immature', ?, NULL, ?, ?, ?, ?, ?)
        """,
        (
            reward["id"],
            reward["miner_id"],
            reward["block_height"],
            units_to_float(int(reward["amount_units"])),
            reward["amount_units"],
            reward["account_id"],
            reward["account_type"],
            reward["matures_at_height"],
            reward["orphaned_at"],
            reward["orphan_reason"],
            reward["related_id"],
            reward["reason"],
            reward["created_at"] or _now(),
        ),
    )


def _restore_snapshot_validator(connection: Any, validator: dict[str, Any], timestamp: str) -> None:
    stake_locked = units_to_float(int(validator["stake_locked_units"]))
    wallet_stake_locked = units_to_float(int(validator["wallet_stake_locked_units"]))
    slashed_amount = units_to_float(int(validator["slashed_amount_units"]))
    connection.execute(
        """
        INSERT INTO validators (
            validator_id, name, public_key, reward_address, registered_at,
            accepted_jobs, rejected_jobs, invalid_results, trust_score, cooldown_until,
            last_seen_at, total_validation_ms, stake_locked, wallet_stake_locked,
            stake_owner_address, slashed_amount, is_banned, enabled, protocol_version,
            online_status, sync_status, effective_height, sync_lag, pending_replay_blocks
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 'offline', 'unknown', 0, 0, 0)
        ON CONFLICT(validator_id) DO UPDATE SET
            name = excluded.name,
            public_key = excluded.public_key,
            reward_address = excluded.reward_address,
            registered_at = excluded.registered_at,
            accepted_jobs = excluded.accepted_jobs,
            rejected_jobs = excluded.rejected_jobs,
            invalid_results = excluded.invalid_results,
            trust_score = excluded.trust_score,
            cooldown_until = excluded.cooldown_until,
            total_validation_ms = excluded.total_validation_ms,
            stake_locked = excluded.stake_locked,
            wallet_stake_locked = excluded.wallet_stake_locked,
            stake_owner_address = excluded.stake_owner_address,
            slashed_amount = excluded.slashed_amount,
            is_banned = excluded.is_banned,
            enabled = excluded.enabled,
            protocol_version = excluded.protocol_version,
            online_status = 'offline',
            sync_status = 'unknown',
            effective_height = 0,
            sync_lag = 0,
            pending_replay_blocks = 0,
            last_seen_at = NULL,
            reason_if_not_eligible = NULL
        """,
        (
            validator["validator_id"],
            validator["name"],
            validator["public_key"],
            validator["reward_address"],
            validator["registered_at"] or timestamp,
            validator["accepted_jobs"],
            validator["rejected_jobs"],
            validator["invalid_results"],
            validator["trust_score"],
            validator["cooldown_until"],
            validator["total_validation_ms"],
            stake_locked,
            wallet_stake_locked,
            validator["stake_owner_address"],
            slashed_amount,
            validator["is_banned"],
            validator["enabled"],
            validator["protocol_version"],
        ),
    )


def _restore_snapshot_balance_ledger_entries(
    connection: Any,
    item: dict[str, Any],
    validators_by_id: dict[str, dict[str, Any]],
    block_height: int,
    snapshot_hash: str,
    timestamp: str,
) -> None:
    account_id = item["account_id"]
    account_type = item["account_type"]
    balance_units = int(item["balance_units"])
    balance = units_to_float(balance_units)
    if account_type != "validator":
        _insert_snapshot_balance_ledger_entry(
            connection,
            account_id,
            account_type,
            balance_units,
            balance_units,
            "snapshot_state_import",
            block_height,
            snapshot_hash,
            "canonical snapshot state import",
            timestamp,
        )
        return

    validator = validators_by_id.get(account_id)
    wallet_stake_units = min(
        balance_units,
        int(validator.get("wallet_stake_locked_units") or 0) if validator else 0,
    )
    remaining_units = balance_units - wallet_stake_units
    running_balance_units = 0
    if wallet_stake_units:
        running_balance_units += wallet_stake_units
        _insert_snapshot_balance_ledger_entry(
            connection,
            account_id,
            account_type,
            wallet_stake_units,
            running_balance_units,
            "validator_stake_lock",
            block_height,
            snapshot_hash,
            "canonical snapshot validator wallet stake import",
            timestamp,
        )
    if remaining_units:
        running_balance_units += remaining_units
        _insert_snapshot_balance_ledger_entry(
            connection,
            account_id,
            account_type,
            remaining_units,
            running_balance_units,
            "snapshot_state_import",
            block_height,
            snapshot_hash,
            "canonical snapshot validator state import",
            timestamp,
        )


def _insert_snapshot_balance_ledger_entry(
    connection: Any,
    account_id: str,
    account_type: str,
    amount_units: int,
    balance_after_units: int,
    entry_type: str,
    block_height: int,
    snapshot_hash: str,
    description: str,
    timestamp: str,
) -> None:
    amount = units_to_float(amount_units)
    balance_after = units_to_float(balance_after_units)
    connection.execute(
        """
        INSERT INTO ledger_entries (
            account_id, account_type, amount, amount_units, balance_after, balance_after_units, entry_type,
            block_height, related_id, description, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account_id,
            account_type,
            amount,
            amount_units,
            balance_after,
            balance_after_units,
            entry_type,
            block_height,
            snapshot_hash,
            description,
            timestamp,
        ),
    )


def _infer_account_type(account_id: str) -> str:
    if account_id == "genesis":
        return "genesis"
    if account_id == "science_compute_reserve":
        return "science_reserve"
    if account_id == "scientific_development_treasury":
        return "scientific_development_treasury"
    if account_id == "audit_treasury":
        return "audit"
    if account_id.startswith("science_stake:"):
        return "science_stake"
    if account_id.startswith("PI"):
        return "wallet"
    if account_id.startswith("validator"):
        return "validator"
    return "miner"


def _decode_checkpoint(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    row["trusted"] = bool(row.get("trusted"))
    row["payload"] = json.loads(row.get("payload") or "{}")
    row["height"] = int(row["height"])
    row["balances_count"] = int(row["balances_count"])
    row["ledger_entries_count"] = int(row["ledger_entries_count"])
    row["total_balance_units"] = int(row.get("total_balance_units") or to_units(row["total_balance"] or 0))
    row["total_balance"] = round(float(row["total_balance"] or 0), 8)
    return row


def _decode_snapshot_import(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    row["height"] = int(row["height"])
    row["balances_count"] = int(row["balances_count"])
    row["total_balance_units"] = int(row.get("total_balance_units") or to_units(row["total_balance"] or 0))
    row["total_balance"] = round(float(row["total_balance"] or 0), 8)
    row["active"] = bool(row.get("active", 0))
    row["state_applied"] = bool(row.get("state_applied", 0))
    row["payload"] = json.loads(row.get("payload") or "{}")
    return row
