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
        },
    }


def export_canonical_snapshot(height: int | None = None) -> dict[str, Any]:
    with get_connection() as connection:
        if height is None:
            latest = connection.execute("SELECT COALESCE(MAX(height), 0) AS height FROM blocks").fetchone()
            height = int(latest["height"] if latest else 0)
        checkpoint = get_checkpoint_in_connection(connection, int(height))
        if checkpoint is None or not (checkpoint.get("payload") or {}).get("nonces_hash"):
            checkpoint = create_canonical_checkpoint_in_connection(
                connection,
                int(height),
                trusted=True,
                source="export",
            )
        block = row_to_dict(
            connection.execute("SELECT timestamp FROM blocks WHERE height = ?", (int(height),)).fetchone()
        )
        if block is None:
            raise StateError(404, "block not found for snapshot export")
        balances = balance_snapshot(connection, int(height), block["timestamp"])
        nonces = account_nonce_snapshot(connection)
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
    if not isinstance(balances, list):
        balances = []
        issues.append("balances must be a list")
    if not isinstance(nonces, list):
        nonces = []
        issues.append("nonces must be a list")
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
    balances_hash = sha256_text(canonical_json({"height": height, "balances": normalized_balances}))
    nonces_hash = sha256_text(canonical_json({"height": height, "nonces": normalized_nonces}))
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
        checkpoint = validation["checkpoint"]

        cleared = _clear_local_chain_state_for_snapshot_restore(connection) if replace_existing else {}
        connection.execute("DELETE FROM ledger_entries")
        connection.execute("DELETE FROM balances")
        connection.execute("DELETE FROM account_nonces")
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
            connection.execute(
                """
                INSERT INTO ledger_entries (
                    account_id, account_type, amount, amount_units, balance_after, balance_after_units, entry_type,
                    block_height, related_id, description, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'snapshot_state_import', ?, ?, 'canonical snapshot state import', ?)
                """,
                (
                    item["account_id"],
                    item["account_type"],
                    balance,
                    balance_units,
                    balance,
                    balance_units,
                    checkpoint["height"],
                    snapshot_hash,
                    timestamp,
                ),
            )
        for item in nonces:
            connection.execute(
                """
                INSERT INTO account_nonces (account_id, nonce, updated_at)
                VALUES (?, ?, ?)
                """,
                (item["account_id"], int(item["nonce"]), timestamp),
            )
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
