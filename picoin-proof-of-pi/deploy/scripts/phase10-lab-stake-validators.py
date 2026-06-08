#!/usr/bin/env python3
"""Apply local lab-only wallet stake to Phase 10 candidate validators.

This helper is intentionally not a production staking path. It exists to turn a
snapshot-restored Phase 10 candidate cluster into an isolated quorum lab after
signed heartbeat gossip has already been proven.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_STAKE = 31.416
DEFAULT_OWNER = "PI" + ("0" * 40)


def connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    return connection


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def load_validator(connection: sqlite3.Connection, validator_id: str) -> dict[str, Any] | None:
    return row_to_dict(
        connection.execute(
            """
            SELECT validator_id, name, enabled, is_banned, online_status, sync_status,
                   node_id, advertised_address, stake_locked, wallet_stake_locked,
                   stake_owner_address, reason_if_not_eligible
            FROM validators
            WHERE validator_id = ?
            """,
            (validator_id,),
        ).fetchone()
    )


def require_lab_db(db_path: Path, allow_non_lab_db: bool) -> None:
    normalized = str(db_path).replace("\\", "/")
    if allow_non_lab_db:
        return
    if "/phase10-candidate/" not in normalized:
        raise SystemExit(
            "Refusing to modify non-lab DB. Expected path containing "
            "'/phase10-candidate/'. Pass --allow-non-lab-db only for a disposable lab clone."
        )


def apply_stake(
    connection: sqlite3.Connection,
    validator_ids: list[str],
    amount: float,
    owner: str,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    timestamp = datetime.now(timezone.utc).isoformat()
    before: list[dict[str, Any]] = []
    after: list[dict[str, Any]] = []

    connection.execute("BEGIN IMMEDIATE")
    try:
        for validator_id in validator_ids:
            validator = load_validator(connection, validator_id)
            if validator is None:
                raise SystemExit(f"validator not found: {validator_id}")
            before.append(validator)
            if int(validator.get("enabled") or 0) != 1:
                raise SystemExit(f"validator disabled: {validator_id}")
            if int(validator.get("is_banned") or 0) != 0:
                raise SystemExit(f"validator banned: {validator_id}")
            connection.execute(
                """
                UPDATE validators
                SET stake_locked = ?,
                    wallet_stake_locked = ?,
                    stake_owner_address = ?,
                    reason_if_not_eligible = CASE
                        WHEN online_status = 'online' AND sync_status != 'out_of_sync' THEN NULL
                        ELSE reason_if_not_eligible
                    END,
                    last_seen_at = ?
                WHERE validator_id = ?
                """,
                (amount, amount, owner, timestamp, validator_id),
            )
            after_validator = load_validator(connection, validator_id)
            if after_validator is not None:
                after.append(after_validator)

        if dry_run:
            connection.rollback()
        else:
            connection.commit()
    except Exception:
        connection.rollback()
        raise

    return {
        "status": "dry_run" if dry_run else "applied",
        "amount": amount,
        "stake_owner_address": owner,
        "validators": {
            "before": before,
            "after": after,
        },
        "notes": [
            "lab-only direct validator stake",
            "does not submit mainnet transactions",
            "intended only for disposable phase10-candidate DBs",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="Candidate SQLite DB path")
    parser.add_argument("--validator", action="append", required=True, help="Validator id to stake")
    parser.add_argument("--amount", type=float, default=DEFAULT_STAKE, help="Stake amount per validator")
    parser.add_argument("--owner", default=DEFAULT_OWNER, help="Synthetic lab stake owner address")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-non-lab-db", action="store_true")
    args = parser.parse_args()

    db_path = args.db.expanduser().resolve()
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")
    require_lab_db(db_path, args.allow_non_lab_db)
    validator_ids = [item.strip() for item in args.validator if item.strip()]
    if not validator_ids:
        raise SystemExit("at least one --validator is required")

    with connect(db_path) as connection:
        report = apply_stake(
            connection,
            validator_ids,
            float(args.amount),
            str(args.owner).strip().upper(),
            dry_run=bool(args.dry_run),
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
