from __future__ import annotations

import asyncio
import itertools
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests

from app.core.crypto import canonical_json, hash_block, sha256_text
from app.core.economics import (
    reward_units_to_float,
    total_units_from_miner_reward_units,
    validator_reward_pool_units,
)
from app.core.money import to_units, units_from_db, units_to_float
from app.core.settings import (
    AUTO_RECOVERY_ENABLED,
    BOOTSTRAP_PEERS,
    GENESIS_HASH,
    MIN_QUORUM_PEERS,
    MIN_VALIDATOR_STAKE,
    NODE_ID,
    PROTOCOL_VERSION,
    REPLAY_BATCH_SIZE,
    REPLAY_STALL_FAILURES,
    REPLAY_WORKER_ENABLED,
    REPLAY_WORKER_INTERVAL_SECONDS,
    REQUIRED_VALIDATOR_APPROVALS,
    VALIDATION_MODE,
    VALIDATOR_ELIGIBILITY_STAKE_FIELD,
    VALIDATOR_MIN_TRUST_SCORE,
    VALIDATOR_REGISTRATION_STAKE,
)
from app.core.signatures import verify_payload_signature
from app.db.database import get_connection, row_to_dict
from app.services.rewards import mature_block_rewards, record_miner_block_reward
from app.services.science import record_science_reserve_for_block
from app.services.state import (
    active_snapshot_base_in_connection,
    balance_snapshot,
    calculate_state_root,
    maybe_create_checkpoint_in_connection,
    update_block_state_root,
)
from app.services.treasury import record_scientific_development_treasury_for_block
from app.services.transactions import (
    apply_block_transactions,
    ensure_block_transactions_in_mempool,
    transaction_commitment,
)
from app.services.wallet import is_valid_address


logger = logging.getLogger(__name__)


class ConsensusError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _eligible_validator_count_for_quorum(connection: Any) -> int:
    row = connection.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM validators
        WHERE is_banned = 0
          AND enabled = 1
          AND online_status = 'online'
          AND sync_status != 'out_of_sync'
          AND protocol_version = ?
          AND {VALIDATOR_ELIGIBILITY_STAKE_FIELD} >= ?
          AND trust_score >= ?
        """,
        (PROTOCOL_VERSION, MIN_VALIDATOR_STAKE, VALIDATOR_MIN_TRUST_SCORE),
    ).fetchone()
    return int((row["count"] if row else 0) or 0)


def _required_validator_approvals_for_quorum(connection: Any) -> int:
    eligible_count = _eligible_validator_count_for_quorum(connection)
    if eligible_count <= 0:
        return REQUIRED_VALIDATOR_APPROVALS
    adaptive = max(1, eligible_count)
    return max(1, min(REQUIRED_VALIDATOR_APPROVALS, adaptive))


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

FORK_CHOICE_RULE = (
    "same height and previous_hash compete; highest approval_weight wins; "
    "then lowest rejection_weight, highest approvals, lowest rejections, "
    "oldest proposal, lexicographically lowest block_hash"
)

LEGACY_ZERO_GENESIS_HASH = "0" * 64

_REPLAY_LOCK = threading.Lock()
_REPLAY_WORKER_TASK: asyncio.Task | None = None
_REPLAY_WORKER_STOP: asyncio.Event | None = None
_REPLAY_METRICS: dict[str, Any] = {
    "active": False,
    "last_started_at": None,
    "last_completed_at": None,
    "last_batch_size": 0,
    "last_processed": 0,
    "last_processed_height": 0,
    "last_duration_ms": 0.0,
    "avg_ms": 0.0,
    "blocks_per_second": 0.0,
    "total_processed": 0,
    "total_batches": 0,
    "last_error": None,
}


def _matches_local_parent(block: dict[str, Any], tip: dict[str, Any]) -> bool:
    previous_hash = str(block["previous_hash"])
    if previous_hash == str(tip["block_hash"]):
        return True
    return (
        int(block["height"]) == 1
        and int(tip["height"]) == 0
        and previous_hash == LEGACY_ZERO_GENESIS_HASH
    )
_REPLAY_HEALTH_LOCK = threading.Lock()
_REPLAY_HEALTH: dict[str, Any] = {
    "sync_status": "healthy",
    "replay_stalled": False,
    "replay_last_progress_at": None,
    "replay_last_imported_height": 0,
    "replay_consecutive_failures": 0,
    "divergence_detected": False,
    "divergence_reason": None,
    "auto_recovery_active": False,
    "min_quorum_peers": MIN_QUORUM_PEERS,
    "auto_recovery_enabled": AUTO_RECOVERY_ENABLED,
    "database_path": None,
}
_DIVERGENCE_MARKERS = (
    "state_root mismatch",
    "orphan replay chain",
    "block_hash does not match canonical payload",
    "missing block fields",
)
_MISSING_ANCESTOR_REPLAY_MARKERS = (
    "cannot import block before ancestors",
    "finalized block previous_hash does not match local chain tip",
)


def _is_missing_ancestor_replay_error(reason: str) -> bool:
    lower = reason.lower()
    return any(marker in lower for marker in _MISSING_ANCESTOR_REPLAY_MARKERS)


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
    should_sync_ancestors = False

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
        elif normalized["height"] == tip["height"] + 1 and not _matches_local_parent(normalized, tip):
            status = "pending_missing_ancestors"
            reason = "proposal accepted but previous_hash is not local chain tip"
            should_sync_ancestors = True
        elif normalized["height"] > tip["height"] + 1:
            status = "pending_missing_ancestors"
            reason = "proposal accepted but missing ancestor blocks"
            should_sync_ancestors = True

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
    if should_sync_ancestors:
        _sync_missing_ancestors_for_proposer(proposer_node_id, int(normalized["height"]))
        with get_connection() as connection:
            _promote_ready_missing_ancestor_proposals(connection)
            proposal = _proposal_by_id(connection, proposal_id) or proposal
    if gossip:
        from app.services.network import gossip_json

        gossip_json(
            "/consensus/proposals?gossip=false",
            {"block": normalized, "proposer_node_id": proposer_node_id},
            "block_proposal_gossip",
        )
    return proposal


def _sync_missing_ancestors_for_proposer(proposer_node_id: str, target_height: int | None = None) -> None:
    peer_addresses: list[str] = []
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT peer_address
            FROM network_peers
            WHERE (node_id = ? OR status IN ('connected', 'stale'))
              AND peer_address <> ''
            ORDER BY
                CASE
                    WHEN node_id = ? THEN 0
                    WHEN status = 'connected' THEN 1
                    ELSE 2
                END,
                last_seen DESC
            """,
            (proposer_node_id, proposer_node_id),
        ).fetchall()
        peer_addresses.extend(row["peer_address"] for row in rows)
    peer_addresses.extend(BOOTSTRAP_PEERS)
    peer_addresses = list(dict.fromkeys(address.rstrip("/") for address in peer_addresses if address))
    if not peer_addresses:
        return

    from app.services.network import sync_blocks_until

    for peer_address in peer_addresses:
        try:
            sync_blocks_until(peer_address, target_height=target_height, limit=100)
            if target_height is None or _local_tip_height() >= target_height - 1:
                return
        except Exception:
            continue


def _local_tip_height() -> int:
    with get_connection() as connection:
        tip = _latest_tip(connection)
        return int(tip["height"])


def _promote_ready_missing_ancestor_proposals(connection: Any) -> int:
    timestamp = utc_now()
    promoted = 0
    rows = connection.execute(
        """
        SELECT proposal_id, height, previous_hash
        FROM consensus_block_proposals
        WHERE status = 'pending_missing_ancestors'
        ORDER BY height ASC, created_at ASC
        """
    ).fetchall()
    active_base = active_snapshot_base_in_connection(connection)
    for row in rows:
        height = int(row["height"])
        previous_hash = row["previous_hash"]
        parent_exists = False
        if height == 1 and previous_hash in {GENESIS_HASH, LEGACY_ZERO_GENESIS_HASH}:
            parent_exists = True
        else:
            parent = connection.execute(
                "SELECT block_hash FROM blocks WHERE height = ?",
                (height - 1,),
            ).fetchone()
            parent_exists = parent is not None and parent["block_hash"] == previous_hash
        if (
            not parent_exists
            and active_base is not None
            and int(active_base["height"]) == height - 1
            and active_base["block_hash"] == previous_hash
        ):
            parent_exists = True
        if not parent_exists:
            continue
        connection.execute(
            """
            UPDATE consensus_block_proposals
            SET status = 'pending',
                rejection_reason = NULL,
                updated_at = ?
            WHERE proposal_id = ? AND status = 'pending_missing_ancestors'
            """,
            (timestamp, row["proposal_id"]),
        )
        promoted += 1
    return promoted


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
            "SELECT * FROM validators WHERE validator_id = ?",
            (validator_id,),
        ).fetchone()
        if validator is None:
            raise ConsensusError(404, "validator not found")
        if bool(validator["is_banned"]):
            raise ConsensusError(403, "validator is banned")
        if not bool(validator["enabled"]):
            raise ConsensusError(403, "validator is disabled")
        if validator["online_status"] != "online":
            raise ConsensusError(403, f"validator is {validator['online_status']}")
        if validator["sync_status"] == "out_of_sync":
            raise ConsensusError(403, "validator is out_of_sync")
        if validator["protocol_version"] != PROTOCOL_VERSION:
            raise ConsensusError(403, "validator protocol version is incompatible")
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
        required_approvals = _required_validator_approvals_for_quorum(connection)
        if proposal["approvals"] >= required_approvals:
            proposal = finalize_proposal(proposal_id, connection=connection)
        elif proposal["rejections"] >= required_approvals:
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
        required_approvals = _required_validator_approvals_for_quorum(connection)
        if proposal["approvals"] < required_approvals:
            raise ConsensusError(409, "validator quorum not reached")
        winner = select_fork_choice(proposal["height"], proposal["previous_hash"], connection=connection)
        if winner is not None and winner["proposal_id"] != proposal_id:
            raise ConsensusError(409, "proposal is not fork-choice winner")
        block = proposal["payload"]
        imported = _import_finalized_block(connection, block, proposal_id)
        if imported:
            _promote_ready_missing_ancestor_proposals(connection)
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
                (proposal_id, required_approvals),
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
                required_approvals,
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


def _replay_queue_snapshot(connection: Any) -> dict[str, Any]:
    finalized = connection.execute(
        """
        SELECT COUNT(*) AS count, MAX(p.height) AS target_height
        FROM consensus_block_proposals p
        LEFT JOIN consensus_finalizations f ON f.proposal_id = p.proposal_id
        WHERE p.status = 'finalized' AND COALESCE(f.imported, 0) = 0
        """
    ).fetchone()
    headers = connection.execute(
        """
        SELECT COUNT(*) AS count, MAX(height) AS target_height
        FROM network_block_headers
        WHERE status IN ('pending_replay', 'pending_missing_ancestors')
        """
    ).fetchone()
    latest = connection.execute("SELECT COALESCE(MAX(height), 0) AS height FROM blocks").fetchone()
    finalized_count = int((finalized["count"] if finalized else 0) or 0)
    header_count = int((headers["count"] if headers else 0) or 0)
    current_height = int((latest["height"] if latest else 0) or 0)
    target_height = max(
        current_height,
        int((finalized["target_height"] if finalized else 0) or 0),
        int((headers["target_height"] if headers else 0) or 0),
    )
    return {
        "queue_size": finalized_count + header_count,
        "finalized_queue_size": finalized_count,
        "header_queue_size": header_count,
        "current_height": current_height,
        "target_height": target_height,
    }


def _replay_health_snapshot() -> dict[str, Any]:
    with _REPLAY_HEALTH_LOCK:
        snapshot = dict(_REPLAY_HEALTH)
    snapshot.pop("database_path", None)
    return snapshot


def _reset_replay_health_for_database(database_path: str | None) -> None:
    with _REPLAY_HEALTH_LOCK:
        if _REPLAY_HEALTH.get("database_path") == database_path:
            return
        _REPLAY_HEALTH.update(
            {
                "sync_status": "healthy",
                "replay_stalled": False,
                "replay_last_progress_at": None,
                "replay_last_imported_height": 0,
                "replay_consecutive_failures": 0,
                "divergence_detected": False,
                "divergence_reason": None,
                "auto_recovery_active": False,
                "database_path": database_path,
            }
        )


def _connection_database_path(connection: Any) -> str | None:
    try:
        row = connection.execute("PRAGMA database_list").fetchone()
        if row is None:
            return None
        keys = row.keys()
        if "file" in keys:
            return str(row["file"])
        return str(row[2])
    except Exception:
        return None


def _mark_replay_divergent(reason: str) -> None:
    now = utc_now()
    with _REPLAY_HEALTH_LOCK:
        _REPLAY_HEALTH.update(
            {
                "sync_status": "divergent",
                "replay_stalled": True,
                "divergence_detected": True,
                "divergence_reason": reason,
                "replay_consecutive_failures": int(_REPLAY_HEALTH.get("replay_consecutive_failures") or 0) + 1,
                "replay_last_progress_at": _REPLAY_HEALTH.get("replay_last_progress_at") or now,
            }
        )


def clear_replay_liveness_status(sync_status: str = "catching_up") -> None:
    with _REPLAY_HEALTH_LOCK:
        _REPLAY_HEALTH.update(
            {
                "sync_status": sync_status,
                "replay_stalled": False,
                "replay_consecutive_failures": 0,
                "divergence_detected": False,
                "divergence_reason": None,
                "auto_recovery_active": False,
            }
        )


def set_replay_auto_recovery_active(active: bool) -> None:
    with _REPLAY_HEALTH_LOCK:
        _REPLAY_HEALTH["auto_recovery_active"] = bool(active)


def _update_replay_liveness(response: dict[str, Any], snapshot: dict[str, Any]) -> None:
    errors = [str(item) for item in response.get("errors") or []]
    error_text = " ; ".join(errors)
    lower_error_text = error_text.lower()
    queue_size = int(snapshot.get("queue_size") or 0)
    current_height = int(snapshot.get("current_height") or 0)
    imported = int(response.get("imported") or 0)
    headers_imported = int(response.get("headers_imported") or 0)
    normalized = int(response.get("normalized") or 0)
    pre_snapshot_skipped = int(response.get("headers_skipped_pre_snapshot") or 0)
    progress = imported + headers_imported + normalized + pre_snapshot_skipped
    divergence_reason = next((marker for marker in _DIVERGENCE_MARKERS if marker in lower_error_text), None)
    now = utc_now()

    with _REPLAY_HEALTH_LOCK:
        if divergence_reason:
            _REPLAY_HEALTH.update(
                {
                    "sync_status": "divergent",
                    "replay_stalled": True,
                    "divergence_detected": True,
                    "divergence_reason": error_text[:500] or divergence_reason,
                    "replay_consecutive_failures": int(_REPLAY_HEALTH.get("replay_consecutive_failures") or 0) + 1,
                    "replay_last_progress_at": _REPLAY_HEALTH.get("replay_last_progress_at") or now,
                    "replay_last_imported_height": current_height,
                }
            )
            return

        if progress > 0:
            _REPLAY_HEALTH.update(
                {
                    "sync_status": "catching_up" if queue_size > 0 else "healthy",
                    "replay_stalled": False,
                    "replay_last_progress_at": now,
                    "replay_last_imported_height": current_height,
                    "replay_consecutive_failures": 0,
                    "divergence_detected": False,
                    "divergence_reason": None,
                }
            )
            return

        if queue_size > 0:
            failures = int(_REPLAY_HEALTH.get("replay_consecutive_failures") or 0) + 1
            stalled = failures >= max(1, int(REPLAY_STALL_FAILURES))
            _REPLAY_HEALTH.update(
                {
                    "sync_status": "stalled" if stalled else "catching_up",
                    "replay_stalled": stalled,
                    "replay_consecutive_failures": failures,
                    "replay_last_progress_at": _REPLAY_HEALTH.get("replay_last_progress_at") or now,
                    "replay_last_imported_height": current_height,
                    "divergence_reason": error_text[:500] if error_text else _REPLAY_HEALTH.get("divergence_reason"),
                }
            )
            return

        _REPLAY_HEALTH.update(
            {
                "sync_status": "healthy",
                "replay_stalled": False,
                "replay_last_imported_height": current_height,
                "replay_consecutive_failures": 0,
                "divergence_detected": False,
                "divergence_reason": None,
            }
        )


def get_replay_status() -> dict[str, Any]:
    with get_connection() as connection:
        _reset_replay_health_for_database(_connection_database_path(connection))
        snapshot = _replay_queue_snapshot(connection)
    avg_ms = float(_REPLAY_METRICS.get("avg_ms") or 0.0)
    queue_size = int(snapshot["queue_size"])
    if queue_size == 0 and not _REPLAY_LOCK.locked():
        with _REPLAY_HEALTH_LOCK:
            _REPLAY_HEALTH.update(
                {
                    "sync_status": "healthy",
                    "replay_stalled": False,
                    "replay_consecutive_failures": 0,
                    "divergence_detected": False,
                    "divergence_reason": None,
                }
            )
    eta = round((queue_size * avg_ms) / 1000.0, 3) if avg_ms > 0 and queue_size > 0 else None
    return {
        **snapshot,
        "active": bool(_REPLAY_METRICS.get("active")) or _REPLAY_LOCK.locked(),
        "replay_queue_size": queue_size,
        "replay_blocks_per_second": round(float(_REPLAY_METRICS.get("blocks_per_second") or 0.0), 6),
        "replay_avg_ms": round(avg_ms, 3),
        "replay_last_processed_height": int(_REPLAY_METRICS.get("last_processed_height") or 0),
        "replay_eta_seconds": eta,
        "last_started_at": _REPLAY_METRICS.get("last_started_at"),
        "last_completed_at": _REPLAY_METRICS.get("last_completed_at"),
        "last_batch_size": int(_REPLAY_METRICS.get("last_batch_size") or 0),
        "last_processed": int(_REPLAY_METRICS.get("last_processed") or 0),
        "last_duration_ms": round(float(_REPLAY_METRICS.get("last_duration_ms") or 0.0), 3),
        "total_processed": int(_REPLAY_METRICS.get("total_processed") or 0),
        "total_batches": int(_REPLAY_METRICS.get("total_batches") or 0),
        "last_error": _REPLAY_METRICS.get("last_error"),
        **_replay_health_snapshot(),
        "checked_at": utc_now(),
    }


async def start_replay_worker() -> None:
    global _REPLAY_WORKER_TASK, _REPLAY_WORKER_STOP
    if not REPLAY_WORKER_ENABLED or _REPLAY_WORKER_TASK is not None:
        return
    _REPLAY_WORKER_STOP = asyncio.Event()
    _REPLAY_WORKER_TASK = asyncio.create_task(_replay_worker_loop())


async def stop_replay_worker() -> None:
    global _REPLAY_WORKER_TASK, _REPLAY_WORKER_STOP
    if _REPLAY_WORKER_STOP is not None:
        _REPLAY_WORKER_STOP.set()
    if _REPLAY_WORKER_TASK is not None:
        try:
            await asyncio.wait_for(_REPLAY_WORKER_TASK, timeout=5)
        except TimeoutError:
            _REPLAY_WORKER_TASK.cancel()
        finally:
            _REPLAY_WORKER_TASK = None
            _REPLAY_WORKER_STOP = None


async def _replay_worker_loop() -> None:
    while _REPLAY_WORKER_STOP is None or not _REPLAY_WORKER_STOP.is_set():
        try:
            status = await asyncio.to_thread(get_replay_status)
            if (
                int(status.get("queue_size") or 0) > 0
                and not bool(status.get("active"))
                and status.get("sync_status") != "divergent"
            ):
                await asyncio.to_thread(replay_finalized_blocks, REPLAY_BATCH_SIZE)
        except Exception as exc:
            _REPLAY_METRICS["last_error"] = str(exc)
            _mark_replay_divergent(str(exc))
        try:
            await asyncio.wait_for(
                _REPLAY_WORKER_STOP.wait() if _REPLAY_WORKER_STOP is not None else asyncio.sleep(0),
                timeout=max(0.1, float(REPLAY_WORKER_INTERVAL_SECONDS)),
            )
        except TimeoutError:
            continue


def replay_finalized_blocks(limit: int = 100) -> dict[str, Any]:
    limit = max(1, min(int(limit or 1), 200))
    if not _REPLAY_LOCK.acquire(blocking=False):
        status = get_replay_status()
        return {
            "status": "active",
            "processed": 0,
            "imported": 0,
            "skipped": 0,
            "headers_imported": 0,
            "headers_skipped": 0,
            "headers_skipped_pre_snapshot": 0,
            "normalized": 0,
            "missing_ancestors": 0,
            "errors": [],
            "checked_at": utc_now(),
            **status,
        }
    started = time.perf_counter()
    started_at = utc_now()
    _REPLAY_METRICS.update(
        {
            "active": True,
            "last_started_at": started_at,
            "last_batch_size": limit,
            "last_error": None,
        }
    )
    logger.info("Picoin replay started batch_size=%s", limit)
    normalized = 0
    imported = 0
    skipped = 0
    headers_imported = 0
    headers_skipped = 0
    headers_skipped_pre_snapshot = 0
    errors: list[str] = []
    missing_ancestors = 0
    try:
        with get_connection() as connection:
            normalized += _mark_existing_block_proposals_imported(connection)
            headers_skipped_pre_snapshot += _quarantine_pre_snapshot_headers(connection)
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
                proposal_id = row["proposal_id"]
                connection.execute("SAVEPOINT replay_finalized_block")
                try:
                    block = json.loads(row["payload"])
                    did_import = _import_finalized_block(connection, block, proposal_id)
                    connection.execute("RELEASE SAVEPOINT replay_finalized_block")
                except Exception as exc:
                    connection.execute("ROLLBACK TO SAVEPOINT replay_finalized_block")
                    connection.execute("RELEASE SAVEPOINT replay_finalized_block")
                    skipped += 1
                    reason = str(exc)
                    if _is_missing_ancestor_replay_error(reason):
                        connection.execute(
                            """
                            UPDATE consensus_block_proposals
                            SET status = 'pending_missing_ancestors',
                                rejection_reason = ?,
                                updated_at = ?
                            WHERE proposal_id = ?
                            """,
                            (reason, utc_now(), proposal_id),
                        )
                        continue
                    errors.append(f"proposal {proposal_id}: {reason}")
                    if any(marker in reason.lower() for marker in _DIVERGENCE_MARKERS):
                        logger.error(
                            "Picoin replay divergence proposal_id=%s reason=%s",
                            proposal_id,
                            reason,
                        )
                    connection.execute(
                        """
                        UPDATE consensus_block_proposals
                        SET rejection_reason = ?, updated_at = ?
                        WHERE proposal_id = ?
                        """,
                        (reason, utc_now(), proposal_id),
                    )
                    continue
                if did_import:
                    imported += 1
                    _promote_ready_missing_ancestor_proposals(connection)
                    connection.execute(
                        "UPDATE consensus_finalizations SET imported = 1 WHERE proposal_id = ?",
                        (proposal_id,),
                    )
                    connection.execute(
                        "UPDATE consensus_block_proposals SET status = 'imported', updated_at = ? WHERE proposal_id = ?",
                        (utc_now(), proposal_id),
                    )
                else:
                    skipped += 1
            remaining = max(limit - imported - skipped, 0)
            if remaining:
                header_result = _replay_pending_block_headers(connection, remaining)
                headers_imported = header_result["imported"]
                headers_skipped = header_result["skipped"]
                headers_skipped_pre_snapshot += header_result.get("skipped_pre_snapshot", 0)
                errors.extend(header_result["errors"])
            missing_ancestors = _missing_ancestor_count(connection)
    except Exception as exc:
        errors.append(str(exc))
        logger.exception("Picoin replay failed")
        response = {
            "status": "error",
            "processed": imported + skipped + headers_imported + headers_skipped,
            "imported": imported,
            "skipped": skipped,
            "headers_imported": headers_imported,
            "headers_skipped": headers_skipped,
            "headers_skipped_pre_snapshot": headers_skipped_pre_snapshot,
            "normalized": normalized,
            "missing_ancestors": missing_ancestors,
            "errors": errors[:20],
            "checked_at": utc_now(),
        }
        _record_replay_metrics(response, started, started_at)
        _REPLAY_LOCK.release()
        return response
    processed = imported + skipped + headers_imported + headers_skipped
    status = "ok"
    if errors or missing_ancestors:
        status = "partial"
    response = {
        "status": status,
        "processed": processed,
        "imported": imported,
        "skipped": skipped,
        "headers_imported": headers_imported,
        "headers_skipped": headers_skipped,
        "headers_skipped_pre_snapshot": headers_skipped_pre_snapshot,
        "normalized": normalized,
        "missing_ancestors": missing_ancestors,
        "errors": errors[:20],
        "checked_at": utc_now(),
    }
    _record_replay_metrics(response, started, started_at)
    logger.info(
        "Picoin replay completed status=%s processed=%s imported=%s headers_imported=%s queue_size=%s avg_ms=%s",
        response["status"],
        response["processed"],
        response["imported"],
        response["headers_imported"],
        response.get("queue_size"),
        response.get("replay_avg_ms"),
    )
    _REPLAY_LOCK.release()
    return response


def _record_replay_metrics(response: dict[str, Any], started: float, started_at: str) -> None:
    duration_ms = max(0.001, (time.perf_counter() - started) * 1000.0)
    processed = int(response.get("imported") or 0) + int(response.get("headers_imported") or 0)
    with get_connection() as connection:
        _reset_replay_health_for_database(_connection_database_path(connection))
        snapshot = _replay_queue_snapshot(connection)
    if processed > 0:
        batch_avg = duration_ms / processed
        previous_avg = float(_REPLAY_METRICS.get("avg_ms") or 0.0)
        avg_ms = batch_avg if previous_avg <= 0 else (previous_avg * 0.75) + (batch_avg * 0.25)
    else:
        avg_ms = float(_REPLAY_METRICS.get("avg_ms") or 0.0)
    _REPLAY_METRICS.update(
        {
            "active": False,
            "last_started_at": started_at,
            "last_completed_at": utc_now(),
            "last_processed": processed,
            "last_processed_height": int(snapshot.get("current_height") or 0),
            "last_duration_ms": duration_ms,
            "avg_ms": avg_ms,
            "blocks_per_second": (processed / (duration_ms / 1000.0)) if processed > 0 else 0.0,
            "total_processed": int(_REPLAY_METRICS.get("total_processed") or 0) + processed,
            "total_batches": int(_REPLAY_METRICS.get("total_batches") or 0) + 1,
            "last_error": "; ".join(response.get("errors") or []) or None,
        }
    )
    _update_replay_liveness(response, snapshot)
    health = _replay_health_snapshot()
    queue_size = int(snapshot.get("queue_size") or 0)
    eta = None
    if float(_REPLAY_METRICS.get("avg_ms") or 0.0) > 0 and queue_size > 0:
        eta = round((queue_size * float(_REPLAY_METRICS["avg_ms"])) / 1000.0, 3)
    response.update(
        {
            **snapshot,
            "active": False,
            "replay_queue_size": queue_size,
            "replay_blocks_per_second": round(float(_REPLAY_METRICS.get("blocks_per_second") or 0.0), 6),
            "replay_avg_ms": round(float(_REPLAY_METRICS.get("avg_ms") or 0.0), 3),
            "replay_last_processed_height": int(_REPLAY_METRICS.get("last_processed_height") or 0),
            "replay_eta_seconds": eta,
            **health,
        }
    )


def _missing_ancestor_count(connection: Any) -> int:
    proposals = connection.execute(
        "SELECT COUNT(*) AS count FROM consensus_block_proposals WHERE status = 'pending_missing_ancestors'"
    ).fetchone()
    headers = connection.execute(
        "SELECT COUNT(*) AS count FROM network_block_headers WHERE status = 'pending_missing_ancestors'"
    ).fetchone()
    return int((proposals["count"] if proposals else 0) or 0) + int((headers["count"] if headers else 0) or 0)


def _mark_existing_block_proposals_imported(connection: Any) -> int:
    timestamp = utc_now()
    cursor = connection.execute(
        """
        UPDATE consensus_block_proposals
        SET status = 'imported',
            rejection_reason = COALESCE(rejection_reason, 'block already imported locally'),
            updated_at = ?
        WHERE status NOT IN ('imported', 'rejected')
          AND EXISTS (
              SELECT 1
              FROM blocks
              WHERE blocks.block_hash = consensus_block_proposals.block_hash
          )
        """,
        (timestamp,),
    )
    return int(cursor.rowcount)


def _quarantine_pre_snapshot_headers(connection: Any) -> int:
    active_base = active_snapshot_base_in_connection(connection)
    if active_base is None or not active_base.get("state_applied"):
        return 0
    timestamp = utc_now()
    cursor = connection.execute(
        """
        UPDATE network_block_headers
        SET status = 'skipped_pre_snapshot',
            reason = 'block covered by active snapshot base'
        WHERE status IN ('pending_replay', 'pending_missing_ancestors')
          AND height <= ?
        """,
        (int(active_base["height"]),),
    )
    if cursor.rowcount:
        _record_consensus_event(
            connection,
            "pre_snapshot_headers_quarantined",
            "skipped",
            {"count": int(cursor.rowcount), "snapshot_height": int(active_base["height"]), "updated_at": timestamp},
        )
    return int(cursor.rowcount)


def _replay_pending_block_headers(connection: Any, limit: int) -> dict[str, Any]:
    active_base = active_snapshot_base_in_connection(connection)
    snapshot_height = int(active_base["height"]) if active_base is not None and active_base.get("state_applied") else 0
    rows = connection.execute(
        """
        SELECT block_hash, height, payload
        FROM network_block_headers
        WHERE status IN ('pending_replay', 'pending_missing_ancestors')
          AND height > ?
        ORDER BY height ASC, received_at ASC
        LIMIT ?
        """,
        (snapshot_height, limit),
    ).fetchall()
    imported = 0
    skipped = 0
    skipped_pre_snapshot = 0
    errors: list[str] = []
    for row in rows:
        block_hash = row["block_hash"]
        if snapshot_height and int(row["height"]) <= snapshot_height:
            skipped_pre_snapshot += 1
            connection.execute(
                """
                UPDATE network_block_headers
                SET status = 'skipped_pre_snapshot',
                    reason = 'block covered by active snapshot base'
                WHERE block_hash = ?
                """,
                (block_hash,),
            )
            continue
        block = _replay_payload_for_block_header(connection, block_hash, row["payload"])
        connection.execute("SAVEPOINT replay_pending_header")
        try:
            did_import = _import_finalized_block(connection, block, f"header:{block_hash}")
            connection.execute("RELEASE SAVEPOINT replay_pending_header")
        except Exception as exc:
            connection.execute("ROLLBACK TO SAVEPOINT replay_pending_header")
            connection.execute("RELEASE SAVEPOINT replay_pending_header")
            skipped += 1
            reason = str(exc)
            missing_ancestor = _is_missing_ancestor_replay_error(reason)
            status = "pending_missing_ancestors" if missing_ancestor else "pending_replay"
            if not missing_ancestor:
                errors.append(f"header {block_hash}: {reason}")
            if not missing_ancestor and any(marker in reason.lower() for marker in _DIVERGENCE_MARKERS):
                logger.error(
                    "Picoin replay divergence header=%s height=%s reason=%s",
                    block_hash,
                    row["height"],
                    reason,
                )
            connection.execute(
                """
                UPDATE network_block_headers
                SET status = ?, reason = ?
                WHERE block_hash = ?
                """,
                (status, reason, block_hash),
            )
            continue
        if did_import:
            imported += 1
            _promote_ready_missing_ancestor_proposals(connection)
            connection.execute(
                """
                UPDATE network_block_headers
                SET status = 'imported', reason = 'imported via canonical header replay'
                WHERE block_hash = ?
                """,
                (block_hash,),
            )
            connection.execute(
                """
                UPDATE consensus_block_proposals
                SET status = 'imported',
                    rejection_reason = COALESCE(rejection_reason, 'imported via canonical header replay'),
                    updated_at = ?
                WHERE block_hash = ? AND status NOT IN ('imported', 'rejected')
                """,
                (utc_now(), block_hash),
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
    return {"imported": imported, "skipped": skipped, "skipped_pre_snapshot": skipped_pre_snapshot, "errors": errors}


def _replay_payload_for_block_header(connection: Any, block_hash: str, header_payload: str) -> dict[str, Any]:
    block = json.loads(header_payload)
    proposal = connection.execute(
        """
        SELECT payload
        FROM consensus_block_proposals
        WHERE block_hash = ? AND status NOT IN ('rejected')
        ORDER BY
            CASE status
                WHEN 'imported' THEN 0
                WHEN 'finalized' THEN 1
                WHEN 'pending' THEN 2
                ELSE 3
            END,
            updated_at DESC
        LIMIT 1
        """,
        (block_hash,),
    ).fetchone()
    if proposal is None:
        return block
    proposal_block = json.loads(proposal["payload"])
    if proposal_block.get("block_hash") != block_hash:
        return block
    return {**block, **proposal_block}


def select_fork_choice(
    height: int | None = None,
    previous_hash: str | None = None,
    connection: Any | None = None,
) -> dict[str, Any] | None:
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
        if previous_hash is None:
            rows = connection.execute(
                """
                SELECT *
                FROM consensus_block_proposals
                WHERE height = ? AND status NOT IN ('rejected')
                AND NOT EXISTS (
                    SELECT 1
                    FROM blocks
                    WHERE blocks.height = consensus_block_proposals.height
                      AND blocks.block_hash != consensus_block_proposals.block_hash
                )
                """,
                (height,),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT *
                FROM consensus_block_proposals
                WHERE height = ? AND previous_hash = ? AND status NOT IN ('rejected')
                AND NOT EXISTS (
                    SELECT 1
                    FROM blocks
                    WHERE blocks.height = consensus_block_proposals.height
                      AND blocks.block_hash != consensus_block_proposals.block_hash
                )
                """,
                (height, previous_hash),
            ).fetchall()
        choices = []
        for row in rows:
            choices.append(_proposal_with_fork_score(connection, row_to_dict(row)))
        if not choices:
            return None
        choices.sort(key=_fork_choice_sort_key)
        return choices[0]
    finally:
        if owns_connection:
            connection.close()


def list_fork_choice_groups(limit: int = 10, connection: Any | None = None) -> list[dict[str, Any]]:
    owns_connection = connection is None
    if owns_connection:
        connection = get_connection()
    try:
        groups = connection.execute(
            """
            SELECT height, previous_hash, COUNT(*) AS proposal_count
            FROM consensus_block_proposals
            WHERE status NOT IN ('rejected')
            AND NOT EXISTS (
                SELECT 1
                FROM blocks
                WHERE blocks.height = consensus_block_proposals.height
                  AND blocks.block_hash != consensus_block_proposals.block_hash
            )
            GROUP BY height, previous_hash
            HAVING COUNT(*) > 1
            ORDER BY height DESC, previous_hash ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for group in groups:
            candidates = [
                _proposal_with_fork_score(connection, row_to_dict(row))
                for row in connection.execute(
                    """
                    SELECT *
                    FROM consensus_block_proposals
                    WHERE height = ? AND previous_hash = ? AND status NOT IN ('rejected')
                    AND NOT EXISTS (
                        SELECT 1
                        FROM blocks
                        WHERE blocks.height = consensus_block_proposals.height
                          AND blocks.block_hash != consensus_block_proposals.block_hash
                    )
                    """,
                    (group["height"], group["previous_hash"]),
                ).fetchall()
            ]
            candidates.sort(key=_fork_choice_sort_key)
            winner = candidates[0] if candidates else None
            result.append(
                {
                    "height": int(group["height"]),
                    "previous_hash": group["previous_hash"],
                    "proposal_count": int(group["proposal_count"]),
                    "winner": _fork_choice_summary(winner) if winner is not None else None,
                    "candidates": [_fork_choice_summary(candidate) for candidate in candidates],
                }
            )
        return result
    finally:
        if owns_connection:
            connection.close()


def list_orphan_candidates(limit: int = 20, connection: Any | None = None) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 1), 100))
    owns_connection = connection is None
    if owns_connection:
        connection = get_connection()
    try:
        candidates: list[dict[str, Any]] = []
        seen: set[tuple[int, str, str]] = set()
        for source in _queued_block_candidates(connection, safe_limit * 3):
            block = source.get("block") or {}
            try:
                height = int(block.get("height") or source.get("height") or 0)
            except (TypeError, ValueError):
                continue
            if height <= 1:
                continue
            previous_hash = str(block.get("previous_hash") or source.get("previous_hash") or "")
            if not previous_hash:
                continue
            local_parent = row_to_dict(
                connection.execute(
                    """
                    SELECT height, block_hash, previous_hash, task_id, miner_id, timestamp
                    FROM blocks
                    WHERE height = ?
                    """,
                    (height - 1,),
                ).fetchone()
            )
            if local_parent is None or str(local_parent["block_hash"]) == previous_hash:
                continue
            key = (height - 1, str(local_parent["block_hash"]), previous_hash)
            if key in seen:
                continue
            seen.add(key)
            remote_parent = _find_queued_block_by_hash(connection, previous_hash)
            local_certificate = _local_finality_certificate_summary(
                connection,
                int(local_parent["height"]),
                str(local_parent["block_hash"]),
            )
            child_certificate = _block_finality_certificate_summary(block)
            remote_parent_certificate = _block_finality_certificate_summary(
                (remote_parent or {}).get("block") or {}
            )
            verdict = "remote_chain_has_certified_child" if child_certificate["quorum_met"] else "missing_remote_parent"
            candidates.append(
                {
                    "local_height": int(local_parent["height"]),
                    "local_block_hash": local_parent["block_hash"],
                    "local_task_id": local_parent.get("task_id"),
                    "remote_parent_hash": previous_hash,
                    "remote_parent_known": remote_parent is not None,
                    "remote_parent_source": (remote_parent or {}).get("source"),
                    "remote_parent_certificate": remote_parent_certificate,
                    "strongest_child": {
                        "height": height,
                        "block_hash": block.get("block_hash") or source.get("block_hash"),
                        "source": source["source"],
                        "status": source.get("status"),
                        "reason": source.get("reason"),
                        "certificate": child_certificate,
                    },
                    "local_certificate": local_certificate,
                    "verdict": verdict,
                    "reorg_required": child_certificate["quorum_met"],
                    "recovery_action": "canonical_reorg_required" if child_certificate["quorum_met"] else "fetch_missing_parent",
                }
            )
            if len(candidates) >= safe_limit:
                break
        return candidates
    finally:
        if owns_connection:
            connection.close()


def _queued_block_candidates(connection: Any, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    proposal_rows = connection.execute(
        """
        SELECT proposal_id AS id, block_hash, height, previous_hash, status,
               rejection_reason AS reason, payload, updated_at AS seen_at
        FROM consensus_block_proposals
        WHERE status NOT IN ('rejected', 'imported')
        ORDER BY height ASC, updated_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    for row in proposal_rows:
        block = _json_block_payload(row["payload"])
        rows.append(
            {
                "source": "proposal",
                "id": row["id"],
                "block_hash": row["block_hash"],
                "height": int(row["height"]),
                "previous_hash": row["previous_hash"],
                "status": row["status"],
                "reason": row["reason"],
                "seen_at": row["seen_at"],
                "block": block,
            }
        )
    header_rows = connection.execute(
        """
        SELECT block_hash AS id, block_hash, height, previous_hash, status,
               reason, payload, received_at AS seen_at
        FROM network_block_headers
        WHERE status IN ('pending_replay', 'pending_missing_ancestors')
        ORDER BY height ASC, received_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    for row in header_rows:
        block = _json_block_payload(row["payload"])
        rows.append(
            {
                "source": "header",
                "id": row["id"],
                "block_hash": row["block_hash"],
                "height": int(row["height"]),
                "previous_hash": row["previous_hash"],
                "status": row["status"],
                "reason": row["reason"],
                "seen_at": row["seen_at"],
                "block": block,
            }
        )
    rows.sort(key=lambda item: (int(item.get("height") or 0), str(item.get("seen_at") or ""), str(item["source"])))
    return rows[:limit]


def _find_queued_block_by_hash(connection: Any, block_hash: str) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT proposal_id AS id, block_hash, height, previous_hash, status,
               rejection_reason AS reason, payload, updated_at AS seen_at
        FROM consensus_block_proposals
        WHERE block_hash = ? AND status NOT IN ('rejected')
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (block_hash,),
    ).fetchone()
    if row is not None:
        return {
            "source": "proposal",
            "id": row["id"],
            "block_hash": row["block_hash"],
            "height": int(row["height"]),
            "previous_hash": row["previous_hash"],
            "status": row["status"],
            "reason": row["reason"],
            "seen_at": row["seen_at"],
            "block": _json_block_payload(row["payload"]),
        }
    row = connection.execute(
        """
        SELECT block_hash AS id, block_hash, height, previous_hash, status,
               reason, payload, received_at AS seen_at
        FROM network_block_headers
        WHERE block_hash = ?
        ORDER BY received_at DESC
        LIMIT 1
        """,
        (block_hash,),
    ).fetchone()
    if row is None:
        return None
    return {
        "source": "header",
        "id": row["id"],
        "block_hash": row["block_hash"],
        "height": int(row["height"]),
        "previous_hash": row["previous_hash"],
        "status": row["status"],
        "reason": row["reason"],
        "seen_at": row["seen_at"],
        "block": _json_block_payload(row["payload"]),
    }


def _json_block_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    try:
        decoded = json.loads(payload or "{}")
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _block_finality_certificate_summary(block: dict[str, Any] | None) -> dict[str, Any]:
    certificate = (block or {}).get("finality_certificate") or {}
    if not isinstance(certificate, dict):
        certificate = {}
    return _certificate_summary(certificate)


def _local_finality_certificate_summary(connection: Any, height: int, block_hash: str) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT required_approvals, approval_count, certificate_hash, job_id, task_id, created_at
        FROM finality_certificates
        WHERE block_height = ? AND block_hash = ?
        """,
        (height, block_hash),
    ).fetchone()
    if row is None:
        return _certificate_summary({})
    return _certificate_summary(row_to_dict(row))


def _certificate_summary(certificate: dict[str, Any]) -> dict[str, Any]:
    try:
        required = int(certificate.get("required_approvals") or 0)
    except (TypeError, ValueError):
        required = 0
    try:
        approvals = int(certificate.get("approval_count") or 0)
    except (TypeError, ValueError):
        approvals = 0
    return {
        "required_approvals": required,
        "approval_count": approvals,
        "quorum_met": required > 0 and approvals >= required,
        "certificate_hash": certificate.get("certificate_hash"),
        "job_id": certificate.get("job_id"),
        "task_id": certificate.get("task_id"),
        "created_at": certificate.get("created_at"),
    }


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
            f"""
            SELECT v.*, validators.trust_score, validators.{VALIDATOR_ELIGIBILITY_STAKE_FIELD} AS eligibility_stake
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
            vote["weight"] = _validator_weight(vote.get("trust_score"), vote.get("eligibility_stake"))
            votes.append(vote)
        return votes


def debug_block_determinism(height: int) -> dict[str, Any]:
    if height < 1:
        raise ConsensusError(422, "height must be positive")
    with get_connection() as connection:
        header = connection.execute(
            """
            SELECT block_hash, payload, status, reason, received_at
            FROM network_block_headers
            WHERE height = ?
            ORDER BY
                CASE status
                    WHEN 'pending_replay' THEN 0
                    WHEN 'pending_missing_ancestors' THEN 1
                    WHEN 'imported' THEN 2
                    ELSE 3
                END,
                received_at DESC
            LIMIT 1
            """,
            (height,),
        ).fetchone()
        proposal = connection.execute(
            """
            SELECT block_hash, payload, status, updated_at
            FROM consensus_block_proposals
            WHERE height = ? AND status NOT IN ('rejected')
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (height,),
        ).fetchone()
        local = connection.execute(
            """
            SELECT height, previous_hash, miner_id, range_start, range_end, algorithm,
                   result_hash, merkle_root, samples, timestamp, block_hash, reward,
                   tx_merkle_root, tx_count, tx_hashes, fee_reward, state_root,
                   difficulty, task_id, protocol_params_id, protocol_version,
                   validation_mode, total_task_ms, total_block_ms,
                   fraudulent, fraud_reason, fraud_detected_at
            FROM blocks
            WHERE height = ?
            """,
            (height,),
        ).fetchone()
    source = None
    block: dict[str, Any] | None = None
    source_meta: dict[str, Any] = {}
    if header is not None and header["payload"]:
        source = "network_block_headers"
        block = json.loads(header["payload"])
        source_meta = {
            "status": header["status"],
            "reason": header["reason"],
            "received_at": header["received_at"],
        }
    elif proposal is not None and proposal["payload"]:
        source = "consensus_block_proposals"
        block = json.loads(proposal["payload"])
        source_meta = {"status": proposal["status"], "updated_at": proposal["updated_at"]}
    elif local is not None:
        source = "blocks"
        block = row_to_dict(local)
        block["samples"] = json.loads(block["samples"]) if isinstance(block.get("samples"), str) else block["samples"]
        block["tx_hashes"] = json.loads(block["tx_hashes"]) if isinstance(block.get("tx_hashes"), str) else []
    if block is None:
        raise ConsensusError(404, "block payload not found")
    debug = block_hash_debug(block)
    return {"height": height, "source": source, "source_meta": source_meta, **debug}


def replay_divergence_report(from_height: int, to_height: int, peer: str | None = None) -> dict[str, Any]:
    from_height = max(1, int(from_height))
    to_height = max(from_height, int(to_height))
    remote_by_height: dict[int, dict[str, Any]] = {}
    if peer:
        peer_url = peer.rstrip("/")
        cursor = from_height - 1
        while cursor < to_height:
            limit = min(100, to_height - cursor)
            response = requests.get(
                f"{peer_url}/node/sync/blocks?from_height={cursor}&limit={limit}",
                timeout=20,
            )
            response.raise_for_status()
            blocks = response.json().get("blocks") or []
            if not blocks:
                break
            for block in blocks:
                height = int(block.get("height") or 0)
                if from_height <= height <= to_height:
                    remote_by_height[height] = block
            cursor = max(int(block.get("height") or cursor) for block in blocks)

    checked: list[dict[str, Any]] = []
    first_divergence: dict[str, Any] | None = None
    with get_connection() as connection:
        for height in range(from_height, to_height + 1):
            block = row_to_dict(
                connection.execute(
                    """
                    SELECT height, block_hash, previous_hash, state_root, timestamp, miner_id,
                           miner_reward_address, reward, fee_reward, tx_count, tx_hashes
                    FROM blocks
                    WHERE height = ?
                    """,
                    (height,),
                ).fetchone()
            )
            remote = remote_by_height.get(height)
            if block is None:
                item = {
                    "height": height,
                    "status": "missing_local",
                    "expected_state_root": remote.get("state_root") if remote else None,
                    "block_hash": remote.get("block_hash") if remote else None,
                }
                checked.append(item)
                first_divergence = first_divergence or item
                break
            actual_state_root = calculate_state_root(connection, height, block.get("timestamp"))
            expected_state_root = (remote or block).get("state_root")
            item = {
                "height": height,
                "status": "ok" if expected_state_root == actual_state_root else "diverged",
                "expected_state_root": expected_state_root,
                "actual_state_root": actual_state_root,
                "block_hash": block.get("block_hash"),
                "remote_block_hash": remote.get("block_hash") if remote else None,
                "previous_hash": block.get("previous_hash"),
                "tx_count": int(block.get("tx_count") or 0),
                "tx_hashes": json.loads(block.get("tx_hashes") or "[]") if isinstance(block.get("tx_hashes"), str) else block.get("tx_hashes"),
                "reward": block.get("reward"),
                "fee_reward": block.get("fee_reward"),
                "miner_id": block.get("miner_id"),
                "miner_reward_address": block.get("miner_reward_address"),
            }
            checked.append(item)
            if item["status"] != "ok":
                item["balances_sample"] = balance_snapshot(connection, height, block.get("timestamp"))[:25]
                first_divergence = item
                break
    return {
        "status": "ok" if first_divergence is None else "diverged",
        "from_height": from_height,
        "to_height": to_height,
        "peer": peer,
        "first_divergence_height": first_divergence.get("height") if first_divergence else None,
        "expected_state_root": first_divergence.get("expected_state_root") if first_divergence else None,
        "actual_state_root": first_divergence.get("actual_state_root") if first_divergence else None,
        "mismatched_accounts": first_divergence.get("balances_sample", []) if first_divergence else [],
        "replay_operations": checked,
        "checked_at": utc_now(),
    }


def consensus_status() -> dict[str, Any]:
    with get_connection() as connection:
        counts = connection.execute(
            """
            SELECT consensus_block_proposals.status, COUNT(*) AS count
            FROM consensus_block_proposals
            WHERE consensus_block_proposals.status NOT IN ('rejected')
            AND NOT EXISTS (
                SELECT 1
                FROM blocks
                WHERE blocks.height = consensus_block_proposals.height
                  AND blocks.block_hash != consensus_block_proposals.block_hash
            )
            GROUP BY status
            """
        ).fetchall()
        latest = connection.execute(
            "SELECT height, block_hash FROM blocks ORDER BY height DESC LIMIT 1"
        ).fetchone()
        finalizations = connection.execute("SELECT COUNT(*) AS count FROM consensus_finalizations").fetchone()
        eligible_count = _eligible_validator_count_for_quorum(connection)
        required_approvals = _required_validator_approvals_for_quorum(connection)
        validation_timing = connection.execute(
            """
            SELECT
                COUNT(*) AS jobs_total,
                COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0) AS jobs_pending,
                COALESCE(SUM(CASE WHEN status = 'pending' AND first_vote_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS pending_with_votes,
                COALESCE(AVG(waiting_for_first_vote_ms), 0) AS avg_waiting_for_first_vote_ms,
                COALESCE(AVG(waiting_for_quorum_ms), 0) AS avg_waiting_for_quorum_ms,
                COALESCE(AVG(finalization_ms), 0) AS avg_finalization_ms
            FROM validation_jobs
            """
        ).fetchone()
        missing_ancestor_rows = connection.execute(
            """
            SELECT *
            FROM consensus_block_proposals
            WHERE status = 'pending_missing_ancestors'
            ORDER BY height ASC, created_at ASC
            LIMIT 10
            """
        ).fetchall()
        fork_groups = list_fork_choice_groups(limit=10, connection=connection)
        competing_proposals = sum(int(group["proposal_count"]) for group in fork_groups)
        orphan_candidates = list_orphan_candidates(limit=10, connection=connection)
    quorum_warning = None
    if eligible_count < required_approvals:
        quorum_warning = (
            f"eligible_validators={eligible_count} is below required_validator_approvals={required_approvals}"
        )
    return {
        "required_validator_approvals": required_approvals,
        "eligible_validators": eligible_count,
        "quorum_warning": quorum_warning,
        "fork_choice_rule": FORK_CHOICE_RULE,
        "latest_block_height": latest["height"] if latest else 0,
        "latest_block_hash": latest["block_hash"] if latest else GENESIS_HASH,
        "proposals": {row["status"]: row["count"] for row in counts},
        "missing_ancestor_proposals": [
            {
                "proposal_id": proposal["proposal_id"],
                "height": proposal["height"],
                "block_hash": proposal["block_hash"],
                "previous_hash": proposal["previous_hash"],
                "age_seconds": proposal.get("age_seconds"),
                "missing_ancestor_hash": proposal.get("missing_ancestor_hash"),
                "missing_ancestor_height": proposal.get("missing_ancestor_height"),
            }
            for proposal in (_decode_proposal(row_to_dict(row)) for row in missing_ancestor_rows)
            if proposal is not None
        ],
        "finalizations": finalizations["count"] if finalizations else 0,
        "fork_group_count": len(fork_groups),
        "competing_proposal_count": competing_proposals,
        "fork_groups": fork_groups,
        "fork_choices": [group["winner"] for group in fork_groups if group.get("winner") is not None],
        "orphan_candidate_count": len(orphan_candidates),
        "orphan_candidates": orphan_candidates,
        "validation_timing": {
            "jobs_total": int(validation_timing["jobs_total"] if validation_timing else 0),
            "jobs_pending": int(validation_timing["jobs_pending"] if validation_timing else 0),
            "pending_with_votes": int(validation_timing["pending_with_votes"] if validation_timing else 0),
            "avg_waiting_for_first_vote_ms": round(float(validation_timing["avg_waiting_for_first_vote_ms"] if validation_timing else 0), 2),
            "avg_waiting_for_quorum_ms": round(float(validation_timing["avg_waiting_for_quorum_ms"] if validation_timing else 0), 2),
            "avg_finalization_ms": round(float(validation_timing["avg_finalization_ms"] if validation_timing else 0), 2),
        },
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
    if not _matches_local_parent(block, tip):
        raise ConsensusError(409, "finalized block previous_hash does not match local chain tip")
    _reject_duplicate_block_material(connection, block)
    _ensure_miner(connection, block["miner_id"])
    local_protocol_params_id = _resolve_local_protocol_params_id(connection, block.get("protocol_params_id"))
    task_id = _ensure_task(connection, block, local_protocol_params_id)
    _validate_finality_certificate_for_block(block, task_id)
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
            result_hash, merkle_root, samples, timestamp, block_hash, reward, reward_units, tx_merkle_root,
            tx_count, tx_hashes, fee_reward, fee_reward_units, miner_reward_address, state_root, difficulty, task_id, protocol_params_id,
            protocol_version, validation_mode, total_task_ms, total_block_ms, validation_ms
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            to_units(block["reward"]),
            block.get("tx_merkle_root"),
            int(block.get("tx_count") or 0),
            json.dumps(block.get("tx_hashes") or [], sort_keys=True),
            round(float(block.get("fee_reward") or 0), 8),
            to_units(block.get("fee_reward") or 0),
            block.get("miner_reward_address"),
            block.get("state_root"),
            block.get("difficulty"),
            task_id,
            local_protocol_params_id,
            block.get("protocol_version", PROTOCOL_VERSION),
            block.get("validation_mode", VALIDATION_MODE),
            block.get("total_task_ms"),
            block.get("total_block_ms"),
            None,
        ),
    )
    record_miner_block_reward(
        connection,
        miner_id=block["miner_id"],
        account_id=block.get("miner_reward_address") or block["miner_id"],
        account_type="wallet" if block.get("miner_reward_address") else "miner",
        block_height=block["height"],
        amount=block["reward"],
        reason="distributed block finalized",
        related_id=proposal_id,
        description="distributed finalized miner reward",
        timestamp=timestamp,
    )
    if transactions:
        apply_block_transactions(
            connection,
            miner_id=block.get("miner_reward_address") or block["miner_id"],
            miner_account_type="wallet" if block.get("miner_reward_address") else "miner",
            block_height=block["height"],
            transactions=transactions,
            timestamp=timestamp,
        )
    total_block_reward_units = total_units_from_miner_reward_units(to_units(block["reward"]))
    total_block_reward = reward_units_to_float(total_block_reward_units)
    record_science_reserve_for_block(connection, block["height"], total_block_reward)
    record_scientific_development_treasury_for_block(connection, block["height"], total_block_reward)
    _apply_distributed_validator_rewards(connection, block, proposal_id, total_block_reward_units, timestamp)
    mature_block_rewards(connection, current_height=block["height"], timestamp=timestamp)
    from app.services.mining import _maybe_run_scheduled_retroactive_audit

    _maybe_run_scheduled_retroactive_audit(connection, block["height"])
    state_root = update_block_state_root(connection, block["height"], timestamp)
    if block.get("state_root") and block["state_root"] != state_root:
        logger.error(
            "state_root mismatch after canonical replay: height=%s expected=%s computed=%s previous_hash=%s block_hash=%s tx_count=%s reward=%s fee_reward=%s miner_id=%s miner_reward_address=%s validator_reward=%s",
            block.get("height"),
            block.get("state_root"),
            state_root,
            block.get("previous_hash"),
            block.get("block_hash"),
            int(block.get("tx_count") or 0),
            block.get("reward"),
            block.get("fee_reward"),
            block.get("miner_id"),
            block.get("miner_reward_address"),
            block.get("validator_reward"),
        )
        raise ConsensusError(422, "state_root mismatch after canonical replay")
    maybe_create_checkpoint_in_connection(connection, block["height"])
    _store_finality_certificate_for_block(connection, block, task_id)
    return True


def _finality_certificate_hash(certificate: dict[str, Any]) -> str:
    return sha256_text(
        canonical_json(
            {
                "payload": certificate.get("payload") or {},
                "votes": certificate.get("votes") or [],
            }
        )
    )


def _validate_finality_certificate_for_block(block: dict[str, Any], task_id: str) -> None:
    certificate = block.get("finality_certificate")
    if certificate is None:
        return
    if not isinstance(certificate, dict):
        raise ConsensusError(422, "invalid finality_certificate")
    payload = certificate.get("payload") or {}
    votes = certificate.get("votes") or []
    if not isinstance(payload, dict) or not isinstance(votes, list):
        raise ConsensusError(422, "invalid finality certificate payload")
    block_payload = payload.get("block") or {}
    validation_payload = payload.get("validation") or {}
    if int(certificate.get("block_height") or 0) != int(block["height"]):
        raise ConsensusError(422, "finality certificate height mismatch")
    if certificate.get("block_hash") != block["block_hash"] or block_payload.get("block_hash") != block["block_hash"]:
        raise ConsensusError(422, "finality certificate block hash mismatch")
    if certificate.get("task_id") != task_id or block_payload.get("task_id") != task_id:
        raise ConsensusError(422, "finality certificate task mismatch")
    if certificate.get("job_id") != validation_payload.get("job_id"):
        raise ConsensusError(422, "finality certificate job mismatch")
    if certificate.get("miner_id") != block["miner_id"]:
        raise ConsensusError(422, "finality certificate miner mismatch")
    computed_hash = _finality_certificate_hash(certificate)
    if computed_hash != certificate.get("certificate_hash"):
        raise ConsensusError(422, "finality certificate hash mismatch")
    required = int(certificate.get("required_approvals") or 0)
    approval_count = int(certificate.get("approval_count") or 0)
    if required <= 0 or approval_count < required or len(votes) < required:
        raise ConsensusError(422, "finality certificate does not contain quorum approvals")
    if int(validation_payload.get("required_approvals") or 0) != required:
        raise ConsensusError(422, "finality certificate required approval mismatch")
    if int(validation_payload.get("approval_count") or 0) != approval_count:
        raise ConsensusError(422, "finality certificate approval count mismatch")
    for vote in votes:
        if not isinstance(vote, dict):
            raise ConsensusError(422, "invalid finality certificate vote")
        signature_payload = vote.get("signature_payload") or {}
        if (
            not vote.get("approved")
            or signature_payload.get("job_id") != certificate.get("job_id")
            or signature_payload.get("task_id") != task_id
            or signature_payload.get("validator_id") != vote.get("validator_id")
        ):
            raise ConsensusError(422, "finality certificate vote payload mismatch")
        public_key = vote.get("validator_public_key")
        signature = vote.get("signature")
        if not public_key or not signature or not verify_payload_signature(public_key, signature_payload, signature):
            raise ConsensusError(422, "finality certificate vote signature invalid")


def _store_finality_certificate_for_block(connection: Any, block: dict[str, Any], task_id: str) -> None:
    certificate = block.get("finality_certificate")
    if certificate is None:
        return
    _validate_finality_certificate_for_block(block, task_id)
    payload = certificate.get("payload") or {}
    votes = certificate.get("votes") or []
    validation_payload = payload.get("validation") or {}
    block_payload = payload.get("block") or {}
    job_id = str(certificate["job_id"])
    existing_job = connection.execute("SELECT 1 FROM validation_jobs WHERE job_id = ?", (job_id,)).fetchone()
    if existing_job is None:
        connection.execute(
            """
            INSERT INTO validation_jobs (
                job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
                samples, tx_merkle_root, selected_tx_hashes_hash, tx_count,
                tx_fee_total_units, status, created_at, completed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'approved', ?, ?)
            """,
            (
                job_id,
                task_id,
                certificate["miner_id"],
                block["result_hash"],
                block.get("merkle_root") or "",
                validation_payload.get("challenge_seed") or "",
                json.dumps(block.get("samples") or [], sort_keys=True),
                block.get("tx_merkle_root") or "",
                block_payload.get("selected_tx_hashes_hash"),
                int(block.get("tx_count") or 0),
                int(block_payload.get("tx_fee_total_units") or 0),
                certificate["created_at"],
                certificate["created_at"],
            ),
        )
    connection.execute(
        """
        INSERT INTO finality_certificates (
            block_height, block_hash, task_id, job_id, miner_id, network_id, chain_id,
            protocol_version, protocol_params_id, required_approvals, approval_count,
            certificate_hash, payload_json, votes_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(block_height) DO UPDATE SET
            block_hash = excluded.block_hash,
            task_id = excluded.task_id,
            job_id = excluded.job_id,
            miner_id = excluded.miner_id,
            network_id = excluded.network_id,
            chain_id = excluded.chain_id,
            protocol_version = excluded.protocol_version,
            protocol_params_id = excluded.protocol_params_id,
            required_approvals = excluded.required_approvals,
            approval_count = excluded.approval_count,
            certificate_hash = excluded.certificate_hash,
            payload_json = excluded.payload_json,
            votes_json = excluded.votes_json,
            created_at = excluded.created_at
        """,
        (
            int(certificate["block_height"]),
            certificate["block_hash"],
            task_id,
            certificate["job_id"],
            certificate["miner_id"],
            certificate["network_id"],
            str(certificate.get("chain_id")),
            certificate["protocol_version"],
            certificate.get("protocol_params_id"),
            int(certificate["required_approvals"]),
            int(certificate["approval_count"]),
            certificate["certificate_hash"],
            canonical_json(payload),
            canonical_json(votes),
            certificate["created_at"],
        ),
    )


def _apply_distributed_validator_rewards(
    connection: Any,
    block: dict[str, Any],
    proposal_id: str,
    total_block_reward_units: int,
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
            (proposal_id, _required_validator_approvals_for_quorum(connection)),
        ).fetchall()
        validator_ids = [row["validator_id"] for row in validator_rows]
    if not validator_ids:
        return
    pool_units = to_units(payload_reward.get("pool") or 0)
    if pool_units <= 0:
        pool_units = validator_reward_pool_units({"base_reward": reward_units_to_float(total_block_reward_units)})
    per_validator_units = to_units(payload_reward.get("per_validator") or 0)
    if per_validator_units <= 0:
        per_validator_units = pool_units // len(validator_ids)
    reward_addresses = payload_reward.get("reward_addresses") if isinstance(payload_reward.get("reward_addresses"), dict) else {}
    distributed_units = 0
    for index, validator_id in enumerate(validator_ids, start=1):
        amount_units = per_validator_units
        if index == len(validator_ids):
            amount_units = pool_units - distributed_units
        distributed_units += amount_units
        reward_address = reward_addresses.get(validator_id)
        legacy_reward_wallet = not reward_address and is_valid_address(validator_id)
        if not legacy_reward_wallet:
            _ensure_validator(connection, validator_id, timestamp)
        _apply_account_delta(
            connection,
            reward_address or validator_id,
            "wallet" if reward_address or legacy_reward_wallet else "validator",
            reward_units_to_float(amount_units),
            "validator_reward",
            block_height,
            proposal_id,
            "distributed consensus validator reward",
            timestamp,
        )
        if not legacy_reward_wallet:
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
        INSERT INTO validators (validator_id, name, public_key, registered_at, last_seen_at, stake_locked)
        VALUES (?, ?, '', ?, ?, ?)
        """,
        (validator_id, f"distributed:{validator_id}", timestamp, timestamp, VALIDATOR_REGISTRATION_STAKE),
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
        "SELECT balance, balance_units FROM balances WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    amount_units = to_units(amount)
    previous_units = units_from_db(current["balance"], current["balance_units"]) if current is not None else 0
    balance_after_units = previous_units + amount_units
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account_id,
            account_type,
            units_to_float(amount_units),
            amount_units,
            balance_after,
            balance_after_units,
            entry_type,
            block_height,
            related_id,
            description,
            timestamp,
        ),
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


def _ensure_task(connection: Any, block: dict[str, Any], local_protocol_params_id: int | None = None) -> str:
    task_id = block.get("task_id") or f"distributed_task_{block['height']}_{block['block_hash'][:12]}"
    existing = connection.execute("SELECT task_id FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    if existing is not None:
        return task_id
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
            local_protocol_params_id,
            block["timestamp"],
            block["timestamp"],
        ),
    )
    return task_id


def _resolve_local_protocol_params_id(connection: Any, protocol_params_id: Any) -> int | None:
    if protocol_params_id is None:
        return None
    existing = connection.execute(
        "SELECT id FROM protocol_params WHERE id = ?",
        (int(protocol_params_id),),
    ).fetchone()
    if existing is not None:
        return int(protocol_params_id)
    return None


def _reject_duplicate_block_material(connection: Any, block: dict[str, Any]) -> None:
    duplicate_hash = connection.execute(
        "SELECT height FROM blocks WHERE result_hash = ?",
        (block["result_hash"],),
    ).fetchone()
    if duplicate_hash is not None:
        raise ConsensusError(409, "duplicate result_hash")


def _validate_block_hash(block: dict[str, Any]) -> None:
    missing = sorted(BLOCK_REQUIRED_FIELDS - set(block))
    if missing:
        raise ConsensusError(422, f"missing block fields: {', '.join(missing)}")
    if len(str(block["block_hash"])) != 64 or len(str(block["previous_hash"])) != 64:
        raise ConsensusError(422, "invalid block hash length")
    if block["block_hash"] not in _block_hash_candidates(block):
        diagnostics = block_hash_debug(block)
        candidate_names = [
            item["name"] for item in diagnostics["candidates"][:12]
        ]
        raise ConsensusError(
            422,
            "block_hash does not match canonical payload; "
            f"expected={diagnostics['expected_hash']} computed={diagnostics['computed_hash']} "
            f"candidate_count={len(diagnostics['candidates'])} candidate_sample={candidate_names}",
        )


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
    if normalized.get("total_block_ms") is not None:
        normalized["total_block_ms"] = int(normalized["total_block_ms"])
    if isinstance(normalized.get("tx_hashes"), str):
        normalized["tx_hashes"] = json.loads(normalized["tx_hashes"])
    normalized["tx_count"] = int(normalized.get("tx_count") or 0)
    normalized["fee_reward"] = round(float(normalized.get("fee_reward") or 0), 8)
    if normalized.get("miner_reward_address"):
        normalized["miner_reward_address"] = str(normalized["miner_reward_address"]).strip().upper()
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
    if block.get("total_block_ms") is not None:
        payload["total_block_ms"] = int(block["total_block_ms"])
    if block.get("merkle_root"):
        payload["merkle_root"] = block["merkle_root"]
    if block.get("miner_reward_address"):
        payload["miner_reward_address"] = str(block["miner_reward_address"]).strip().upper()
    if int(block.get("tx_count") or 0) > 0:
        payload["tx_merkle_root"] = block.get("tx_merkle_root")
        payload["tx_count"] = int(block.get("tx_count") or 0)
        payload["tx_hashes"] = block.get("tx_hashes") or []
        payload["fee_reward"] = round(float(block.get("fee_reward") or 0), 8)
    if include_protocol:
        payload["protocol_version"] = block.get("protocol_version", PROTOCOL_VERSION)
        payload["validation_mode"] = block.get("validation_mode", VALIDATION_MODE)
    return payload


def _block_hash_variant_payloads(block: dict[str, Any]) -> list[dict[str, Any]]:
    mandatory = {
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
    optional_groups: list[tuple[str, dict[str, Any]]] = []
    if block.get("difficulty") is not None:
        optional_groups.append(("difficulty", {"difficulty": float(block["difficulty"])}))
    if block.get("protocol_params_id") is not None:
        optional_groups.append(("protocol_params_id", {"protocol_params_id": int(block["protocol_params_id"])}))
    if block.get("total_block_ms") is not None:
        optional_groups.append(("total_block_ms", {"total_block_ms": int(block["total_block_ms"])}))
    if block.get("merkle_root"):
        optional_groups.append(("merkle_root", {"merkle_root": block["merkle_root"]}))
    if block.get("miner_reward_address"):
        optional_groups.append(
            ("miner_reward_address", {"miner_reward_address": str(block["miner_reward_address"]).strip().upper()})
        )
    protocol_version = block.get("protocol_version", PROTOCOL_VERSION)
    validation_mode = block.get("validation_mode", VALIDATION_MODE)
    optional_groups.append(("protocol_version", {"protocol_version": protocol_version}))
    optional_groups.append(("validation_mode", {"validation_mode": validation_mode}))
    if int(block.get("tx_count") or 0) > 0:
        optional_groups.append(
            (
                "transactions",
                {
                    "tx_merkle_root": block.get("tx_merkle_root"),
                    "tx_count": int(block.get("tx_count") or 0),
                    "tx_hashes": block.get("tx_hashes") or [],
                    "fee_reward": round(float(block.get("fee_reward") or 0), 8),
                },
            )
        )
    optional_groups.append(
        (
            "fraud_fields",
            {
                "fraudulent": bool(block.get("fraudulent", False)),
                "fraud_reason": block.get("fraud_reason"),
                "fraud_detected_at": block.get("fraud_detected_at"),
            },
        )
    )

    # Current canonical shape first, then explicit historical schema variants.
    variants: list[dict[str, Any]] = [
        _canonical_block_payload(block, include_protocol=True),
        _canonical_block_payload(block, include_protocol=False),
    ]
    seen = {canonical_json(payload) for payload in variants}
    for size in range(len(optional_groups) + 1):
        for combo in itertools.combinations(optional_groups, size):
            payload = dict(mandatory)
            names = []
            for name, fields in combo:
                names.append(name)
                payload.update(fields)
            key = canonical_json(payload)
            if key in seen:
                continue
            seen.add(key)
            variants.append(payload)
    return variants


def _block_hash_variant_diagnostics(block: dict[str, Any]) -> list[dict[str, Any]]:
    expected = block.get("block_hash")
    diagnostics = []
    for index, payload in enumerate(_block_hash_variant_payloads(block)):
        hash_input = canonical_json(payload)
        computed = sha256_text(hash_input)
        diagnostics.append(
            {
                "index": index,
                "name": "current" if index == 0 else "legacy_no_protocol" if index == 1 else f"schema_variant_{index}",
                "hash": computed,
                "matches": computed == expected,
                "payload": payload,
                "hash_input": hash_input,
            }
        )
    return diagnostics


def _block_hash_candidates(block: dict[str, Any]) -> set[str]:
    return {item["hash"] for item in _block_hash_variant_diagnostics(block)}


def block_hash_debug(block: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_block(block)
    candidates = _block_hash_variant_diagnostics(normalized)
    first = candidates[0]
    match = next((item for item in candidates if item["matches"]), None)
    return {
        "expected_hash": normalized.get("block_hash"),
        "computed_hash": first["hash"],
        "matched": match is not None,
        "matched_variant": match["name"] if match else None,
        "canonical_payload": first["payload"],
        "normalized_payload": normalized,
        "hash_input": first["hash_input"],
        "candidates": candidates,
    }


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


def _proposal_with_fork_score(connection: Any, row: dict[str, Any] | None) -> dict[str, Any]:
    proposal = _decode_proposal(row)
    proposal["approval_weight"] = _proposal_approval_weight(connection, proposal["proposal_id"])
    proposal["rejection_weight"] = _proposal_rejection_weight(connection, proposal["proposal_id"])
    return proposal


def _fork_choice_sort_key(proposal: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _fork_choice_status_rank(proposal["status"]),
        -float(proposal["approval_weight"]),
        float(proposal["rejection_weight"]),
        -int(proposal["approvals"]),
        int(proposal["rejections"]),
        proposal["created_at"],
        proposal["block_hash"],
    )


def _fork_choice_status_rank(status: str) -> int:
    return {
        "imported": 0,
        "finalized": 1,
        "approved": 2,
        "validation_pending": 3,
        "pending": 4,
        "pending_missing_ancestors": 5,
        "pending_replay": 6,
    }.get(status, 7)


def _fork_choice_summary(proposal: dict[str, Any]) -> dict[str, Any]:
    return {
        "height": proposal["height"],
        "previous_hash": proposal["previous_hash"],
        "proposal_id": proposal["proposal_id"],
        "block_hash": proposal["block_hash"],
        "status": proposal["status"],
        "approvals": proposal["approvals"],
        "rejections": proposal["rejections"],
        "approval_weight": proposal.get("approval_weight", 0.0),
        "rejection_weight": proposal.get("rejection_weight", 0.0),
        "created_at": proposal["created_at"],
    }


def _proposal_vote_weight(connection: Any, proposal_id: str, approved: bool) -> float:
    rows = connection.execute(
        f"""
        SELECT validators.trust_score, validators.{VALIDATOR_ELIGIBILITY_STAKE_FIELD} AS eligibility_stake
        FROM consensus_votes
        LEFT JOIN validators ON validators.validator_id = consensus_votes.validator_id
        WHERE consensus_votes.proposal_id = ? AND consensus_votes.approved = ?
        """,
        (proposal_id, 1 if approved else 0),
    ).fetchall()
    return round(sum(_validator_weight(row["trust_score"], row["eligibility_stake"]) for row in rows), 8)


def _validator_weight(trust_score: Any, eligibility_stake: Any) -> float:
    trust = max(0.0, float(trust_score if trust_score is not None else 0.0))
    stake = max(0.0, float(eligibility_stake if eligibility_stake is not None else 0.0))
    stake_units = min(stake / MIN_VALIDATOR_STAKE, 100.0) if MIN_VALIDATOR_STAKE > 0 else 0.0
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
    row["age_seconds"] = _age_seconds(row.get("created_at"))
    if row.get("status") == "pending_missing_ancestors":
        row["missing_ancestor_hash"] = row.get("previous_hash")
        row["missing_ancestor_height"] = max(0, int(row.get("height") or 0) - 1)
    return row


def _age_seconds(timestamp: Any) -> int | None:
    if not timestamp:
        return None
    try:
        value = str(timestamp).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - parsed).total_seconds()))
    except Exception:
        return None


def _record_consensus_event(connection: Any, event_type: str, status: str, details: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO network_sync_events (peer_id, event_type, direction, status, details, created_at)
        VALUES (NULL, ?, 'consensus', ?, ?, ?)
        """,
        (event_type, status, json.dumps(details, sort_keys=True), utc_now()),
    )
