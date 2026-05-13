from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.crypto import hash_block, sha256_text
from app.core.settings import (
    GENESIS_HASH,
    NODE_ID,
    PROOF_OF_PI_REWARD_PERCENT,
    PROTOCOL_VERSION,
    REQUIRED_VALIDATOR_APPROVALS,
    VALIDATION_MODE,
    VALIDATOR_REWARD_PERCENT_OF_BLOCK,
)
from app.core.signatures import verify_payload_signature
from app.db.database import get_connection, row_to_dict
from app.services.science import record_science_reserve_for_block
from app.services.state import active_snapshot_base_in_connection, maybe_create_checkpoint_in_connection, update_block_state_root
from app.services.treasury import record_scientific_development_treasury_for_block
from app.services.transactions import (
    apply_block_transactions,
    ensure_block_transactions_in_mempool,
    transaction_commitment,
)


class ConsensusError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


BLOCK_REQUIRED_FIELDS = {
    "height",
    "previous_hash",
    "miner_id",
    "range_start",
    "range_end",
    "algorithm",
    "result_hash",
    "samples",
    "timestamp",
    "block_hash",
    "reward",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def consensus_vote_payload(
    *,
    proposal_id: str,
    block_hash: str,
    height: int,
    validator_id: str,
    approved: bool,
    reason: str,
    signed_at: str,
) -> dict[str, Any]:
    return {
        "approved": approved,
        "block_hash": block_hash,
        "height": int(height),
        "proposal_id": proposal_id,
        "reason": reason,
        "signed_at": signed_at,
        "validator_id": validator_id,
    }


def propose_block(block: dict[str, Any], proposer_node_id: str, gossip: bool = True) -> dict[str, Any]:
    normalized = _normalize_block(block)
    _validate_block_hash(normalized)
    proposal_id = _proposal_id(normalized["block_hash"])
    timestamp = utc_now()
    status = "pending"
    reason = None

    with get_connection() as connection:
        tip = _latest_tip(connection)
        if normalized["height"] <= tip["height"]:
            existing = connection.execute(
                "SELECT block_hash FROM blocks WHERE height = ?",
                (normalized["height"],),
            ).fetchone()
            if existing is not None and existing["block_hash"] == normalized["block_hash"]:
                status = "known"
                reason = "block already exists locally"
            else:
                active_base = active_snapshot_base_in_connection(connection)
                if (
                    active_base is not None
                    and int(active_base["height"]) == normalized["height"]
                    and active_base["block_hash"] == normalized["block_hash"]
                ):
                    status = "known"
                    reason = "block already covered by active snapshot base"
                else:
                    raise ConsensusError(409, "proposal conflicts with local finalized chain")
        elif normalized["height"] == tip["height"] + 1 and normalized["previous_hash"] != tip["block_hash"]:
            raise ConsensusError(409, "proposal previous_hash does not match local chain tip")
        elif normalized["height"] > tip["height"] + 1:
            status = "pending_missing_ancestors"
            reason = "proposal accepted but missing ancestor blocks"

        connection.execute(
            """
            INSERT INTO consensus_block_proposals (
                proposal_id, block_hash, height, previous_hash, proposer_node_id,
                status, payload, approvals, rejections, rejection_reason,
                finalized_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, NULL, ?, ?)
            ON CONFLICT(proposal_id) DO UPDATE SET
                proposer_node_id = excluded.proposer_node_id,
                status = CASE
                    WHEN consensus_block_proposals.status IN ('finalized', 'imported', 'rejected')
                    THEN consensus_block_proposals.status
                    ELSE excluded.status
                END,
                payload = excluded.payload,
                rejection_reason = COALESCE(consensus_block_proposals.rejection_reason, excluded.rejection_reason),
                updated_at = excluded.updated_at
            """,
            (
                proposal_id,
                normalized["block_hash"],
                normalized["height"],
                normalized["previous_hash"],
                proposer_node_id,
                status,
                json.dumps(normalized, sort_keys=True),
                reason,
                timestamp,
                timestamp,
            ),
        )
        _record_consensus_event(
            connection,
            "block_proposed",
            status,
            {"proposal_id": proposal_id, "block_hash": normalized["block_hash"], "height": normalized["height"]},
        )
        proposal = _proposal_by_id(connection, proposal_id)
    if gossip:
        from app.services.network import gossip_json

        gossip_json(
            "/consensus/proposals?gossip=false",
            {"block": normalized, "proposer_node_id": proposer_node_id},
            "block_proposal_gossip",
        )
    return proposal


def record_local_block_proposal(connection: Any, block: dict[str, Any], proposer_node_id: str | None = None) -> dict[str, Any]:
    normalized = _normalize_block(block)
    _validate_block_hash(normalized)
    proposal_id = _proposal_id(normalized["block_hash"])
    timestamp = utc_now()
    connection.execute(
        """
        INSERT INTO consensus_block_proposals (
            proposal_id, block_hash, height, previous_hash, proposer_node_id,
            status, payload, approvals, rejections, rejection_reason,
            finalized_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, 'imported', ?, 0, 0, 'locally mined block', ?, ?, ?)
        ON CONFLICT(proposal_id) DO UPDATE SET
            status = CASE
                WHEN consensus_block_proposals.status IN ('finalized', 'imported')
                THEN consensus_block_proposals.status
                ELSE 'imported'
            END,
            payload = excluded.payload,
            finalized_at = COALESCE(consensus_block_proposals.finalized_at, excluded.finalized_at),
            updated_at = excluded.updated_at
        """,
        (
            proposal_id,
            normalized["block_hash"],
            normalized["height"],
            normalized["previous_hash"],
            proposer_node_id or NODE_ID,
            json.dumps(normalized, sort_keys=True),
            timestamp,
            timestamp,
            timestamp,
        ),
    )
    _record_consensus_event(
        connection,
        "local_block_proposed",
        "imported",
        {"proposal_id": proposal_id, "block_hash": normalized["block_hash"], "height": normalized["height"]},
    )
    return _proposal_by_id(connection, proposal_id)


def vote_on_proposal(
    proposal_id: str,
    validator_id: str,
    approved: bool,
    reason: str,
    signature: str,
    signed_at: str,
    gossip: bool = True,
) -> dict[str, Any]:
    timestamp = utc_now()
    with get_connection() as connection:
        proposal = _proposal_by_id(connection, proposal_id)
        if proposal is None:
            raise ConsensusError(404, "block proposal not found")
        if proposal["status"] in {"finalized", "imported", "rejected"}:
            raise ConsensusError(409, f"proposal already {proposal['status']}")
        validator = connection.execute(
            "SELECT validator_id, public_key, is_banned FROM validators WHERE validator_id = ?",
            (validator_id,),
        ).fetchone()
        if validator is None:
            raise ConsensusError(404, "validator not found")
        if bool(validator["is_banned"]):
            raise ConsensusError(403, "validator is banned")
        existing_vote = connection.execute(
            "SELECT 1 FROM consensus_votes WHERE proposal_id = ? AND validator_id = ?",
            (proposal_id, validator_id),
        ).fetchone()
        if existing_vote is not None:
            raise ConsensusError(409, "validator already voted on proposal")
        competing_vote = connection.execute(
            """
            SELECT p.proposal_id
            FROM consensus_votes v
            JOIN consensus_block_proposals p ON p.proposal_id = v.proposal_id
            WHERE v.validator_id = ?
              AND p.height = ?
              AND p.previous_hash = ?
              AND p.proposal_id <> ?
              AND p.status NOT IN ('rejected')
            LIMIT 1
            """,
            (validator_id, proposal["height"], proposal["previous_hash"], proposal_id),
        ).fetchone()
        if competing_vote is not None:
            raise ConsensusError(409, "validator already voted on competing fork")

        payload = consensus_vote_payload(
            proposal_id=proposal_id,
            block_hash=proposal["block_hash"],
            height=proposal["height"],
            validator_id=validator_id,
            approved=approved,
            reason=reason,
            signed_at=signed_at,
        )
        if not verify_payload_signature(validator["public_key"], payload, signature):
            raise ConsensusError(401, "invalid consensus vote signature")
        vote_id = sha256_text(json.dumps(payload, sort_keys=True))
        connection.execute(
            """
            INSERT INTO consensus_votes (
                vote_id, proposal_id, block_hash, validator_id, approved,
                reason, signature, signed_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                vote_id,
                proposal_id,
                proposal["block_hash"],
                validator_id,
                1 if approved else 0,
                reason,
                signature,
                signed_at,
                timestamp,
            ),
        )
        _refresh_vote_counts(connection, proposal_id)
        proposal = _proposal_by_id(connection, proposal_id)
        _record_consensus_event(
            connection,
            "consensus_vote",
            "accepted",
            {
                "proposal_id": proposal_id,
                "validator_id": validator_id,
                "approved": approved,
                "approvals": proposal["approvals"],
                "rejections": proposal["rejections"],
            },
        )
        if proposal["approvals"] >= REQUIRED_VALIDATOR_APPROVALS:
            proposal = finalize_proposal(proposal_id, connection=connection)
        elif proposal["rejections"] >= REQUIRED_VALIDATOR_APPROVALS:
            connection.execute(
                """
                UPDATE consensus_block_proposals
                SET status = 'rejected', rejection_reason = 'validator rejection quorum reached', updated_at = ?
                WHERE proposal_id = ?
                """,
                (timestamp, proposal_id),
            )
            proposal = _proposal_by_id(connection, proposal_id)
    if gossip:
        from app.services.network import gossip_json

        gossip_json(
            f"/consensus/proposals/{proposal_id}/vote?gossip=false",
            {
                "validator_id": validator_id,
                "approved": approved,
                "reason": reason,
                "signature": signature,
                "signed_at": signed_at,
            },
            "consensus_vote_gossip",
        )
    return proposal


def finalize_proposal(proposal_id: str, connection: Any | None = None) -> dict[str, Any]:
    owns_connection = connection is None
    if owns_connection:
        connection = get_connection()
    try:
        proposal = _proposal_by_id(connection, proposal_id)
        if proposal is None:
            raise ConsensusError(404, "block proposal not found")
        if proposal["status"] in {"finalized", "imported"}:
            return proposal
        if proposal["approvals"] < REQUIRED_VALIDATOR_APPROVALS:
            raise ConsensusError(409, "validator quorum not reached")
        winner = select_fork_choice(proposal["height"], connection=connection)
        if winner is not None and winner["proposal_id"] != proposal_id:
            raise ConsensusError(409, "proposal is not fork-choice winner")
        block = proposal["payload"]
        imported = _import_finalized_block(connection, block, proposal_id)
        timestamp = utc_now()
        validators = [
            row["validator_id"]
            for row in connection.execute(
                """
                SELECT validator_id
                FROM consensus_votes
                WHERE proposal_id = ? AND approved = 1
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (proposal_id, REQUIRED_VALIDATOR_APPROVALS),
            ).fetchall()
        ]
        finalization_id = sha256_text(f"{proposal_id}:{timestamp}:finalized")
        connection.execute(
            """
            INSERT INTO consensus_finalizations (
                finalization_id, proposal_id, block_hash, height, quorum,
                approvals, validator_ids, imported, finalized_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(proposal_id) DO UPDATE SET
                approvals = excluded.approvals,
                validator_ids = excluded.validator_ids,
                imported = excluded.imported,
                finalized_at = excluded.finalized_at
            """,
            (
                finalization_id,
                proposal_id,
                proposal["block_hash"],
                proposal["height"],
                REQUIRED_VALIDATOR_APPROVALS,
                proposal["approvals"],
                json.dumps(validators),
                1 if imported else 0,
                timestamp,
            ),
        )
        connection.execute(
            """
            UPDATE consensus_block_proposals
            SET status = ?, finalized_at = ?, updated_at = ?
            WHERE proposal_id = ?
            """,
            ("imported" if imported else "finalized", timestamp, timestamp, proposal_id),
        )
        _record_consensus_event(
            connection,
            "block_finalized",
            "imported" if imported else "finalized",
            {
                "proposal_id": proposal_id,
                "block_hash": proposal["block_hash"],
                "height": proposal["height"],
                "validators": validators,
            },
        )
        if owns_connection:
            connection.commit()
        return _proposal_by_id(connection, proposal_id)
    finally:
        if owns_connection:
            connection.close()


def replay_finalized_blocks(limit: int = 100) -> dict[str, Any]:
    imported = 0
    skipped = 0
    headers_imported = 0
    headers_skipped = 0
    errors: list[str] = []
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT p.proposal_id, p.payload
            FROM consensus_block_proposals p
            LEFT JOIN consensus_finalizations f ON f.proposal_id = p.proposal_id
            WHERE p.status = 'finalized' AND COALESCE(f.imported, 0) = 0
            ORDER BY p.height ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        for row in rows:
            block = json.loads(row["payload"])
            if _import_finalized_block(connection, block, row["proposal_id"]):
                imported += 1
                connection.execute(
                    "UPDATE consensus_finalizations SET imported = 1 WHERE proposal_id = ?",
                    (row["proposal_id"],),
                )
                connection.execute(
                    "UPDATE consensus_block_proposals SET status = 'imported', updated_at = ? WHERE proposal_id = ?",
                    (utc_now(), row["proposal_id"]),
                )
            else:
                skipped += 1
        remaining = max(int(limit) - imported - skipped, 0)
        if remaining:
            header_result = _replay_pending_block_headers(connection, remaining)
            headers_imported = header_result["imported"]
            headers_skipped = header_result["skipped"]
            errors.extend(header_result["errors"])
    return {
        "imported": imported,
        "skipped": skipped,
        "headers_imported": headers_imported,
        "headers_skipped": headers_skipped,
        "errors": errors,
    }


def _replay_pending_block_headers(connection: Any, limit: int) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT block_hash, height, payload
        FROM network_block_headers
        WHERE status = 'pending_replay'
        ORDER BY height ASC, received_at ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    imported = 0
    skipped = 0
    errors: list[str] = []
    for row in rows:
        block_hash = row["block_hash"]
        block = json.loads(row["payload"])
        connection.execute("SAVEPOINT replay_pending_header")
        try:
            did_import = _import_finalized_block(connection, block, f"header:{block_hash}")
            connection.execute("RELEASE SAVEPOINT replay_pending_header")
        except Exception as exc:
            connection.execute("ROLLBACK TO SAVEPOINT replay_pending_header")
            connection.execute("RELEASE SAVEPOINT replay_pending_header")
            skipped += 1
            reason = str(exc)
            errors.append(f"header {block_hash}: {reason}")
            connection.execute(
                """
                UPDATE network_block_headers
                SET reason = ?
                WHERE block_hash = ?
                """,
                (reason, block_hash),
            )
            continue
        if did_import:
            imported += 1
            connection.execute(
                """
                UPDATE network_block_headers
                SET status = 'imported', reason = 'imported via canonical header replay'
                WHERE block_hash = ?
                """,
                (block_hash,),
            )
        else:
            skipped += 1
            connection.execute(
                """
                UPDATE network_block_headers
                SET status = 'known', reason = 'block already known locally'
                WHERE block_hash = ?
                """,
                (block_hash,),
            )
    return {"imported": imported, "skipped": skipped, "errors": errors}


def select_fork_choice(height: int | None = None, connection: Any | None = None) -> dict[str, Any] | None:
    owns_connection = connection is None
    if owns_connection:
        connection = get_connection()
    try:
        if height is None:
            latest = connection.execute(
                "SELECT MAX(height) AS height FROM consensus_block_proposals WHERE status NOT IN ('rejected')"
            ).fetchone()
            if latest is None or latest["height"] is None:
                return None
            height = int(latest["height"])
        rows = connection.execute(
            """
            SELECT *
            FROM consensus_block_proposals
            WHERE height = ? AND status NOT IN ('rejected')
            """,
            (height,),
        ).fetchall()
        choices = []
        for row in rows:
            proposal = _decode_proposal(row_to_dict(row))
            proposal["approval_weight"] = _proposal_approval_weight(connection, proposal["proposal_id"])
            proposal["rejection_weight"] = _proposal_rejection_weight(connection, proposal["proposal_id"])
            choices.append(proposal)
        if not choices:
            return None
        choices.sort(
            key=lambda item: (
                -float(item["approval_weight"]),
                float(item["rejection_weight"]),
                -int(item["approvals"]),
                int(item["rejections"]),
                item["created_at"],
                item["block_hash"],
            )
        )
        return choices[0]
    finally:
        if owns_connection:
            connection.close()


def list_block_proposals(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    query = "SELECT * FROM consensus_block_proposals"
    params: tuple[Any, ...]
    if status:
        query += " WHERE status = ?"
        params = (status,)
    else:
        params = ()
    query += " ORDER BY height DESC, created_at DESC LIMIT ?"
    params = (*params, limit)
    with get_connection() as connection:
        return [_decode_proposal(row_to_dict(row)) for row in connection.execute(query, params).fetchall()]


def get_block_proposal(proposal_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        return _proposal_by_id(connection, proposal_id)


def list_consensus_votes(proposal_id: str) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT v.*, validators.trust_score, validators.stake_locked
            FROM consensus_votes v
            LEFT JOIN validators ON validators.validator_id = v.validator_id
            WHERE v.proposal_id = ?
            ORDER BY v.created_at ASC
            """,
            (proposal_id,),
        ).fetchall()
        votes = []
        for row in rows:
            vote = row_to_dict(row)
            vote["approved"] = bool(vote["approved"])
            vote["weight"] = _validator_weight(vote.get("trust_score"), vote.get("stake_locked"))
            votes.append(vote)
        return votes


def consensus_status() -> dict[str, Any]:
    with get_connection() as connection:
        counts = connection.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM consensus_block_proposals
            GROUP BY status
            """
        ).fetchall()
        latest = connection.execute(
            "SELECT height, block_hash FROM blocks ORDER BY height DESC LIMIT 1"
        ).fetchone()
        finalizations = connection.execute("SELECT COUNT(*) AS count FROM consensus_finalizations").fetchone()
        fork_rows = connection.execute(
            """
            SELECT height
            FROM consensus_block_proposals
            WHERE status NOT IN ('rejected')
            GROUP BY height
            HAVING COUNT(*) > 1
            ORDER BY height DESC
            LIMIT 10
            """
        ).fetchall()
        fork_choices = [select_fork_choice(int(row["height"]), connection=connection) for row in fork_rows]
    return {
        "required_validator_approvals": REQUIRED_VALIDATOR_APPROVALS,
        "latest_block_height": latest["height"] if latest else 0,
        "latest_block_hash": latest["block_hash"] if latest else GENESIS_HASH,
        "proposals": {row["status"]: row["count"] for row in counts},
        "finalizations": finalizations["count"] if finalizations else 0,
        "fork_choices": [
            {
                "height": choice["height"],
                "proposal_id": choice["proposal_id"],
                "block_hash": choice["block_hash"],
                "approvals": choice["approvals"],
                "rejections": choice["rejections"],
                "approval_weight": choice.get("approval_weight", 0.0),
                "rejection_weight": choice.get("rejection_weight", 0.0),
            }
            for choice in fork_choices
            if choice is not None
        ],
        "checked_at": utc_now(),
    }


def _import_finalized_block(connection: Any, block: dict[str, Any], proposal_id: str) -> bool:
    _validate_block_hash(block)
    tip = _latest_tip(connection)
    if block["height"] <= tip["height"]:
        existing = connection.execute(
            "SELECT block_hash FROM blocks WHERE height = ?",
            (block["height"],),
        ).fetchone()
        if existing is not None and existing["block_hash"] == block["block_hash"]:
            return False
        raise ConsensusError(409, "finalized block conflicts with local chain")
    if block["height"] != tip["height"] + 1:
        raise ConsensusError(409, "cannot import block before ancestors")
    if block["previous_hash"] != tip["block_hash"]:
        raise ConsensusError(409, "finalized block previous_hash does not match local chain tip")
    _reject_duplicate_block_material(connection, block)
    _ensure_miner(connection, block["miner_id"])
    task_id = _ensure_task(connection, block)
    timestamp = block["timestamp"]
    samples_json = json.dumps(block["samples"], sort_keys=True)
    transactions = block.get("transactions") or []
    if int(block.get("tx_count") or 0) > 0:
        commitment = transaction_commitment(transactions)
        if commitment["tx_count"] != int(block.get("tx_count") or 0):
            raise ConsensusError(422, "block transactions are missing from proposal payload")
        if commitment["tx_merkle_root"] != block.get("tx_merkle_root"):
            raise ConsensusError(422, "block transaction merkle root mismatch")
        ensure_block_transactions_in_mempool(connection, transactions, timestamp)
    connection.execute(
        """
        INSERT INTO blocks (
            height, previous_hash, miner_id, range_start, range_end, algorithm,
            result_hash, merkle_root, samples, timestamp, block_hash, reward, tx_merkle_root,
            tx_count, tx_hashes, fee_reward, state_root, difficulty, task_id, protocol_params_id,
            protocol_version, validation_mode, total_task_ms, validation_ms
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            block["height"],
            block["previous_hash"],
            block["miner_id"],
            block["range_start"],
            block["range_end"],
            block["algorithm"],
            block["result_hash"],
            block.get("merkle_root"),
            samples_json,
            timestamp,
            block["block_hash"],
            block["reward"],
            block.get("tx_merkle_root"),
            int(block.get("tx_count") or 0),
            json.dumps(block.get("tx_hashes") or [], sort_keys=True),
            round(float(block.get("fee_reward") or 0), 8),
            block.get("state_root"),
            block.get("difficulty"),
            task_id,
            block.get("protocol_params_id"),
            block.get("protocol_version", PROTOCOL_VERSION),
            block.get("validation_mode", VALIDATION_MODE),
            None,
            None,
        ),
    )
    connection.execute(
        """
        INSERT INTO rewards (miner_id, block_height, amount, reason, created_at)
        VALUES (?, ?, ?, 'distributed block finalized', ?)
        """,
        (block["miner_id"], block["height"], block["reward"], timestamp),
    )
    _apply_account_delta(
        connection,
        block["miner_id"],
        "miner",
        block["reward"],
        "block_reward",
        block["height"],
        proposal_id,
        "distributed finalized miner reward",
        timestamp,
    )
    if transactions:
        apply_block_transactions(
            connection,
            miner_id=block["miner_id"],
            block_height=block["height"],
            transactions=transactions,
            timestamp=timestamp,
        )
    total_block_reward = round(float(block["reward"]) / PROOF_OF_PI_REWARD_PERCENT, 8)
    record_science_reserve_for_block(connection, block["height"], total_block_reward)
    record_scientific_development_treasury_for_block(connection, block["height"], total_block_reward)
    _apply_distributed_validator_rewards(connection, block, proposal_id, total_block_reward, timestamp)
    state_root = update_block_state_root(connection, block["height"], timestamp)
    if block.get("state_root") and block["state_root"] != state_root:
        raise ConsensusError(422, "state_root mismatch after canonical replay")
    maybe_create_checkpoint_in_connection(connection, block["height"])
    return True


def _apply_distributed_validator_rewards(
    connection: Any,
    block: dict[str, Any],
    proposal_id: str,
    total_block_reward: float,
    timestamp: str,
) -> None:
    block_height = int(block["height"])
    payload_reward = block.get("validator_reward") if isinstance(block.get("validator_reward"), dict) else {}
    validator_ids = list(payload_reward.get("validator_ids") or [])
    if not validator_ids:
        validator_rows = connection.execute(
            """
            SELECT validator_id
            FROM consensus_votes
            WHERE proposal_id = ? AND approved = 1
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (proposal_id, REQUIRED_VALIDATOR_APPROVALS),
        ).fetchall()
        validator_ids = [row["validator_id"] for row in validator_rows]
    if not validator_ids:
        return
    pool = round(float(payload_reward.get("pool") or 0), 8)
    if pool <= 0:
        pool = round(total_block_reward * VALIDATOR_REWARD_PERCENT_OF_BLOCK, 8)
    per_validator = round(float(payload_reward.get("per_validator") or 0), 8)
    if per_validator <= 0:
        per_validator = round(pool / len(validator_ids), 8)
    for validator_id in validator_ids:
        _ensure_validator(connection, validator_id, timestamp)
        _apply_account_delta(
            connection,
            validator_id,
            "validator",
            per_validator,
            "validator_reward",
            block_height,
            proposal_id,
            "distributed consensus validator reward",
            timestamp,
        )
        connection.execute(
            """
            UPDATE validators
            SET accepted_jobs = accepted_jobs + 1, last_seen_at = ?
            WHERE validator_id = ?
            """,
            (timestamp, validator_id),
        )


def _ensure_validator(connection: Any, validator_id: str, timestamp: str) -> None:
    existing = connection.execute("SELECT 1 FROM validators WHERE validator_id = ?", (validator_id,)).fetchone()
    if existing is not None:
        return
    connection.execute(
        """
        INSERT INTO validators (validator_id, name, public_key, registered_at, last_seen_at)
        VALUES (?, ?, '', ?, ?)
        """,
        (validator_id, f"distributed:{validator_id}", timestamp, timestamp),
    )


def _apply_account_delta(
    connection: Any,
    account_id: str,
    account_type: str,
    amount: float,
    entry_type: str,
    block_height: int,
    related_id: str,
    description: str,
    timestamp: str,
) -> None:
    current = connection.execute(
        "SELECT balance FROM balances WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    previous = float(current["balance"]) if current is not None else 0.0
    balance_after = round(previous + float(amount), 8)
    connection.execute(
        """
        INSERT INTO balances (account_id, account_type, balance, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(account_id) DO UPDATE SET
            account_type = excluded.account_type,
            balance = excluded.balance,
            updated_at = excluded.updated_at
        """,
        (account_id, account_type, balance_after, timestamp),
    )
    connection.execute(
        """
        INSERT INTO ledger_entries (
            account_id, account_type, amount, balance_after, entry_type,
            block_height, related_id, description, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (account_id, account_type, amount, balance_after, entry_type, block_height, related_id, description, timestamp),
    )


def _ensure_miner(connection: Any, miner_id: str) -> None:
    existing = connection.execute("SELECT 1 FROM miners WHERE miner_id = ?", (miner_id,)).fetchone()
    if existing is not None:
        return
    timestamp = utc_now()
    connection.execute(
        """
        INSERT INTO miners (miner_id, name, public_key, registered_at)
        VALUES (?, ?, NULL, ?)
        """,
        (miner_id, f"distributed:{miner_id}", timestamp),
    )


def _ensure_task(connection: Any, block: dict[str, Any]) -> str:
    task_id = block.get("task_id") or f"distributed_task_{block['height']}_{block['block_hash'][:12]}"
    existing = connection.execute("SELECT task_id FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    if existing is not None:
        return task_id
    matching_range = connection.execute(
        """
        SELECT task_id
        FROM tasks
        WHERE range_start = ? AND range_end = ? AND algorithm = ?
        """,
        (block["range_start"], block["range_end"], block["algorithm"]),
    ).fetchone()
    if matching_range is not None:
        return matching_range["task_id"]
    connection.execute(
        """
        INSERT INTO tasks (
            task_id, miner_id, range_start, range_end, algorithm, status,
            assignment_seed, assignment_mode, protocol_params_id, created_at, submitted_at
        )
        VALUES (?, ?, ?, ?, ?, 'accepted', ?, 'distributed_replay', ?, ?, ?)
        """,
        (
            task_id,
            block["miner_id"],
            block["range_start"],
            block["range_end"],
            block["algorithm"],
            block["block_hash"],
            block.get("protocol_params_id"),
            block["timestamp"],
            block["timestamp"],
        ),
    )
    return task_id


def _reject_duplicate_block_material(connection: Any, block: dict[str, Any]) -> None:
    duplicate_hash = connection.execute(
        "SELECT height FROM blocks WHERE result_hash = ?",
        (block["result_hash"],),
    ).fetchone()
    if duplicate_hash is not None:
        raise ConsensusError(409, "duplicate result_hash")
    overlap = connection.execute(
        """
        SELECT height
        FROM blocks
        WHERE algorithm = ?
          AND range_start <= ?
          AND range_end >= ?
        LIMIT 1
        """,
        (block["algorithm"], block["range_end"], block["range_start"]),
    ).fetchone()
    if overlap is not None:
        raise ConsensusError(409, "block range overlaps local chain")


def _validate_block_hash(block: dict[str, Any]) -> None:
    missing = sorted(BLOCK_REQUIRED_FIELDS - set(block))
    if missing:
        raise ConsensusError(422, f"missing block fields: {', '.join(missing)}")
    if len(str(block["block_hash"])) != 64 or len(str(block["previous_hash"])) != 64:
        raise ConsensusError(422, "invalid block hash length")
    if block["block_hash"] not in _block_hash_candidates(block):
        raise ConsensusError(422, "block_hash does not match canonical payload")


def _normalize_block(block: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(block)
    if isinstance(normalized.get("samples"), str):
        normalized["samples"] = json.loads(normalized["samples"])
    normalized["height"] = int(normalized["height"])
    normalized["range_start"] = int(normalized["range_start"])
    normalized["range_end"] = int(normalized["range_end"])
    normalized["reward"] = round(float(normalized["reward"]), 8)
    if normalized.get("difficulty") is not None:
        normalized["difficulty"] = float(normalized["difficulty"])
    if normalized.get("protocol_params_id") is not None:
        normalized["protocol_params_id"] = int(normalized["protocol_params_id"])
    if isinstance(normalized.get("tx_hashes"), str):
        normalized["tx_hashes"] = json.loads(normalized["tx_hashes"])
    normalized["tx_count"] = int(normalized.get("tx_count") or 0)
    normalized["fee_reward"] = round(float(normalized.get("fee_reward") or 0), 8)
    if normalized.get("state_root") is not None and len(str(normalized["state_root"])) != 64:
        raise ConsensusError(422, "invalid state_root length")
    if normalized["tx_count"] > 0 and not normalized.get("tx_merkle_root"):
        normalized["tx_merkle_root"] = transaction_commitment(normalized.get("transactions") or [])["tx_merkle_root"]
    normalized["protocol_version"] = normalized.get("protocol_version") or PROTOCOL_VERSION
    normalized["validation_mode"] = normalized.get("validation_mode") or VALIDATION_MODE
    return normalized


def _canonical_block_payload(block: dict[str, Any], include_protocol: bool) -> dict[str, Any]:
    payload = {
        "algorithm": block["algorithm"],
        "height": int(block["height"]),
        "miner_id": block["miner_id"],
        "previous_hash": block["previous_hash"],
        "range_end": int(block["range_end"]),
        "range_start": int(block["range_start"]),
        "result_hash": block["result_hash"],
        "reward": round(float(block["reward"]), 8),
        "samples": block["samples"],
        "timestamp": block["timestamp"],
    }
    if block.get("difficulty") is not None:
        payload["difficulty"] = float(block["difficulty"])
    if block.get("protocol_params_id") is not None:
        payload["protocol_params_id"] = int(block["protocol_params_id"])
    if block.get("merkle_root"):
        payload["merkle_root"] = block["merkle_root"]
    if int(block.get("tx_count") or 0) > 0:
        payload["tx_merkle_root"] = block.get("tx_merkle_root")
        payload["tx_count"] = int(block.get("tx_count") or 0)
        payload["tx_hashes"] = block.get("tx_hashes") or []
        payload["fee_reward"] = round(float(block.get("fee_reward") or 0), 8)
    if include_protocol:
        payload["protocol_version"] = block.get("protocol_version", PROTOCOL_VERSION)
        payload["validation_mode"] = block.get("validation_mode", VALIDATION_MODE)
    return payload


def _block_hash_candidates(block: dict[str, Any]) -> set[str]:
    candidates = {
        hash_block(_canonical_block_payload(block, include_protocol=True)),
        hash_block(_canonical_block_payload(block, include_protocol=False)),
    }
    fraud_payload = _canonical_block_payload(block, include_protocol=True)
    fraud_payload["fraudulent"] = bool(block.get("fraudulent", False))
    fraud_payload["fraud_reason"] = block.get("fraud_reason")
    fraud_payload["fraud_detected_at"] = block.get("fraud_detected_at")
    candidates.add(hash_block(fraud_payload))
    legacy_fraud_payload = _canonical_block_payload(block, include_protocol=False)
    legacy_fraud_payload["fraudulent"] = bool(block.get("fraudulent", False))
    legacy_fraud_payload["fraud_reason"] = block.get("fraud_reason")
    legacy_fraud_payload["fraud_detected_at"] = block.get("fraud_detected_at")
    candidates.add(hash_block(legacy_fraud_payload))
    return candidates


def _latest_tip(connection: Any) -> dict[str, Any]:
    latest = connection.execute("SELECT height, block_hash FROM blocks ORDER BY height DESC LIMIT 1").fetchone()
    if latest is None:
        active_base = active_snapshot_base_in_connection(connection)
        if active_base is not None and active_base.get("state_applied"):
            return {"height": int(active_base["height"]), "block_hash": active_base["block_hash"]}
        return {"height": 0, "block_hash": GENESIS_HASH}
    return {"height": int(latest["height"]), "block_hash": latest["block_hash"]}


def _proposal_id(block_hash: str) -> str:
    return sha256_text(f"proposal:{block_hash}")


def _refresh_vote_counts(connection: Any, proposal_id: str) -> None:
    counts = connection.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN approved = 1 THEN 1 ELSE 0 END), 0) AS approvals,
            COALESCE(SUM(CASE WHEN approved = 0 THEN 1 ELSE 0 END), 0) AS rejections
        FROM consensus_votes
        WHERE proposal_id = ?
        """,
        (proposal_id,),
    ).fetchone()
    connection.execute(
        """
        UPDATE consensus_block_proposals
        SET approvals = ?, rejections = ?, updated_at = ?
        WHERE proposal_id = ?
        """,
        (counts["approvals"], counts["rejections"], utc_now(), proposal_id),
    )


def _proposal_approval_weight(connection: Any, proposal_id: str) -> float:
    return _proposal_vote_weight(connection, proposal_id, approved=True)


def _proposal_rejection_weight(connection: Any, proposal_id: str) -> float:
    return _proposal_vote_weight(connection, proposal_id, approved=False)


def _proposal_vote_weight(connection: Any, proposal_id: str, approved: bool) -> float:
    rows = connection.execute(
        """
        SELECT validators.trust_score, validators.stake_locked
        FROM consensus_votes
        LEFT JOIN validators ON validators.validator_id = consensus_votes.validator_id
        WHERE consensus_votes.proposal_id = ? AND consensus_votes.approved = ?
        """,
        (proposal_id, 1 if approved else 0),
    ).fetchall()
    return round(sum(_validator_weight(row["trust_score"], row["stake_locked"]) for row in rows), 8)


def _validator_weight(trust_score: Any, stake_locked: Any) -> float:
    trust = max(0.0, float(trust_score if trust_score is not None else 0.0))
    stake = max(0.0, float(stake_locked if stake_locked is not None else 0.0))
    stake_units = min(stake / 31.416, 100.0)
    return round(trust * (1.0 + stake_units), 8)


def _proposal_by_id(connection: Any, proposal_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM consensus_block_proposals WHERE proposal_id = ?",
        (proposal_id,),
    ).fetchone()
    return _decode_proposal(row_to_dict(row))


def _decode_proposal(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    row["payload"] = json.loads(row["payload"])
    row["approvals"] = int(row.get("approvals") or 0)
    row["rejections"] = int(row.get("rejections") or 0)
    return row


def _record_consensus_event(connection: Any, event_type: str, status: str, details: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO network_sync_events (peer_id, event_type, direction, status, details, created_at)
        VALUES (NULL, ?, 'consensus', ?, ?, ?)
        """,
        (event_type, status, json.dumps(details, sort_keys=True), utc_now()),
    )
