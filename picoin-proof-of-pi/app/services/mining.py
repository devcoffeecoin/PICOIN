import json
import logging
import os
import random
import sqlite3
import threading
import time
import uuid
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.crypto import canonical_json, hash_block, hash_result, sha256_text
from app.core.difficulty import calculate_difficulty, calculate_reward
from app.core.economics import (
    miner_reward_units,
    reward_units_to_float,
    science_reserve_units_from_total,
    scientific_development_units_from_total,
    total_block_reward_units,
    validator_reward_pool_units,
)
from app.services.difficulty_service import DifficultyService
from app.core.merkle import verify_merkle_proof
from app.core.money import to_units, units_from_db, units_to_float
from app.core.performance import elapsed_ms, now_perf
from app.core.pi import calculate_pi_segment
from app.core.pi import pi_cache_info
from app.core.signatures import (
    build_commit_signature_payload,
    build_reveal_signature_payload,
    build_submission_signature_payload,
    build_validation_result_signature_payload,
    validate_public_key,
    verify_payload_signature,
)
from app.core.settings import (
    CHAIN_ID,
    BLOCK_MATURITY_DEPTH,
    COOLDOWN_AFTER_REJECTIONS,
    COOLDOWN_SECONDS,
    FAUCET_ALLOWED_NETWORKS,
    FAUCET_DEFAULT_AMOUNT,
    FAUCET_MAX_AMOUNT,
    FAUCET_RATE_LIMIT_MAX_REQUESTS,
    FAUCET_RATE_LIMIT_WINDOW_SECONDS,
    FRAUD_COOLDOWN_SECONDS,
    FRAUD_MINER_PENALTY_POINTS,
    FRAUD_VALIDATOR_INVALID_RESULTS,
    GENESIS_ACCOUNT_ID,
    GENESIS_SUPPLY,
    MAX_TRANSACTIONS_PER_BLOCK,
    MIN_VALIDATOR_STAKE,
    MINING_TASK_MODE,
    NETWORK_ID,
    NETWORK_PROFILE,
    NODE_TYPE,
    NODE_ROLE,
    PENALTY_DUPLICATE,
    PENALTY_INVALID_RESULT,
    PENALTY_INVALID_SIGNATURE,
    PROOF_OF_PI_REWARD_PERCENT,
    PROJECT_NAME,
    PROTOCOL_VERSION,
    RANGE_START_WINDOW_SIZE,
    RANGE_WINDOW_LOOKAHEAD_MULTIPLIER,
    RANGE_WINDOW_MAX_AGE_BLOCKS,
    RANGE_WINDOW_RETIRE_OCCUPANCY,
    REQUIRED_VALIDATOR_APPROVALS,
    RETROACTIVE_AUDIT_INTERVAL_BLOCKS,
    RETROACTIVE_AUDIT_REWARD_ACCOUNT_ID,
    RETROACTIVE_AUDIT_REWARD_PERCENT_OF_BLOCK,
    RETROACTIVE_AUDIT_SAMPLE_MULTIPLIER,
    RETARGET_EPOCH_BLOCKS,
    RETARGET_MAX_DIFFICULTY,
    RETARGET_MAX_PI_POSITION,
    RETARGET_MIN_DIFFICULTY,
    RETARGET_TARGET_BLOCK_MS,
    RETARGET_TOLERANCE,
    RETARGET_WINDOW_BLOCKS,
    RETARGET_MIN_SEGMENT_SIZE,
    RETARGET_MAX_SEGMENT_SIZE,
    SCIENCE_BASE_MONTHLY_QUOTA_UNITS,
    SCIENCE_COMPUTE_REWARD_PERCENT_OF_BLOCK,
    SCIENCE_RESERVE_ACCOUNT_ID,
    SCIENTIFIC_DEVELOPMENT_GOVERNANCE_WALLET,
    SCIENTIFIC_DEVELOPMENT_REWARD_PERCENT_OF_BLOCK,
    SCIENTIFIC_DEVELOPMENT_TREASURY_ACCOUNT_ID,
    SCIENTIFIC_DEVELOPMENT_TREASURY_WALLET,
    SCIENTIFIC_DEVELOPMENT_UNLOCK_INTERVAL_DAYS,
    TASK_RATE_LIMIT_MAX_ASSIGNMENTS,
    TASK_RATE_LIMIT_WINDOW_SECONDS,
    VALIDATOR_BAN_AFTER_INVALID_RESULTS,
    VALIDATOR_AVAILABILITY_WINDOW_SECONDS,
    VALIDATOR_COOLDOWN_AFTER_INVALID_RESULTS,
    VALIDATOR_COOLDOWN_SECONDS,
    VALIDATOR_ELIGIBILITY_STAKE_FIELD,
    VALIDATOR_ELIGIBILITY_STAKE_SOURCE,
    VALIDATOR_MIN_TRUST_SCORE,
    VALIDATOR_PENALTY_INVALID_SIGNATURE,
    VALIDATOR_ROTATION_WINDOW_SECONDS,
    VALIDATOR_REGISTRATION_STAKE,
    VALIDATOR_REWARD_PERCENT_OF_BLOCK,
    VALIDATOR_AUDITOR_REWARD_PERCENT,
    VALIDATOR_SELECTION_AVAILABILITY_WEIGHT,
    VALIDATOR_SELECTION_MODE,
    VALIDATOR_SELECTION_POOL_MULTIPLIER,
    VALIDATOR_SELECTION_ROTATION_WEIGHT,
    VALIDATOR_SELECTION_STAKE_WEIGHT,
    VALIDATOR_SELECTION_TRUST_WEIGHT,
    VALIDATOR_SLASH_INVALID_SIGNATURE,
    VALIDATION_MODE,
    get_dynamic_expiration,
)
from app.services.readiness import build_node_readiness, node_capabilities_for_role
from app.db import database as database_module
from app.db.database import get_connection, row_to_dict
from app.services.consensus import record_local_block_proposal
from app.services.rewards import (
    immature_reward_total_for_account,
    mature_block_rewards,
    record_miner_block_reward,
)
from app.services.science import record_science_reserve_for_block, science_events_for_node
from app.services.state import (
    active_snapshot_base,
    active_snapshot_base_in_connection,
    calculate_state_root,
    create_canonical_checkpoint_in_connection,
    maybe_create_checkpoint_in_connection,
    update_block_state_root,
)
from app.services.treasury import record_scientific_development_treasury_for_block
from app.services.transactions import (
    apply_block_transactions,
    freeze_transactions_for_competitive_round_task,
    freeze_transactions_for_task,
    get_task_tx_snapshot,
    load_snapshot_transactions,
    release_selected_transactions,
    selected_tx_hashes_hash,
    TransactionExecutionError,
    transaction_commitment,
)
from app.services.wallet import is_valid_address
from validator.proof import validate_submission


logger = logging.getLogger(__name__)
GENESIS_HASH = "0" * 64
ECONOMIC_AUDIT_TOLERANCE = 0.000001
COMPETITIVE_ROUND_ASSIGNMENT_MODE = "competitive_round"
PARTICIPANT_ONLINE_SECONDS = 120
PARTICIPANT_OFFLINE_SECONDS = 300
VALIDATOR_SYNC_LAG_BLOCKS = 3
VALIDATOR_OUT_OF_SYNC_SECONDS = 60
VALIDATION_JOB_ASSIGNMENT_TIMEOUT_SECONDS = 60
PARTICIPANT_LIVENESS_INTERVAL_SECONDS = int(os.getenv("PICOIN_LIVENESS_INTERVAL_SECONDS", "30"))
PARTICIPANT_LIVENESS_MIN_INTERVAL_SECONDS = int(
    os.getenv("PICOIN_LIVENESS_MIN_INTERVAL_SECONDS", str(max(5, PARTICIPANT_LIVENESS_INTERVAL_SECONDS)))
)
MINER_TASK_HEARTBEAT_MIN_INTERVAL_SECONDS = int(os.getenv("PICOIN_MINER_TASK_HEARTBEAT_MIN_INTERVAL_SECONDS", "60"))
EXPIRED_TASK_CLEANUP_MIN_INTERVAL_SECONDS = int(os.getenv("PICOIN_EXPIRED_TASK_CLEANUP_MIN_INTERVAL_SECONDS", "5"))
STATUS_ENDPOINT_CACHE_SECONDS = int(os.getenv("PICOIN_STATUS_ENDPOINT_CACHE_SECONDS", "10"))
HEALTH_ENDPOINT_CACHE_SECONDS = int(os.getenv("PICOIN_HEALTH_ENDPOINT_CACHE_SECONDS", "15"))
_PARTICIPANT_LIVENESS_TASK: asyncio.Task | None = None
_PARTICIPANT_LIVENESS_LOCK = threading.Lock()
_PARTICIPANT_LIVENESS_LAST_RUN_MONOTONIC = 0.0
_EXPIRED_TASK_CLEANUP_LOCK = threading.Lock()
_EXPIRED_TASK_CLEANUP_LAST_RUN_MONOTONIC = 0.0
_STATUS_ENDPOINT_CACHE_LOCK = threading.Lock()
_STATUS_ENDPOINT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


class MiningError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail


class ValidationFinalizationDeferred(Exception):
    pass


def utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def utc_now() -> str:
    return utc_now_dt().isoformat()


def iso_at(seconds_from_now: int) -> str:
    return (utc_now_dt() + timedelta(seconds=seconds_from_now)).isoformat()


def _status_cache_get(key: str, ttl_seconds: int) -> dict[str, Any] | None:
    if ttl_seconds <= 0:
        return None
    now = time.monotonic()
    with _STATUS_ENDPOINT_CACHE_LOCK:
        cached = _STATUS_ENDPOINT_CACHE.get(key)
        if cached is None:
            return None
        stored_at, payload = cached
        if now - stored_at > ttl_seconds:
            _STATUS_ENDPOINT_CACHE.pop(key, None)
            return None
        return payload


def _status_cache_set(key: str, payload: dict[str, Any]) -> dict[str, Any]:
    with _STATUS_ENDPOINT_CACHE_LOCK:
        _STATUS_ENDPOINT_CACHE[key] = (time.monotonic(), payload)
    return payload


def _cached_status_payload(key: str, ttl_seconds: int, builder: Any) -> dict[str, Any]:
    cached = _status_cache_get(key, ttl_seconds)
    if cached is not None:
        return cached
    return _status_cache_set(key, builder())


def _task_expiration_seconds_for_position(params: dict[str, Any], position: int | None) -> int:
    try:
        configured = int(params.get("task_expiration_seconds") or 0)
    except (TypeError, ValueError):
        configured = 0
    try:
        depth = max(1, int(position or 1))
    except (TypeError, ValueError):
        depth = 1
    return max(configured, int(get_dynamic_expiration(depth)))


def iso_ago(seconds_before_now: int) -> str:
    return (utc_now_dt() - timedelta(seconds=seconds_before_now)).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _elapsed_iso_ms(start_iso: str | None, end_iso: str | None) -> int | None:
    try:
        start = parse_iso(start_iso)
        end = parse_iso(end_iso)
    except (TypeError, ValueError):
        return None
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds() * 1000))


NODE_STARTED_AT = utc_now_dt()


def _normalize_reward_address(reward_address: str | None) -> str | None:
    if reward_address is None:
        return None
    normalized = reward_address.strip().upper()
    if not normalized:
        return None
    if not is_valid_address(normalized):
        raise MiningError(400, "reward_address must be a valid PI wallet address")
    return normalized


def miner_id_from_public_key(public_key: str) -> str:
    normalized = str(public_key or "").strip()
    return f"miner_{sha256_text(canonical_json({'public_key': normalized}))[:16]}"


def register_miner(name: str, public_key: str | None = None, reward_address: str | None = None) -> dict[str, Any]:
    if public_key is None:
        raise MiningError(400, "public_key is required")
    try:
        validate_public_key(public_key)
    except (RuntimeError, ValueError) as exc:
        raise MiningError(400, str(exc)) from exc
    reward_address = _normalize_reward_address(reward_address)

    timestamp = utc_now()
    with get_connection() as connection:
        existing = row_to_dict(
            connection.execute(
                """
                SELECT *
                FROM miners
                WHERE public_key = ?
                ORDER BY registered_at ASC, miner_id ASC
                LIMIT 1
                """,
                (public_key,),
            ).fetchone()
        )
        if existing is not None:
            connection.execute(
                """
                UPDATE miners
                SET name = COALESCE(NULLIF(?, ''), name),
                    reward_address = COALESCE(?, reward_address),
                    last_seen_at = ?,
                    last_heartbeat_at = ?,
                    online_status = 'online',
                    protocol_version = ?
                WHERE miner_id = ?
                """,
                (
                    (name or existing["miner_id"])[:80],
                    reward_address,
                    timestamp,
                    timestamp,
                    PROTOCOL_VERSION,
                    existing["miner_id"],
                ),
            )
            _ensure_balance_account(connection, existing["miner_id"], "miner")
            if reward_address:
                _ensure_balance_account(connection, reward_address, "wallet")
            row = connection.execute("SELECT * FROM miners WHERE miner_id = ?", (existing["miner_id"],)).fetchone()
            return enrich_miner(row_to_dict(row))

        miner_id = miner_id_from_public_key(public_key)
        collision = connection.execute(
            """
            SELECT public_key
            FROM miners
            WHERE miner_id = ?
              AND COALESCE(public_key, '') != ?
            LIMIT 1
            """,
            (miner_id, public_key),
        ).fetchone()
        if collision is not None:
            raise MiningError(409, "miner id collision for public_key")

        connection.execute(
            """
            INSERT INTO miners (
                miner_id, name, public_key, reward_address, registered_at,
                last_seen_at, last_heartbeat_at, online_status, protocol_version, enabled
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'online', ?, 1)
            ON CONFLICT(miner_id) DO UPDATE SET
                name = COALESCE(NULLIF(excluded.name, ''), miners.name),
                public_key = COALESCE(miners.public_key, excluded.public_key),
                reward_address = COALESCE(excluded.reward_address, miners.reward_address),
                last_seen_at = excluded.last_seen_at,
                last_heartbeat_at = excluded.last_heartbeat_at,
                online_status = 'online',
                protocol_version = excluded.protocol_version
            """,
            (miner_id, name, public_key, reward_address, timestamp, timestamp, timestamp, PROTOCOL_VERSION),
        )
        _ensure_balance_account(connection, miner_id, "miner")
        if reward_address:
            _ensure_balance_account(connection, reward_address, "wallet")
        row = connection.execute("SELECT * FROM miners WHERE miner_id = ?", (miner_id,)).fetchone()
    return enrich_miner(row_to_dict(row))


def get_miner(miner_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM miners WHERE miner_id = ?", (miner_id,)).fetchone()
    miner = row_to_dict(row)
    if miner is None:
        return None
    return enrich_miner(miner)


def register_validator(name: str, public_key: str, reward_address: str | None = None) -> dict[str, Any]:
    try:
        validate_public_key(public_key)
    except (RuntimeError, ValueError) as exc:
        raise MiningError(400, str(exc)) from exc
    reward_address = _normalize_reward_address(reward_address)

    validator_id = f"validator_{uuid.uuid4().hex[:16]}"
    timestamp = utc_now()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO validators (
                validator_id, name, public_key, reward_address, registered_at,
                last_seen_at, last_heartbeat_at, online_status, sync_status,
                protocol_version, enabled, stake_locked
            )
            VALUES (?, ?, ?, ?, ?, ?, NULL, 'offline', 'unknown', ?, 1, ?)
            """,
            (validator_id, name, public_key, reward_address, timestamp, timestamp, PROTOCOL_VERSION, VALIDATOR_REGISTRATION_STAKE),
        )
        _ensure_balance_account(connection, validator_id, "validator")
        if reward_address:
            _ensure_balance_account(connection, reward_address, "wallet")
        row = connection.execute("SELECT * FROM validators WHERE validator_id = ?", (validator_id,)).fetchone()
    return enrich_validator(row_to_dict(row))


def get_validator(validator_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM validators WHERE validator_id = ?", (validator_id,)).fetchone()
    return enrich_validator(row_to_dict(row))


def get_validators(limit: int = 100, eligible_only: bool = False) -> list[dict[str, Any]]:
    refresh_participant_liveness()
    where = ""
    params: tuple[Any, ...] = ()
    if eligible_only:
        where = f"""
        WHERE is_banned = 0
        AND enabled = 1
        AND online_status = 'online'
        AND sync_status != 'out_of_sync'
        AND COALESCE(node_id, '') != ''
        AND COALESCE(advertised_address, '') != ''
        AND protocol_version = ?
        AND {VALIDATOR_ELIGIBILITY_STAKE_FIELD} >= ?
        AND trust_score >= ?
        """
        params = (PROTOCOL_VERSION, MIN_VALIDATOR_STAKE, VALIDATOR_MIN_TRUST_SCORE)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT * FROM validators
            {where}
            ORDER BY trust_score DESC, {VALIDATOR_ELIGIBILITY_STAKE_FIELD} DESC, accepted_jobs DESC, registered_at ASC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        validators = [enrich_validator(row_to_dict(row), connection) for row in rows]
    validators.sort(key=lambda item: (-float(item["selection_score"]), item["validator_id"]))
    return validators


def enrich_validator(validator: dict[str, Any] | None, connection: Any | None = None) -> dict[str, Any] | None:
    if validator is None:
        return None
    completed_jobs = int(validator["accepted_jobs"]) + int(validator["rejected_jobs"])
    total_validation_ms = int(validator.get("total_validation_ms") or 0)
    validator["completed_jobs"] = completed_jobs
    validator["avg_validation_ms"] = round(total_validation_ms / completed_jobs, 2) if completed_jobs else 0.0
    reward_address = validator.get("reward_address")
    validator["balance"] = get_balance_amount(reward_address or validator["validator_id"])
    validator["is_banned"] = bool(validator["is_banned"])
    validator["enabled"] = bool(validator.get("enabled", 1))
    validator["online_status"] = validator.get("online_status") or "offline"
    validator["sync_status"] = validator.get("sync_status") or "unknown"
    validator["eligibility_stake"] = round(_validator_eligibility_stake(validator), 8)
    validator["eligibility_stake_source"] = VALIDATOR_ELIGIBILITY_STAKE_SOURCE
    validator["eligible"] = _validator_row_is_eligible(validator)
    validator["total_rewards"] = _validator_reward_total(
        [validator["validator_id"], reward_address] if reward_address else [validator["validator_id"]]
    )
    if connection is None:
        with get_connection() as score_connection:
            selection = _validator_selection_metrics(score_connection, validator)
    else:
        selection = _validator_selection_metrics(connection, validator)
    validator.update(selection)
    return validator


def enrich_miner(miner: dict[str, Any] | None) -> dict[str, Any] | None:
    if miner is None:
        return None
    with get_connection() as connection:
        accepted_blocks = connection.execute(
            "SELECT COUNT(*) AS count, COALESCE(SUM(reward), 0) AS rewards FROM blocks WHERE miner_id = ?",
            (miner["miner_id"],),
        ).fetchone()
        rejected = connection.execute(
            "SELECT COUNT(*) AS count FROM submissions WHERE miner_id = ? AND accepted = 0",
            (miner["miner_id"],),
        ).fetchone()
    miner["accepted_blocks"] = accepted_blocks["count"]
    miner["total_rewards"] = accepted_blocks["rewards"]
    miner["rejected_submissions"] = rejected["count"]
    reward_address = miner.get("reward_address")
    miner["balance"] = get_balance_amount(reward_address or miner["miner_id"])
    miner["is_banned"] = bool(miner["is_banned"])
    miner["enabled"] = bool(miner.get("enabled", 1))
    miner["online_status"] = miner.get("online_status") or "offline"
    return miner


def _ensure_replay_can_accept_work() -> None:
    try:
        from app.services.consensus import get_replay_liveness_status

        replay_status = get_replay_liveness_status()
    except Exception:
        return
    sync_status = str(replay_status.get("sync_status") or "healthy")
    if sync_status in {"stalled", "divergent"}:
        reason = replay_status.get("divergence_reason") or f"replay {sync_status}"
        raise MiningError(503, f"node replay is {sync_status}: {reason}")


async def start_participant_liveness_worker() -> None:
    global _PARTICIPANT_LIVENESS_TASK
    if _PARTICIPANT_LIVENESS_TASK is not None and not _PARTICIPANT_LIVENESS_TASK.done():
        return
    _PARTICIPANT_LIVENESS_TASK = asyncio.create_task(_participant_liveness_loop())


async def stop_participant_liveness_worker() -> None:
    global _PARTICIPANT_LIVENESS_TASK
    task = _PARTICIPANT_LIVENESS_TASK
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    _PARTICIPANT_LIVENESS_TASK = None


async def _participant_liveness_loop() -> None:
    while True:
        try:
            refresh_participant_liveness()
        except Exception:
            pass
        await asyncio.sleep(max(5, PARTICIPANT_LIVENESS_INTERVAL_SECONDS))


def _heartbeat_signature_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "signature"}


VALIDATOR_HEARTBEAT_FUTURE_SKEW_SECONDS = 60


def _public_validator_heartbeat_payload(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "validator_id",
        "node_id",
        "public_key",
        "address",
        "local_height",
        "effective_height",
        "latest_block_hash",
        "pending_replay_blocks",
        "sync_lag",
        "version",
        "heartbeat_at",
        "signature",
    }
    return {key: payload[key] for key in allowed if key in payload and payload.get(key) is not None}


def _parse_heartbeat_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parse_iso(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _validator_heartbeat_time(payload: dict[str, Any], observed_at: str | None, now_iso: str) -> str:
    now_dt = parse_iso(now_iso) or utc_now_dt()
    heartbeat_dt = _parse_heartbeat_time(str(payload.get("heartbeat_at") or "") or None)
    if heartbeat_dt is None:
        heartbeat_dt = _parse_heartbeat_time(observed_at)
    if heartbeat_dt is None:
        heartbeat_dt = now_dt
    if heartbeat_dt > now_dt + timedelta(seconds=VALIDATOR_HEARTBEAT_FUTURE_SKEW_SECONDS):
        raise MiningError(400, "validator heartbeat timestamp is too far in the future")
    return heartbeat_dt.isoformat()


def _validator_heartbeat_id(payload: dict[str, Any]) -> str:
    public_payload = _public_validator_heartbeat_payload(payload)
    return sha256_text(canonical_json({"validator_heartbeat": public_payload}))


def _store_validator_heartbeat_observation(
    connection: Any,
    *,
    payload: dict[str, Any],
    heartbeat_at: str,
    observed_at: str,
    source_peer: str | None,
) -> tuple[str, bool]:
    public_payload = _public_validator_heartbeat_payload(payload)
    heartbeat_id = _validator_heartbeat_id(public_payload)
    result = connection.execute(
        """
        INSERT OR IGNORE INTO validator_heartbeats (
            heartbeat_id, validator_id, public_key, node_id, advertised_address,
            local_height, effective_height, latest_block_hash, pending_replay_blocks,
            sync_lag, protocol_version, heartbeat_at, observed_at, source_peer,
            signature, payload, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            heartbeat_id,
            str(public_payload.get("validator_id") or "").strip(),
            str(public_payload.get("public_key") or ""),
            str(public_payload.get("node_id") or "").strip(),
            str(public_payload.get("address") or "").strip().rstrip("/"),
            max(0, int(public_payload.get("local_height") or 0)),
            max(0, int(public_payload.get("effective_height") or 0)),
            public_payload.get("latest_block_hash"),
            max(0, int(public_payload.get("pending_replay_blocks") or 0)),
            max(0, int(public_payload.get("sync_lag") or 0)),
            str(public_payload.get("version") or PROTOCOL_VERSION),
            heartbeat_at,
            observed_at,
            source_peer,
            str(public_payload.get("signature") or ""),
            canonical_json(public_payload),
            utc_now(),
        ),
    )
    return heartbeat_id, result.rowcount > 0


def _status_from_heartbeat(last_heartbeat_at: str | None, now: datetime | None = None) -> str:
    if not last_heartbeat_at:
        return "offline"
    now_dt = now or utc_now_dt()
    heartbeat_dt = parse_iso(last_heartbeat_at)
    if heartbeat_dt is None:
        return "offline"
    age_seconds = (now_dt - heartbeat_dt).total_seconds()
    if age_seconds <= PARTICIPANT_ONLINE_SECONDS:
        return "online"
    if age_seconds <= PARTICIPANT_OFFLINE_SECONDS:
        return "stale"
    return "offline"


def _sync_status_from_metrics(
    *,
    sync_lag: int,
    pending_replay_blocks: int,
    out_of_sync_since: str | None,
    now: datetime | None = None,
) -> tuple[str, str | None]:
    now_dt = now or utc_now_dt()
    is_bad = sync_lag > VALIDATOR_SYNC_LAG_BLOCKS or pending_replay_blocks > 0
    if not is_bad:
        return "synced", None
    since = out_of_sync_since or now_dt.isoformat()
    since_dt = parse_iso(since)
    if since_dt is not None and (now_dt - since_dt).total_seconds() >= VALIDATOR_OUT_OF_SYNC_SECONDS:
        return "out_of_sync", since
    return "syncing", since


def _validator_row_is_eligible(validator: dict[str, Any]) -> bool:
    cooldown_until = parse_iso(validator.get("cooldown_until"))
    if cooldown_until is not None and cooldown_until > utc_now_dt():
        return False
    return (
        not bool(validator.get("is_banned"))
        and bool(validator.get("enabled", 1))
        and _validator_eligibility_stake(validator) >= MIN_VALIDATOR_STAKE
        and float(validator.get("trust_score") or 0) >= VALIDATOR_MIN_TRUST_SCORE
        and str(validator.get("online_status") or "") == "online"
        and str(validator.get("sync_status") or "") != "out_of_sync"
        and bool(str(validator.get("node_id") or "").strip())
        and bool(str(validator.get("advertised_address") or "").strip())
        and str(validator.get("protocol_version") or PROTOCOL_VERSION) == PROTOCOL_VERSION
    )


def _validator_reason_if_not_eligible(validator: dict[str, Any]) -> str | None:
    cooldown_until = parse_iso(validator.get("cooldown_until"))
    if cooldown_until is not None and cooldown_until > utc_now_dt():
        return f"validator is in cooldown until {validator.get('cooldown_until')}"
    if not bool(validator.get("enabled", 1)):
        return validator.get("reason_if_not_eligible") or "validator disabled"
    if bool(validator.get("is_banned")):
        return "validator banned"
    if str(validator.get("online_status") or "") != "online":
        return f"validator {validator.get('online_status') or 'offline'}"
    if not str(validator.get("node_id") or "").strip() or not str(validator.get("advertised_address") or "").strip():
        return "validator node heartbeat required"
    if str(validator.get("sync_status") or "unknown") == "out_of_sync":
        return "validator out of sync"
    if str(validator.get("protocol_version") or PROTOCOL_VERSION) != PROTOCOL_VERSION:
        return "protocol version mismatch"
    if _validator_eligibility_stake(validator) < MIN_VALIDATOR_STAKE:
        return _validator_min_stake_reason()
    if float(validator.get("trust_score") or 0) < VALIDATOR_MIN_TRUST_SCORE:
        return "validator trust score is below the minimum required"
    return None


def _validator_eligibility_stake(validator: dict[str, Any]) -> float:
    return float(validator.get(VALIDATOR_ELIGIBILITY_STAKE_FIELD) or 0)


def _validator_min_stake_reason() -> str:
    if VALIDATOR_ELIGIBILITY_STAKE_FIELD == "wallet_stake_locked":
        return "validator wallet-backed stake is below the minimum required"
    return "validator stake is below the minimum required"


def adaptive_required_validator_approvals(eligible_validators: int) -> int:
    if eligible_validators <= 0:
        return 1
    return max(1, eligible_validators)


def _effective_required_validator_approvals(connection: Any, params: dict[str, Any] | None = None) -> int:
    return max(1, int((params or {}).get("required_validator_approvals") or REQUIRED_VALIDATOR_APPROVALS))


def refresh_participant_liveness(now: datetime | None = None, *, force: bool = False) -> dict[str, Any]:
    global _PARTICIPANT_LIVENESS_LAST_RUN_MONOTONIC
    monotonic_now = time.monotonic()
    if not force and (
        monotonic_now - _PARTICIPANT_LIVENESS_LAST_RUN_MONOTONIC
    ) < PARTICIPANT_LIVENESS_MIN_INTERVAL_SECONDS:
        return {"updated": {"validators": 0, "miners": 0}, "checked_at": utc_now(), "skipped": "throttled"}
    if not _PARTICIPANT_LIVENESS_LOCK.acquire(blocking=False):
        return {"updated": {"validators": 0, "miners": 0}, "checked_at": utc_now(), "skipped": "already_running"}
    now_dt = now or utc_now_dt()
    updates = {"validators": 0, "miners": 0}
    try:
        with get_connection() as connection:
            validator_rows = connection.execute("SELECT * FROM validators").fetchall()
            for row in validator_rows:
                validator = row_to_dict(row)
                online_status = _status_from_heartbeat(validator.get("last_heartbeat_at"), now_dt)
                if bool(validator.get("is_banned")) or not bool(validator.get("enabled", 1)):
                    online_status = "offline"
                sync_status, out_of_sync_since = _sync_status_from_metrics(
                    sync_lag=int(validator.get("sync_lag") or 0),
                    pending_replay_blocks=int(validator.get("pending_replay_blocks") or 0),
                    out_of_sync_since=validator.get("out_of_sync_since"),
                    now=now_dt,
                )
                validator["online_status"] = online_status
                validator["sync_status"] = sync_status
                validator["out_of_sync_since"] = out_of_sync_since
                reason = _validator_reason_if_not_eligible(validator)
                connection.execute(
                    """
                    UPDATE validators
                    SET online_status = ?, sync_status = ?, out_of_sync_since = ?,
                        reason_if_not_eligible = ?
                    WHERE validator_id = ?
                    """,
                    (online_status, sync_status, out_of_sync_since, reason, validator["validator_id"]),
                )
                updates["validators"] += 1

            miner_rows = connection.execute("SELECT * FROM miners").fetchall()
            for row in miner_rows:
                miner = row_to_dict(row)
                online_status = _status_from_heartbeat(miner.get("last_heartbeat_at"), now_dt)
                if bool(miner.get("is_banned")) or not bool(miner.get("enabled", 1)):
                    online_status = "offline"
                connection.execute(
                    "UPDATE miners SET online_status = ? WHERE miner_id = ?",
                    (online_status, miner["miner_id"]),
                )
                updates["miners"] += 1
        _PARTICIPANT_LIVENESS_LAST_RUN_MONOTONIC = monotonic_now
        return {"updated": updates, "checked_at": now_dt.isoformat()}
    except sqlite3.OperationalError as exc:
        if "database is locked" in str(exc).lower():
            logger.warning("participant liveness refresh skipped: database is locked")
            return {"updated": updates, "checked_at": now_dt.isoformat(), "skipped": "database_locked"}
        raise
    finally:
        _PARTICIPANT_LIVENESS_LOCK.release()


def record_validator_heartbeat(
    payload: dict[str, Any],
    client_host: str | None = None,
    *,
    source_peer: str | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    signed_payload = _heartbeat_signature_payload(payload)
    public_key = str(payload.get("public_key") or "")
    try:
        validate_public_key(public_key)
        signature_valid = verify_payload_signature(public_key, signed_payload, str(payload.get("signature") or ""))
    except (RuntimeError, ValueError):
        signature_valid = False
    if not signature_valid:
        raise MiningError(401, "invalid validator heartbeat signature")

    validator_id = str(payload.get("validator_id") or "").strip()
    if not validator_id:
        raise MiningError(400, "validator_id is required")
    node_id = str(payload.get("node_id") or "").strip()
    advertised_address = str(payload.get("address") or "").strip().rstrip("/")
    if not node_id or not advertised_address:
        raise MiningError(400, "validator heartbeat requires active node_id and address")
    timestamp = utc_now()
    heartbeat_at = _validator_heartbeat_time(payload, observed_at, timestamp)
    heartbeat_online_status = _status_from_heartbeat(heartbeat_at)
    effective_height = max(0, int(payload.get("effective_height") or payload.get("local_height") or 0))
    sync_lag = max(0, int(payload.get("sync_lag") or 0))
    pending_replay = max(0, int(payload.get("pending_replay_blocks") or 0))
    heartbeat_id = ""
    heartbeat_inserted = False

    with get_connection() as connection:
        sync_lag = max(sync_lag, max(0, _latest_block_height(connection) - effective_height))
        heartbeat_id, heartbeat_inserted = _store_validator_heartbeat_observation(
            connection,
            payload=payload,
            heartbeat_at=heartbeat_at,
            observed_at=observed_at or heartbeat_at,
            source_peer=source_peer,
        )
        duplicate = row_to_dict(
            connection.execute(
                "SELECT validator_id FROM validators WHERE public_key = ? AND validator_id != ? LIMIT 1",
                (public_key, validator_id),
            ).fetchone()
        )
        if duplicate is not None:
            connection.execute(
                """
                INSERT INTO validators (
                    validator_id, name, public_key, registered_at, last_seen_at,
                    last_heartbeat_at, online_status, sync_status, enabled,
                    reason_if_not_eligible, node_id, advertised_address, last_ip,
                    effective_height, sync_lag, pending_replay_blocks, protocol_version,
                    stake_locked
                )
                VALUES (?, ?, ?, ?, ?, ?, 'duplicated_identity', 'unknown', 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(validator_id) DO UPDATE SET
                    public_key = excluded.public_key,
                    last_seen_at = excluded.last_seen_at,
                    last_heartbeat_at = excluded.last_heartbeat_at,
                    online_status = 'duplicated_identity',
                    enabled = 0,
                    reason_if_not_eligible = excluded.reason_if_not_eligible,
                    node_id = excluded.node_id,
                    advertised_address = excluded.advertised_address,
                    last_ip = excluded.last_ip,
                    effective_height = excluded.effective_height,
                    sync_lag = excluded.sync_lag,
                    pending_replay_blocks = excluded.pending_replay_blocks,
                    protocol_version = excluded.protocol_version
                """,
                (
                    validator_id,
                    str(payload.get("name") or validator_id)[:80],
                    public_key,
                    timestamp,
                    timestamp,
                    heartbeat_at,
                    "duplicate public key identity detected",
                    node_id,
                    advertised_address,
                    client_host,
                    effective_height,
                    sync_lag,
                    pending_replay,
                    str(payload.get("version") or PROTOCOL_VERSION),
                    0.0,
                ),
            )
        else:
            existing = row_to_dict(connection.execute("SELECT * FROM validators WHERE validator_id = ?", (validator_id,)).fetchone())
            sync_status, out_of_sync_since = _sync_status_from_metrics(
                sync_lag=sync_lag,
                pending_replay_blocks=pending_replay,
                out_of_sync_since=(existing or {}).get("out_of_sync_since"),
            )
            connection.execute(
                """
                INSERT INTO validators (
                    validator_id, name, public_key, registered_at, last_seen_at,
                    last_heartbeat_at, online_status, sync_status, out_of_sync_since,
                    node_id, advertised_address, last_ip, effective_height, sync_lag,
                    pending_replay_blocks, protocol_version, enabled, stake_locked
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(validator_id) DO UPDATE SET
                    name = COALESCE(NULLIF(excluded.name, ''), validators.name),
                    public_key = excluded.public_key,
                    last_seen_at = excluded.last_seen_at,
                    last_heartbeat_at = excluded.last_heartbeat_at,
                    online_status = excluded.online_status,
                    sync_status = excluded.sync_status,
                    out_of_sync_since = excluded.out_of_sync_since,
                    node_id = excluded.node_id,
                    advertised_address = excluded.advertised_address,
                    last_ip = excluded.last_ip,
                    effective_height = excluded.effective_height,
                    sync_lag = excluded.sync_lag,
                    pending_replay_blocks = excluded.pending_replay_blocks,
                    protocol_version = excluded.protocol_version
                """,
                (
                    validator_id,
                    str(payload.get("name") or validator_id)[:80],
                    public_key,
                    timestamp,
                    timestamp,
                    heartbeat_at,
                    heartbeat_online_status,
                    sync_status,
                    out_of_sync_since,
                    node_id,
                    advertised_address,
                    client_host,
                    effective_height,
                    sync_lag,
                    pending_replay,
                    str(payload.get("version") or PROTOCOL_VERSION),
                    VALIDATOR_REGISTRATION_STAKE,
                ),
            )
            _ensure_balance_account(connection, validator_id, "validator")
        row = connection.execute("SELECT * FROM validators WHERE validator_id = ?", (validator_id,)).fetchone()
        validator = row_to_dict(row)
        if validator is not None:
            reason = _validator_reason_if_not_eligible(validator)
            connection.execute(
                "UPDATE validators SET reason_if_not_eligible = ? WHERE validator_id = ?",
                (reason, validator_id),
            )
            validator["reason_if_not_eligible"] = reason
    try:
        from app.core.settings import GENESIS_HASH as SETTINGS_GENESIS_HASH
        from app.services.network import register_peer

        register_peer(
            node_id=node_id,
            peer_address=advertised_address,
            peer_type="validator",
            protocol_version=str(payload.get("version") or PROTOCOL_VERSION),
            network_id=NETWORK_ID,
            chain_id=CHAIN_ID,
            genesis_hash=SETTINGS_GENESIS_HASH,
            metadata={"source": "validator_heartbeat", "validator_id": validator_id},
        )
    except Exception as exc:
        logger.warning("validator heartbeat peer registration failed validator_id=%s error=%s", validator_id, exc)
    if validator is None:
        validator = {"validator_id": validator_id, "eligible": False, "reason_if_not_eligible": "validator not found"}
    else:
        validator["is_banned"] = bool(validator.get("is_banned"))
        validator["enabled"] = bool(validator.get("enabled", 1))
        validator["online_status"] = validator.get("online_status") or "offline"
        validator["sync_status"] = validator.get("sync_status") or "unknown"
        validator["eligibility_stake"] = round(_validator_eligibility_stake(validator), 8)
        validator["eligibility_stake_source"] = VALIDATOR_ELIGIBILITY_STAKE_SOURCE
        validator["eligible"] = _validator_row_is_eligible(validator)
    validator["heartbeat_id"] = heartbeat_id
    validator["heartbeat_inserted"] = heartbeat_inserted
    return validator


def _decode_validator_heartbeat_row(row: Any) -> dict[str, Any]:
    item = row_to_dict(row)
    payload = item.get("payload")
    try:
        decoded_payload = json.loads(payload) if isinstance(payload, str) else dict(payload or {})
    except (TypeError, ValueError):
        decoded_payload = {}
    return {
        "heartbeat_id": item.get("heartbeat_id"),
        "validator_id": item.get("validator_id"),
        "public_key": item.get("public_key"),
        "node_id": item.get("node_id"),
        "address": item.get("advertised_address"),
        "local_height": int(item.get("local_height") or 0),
        "effective_height": int(item.get("effective_height") or 0),
        "latest_block_hash": item.get("latest_block_hash"),
        "pending_replay_blocks": int(item.get("pending_replay_blocks") or 0),
        "sync_lag": int(item.get("sync_lag") or 0),
        "version": item.get("protocol_version"),
        "heartbeat_at": item.get("heartbeat_at"),
        "observed_at": item.get("observed_at"),
        "source_peer": item.get("source_peer"),
        "signature": item.get("signature"),
        "heartbeat": decoded_payload,
    }


def list_validator_heartbeat_inventory(limit: int = 100, include_stale: bool = False) -> dict[str, Any]:
    query = "SELECT * FROM validator_heartbeats"
    params: list[Any] = []
    if not include_stale:
        cutoff = (utc_now_dt() - timedelta(seconds=PARTICIPANT_OFFLINE_SECONDS)).isoformat()
        query += " WHERE heartbeat_at >= ?"
        params.append(cutoff)
    query += " ORDER BY heartbeat_at DESC, observed_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), 500)) * 3)

    with get_connection() as connection:
        rows = connection.execute(query, tuple(params)).fetchall()

    seen_validators: set[str] = set()
    heartbeats: list[dict[str, Any]] = []
    for row in rows:
        item = _decode_validator_heartbeat_row(row)
        validator_id = str(item.get("validator_id") or "")
        if validator_id in seen_validators:
            continue
        seen_validators.add(validator_id)
        heartbeats.append(item)
        if len(heartbeats) >= int(limit):
            break
    return {
        "heartbeats": heartbeats,
        "count": len(heartbeats),
        "include_stale": include_stale,
        "checked_at": utc_now(),
    }


def receive_validator_heartbeat_gossip(payload: dict[str, Any], source_peer: str | None = None) -> dict[str, Any]:
    envelope = dict(payload or {})
    heartbeat = envelope.get("heartbeat") if isinstance(envelope.get("heartbeat"), dict) else envelope
    observed_at = str(envelope.get("observed_at") or "") or None
    source = source_peer or str(envelope.get("source_peer") or "") or None
    validator = record_validator_heartbeat(
        dict(heartbeat),
        client_host=None,
        source_peer=source,
        observed_at=observed_at,
    )
    return {
        "status": "accepted" if validator.get("heartbeat_inserted") else "duplicate",
        "heartbeat_id": validator.get("heartbeat_id"),
        "validator": validator,
    }


def _json_payload_text(value: Any, default: Any) -> str:
    if value is None:
        return json.dumps(default, sort_keys=True)
    if isinstance(value, str):
        try:
            json.loads(value)
            return value
        except (TypeError, ValueError):
            return json.dumps(default, sort_keys=True)
    return json.dumps(value, sort_keys=True)


TASK_GOSSIP_STATUSES = {"assigned", "committed", "revealed", "accepted", "rejected", "stale", "expired"}
_TASK_GOSSIP_STATUS_RANK = {
    "assigned": 10,
    "committed": 20,
    "revealed": 30,
    "expired": 40,
    "rejected": 40,
    "stale": 40,
    "accepted": 50,
}


def _decode_task_gossip_row(row: Any) -> dict[str, Any]:
    task = row_to_dict(row)
    if task is None:
        return {}
    task["selected_tx_hashes"] = json.loads(task.get("selected_tx_hashes") or "[]")
    return task


def _decode_commitment_gossip_row(row: Any) -> dict[str, Any] | None:
    commitment = row_to_dict(row)
    if commitment is None:
        return None
    commitment["samples"] = json.loads(commitment.get("samples") or "[]")
    return commitment


def _decode_task_tx_snapshot_gossip_row(row: Any) -> dict[str, Any] | None:
    snapshot = row_to_dict(row)
    if snapshot is None:
        return None
    snapshot["tx_hashes"] = json.loads(snapshot.get("tx_hashes_json") or "[]")
    return snapshot


def list_task_inventory(status: str | None = None, limit: int = 100) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit), 500))
    filters: list[str] = []
    params: list[Any] = []
    if status:
        if status not in TASK_GOSSIP_STATUSES:
            raise MiningError(422, "invalid task status")
        filters.append("status = ?")
        params.append(status)
    query = "SELECT * FROM tasks"
    if filters:
        query += " WHERE " + " AND ".join(filters)
    query += " ORDER BY COALESCE(submitted_at, stale_at, created_at) DESC, task_id DESC LIMIT ?"
    params.append(safe_limit)

    with get_connection() as connection:
        rows = connection.execute(query, tuple(params)).fetchall()
        tasks: list[dict[str, Any]] = []
        for row in rows:
            task = _decode_task_gossip_row(row)
            if not task:
                continue
            miner = row_to_dict(connection.execute("SELECT * FROM miners WHERE miner_id = ?", (task["miner_id"],)).fetchone())
            commitment = _decode_commitment_gossip_row(
                connection.execute("SELECT * FROM commitments WHERE task_id = ?", (task["task_id"],)).fetchone()
            )
            snapshot = _decode_task_tx_snapshot_gossip_row(
                connection.execute(
                    """
                    SELECT task_tx_snapshots.*
                    FROM tasks
                    LEFT JOIN task_tx_snapshots
                      ON task_tx_snapshots.task_id = tasks.task_id
                      OR task_tx_snapshots.snapshot_id = tasks.mempool_snapshot_id
                    WHERE tasks.task_id = ?
                    ORDER BY CASE WHEN task_tx_snapshots.task_id = tasks.task_id THEN 0 ELSE 1 END
                    LIMIT 1
                    """,
                    (task["task_id"],),
                ).fetchone()
            )
            tasks.append(
                {
                    "gossip_version": "task-state-v1",
                    "task": task,
                    "miner": miner,
                    "commitment": commitment,
                    "snapshot": snapshot,
                }
            )
    return {
        "status": status,
        "count": len(tasks),
        "tasks": tasks,
        "checked_at": utc_now(),
    }


def _gossip_task_protocol_params_id(connection: Any, task: dict[str, Any]) -> int | None:
    protocol_params_id = task.get("protocol_params_id")
    if protocol_params_id is not None:
        exists = connection.execute("SELECT 1 FROM protocol_params WHERE id = ?", (protocol_params_id,)).fetchone()
        if exists is not None:
            return int(protocol_params_id)
    active_params = connection.execute("SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1").fetchone()
    return int(active_params["id"]) if active_params else None


def _task_gossip_block_height(task: dict[str, Any], snapshot: dict[str, Any] | None) -> int:
    if snapshot and snapshot.get("block_height") is not None:
        return int(snapshot.get("block_height") or 0)
    if task.get("competitive_round_height") is not None:
        return int(task.get("competitive_round_height") or 0)
    return 0


def _upsert_task_tx_snapshot_from_gossip(connection: Any, task: dict[str, Any], snapshot: dict[str, Any] | None) -> None:
    task_id = str(task.get("task_id") or "")
    selected_hashes = task.get("selected_tx_hashes")
    if isinstance(selected_hashes, str):
        try:
            selected_hashes = json.loads(selected_hashes)
        except (TypeError, ValueError):
            selected_hashes = []
    if not isinstance(selected_hashes, list):
        selected_hashes = []
    snapshot_id = str(
        (snapshot or {}).get("snapshot_id")
        or task.get("mempool_snapshot_id")
        or ""
    )
    if not snapshot_id:
        return
    existing = connection.execute(
        "SELECT 1 FROM task_tx_snapshots WHERE snapshot_id = ? OR task_id = ?",
        (snapshot_id, task_id),
    ).fetchone()
    if existing is not None:
        return
    tx_hashes_json = _json_payload_text((snapshot or {}).get("tx_hashes") or selected_hashes, [])
    connection.execute(
        """
        INSERT INTO task_tx_snapshots (
            snapshot_id, task_id, block_height, tx_hashes_json, tx_merkle_root,
            tx_count, tx_fee_total_units, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            task_id,
            _task_gossip_block_height(task, snapshot),
            tx_hashes_json,
            str((snapshot or {}).get("tx_merkle_root") or task.get("tx_merkle_root") or ""),
            int((snapshot or {}).get("tx_count") or task.get("tx_count") or 0),
            int((snapshot or {}).get("tx_fee_total_units") or task.get("tx_fee_total_units") or 0),
            str((snapshot or {}).get("created_at") or task.get("created_at") or utc_now()),
        ),
    )


def _validate_gossip_commitment(task: dict[str, Any], miner: dict[str, Any], commitment: dict[str, Any]) -> None:
    signature = str(commitment.get("signature") or "")
    signed_at = str(commitment.get("signed_at") or "")
    payload = build_commit_signature_payload(
        task_id=str(task["task_id"]),
        miner_id=str(task["miner_id"]),
        range_start=int(task.get("range_start") or 0),
        range_end=int(task.get("range_end") or 0),
        algorithm=str(task.get("algorithm") or "bbp_hex_v1"),
        result_hash=str(commitment.get("result_hash") or ""),
        merkle_root=str(commitment.get("merkle_root") or ""),
        signed_at=signed_at,
        tx_merkle_root=str(commitment.get("tx_merkle_root") or ""),
        mempool_snapshot_id=str(commitment.get("mempool_snapshot_id") or ""),
        selected_tx_hashes_hash=str(commitment.get("selected_tx_hashes_hash") or ""),
        tx_count=int(commitment.get("tx_count") or 0),
        tx_fee_total_units=int(commitment.get("tx_fee_total_units") or 0),
        chain_id=CHAIN_ID,
        network_id=NETWORK_ID,
    )
    try:
        signature_valid = verify_payload_signature(str(miner.get("public_key") or ""), payload, signature)
    except (RuntimeError, ValueError):
        signature_valid = False
    if not signature_valid and int(commitment.get("tx_count") or 0) == 0:
        legacy_payload = build_commit_signature_payload(
            task_id=str(task["task_id"]),
            miner_id=str(task["miner_id"]),
            range_start=int(task.get("range_start") or 0),
            range_end=int(task.get("range_end") or 0),
            algorithm=str(task.get("algorithm") or "bbp_hex_v1"),
            result_hash=str(commitment.get("result_hash") or ""),
            merkle_root=str(commitment.get("merkle_root") or ""),
            signed_at=signed_at,
        )
        try:
            signature_valid = verify_payload_signature(str(miner.get("public_key") or ""), legacy_payload, signature)
        except (RuntimeError, ValueError):
            signature_valid = False
    if not signature_valid:
        raise MiningError(400, "invalid task commitment signature")


def receive_task_gossip(payload: dict[str, Any], source_peer: str | None = None) -> dict[str, Any]:
    envelope = dict(payload or {})
    task = dict(envelope.get("task") or envelope)
    miner = envelope.get("miner") if isinstance(envelope.get("miner"), dict) else None
    commitment = envelope.get("commitment") if isinstance(envelope.get("commitment"), dict) else None
    snapshot = envelope.get("snapshot") if isinstance(envelope.get("snapshot"), dict) else None
    task_id = str(task.get("task_id") or "").strip()
    miner_id = str(task.get("miner_id") or "").strip()
    status = str(task.get("status") or "").strip()
    if not task_id or not miner_id or not status:
        raise MiningError(422, "task gossip requires task_id, miner_id, and status")
    if status not in TASK_GOSSIP_STATUSES:
        raise MiningError(422, "invalid task gossip status")

    with get_connection() as connection:
        existing_task = row_to_dict(connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone())
        inserted_task = existing_task is None
        commitment_inserted = False
        if existing_task is not None:
            for column in ("miner_id", "range_start", "range_end", "algorithm"):
                if str(existing_task.get(column)) != str(task.get(column, existing_task.get(column))):
                    raise MiningError(409, f"task gossip mismatch: {column}")
        else:
            _insert_gossip_miner_if_missing(connection, miner, miner_id)
            protocol_params_id = _gossip_task_protocol_params_id(connection, task)
            connection.execute(
                """
                INSERT INTO tasks (
                    task_id, miner_id, range_start, range_end, algorithm, status,
                    assignment_seed, assignment_mode, competitive_round_height,
                    competitive_round_previous_hash, assignment_ms, compute_ms,
                    protocol_params_id, created_at, expires_at, submitted_at,
                    stale_at, stale_reason, mempool_snapshot_id, selected_tx_hashes,
                    tx_merkle_root, tx_count, tx_fee_total_units, selected_tx_hashes_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    miner_id,
                    int(task.get("range_start") or 0),
                    int(task.get("range_end") or 0),
                    str(task.get("algorithm") or "bbp_hex_v1"),
                    status,
                    task.get("assignment_seed"),
                    task.get("assignment_mode"),
                    task.get("competitive_round_height"),
                    task.get("competitive_round_previous_hash"),
                    task.get("assignment_ms"),
                    task.get("compute_ms"),
                    protocol_params_id,
                    str(task.get("created_at") or utc_now()),
                    task.get("expires_at"),
                    task.get("submitted_at"),
                    task.get("stale_at"),
                    task.get("stale_reason"),
                    task.get("mempool_snapshot_id"),
                    _json_payload_text(task.get("selected_tx_hashes"), []),
                    str(task.get("tx_merkle_root") or ""),
                    int(task.get("tx_count") or 0),
                    int(task.get("tx_fee_total_units") or 0),
                    task.get("selected_tx_hashes_hash"),
                ),
            )
            existing_task = row_to_dict(connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone())

        _upsert_task_tx_snapshot_from_gossip(connection, task, snapshot)

        if commitment is not None:
            db_miner = row_to_dict(connection.execute("SELECT * FROM miners WHERE miner_id = ?", (miner_id,)).fetchone())
            if db_miner is None:
                raise MiningError(404, "task gossip miner not found")
            existing_commitment = row_to_dict(connection.execute("SELECT * FROM commitments WHERE task_id = ?", (task_id,)).fetchone())
            if existing_commitment is not None:
                for column in ("miner_id", "result_hash", "merkle_root", "challenge_seed"):
                    if str(existing_commitment.get(column)) != str(commitment.get(column, existing_commitment.get(column))):
                        raise MiningError(409, f"task commitment mismatch: {column}")
            else:
                _validate_gossip_commitment(task, db_miner, commitment)
                connection.execute(
                    """
                    INSERT INTO commitments (
                        task_id, miner_id, result_hash, merkle_root, challenge_seed,
                        samples, tx_merkle_root, mempool_snapshot_id, selected_tx_hashes_hash,
                        tx_count, tx_fee_total_units, signature, signed_at, commit_ms, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        miner_id,
                        str(commitment.get("result_hash") or ""),
                        str(commitment.get("merkle_root") or ""),
                        str(commitment.get("challenge_seed") or ""),
                        _json_payload_text(commitment.get("samples"), []),
                        str(commitment.get("tx_merkle_root") or ""),
                        commitment.get("mempool_snapshot_id"),
                        commitment.get("selected_tx_hashes_hash"),
                        int(commitment.get("tx_count") or 0),
                        int(commitment.get("tx_fee_total_units") or 0),
                        str(commitment.get("signature") or ""),
                        str(commitment.get("signed_at") or ""),
                        commitment.get("commit_ms"),
                        str(commitment.get("created_at") or utc_now()),
                    ),
                )
                commitment_inserted = True

        incoming_rank = _TASK_GOSSIP_STATUS_RANK[status]
        existing_status = str((existing_task or {}).get("status") or "")
        existing_rank = _TASK_GOSSIP_STATUS_RANK.get(existing_status, 0)
        selected_tx_hashes_text = _json_payload_text(task.get("selected_tx_hashes"), [])
        task_changed = inserted_task or status != existing_status
        for column in (
            "expires_at",
            "submitted_at",
            "stale_at",
            "stale_reason",
            "compute_ms",
            "mempool_snapshot_id",
            "tx_merkle_root",
            "tx_count",
            "tx_fee_total_units",
            "selected_tx_hashes_hash",
        ):
            if task.get(column) is not None and str((existing_task or {}).get(column) or "") != str(task.get(column) or ""):
                task_changed = True
                break
        if str((existing_task or {}).get("selected_tx_hashes") or "[]") != selected_tx_hashes_text:
            task_changed = True

        if incoming_rank > existing_rank or (incoming_rank == existing_rank and (task_changed or commitment_inserted)):
            connection.execute(
                """
                UPDATE tasks
                SET status = ?, expires_at = ?, submitted_at = ?, stale_at = ?,
                    stale_reason = ?, compute_ms = COALESCE(?, compute_ms),
                    mempool_snapshot_id = COALESCE(?, mempool_snapshot_id),
                    selected_tx_hashes = ?,
                    tx_merkle_root = COALESCE(?, tx_merkle_root),
                    tx_count = COALESCE(?, tx_count),
                    tx_fee_total_units = COALESCE(?, tx_fee_total_units),
                    selected_tx_hashes_hash = COALESCE(?, selected_tx_hashes_hash)
                WHERE task_id = ?
                """,
                (
                    status,
                    task.get("expires_at"),
                    task.get("submitted_at"),
                    task.get("stale_at"),
                    task.get("stale_reason"),
                    task.get("compute_ms"),
                    task.get("mempool_snapshot_id"),
                    selected_tx_hashes_text,
                    task.get("tx_merkle_root"),
                    task.get("tx_count"),
                    task.get("tx_fee_total_units"),
                    task.get("selected_tx_hashes_hash"),
                    task_id,
                ),
            )
            return {"status": "accepted", "task_id": task_id, "task_status": status, "source_peer": source_peer}
    return {"status": "duplicate", "task_id": task_id, "task_status": existing_status, "source_peer": source_peer}


def _decode_validation_job_gossip_row(row: Any) -> dict[str, Any]:
    job = row_to_dict(row)
    job["samples"] = json.loads(job.get("samples") or "[]")
    job["tx_hashes"] = json.loads(job.get("tx_hashes_json") or "[]")
    job["transactions"] = json.loads(job.get("transactions_json") or "[]")
    return job


def list_validation_job_inventory(status: str | None = "pending", limit: int = 100) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit), 500))
    allowed_statuses = {"pending", "approved", "rejected", "expired"}
    filters: list[str] = []
    params: list[Any] = []
    if status:
        if status not in allowed_statuses:
            raise MiningError(422, "invalid validation job status")
        filters.append("status = ?")
        params.append(status)
    query = "SELECT * FROM validation_jobs"
    if filters:
        query += " WHERE " + " AND ".join(filters)
    query += " ORDER BY created_at DESC, job_id DESC LIMIT ?"
    params.append(safe_limit)

    with get_connection() as connection:
        rows = connection.execute(query, tuple(params)).fetchall()
        jobs: list[dict[str, Any]] = []
        for row in rows:
            job = _decode_validation_job_gossip_row(row)
            task = row_to_dict(connection.execute("SELECT * FROM tasks WHERE task_id = ?", (job["task_id"],)).fetchone())
            miner = row_to_dict(connection.execute("SELECT * FROM miners WHERE miner_id = ?", (job["miner_id"],)).fetchone())
            if task is None:
                continue
            jobs.append(
                {
                    "gossip_version": "validation-job-v1",
                    "job": job,
                    "task": task,
                    "miner": miner,
                }
            )
    return {
        "status": status,
        "count": len(jobs),
        "jobs": jobs,
        "checked_at": utc_now(),
    }


def _insert_gossip_miner_if_missing(connection: Any, miner: dict[str, Any] | None, miner_id: str) -> None:
    existing = connection.execute("SELECT 1 FROM miners WHERE miner_id = ?", (miner_id,)).fetchone()
    if existing is not None:
        return
    payload = dict(miner or {})
    timestamp = str(payload.get("registered_at") or utc_now())
    connection.execute(
        """
        INSERT INTO miners (miner_id, name, public_key, reward_address, registered_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            miner_id,
            str(payload.get("name") or miner_id),
            payload.get("public_key"),
            payload.get("reward_address"),
            timestamp,
        ),
    )


def receive_validation_job_gossip(payload: dict[str, Any], source_peer: str | None = None) -> dict[str, Any]:
    envelope = dict(payload or {})
    job = dict(envelope.get("job") or envelope)
    task = dict(envelope.get("task") or {})
    miner = envelope.get("miner") if isinstance(envelope.get("miner"), dict) else None
    job_id = str(job.get("job_id") or "").strip()
    task_id = str(job.get("task_id") or task.get("task_id") or "").strip()
    miner_id = str(job.get("miner_id") or task.get("miner_id") or "").strip()
    if not job_id or not task_id or not miner_id:
        raise MiningError(422, "validation job gossip requires job_id, task_id, and miner_id")
    if str(task.get("task_id") or task_id) != task_id:
        raise MiningError(409, "validation job task_id mismatch")
    status = str(job.get("status") or "pending")
    if status not in {"pending", "approved", "rejected", "expired"}:
        raise MiningError(422, "invalid validation job status")

    with get_connection() as connection:
        existing_job = row_to_dict(connection.execute("SELECT * FROM validation_jobs WHERE job_id = ?", (job_id,)).fetchone())
        existing_task = row_to_dict(connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone())
        if existing_task is not None:
            for column in ("miner_id", "range_start", "range_end", "algorithm"):
                if str(existing_task.get(column)) != str(task.get(column, existing_task.get(column))):
                    raise MiningError(409, f"validation job task mismatch: {column}")
            effective_job_status = str((existing_job or job).get("status") or status)
            _promote_task_for_pending_validation_job_gossip(connection, existing_task, task, effective_job_status)
        if existing_task is None:
            _insert_gossip_miner_if_missing(connection, miner, miner_id)
            protocol_params_id = task.get("protocol_params_id")
            if protocol_params_id is not None:
                exists = connection.execute("SELECT 1 FROM protocol_params WHERE id = ?", (protocol_params_id,)).fetchone()
                if exists is None:
                    protocol_params_id = None
            if protocol_params_id is None:
                active_params = connection.execute(
                    "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
                ).fetchone()
                protocol_params_id = active_params["id"] if active_params else None
            created_at = str(task.get("created_at") or job.get("created_at") or utc_now())
            connection.execute(
                """
                INSERT INTO tasks (
                    task_id, miner_id, range_start, range_end, algorithm, status,
                    assignment_seed, assignment_mode, competitive_round_height,
                    competitive_round_previous_hash, assignment_ms, compute_ms,
                    protocol_params_id, created_at, expires_at, submitted_at,
                    stale_at, stale_reason, mempool_snapshot_id, selected_tx_hashes,
                    tx_merkle_root, tx_count, tx_fee_total_units, selected_tx_hashes_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    miner_id,
                    int(task.get("range_start") or 0),
                    int(task.get("range_end") or 0),
                    str(task.get("algorithm") or "bbp_hex_v1"),
                    str(task.get("status") or "revealed"),
                    task.get("assignment_seed"),
                    task.get("assignment_mode"),
                    task.get("competitive_round_height"),
                    task.get("competitive_round_previous_hash"),
                    task.get("assignment_ms"),
                    task.get("compute_ms"),
                    protocol_params_id,
                    created_at,
                    task.get("expires_at"),
                    task.get("submitted_at"),
                    task.get("stale_at"),
                    task.get("stale_reason"),
                    task.get("mempool_snapshot_id") or job.get("mempool_snapshot_id"),
                    _json_payload_text(task.get("selected_tx_hashes") or job.get("tx_hashes"), []),
                    task.get("tx_merkle_root") or job.get("tx_merkle_root") or "",
                    int(task.get("tx_count") or job.get("tx_count") or 0),
                    int(task.get("tx_fee_total_units") or job.get("tx_fee_total_units") or 0),
                    task.get("selected_tx_hashes_hash") or job.get("selected_tx_hashes_hash"),
                ),
            )
        if existing_job is not None:
            return {"status": "duplicate", "job_id": job_id, "task_id": task_id}

        created_at = str(job.get("created_at") or utc_now())
        job_created_at = str(job.get("job_created_at") or created_at)
        connection.execute(
            """
            INSERT INTO validation_jobs (
                job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
                samples, tx_merkle_root, mempool_snapshot_id, selected_tx_hashes_hash,
                tx_count, tx_fee_total_units, tx_hashes_json, transactions_json,
                status, result_reason, validator_signature, validation_ms,
                job_created_at, first_vote_at, second_vote_at, quorum_reached_at,
                finalized_at, waiting_for_first_vote_ms, waiting_for_quorum_ms,
                finalization_ms, created_at, completed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                task_id,
                miner_id,
                str(job.get("result_hash") or ""),
                str(job.get("merkle_root") or ""),
                str(job.get("challenge_seed") or ""),
                _json_payload_text(job.get("samples"), []),
                str(job.get("tx_merkle_root") or ""),
                job.get("mempool_snapshot_id"),
                job.get("selected_tx_hashes_hash"),
                int(job.get("tx_count") or 0),
                int(job.get("tx_fee_total_units") or 0),
                _json_payload_text(job.get("tx_hashes") or job.get("tx_hashes_json"), []),
                _json_payload_text(job.get("transactions") or job.get("transactions_json"), []),
                status,
                job.get("result_reason"),
                job.get("validator_signature"),
                job.get("validation_ms"),
                job_created_at,
                job.get("first_vote_at"),
                job.get("second_vote_at"),
                job.get("quorum_reached_at"),
                job.get("finalized_at"),
                job.get("waiting_for_first_vote_ms"),
                job.get("waiting_for_quorum_ms"),
                job.get("finalization_ms"),
                created_at,
                job.get("completed_at"),
            ),
        )
    return {"status": "accepted", "job_id": job_id, "task_id": task_id, "source_peer": source_peer}


def _promote_task_for_pending_validation_job_gossip(
    connection: Any,
    existing_task: dict[str, Any],
    incoming_task: dict[str, Any],
    job_status: str,
) -> None:
    if str(job_status or "") != "pending":
        return
    if str(existing_task.get("status") or "") not in {"assigned", "committed", "expired"}:
        return
    params = _protocol_params_for_task(connection, existing_task)
    expires_at = incoming_task.get("expires_at") or iso_at(
        _task_expiration_seconds_for_position(params, existing_task.get("range_end"))
    )
    connection.execute(
        """
        UPDATE tasks
        SET status = 'revealed',
            expires_at = ?,
            stale_at = NULL,
            stale_reason = NULL
        WHERE task_id = ?
        """,
        (expires_at, existing_task["task_id"]),
    )


def _promote_tasks_for_pending_validation_jobs(connection: Any, limit: int = 100) -> int:
    rows = connection.execute(
        """
        SELECT tasks.*
        FROM validation_jobs
        JOIN tasks ON tasks.task_id = validation_jobs.task_id
        WHERE validation_jobs.status IN ('pending', 'approved')
          AND tasks.status IN ('assigned', 'committed', 'expired')
        ORDER BY validation_jobs.created_at ASC
        LIMIT ?
        """,
        (max(1, min(int(limit), 500)),),
    ).fetchall()
    promoted = 0
    for row in rows:
        task = row_to_dict(row)
        if task is None:
            continue
        _promote_task_for_pending_validation_job_gossip(connection, task, {}, "pending")
        promoted += 1
    return promoted


def list_validation_vote_inventory(limit: int = 100) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit), 500))
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT validation_votes.*
            FROM validation_votes
            JOIN validation_jobs ON validation_jobs.job_id = validation_votes.job_id
            ORDER BY validation_votes.created_at DESC, validation_votes.id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    votes: list[dict[str, Any]] = []
    for row in rows:
        vote = row_to_dict(row)
        vote["approved"] = bool(vote.get("approved"))
        votes.append({"gossip_version": "validation-vote-v1", "vote": vote})
    return {
        "count": len(votes),
        "votes": votes,
        "checked_at": utc_now(),
    }


def _finalize_validation_quorum_from_gossip(
    connection: Any,
    *,
    job: dict[str, Any],
    task: dict[str, Any],
    validator_id: str,
    approved: bool,
    reason: str,
    signature: str,
    validation_ms: int,
    params: dict[str, Any],
    counts: dict[str, int],
    required: int,
) -> dict[str, Any] | None:
    job_id = job["job_id"]
    if approved and counts["approvals"] >= required:
        duplicate_block = row_to_dict(connection.execute(
            "SELECT * FROM blocks WHERE result_hash = ? OR task_id = ?",
            (job["result_hash"], job["task_id"]),
        ).fetchone())
        if duplicate_block is not None:
            finalized_at = utc_now()
            if (
                str(duplicate_block.get("task_id") or "") == str(job["task_id"])
                and str(duplicate_block.get("result_hash") or "") == str(job["result_hash"])
            ):
                reason_text = f"block already accepted at height {duplicate_block['height']}"
                connection.execute(
                    """
                    UPDATE tasks
                    SET status = 'accepted', submitted_at = COALESCE(submitted_at, ?)
                    WHERE task_id = ?
                    """,
                    (finalized_at, job["task_id"]),
                )
                _mark_validation_job_finalized(connection, job_id=job_id, finalized_at=finalized_at)
                connection.execute(
                    """
                    UPDATE validation_jobs
                    SET status = 'approved', assigned_validator_id = ?, result_reason = ?,
                        validator_signature = ?, validation_ms = ?, completed_at = ?
                    WHERE job_id = ?
                    """,
                    (validator_id, reason_text, signature, validation_ms, finalized_at, job_id),
                )
                finality_certificate = _create_finality_certificate(
                    connection,
                    block=duplicate_block,
                    job_id=job_id,
                    required_approvals=required,
                    created_at=finalized_at,
                )
                return {
                    "accepted": True,
                    "status": "approved",
                    "message": reason_text,
                    "block": _decode_block(duplicate_block),
                    "finality_certificate": finality_certificate,
                    "approvals": counts["approvals"],
                    "rejections": counts["rejections"],
                    "required_approvals": required,
                    "required_rejections": required,
                }

            reason_text = f"duplicate competitive result already accepted at block {duplicate_block['height']}"
            connection.execute(
                "UPDATE tasks SET status = 'rejected', submitted_at = ? WHERE task_id = ?",
                (finalized_at, job["task_id"]),
            )
            release_selected_transactions(connection, job["task_id"], reason_text, finalized_at)
            _mark_validation_job_finalized(connection, job_id=job_id, finalized_at=finalized_at)
            connection.execute(
                """
                UPDATE validation_jobs
                SET status = 'rejected', assigned_validator_id = ?, result_reason = ?,
                    validator_signature = ?, validation_ms = ?, completed_at = ?
                WHERE job_id = ?
                """,
                (validator_id, reason_text, signature, validation_ms, finalized_at, job_id),
            )
            return {
                "accepted": False,
                "status": "rejected",
                "message": reason_text,
                "block": None,
                "approvals": counts["approvals"],
                "rejections": counts["rejections"],
                "required_approvals": required,
                "required_rejections": required,
            }

        savepoint_name = "validation_gossip_block_finalization"
        connection.execute(f"SAVEPOINT {savepoint_name}")
        try:
            block = _accept_block_in_connection(
                connection=connection,
                task=task,
                miner_id=job["miner_id"],
                result_hash=job["result_hash"],
                merkle_root=job["merkle_root"],
                samples=json.loads(job.get("samples") or "[]"),
                signature=signature,
                submission_reason=f"external validation approved by {validator_id}",
                validation_ms=validation_ms,
                params=params,
                validation_job_id=job_id,
            )
        except ValidationFinalizationDeferred as exc:
            connection.execute(f"ROLLBACK TO {savepoint_name}")
            connection.execute(f"RELEASE {savepoint_name}")
            logger.warning(
                "validation finalization deferred job_id=%s task_id=%s reason=%s",
                job_id,
                job["task_id"],
                exc,
            )
            return {
                "accepted": True,
                "status": "validation_pending",
                "message": str(exc),
                "block": None,
                "approvals": counts["approvals"],
                "rejections": counts["rejections"],
                "required_approvals": required,
                "required_rejections": required,
            }
        except TransactionExecutionError as exc:
            connection.execute(f"ROLLBACK TO {savepoint_name}")
            connection.execute(f"RELEASE {savepoint_name}")
            finalized_at = utc_now()
            raw_reason = str(exc)
            is_competitive_stale = raw_reason.startswith("competitive round")
            reason_text = raw_reason if is_competitive_stale else f"transaction finalization failed: {exc}"
            if is_competitive_stale:
                _mark_competitive_task_stale(connection, job["task_id"], reason_text, finalized_at)
            else:
                connection.execute(
                    "UPDATE tasks SET status = 'rejected', submitted_at = ? WHERE task_id = ?",
                    (finalized_at, job["task_id"]),
                )
                release_selected_transactions(connection, job["task_id"], reason_text, finalized_at)
            _mark_validation_job_finalized(connection, job_id=job_id, finalized_at=finalized_at)
            connection.execute(
                """
                UPDATE validation_jobs
                SET status = 'rejected', assigned_validator_id = ?, result_reason = ?,
                    validator_signature = ?, validation_ms = ?, completed_at = ?
                WHERE job_id = ?
                """,
                (validator_id, reason_text, signature, validation_ms, finalized_at, job_id),
            )
            return {
                "accepted": False,
                "status": "stale" if is_competitive_stale else "rejected",
                "message": reason_text,
                "block": None,
                "approvals": counts["approvals"],
                "rejections": counts["rejections"],
                "required_approvals": required,
                "required_rejections": required,
            }
        connection.execute(f"RELEASE {savepoint_name}")
        finalized_at = utc_now()
        _mark_validation_job_finalized(connection, job_id=job_id, finalized_at=finalized_at)
        connection.execute(
            """
            UPDATE validation_jobs
            SET status = 'approved', assigned_validator_id = ?, result_reason = ?,
                validator_signature = ?, validation_ms = ?, completed_at = ?
            WHERE job_id = ?
            """,
            (validator_id, reason, signature, validation_ms, finalized_at, job_id),
        )
        finality_certificate = _create_finality_certificate(
            connection,
            block=block,
            job_id=job_id,
            required_approvals=required,
            created_at=finalized_at,
        )
        return {
            "accepted": True,
            "status": "approved",
            "message": "block accepted by validator quorum",
            "block": block,
            "finality_certificate": finality_certificate,
            "approvals": counts["approvals"],
            "rejections": counts["rejections"],
            "required_approvals": required,
            "required_rejections": required,
        }

    if not approved and counts["rejections"] >= required:
        finalized_at = utc_now()
        connection.execute(
            "UPDATE tasks SET status = 'rejected', submitted_at = ? WHERE task_id = ?",
            (finalized_at, job["task_id"]),
        )
        _mark_validation_job_finalized(connection, job_id=job_id, finalized_at=finalized_at)
        connection.execute(
            """
            UPDATE validation_jobs
            SET status = 'rejected', assigned_validator_id = ?, result_reason = ?,
                validator_signature = ?, validation_ms = ?, completed_at = ?
            WHERE job_id = ?
            """,
            (validator_id, reason, signature, validation_ms, finalized_at, job_id),
        )
        _apply_penalty(connection, job["miner_id"], job["task_id"], PENALTY_INVALID_RESULT, reason)
        return {
            "accepted": True,
            "status": "rejected",
            "message": "validation rejected task by validator quorum",
            "block": None,
            "approvals": counts["approvals"],
            "rejections": counts["rejections"],
            "required_approvals": required,
            "required_rejections": required,
        }
    return None


def _finalize_pending_validation_job_from_existing_votes(
    connection: Any,
    *,
    job: dict[str, Any],
    counts: dict[str, int],
    required: int,
) -> dict[str, Any] | None:
    if str(job.get("status") or "") != "pending" or counts["approvals"] < required:
        return None
    task = row_to_dict(connection.execute("SELECT * FROM tasks WHERE task_id = ?", (job["task_id"],)).fetchone())
    if task is None:
        raise MiningError(404, "validation task not found")
    vote = row_to_dict(
        connection.execute(
            """
            SELECT *
            FROM validation_votes
            WHERE job_id = ?
              AND approved = 1
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (job["job_id"],),
        ).fetchone()
    )
    if vote is None:
        return None
    params = _protocol_params_for_task(connection, task)
    return _finalize_validation_quorum_from_gossip(
        connection,
        job=job,
        task=task,
        validator_id=vote["validator_id"],
        approved=True,
        reason=vote["reason"],
        signature=vote["signature"],
        validation_ms=int(vote.get("validation_ms") or 0),
        params=params,
        counts=counts,
        required=required,
    )


def receive_validation_vote_gossip(payload: dict[str, Any], source_peer: str | None = None) -> dict[str, Any]:
    envelope = dict(payload or {})
    vote = dict(envelope.get("vote") or envelope)
    job_id = str(vote.get("job_id") or "").strip()
    task_id = str(vote.get("task_id") or "").strip()
    validator_id = str(vote.get("validator_id") or "").strip()
    if not job_id or not task_id or not validator_id:
        raise MiningError(422, "validation vote gossip requires job_id, task_id, and validator_id")
    approved = bool(vote.get("approved"))
    reason = str(vote.get("reason") or "")
    signature = str(vote.get("signature") or "")
    signed_at = str(vote.get("signed_at") or "")

    with get_connection() as connection:
        job = row_to_dict(connection.execute("SELECT * FROM validation_jobs WHERE job_id = ?", (job_id,)).fetchone())
        if job is None:
            raise MiningError(404, "validation job not found")
        if str(job.get("task_id")) != task_id:
            raise MiningError(409, "validation vote task mismatch")
        validator = row_to_dict(connection.execute("SELECT * FROM validators WHERE validator_id = ?", (validator_id,)).fetchone())
        if validator is None:
            raise MiningError(404, "validator not found")
        existing_vote = connection.execute(
            "SELECT 1 FROM validation_votes WHERE job_id = ? AND validator_id = ?",
            (job_id, validator_id),
        ).fetchone()
        if existing_vote is not None:
            counts = _validation_vote_counts(connection, job_id)
            params = _protocol_params_for_task(connection, job)
            required = _effective_required_validator_approvals(connection, params)
            response = {
                "status": "duplicate",
                "job_id": job_id,
                "validator_id": validator_id,
                "approvals": counts["approvals"],
                "rejections": counts["rejections"],
                "required_approvals": required,
                "required_rejections": required,
            }
            finalization = _finalize_pending_validation_job_from_existing_votes(
                connection,
                job=job,
                counts=counts,
                required=required,
            )
            if finalization is not None:
                response["finalization"] = finalization
            return response

        signature_payload = build_validation_result_signature_payload(
            job_id=job_id,
            validator_id=validator_id,
            task_id=task_id,
            approved=approved,
            reason=reason,
            signed_at=signed_at,
        )
        try:
            signature_valid = verify_payload_signature(validator["public_key"], signature_payload, signature)
        except (RuntimeError, ValueError):
            signature_valid = False
        if not signature_valid:
            raise MiningError(400, "invalid validator signature")

        received_at = str(vote.get("created_at") or utc_now())
        validation_ms = int(vote.get("validation_ms") or 0)
        submit_result_latency_ms = vote.get("submit_result_latency_ms")
        connection.execute(
            """
            INSERT INTO validation_votes (
                job_id, task_id, validator_id, approved, reason, signature,
                signed_at, validation_ms, submit_result_latency_ms, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                task_id,
                validator_id,
                int(approved),
                reason,
                signature,
                signed_at,
                validation_ms,
                submit_result_latency_ms,
                received_at,
            ),
        )
        _record_validator_completed_vote(connection, validator_id, approved, validation_ms)
        counts = _validation_vote_counts(connection, job_id)
        task = row_to_dict(connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone())
        if task is None:
            raise MiningError(404, "validation task not found")
        params = _protocol_params_for_task(connection, task)
        required = _effective_required_validator_approvals(connection, params)
        _refresh_validation_job_timing(connection, job_id=job_id, counts=counts, required=required, received_at=received_at)
        finalization = None
        if job.get("status") == "pending":
            finalization = _finalize_validation_quorum_from_gossip(
                connection,
                job=job,
                task=task,
                validator_id=validator_id,
                approved=approved,
                reason=reason,
                signature=signature,
                validation_ms=validation_ms,
                params=params,
                counts=counts,
                required=required,
            )
    if finalization is not None:
        return {"status": "accepted", "job_id": job_id, "validator_id": validator_id, "finalization": finalization, "source_peer": source_peer}
    return {
        "status": "accepted",
        "job_id": job_id,
        "validator_id": validator_id,
        "approvals": counts["approvals"],
        "rejections": counts["rejections"],
        "required_approvals": required,
        "required_rejections": required,
        "source_peer": source_peer,
    }


def record_miner_heartbeat(payload: dict[str, Any], client_host: str | None = None) -> dict[str, Any]:
    signed_payload = _heartbeat_signature_payload(payload)
    public_key = str(payload.get("public_key") or "")
    try:
        validate_public_key(public_key)
        signature_valid = verify_payload_signature(public_key, signed_payload, str(payload.get("signature") or ""))
    except (RuntimeError, ValueError):
        signature_valid = False
    if not signature_valid:
        raise MiningError(401, "invalid miner heartbeat signature")

    miner_id = str(payload.get("miner_id") or "").strip()
    if not miner_id:
        raise MiningError(400, "miner_id is required")
    timestamp = utc_now()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO miners (
                miner_id, name, public_key, registered_at, last_seen_at, last_heartbeat_at,
                online_status, node_id, advertised_address, last_ip, last_task_id,
                last_task_status, last_compute_ms, protocol_version, enabled
            )
            VALUES (?, ?, ?, ?, ?, ?, 'online', ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(miner_id) DO UPDATE SET
                name = COALESCE(NULLIF(excluded.name, ''), miners.name),
                public_key = COALESCE(miners.public_key, excluded.public_key),
                last_seen_at = excluded.last_seen_at,
                last_heartbeat_at = excluded.last_heartbeat_at,
                online_status = 'online',
                node_id = excluded.node_id,
                advertised_address = excluded.advertised_address,
                last_ip = excluded.last_ip,
                last_task_id = excluded.last_task_id,
                last_task_status = excluded.last_task_status,
                last_compute_ms = excluded.last_compute_ms,
                protocol_version = excluded.protocol_version
            """,
            (
                miner_id,
                str(payload.get("name") or miner_id)[:80],
                public_key,
                timestamp,
                timestamp,
                timestamp,
                payload.get("node_id"),
                payload.get("server"),
                client_host,
                payload.get("last_task_id"),
                payload.get("last_task_status"),
                payload.get("last_compute_ms"),
                str(payload.get("version") or PROTOCOL_VERSION),
            ),
        )
        _ensure_balance_account(connection, miner_id, "miner")
        row = connection.execute("SELECT * FROM miners WHERE miner_id = ?", (miner_id,)).fetchone()
    refresh_participant_liveness()
    return enrich_miner(row_to_dict(row))


def get_validators_status(limit: int = 500) -> dict[str, Any]:
    cache_key = f"validators_status:{database_module.DATABASE_PATH}:{int(limit)}"

    def build() -> dict[str, Any]:
        with get_connection() as connection:
            _maybe_expire_assigned_tasks(connection)
            rows = connection.execute(
                """
                SELECT *
                FROM validators
                ORDER BY
                    CASE online_status
                        WHEN 'online' THEN 0
                        WHEN 'stale' THEN 1
                        WHEN 'offline' THEN 2
                        ELSE 3
                    END,
                    effective_height DESC,
                    validator_id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            validators = [enrich_validator(row_to_dict(row), connection) for row in rows]
            counts = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(CASE WHEN online_status = 'online' THEN 1 ELSE 0 END), 0) AS online,
                    COALESCE(SUM(CASE WHEN online_status = 'stale' THEN 1 ELSE 0 END), 0) AS stale,
                    COALESCE(SUM(CASE WHEN online_status = 'offline' THEN 1 ELSE 0 END), 0) AS offline,
                    COALESCE(SUM(CASE WHEN sync_status = 'out_of_sync' THEN 1 ELSE 0 END), 0) AS out_of_sync,
                    COALESCE(SUM(CASE WHEN enabled = 0 THEN 1 ELSE 0 END), 0) AS disabled
                FROM validators
                """
            ).fetchone()
            eligible = len(_eligible_validator_rows(connection))
            required = _effective_required_validator_approvals(connection)
        return {
            "checked_at": utc_now(),
            "required_validator_approvals": required,
            "eligible_validators": eligible,
            "counts": {key: int(counts[key]) for key in counts.keys()},
            "validators": validators,
        }

    return _cached_status_payload(cache_key, STATUS_ENDPOINT_CACHE_SECONDS, build)


def get_validation_jobs_health(stale_after_seconds: int = VALIDATION_JOB_ASSIGNMENT_TIMEOUT_SECONDS * 2, limit: int = 20) -> dict[str, Any]:
    cache_key = f"validation_jobs_health:{database_module.DATABASE_PATH}:{int(stale_after_seconds)}:{int(limit)}"

    def build() -> dict[str, Any]:
        return _get_validation_jobs_health_uncached(stale_after_seconds, limit)

    return _cached_status_payload(cache_key, STATUS_ENDPOINT_CACHE_SECONDS, build)


def _get_validation_jobs_health_uncached(
    stale_after_seconds: int = VALIDATION_JOB_ASSIGNMENT_TIMEOUT_SECONDS * 2,
    limit: int = 20,
) -> dict[str, Any]:
    stale_after_seconds = max(1, int(stale_after_seconds))
    limit = max(1, int(limit))
    now_iso = utc_now()

    def age_seconds(value: str | None) -> int | None:
        elapsed = _elapsed_iso_ms(value, now_iso)
        if elapsed is None:
            return None
        return max(0, elapsed // 1000)

    with get_connection() as connection:
        _maybe_expire_assigned_tasks(connection)
        pending_rows = connection.execute(
            """
            SELECT
                validation_jobs.job_id,
                validation_jobs.task_id,
                validation_jobs.miner_id,
                validation_jobs.status,
                validation_jobs.assigned_validator_id,
                validation_jobs.assigned_at,
                validation_jobs.assignment_failures,
                validation_jobs.blocking_reason,
                validation_jobs.job_created_at,
                validation_jobs.created_at,
                validation_jobs.first_vote_at,
                validation_jobs.second_vote_at,
                validation_jobs.quorum_reached_at,
                validation_jobs.finalized_at,
                tasks.status AS task_status,
                tasks.assignment_mode,
                tasks.assignment_seed,
                tasks.protocol_params_id,
                tasks.range_start,
                tasks.range_end
            FROM validation_jobs
            JOIN tasks ON tasks.task_id = validation_jobs.task_id
            WHERE validation_jobs.status = 'pending'
            ORDER BY validation_jobs.created_at ASC
            """
        ).fetchall()
        eligible_rows = _eligible_validator_rows(connection)
        eligible = len(eligible_rows)
        active_required = _effective_required_validator_approvals(connection)

        jobs: list[dict[str, Any]] = []
        counts = {
            "pending_recent": 0,
            "stuck_no_votes": 0,
            "stuck_waiting_for_quorum": 0,
            "assignment_timeout_pending_release": 0,
            "waiting_for_assigned_validator": 0,
            "quorum_reached_waiting_finalization": 0,
            "competitive_round_waiting": 0,
        }

        for row in pending_rows:
            job = row_to_dict(row)
            params = _protocol_params_for_task(connection, job)
            required = _effective_required_validator_approvals(connection, params)
            vote_counts = _validation_vote_counts(connection, job["job_id"])
            approvals = vote_counts["approvals"]
            rejections = vote_counts["rejections"]
            total_votes = approvals + rejections
            voted_validator_ids = {
                str(vote_row["validator_id"])
                for vote_row in connection.execute(
                    "SELECT validator_id FROM validation_votes WHERE job_id = ?",
                    (job["job_id"],),
                ).fetchall()
            }
            missing_eligible_validators = [
                {
                    "validator_id": str(validator["validator_id"]),
                    "node_id": validator.get("node_id"),
                    "online_status": validator.get("online_status"),
                    "sync_status": validator.get("sync_status"),
                    "effective_height": int(validator.get("effective_height") or 0),
                    "sync_lag": int(validator.get("sync_lag") or 0),
                }
                for validator in eligible_rows
                if str(validator["validator_id"]) not in voted_validator_ids
            ]
            job_age_seconds = age_seconds(job.get("job_created_at") or job.get("created_at"))
            assigned_age_seconds = age_seconds(job.get("assigned_at"))
            quorum_reached = approvals >= required or rejections >= required
            assigned_timeout = (
                assigned_age_seconds is not None
                and assigned_age_seconds >= VALIDATION_JOB_ASSIGNMENT_TIMEOUT_SECONDS
                and bool(job.get("assigned_validator_id"))
            )
            assigned_active = (
                assigned_age_seconds is not None
                and assigned_age_seconds < VALIDATION_JOB_ASSIGNMENT_TIMEOUT_SECONDS
                and bool(job.get("assigned_validator_id"))
            )
            competitive_round_waiting = _competitive_round_has_earlier_pending_validation_job(connection, job)
            stale = job_age_seconds is not None and job_age_seconds >= stale_after_seconds

            if quorum_reached:
                health = "quorum_reached_waiting_finalization"
            elif competitive_round_waiting:
                health = "competitive_round_waiting"
            elif assigned_timeout:
                health = "assignment_timeout_pending_release"
            elif assigned_active:
                health = "waiting_for_assigned_validator"
            elif stale and total_votes == 0:
                health = "stuck_no_votes"
            elif stale and total_votes > 0:
                health = "stuck_waiting_for_quorum"
            else:
                health = "pending_recent"
            counts[health] += 1

            jobs.append(
                {
                    "job_id": job["job_id"],
                    "task_id": job["task_id"],
                    "miner_id": job["miner_id"],
                    "task_status": job.get("task_status"),
                    "age_seconds": job_age_seconds,
                    "assigned_validator_id": job.get("assigned_validator_id"),
                    "assigned_age_seconds": assigned_age_seconds,
                    "assignment_failures": int(job.get("assignment_failures") or 0),
                    "blocking_reason": job.get("blocking_reason"),
                    "approvals": approvals,
                    "rejections": rejections,
                    "total_votes": total_votes,
                    "required_approvals": required,
                    "missing_approvals": max(0, required - approvals),
                    "voted_validator_ids": sorted(voted_validator_ids),
                    "missing_eligible_validator_ids": [
                        validator["validator_id"] for validator in missing_eligible_validators
                    ],
                    "missing_eligible_validators": missing_eligible_validators,
                    "health": health,
                    "first_vote_at": job.get("first_vote_at"),
                    "second_vote_at": job.get("second_vote_at"),
                    "quorum_reached_at": job.get("quorum_reached_at"),
                    "created_at": job.get("job_created_at") or job.get("created_at"),
                }
            )

    stuck_count = (
        counts["stuck_no_votes"]
        + counts["stuck_waiting_for_quorum"]
        + counts["assignment_timeout_pending_release"]
        + counts["quorum_reached_waiting_finalization"]
    )
    jobs.sort(
        key=lambda item: (
            0 if item["health"] != "pending_recent" else 1,
            -(item["age_seconds"] or 0),
            item["job_id"],
        )
    )
    return {
        "checked_at": now_iso,
        "healthy": stuck_count == 0,
        "pending_count": len(pending_rows),
        "stuck_count": stuck_count,
        "stale_after_seconds": stale_after_seconds,
        "assignment_timeout_seconds": VALIDATION_JOB_ASSIGNMENT_TIMEOUT_SECONDS,
        "eligible_validators": eligible,
        "required_validator_approvals": active_required,
        "counts": counts,
        "jobs": jobs[:limit],
    }


def get_miners_status(limit: int = 500) -> dict[str, Any]:
    cache_key = f"miners_status:{int(limit)}"

    def build() -> dict[str, Any]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM miners
                ORDER BY
                    CASE online_status
                        WHEN 'online' THEN 0
                        WHEN 'stale' THEN 1
                        WHEN 'offline' THEN 2
                        ELSE 3
                    END,
                    COALESCE(last_compute_ms, 0) ASC,
                    miner_id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            miners = [enrich_miner(row_to_dict(row)) for row in rows]
            counts = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(CASE WHEN online_status = 'online' THEN 1 ELSE 0 END), 0) AS online,
                    COALESCE(SUM(CASE WHEN online_status = 'stale' THEN 1 ELSE 0 END), 0) AS stale,
                    COALESCE(SUM(CASE WHEN online_status = 'offline' THEN 1 ELSE 0 END), 0) AS offline,
                    COALESCE(SUM(CASE WHEN enabled = 0 THEN 1 ELSE 0 END), 0) AS disabled
                FROM miners
                """
            ).fetchone()
        return {
            "checked_at": utc_now(),
            "counts": {key: int(counts[key]) for key in counts.keys()},
            "miners": miners,
        }

    return _cached_status_payload(cache_key, STATUS_ENDPOINT_CACHE_SECONDS, build)


def _mining_metric_from_row(row: Any) -> dict[str, Any]:
    keys = set(row.keys()) if hasattr(row, "keys") else set()

    def optional(name: str, default: Any = None) -> Any:
        return row[name] if name in keys else default

    range_start = int(row["range_start"] or 0)
    range_end = int(row["range_end"] or range_start)
    segment_size = int(row["segment_size"] or max(1, range_end - range_start + 1))
    compute_ms = int(row["compute_ms"] or row["total_task_ms"] or row["total_block_ms"] or 0)
    task_ms = int(row["total_task_ms"] or compute_ms or row["total_block_ms"] or 0)
    block_ms = int(row["total_block_ms"] or row["total_task_ms"] or 0)
    compute_seconds = compute_ms / 1000 if compute_ms > 0 else 0
    block_seconds = block_ms / 1000 if block_ms > 0 else 0
    work_rate_hps = round(segment_size / compute_seconds, 4) if compute_seconds > 0 else 0.0
    block_rate_hps = round(segment_size / block_seconds, 4) if block_seconds > 0 else 0.0
    return {
        "height": int(row["height"]),
        "timestamp": row["timestamp"],
        "miner_id": row["miner_id"],
        "miner_reward_address": row["miner_reward_address"],
        "block_hash": row["block_hash"],
        "result_hash": row["result_hash"],
        "range_start": range_start,
        "range_end": range_end,
        "segment_size": segment_size,
        "reward": round(float(row["reward"] or 0), 8),
        "difficulty": round(float(row["difficulty"] or 0), 8),
        "compute_ms": compute_ms,
        "total_task_ms": task_ms,
        "total_block_ms": block_ms,
        "validation_ms": int(row["validation_ms"] or 0),
        "work_rate_hps": work_rate_hps,
        "hashrate_hps": work_rate_hps,
        "block_rate_hps": block_rate_hps,
        "reward_status": optional("reward_status"),
        "matures_at_height": optional("matures_at_height"),
        "matured_at": optional("matured_at"),
    }


def get_mining_metrics(limit: int = 120) -> dict[str, Any]:
    sample_limit = max(1, min(int(limit), 500))
    refresh_participant_liveness()
    with get_connection() as connection:
        params = _active_protocol_params(connection)
        rows = connection.execute(
            """
            SELECT
                blocks.height,
                blocks.timestamp,
                blocks.miner_id,
                blocks.miner_reward_address,
                blocks.block_hash,
                blocks.result_hash,
                blocks.range_start,
                blocks.range_end,
                blocks.reward,
                COALESCE(blocks.difficulty, protocol_params.difficulty, 0) AS difficulty,
                tasks.compute_ms AS compute_ms,
                blocks.total_task_ms,
                blocks.total_block_ms,
                blocks.validation_ms,
                COALESCE(protocol_params.segment_size, blocks.range_end - blocks.range_start + 1) AS segment_size,
                rewards.status AS reward_status,
                rewards.matures_at_height AS matures_at_height,
                rewards.matured_at AS matured_at
            FROM blocks
            LEFT JOIN protocol_params ON protocol_params.id = blocks.protocol_params_id
            LEFT JOIN tasks ON tasks.task_id = blocks.task_id
            LEFT JOIN rewards ON rewards.block_height = blocks.height
                AND rewards.miner_id = blocks.miner_id
                AND ABS(COALESCE(rewards.amount, 0) - COALESCE(blocks.reward, 0)) <= ?
            ORDER BY blocks.height DESC
            LIMIT ?
            """,
            (ECONOMIC_AUDIT_TOLERANCE, sample_limit),
        ).fetchall()
        miner_rows = connection.execute(
            """
            SELECT
                blocks.miner_id,
                blocks.miner_reward_address,
                COUNT(*) AS accepted_blocks,
                COALESCE(SUM(blocks.reward), 0) AS total_rewards,
                COALESCE(AVG(tasks.compute_ms), AVG(blocks.total_task_ms), 0) AS avg_compute_ms,
                COALESCE(AVG(blocks.total_task_ms), 0) AS avg_total_task_ms,
                COALESCE(AVG(blocks.total_block_ms), 0) AS avg_total_block_ms,
                COALESCE(AVG(blocks.difficulty), 0) AS avg_difficulty,
                MAX(miners.last_compute_ms) AS last_compute_ms,
                MAX(miners.online_status) AS online_status,
                MAX(blocks.height) AS latest_block_height,
                MAX(blocks.timestamp) AS latest_block_at
            FROM blocks
            LEFT JOIN tasks ON tasks.task_id = blocks.task_id
            LEFT JOIN miners ON miners.miner_id = blocks.miner_id
            GROUP BY blocks.miner_id, blocks.miner_reward_address
            ORDER BY accepted_blocks DESC, total_rewards DESC, blocks.miner_id ASC
            LIMIT 12
            """
        ).fetchall()
        active_compute_rows = connection.execute(
            """
            SELECT miner_id, online_status, last_compute_ms
            FROM miners
            WHERE enabled = 1
              AND online_status = 'online'
              AND COALESCE(last_compute_ms, 0) > 0
            """
        ).fetchall()
        miner_counts = connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN online_status = 'online' THEN 1 ELSE 0 END), 0) AS online,
                COALESCE(SUM(CASE WHEN online_status = 'stale' THEN 1 ELSE 0 END), 0) AS stale,
                COALESCE(SUM(CASE WHEN online_status = 'offline' THEN 1 ELSE 0 END), 0) AS offline
            FROM miners
            """
        ).fetchone()
        current_height = _latest_block_height(connection)
        latest_hash = _latest_block_hash(connection)

    blocks = [_mining_metric_from_row(row) for row in rows]
    blocks.reverse()
    work_rates = [block["work_rate_hps"] for block in blocks if block["work_rate_hps"] > 0]
    block_times = [block["total_block_ms"] for block in blocks if block["total_block_ms"] > 0]
    segment_size = float(params["segment_size"] or 64)
    active_rates = []
    active_compute_ms = []
    for row in active_compute_rows:
        compute_ms = int(row["last_compute_ms"] or 0)
        if compute_ms <= 0:
            continue
        active_compute_ms.append(compute_ms)
        active_rates.append(segment_size / (compute_ms / 1000))
    avg_block_compute_rate = round(sum(work_rates) / len(work_rates), 4) if work_rates else 0.0
    online_miners = int(miner_counts["online"] or 0)
    if active_rates:
        network_compute_rate = round(sum(active_rates), 4)
    elif avg_block_compute_rate > 0 and online_miners > 0:
        network_compute_rate = round(avg_block_compute_rate * online_miners, 4)
    else:
        network_compute_rate = 0.0
    latest = blocks[-1] if blocks else None
    top_miners = []
    for row in miner_rows:
        avg_compute_ms = blocks_or_zero(row["avg_compute_ms"] or row["avg_total_task_ms"] or row["avg_total_block_ms"])
        avg_task_ms = blocks_or_zero(row["avg_total_task_ms"] or row["avg_total_block_ms"])
        avg_work_rate = round(segment_size / (avg_compute_ms / 1000), 4) if avg_compute_ms > 0 else 0.0
        top_miners.append(
            {
                "miner_id": row["miner_id"],
                "miner_reward_address": row["miner_reward_address"],
                "online_status": row["online_status"],
                "accepted_blocks": int(row["accepted_blocks"] or 0),
                "total_rewards": round(float(row["total_rewards"] or 0), 8),
                "avg_compute_ms": round(avg_compute_ms, 2),
                "avg_total_task_ms": round(avg_task_ms, 2),
                "avg_total_block_ms": round(blocks_or_zero(row["avg_total_block_ms"]), 2),
                "avg_difficulty": round(float(row["avg_difficulty"] or 0), 8),
                "last_compute_ms": int(row["last_compute_ms"] or 0),
                "avg_work_rate_hps": avg_work_rate,
                "latest_block_height": int(row["latest_block_height"] or 0),
                "latest_block_at": row["latest_block_at"],
            }
        )

    return {
        "checked_at": utc_now(),
        "limit": sample_limit,
        "summary": {
            "current_height": latest["height"] if latest else current_height,
            "latest_block_hash": latest["block_hash"] if latest else latest_hash,
            "latest_difficulty": latest["difficulty"] if latest else calculate_difficulty(params),
            "active_difficulty": calculate_difficulty(params),
            "target_block_ms": params.get("target_block_time_ms") or RETARGET_TARGET_BLOCK_MS,
            "network_compute_rate_hps": network_compute_rate,
            "avg_work_rate_hps": network_compute_rate or avg_block_compute_rate,
            "avg_accepted_block_work_rate_hps": avg_block_compute_rate,
            "avg_compute_ms": round(sum(active_compute_ms) / len(active_compute_ms), 2) if active_compute_ms else 0.0,
            "avg_total_block_ms": round(sum(block_times) / len(block_times), 2) if block_times else 0.0,
            "blocks_sampled": len(blocks),
            "online_compute_miners": len(active_compute_ms),
            "network_compute_rate_source": "miner_heartbeat" if active_rates else ("accepted_block_estimate" if network_compute_rate else "none"),
            "active_miners": online_miners,
            "total_miners": int(miner_counts["total"] or 0),
            "block_maturity_depth": BLOCK_MATURITY_DEPTH,
        },
        "blocks": blocks,
        "top_miners": top_miners,
    }


def lookup_miner_activity(query: str, limit: int = 25) -> dict[str, Any]:
    search = query.strip()
    if not search:
        raise MiningError(400, "query is required")
    block_limit = max(1, min(int(limit), 100))
    normalized_wallet = search.upper()
    refresh_participant_liveness()
    with get_connection() as connection:
        miner_rows = connection.execute(
            """
            SELECT *
            FROM miners
            WHERE miner_id = ?
               OR UPPER(COALESCE(reward_address, '')) = ?
            ORDER BY
                CASE online_status
                    WHEN 'online' THEN 0
                    WHEN 'stale' THEN 1
                    WHEN 'offline' THEN 2
                    ELSE 3
                END,
                registered_at ASC
            LIMIT 50
            """,
            (search, normalized_wallet),
        ).fetchall()
        miner_ids = [row["miner_id"] for row in miner_rows]
        params: list[Any] = []
        where_parts = ["blocks.miner_id = ?", "UPPER(COALESCE(blocks.miner_reward_address, '')) = ?"]
        params.extend([search, normalized_wallet])
        if miner_ids:
            where_parts.append(f"blocks.miner_id IN ({','.join('?' for _ in miner_ids)})")
            params.extend(miner_ids)
        where_sql = " OR ".join(where_parts)
        aggregate = connection.execute(
            f"""
            SELECT
                COUNT(*) AS accepted_blocks,
                COALESCE(SUM(blocks.reward), 0) AS total_rewards,
                COALESCE(AVG(tasks.compute_ms), AVG(blocks.total_task_ms), 0) AS avg_compute_ms,
                COALESCE(AVG(blocks.total_task_ms), 0) AS avg_total_task_ms,
                COALESCE(AVG(blocks.total_block_ms), 0) AS avg_total_block_ms,
                COALESCE(AVG(blocks.difficulty), 0) AS avg_difficulty,
                MIN(blocks.height) AS first_block_height,
                MAX(blocks.height) AS latest_block_height,
                MAX(blocks.timestamp) AS latest_block_at
            FROM blocks
            LEFT JOIN tasks ON tasks.task_id = blocks.task_id
            WHERE {where_sql}
            """,
            tuple(params),
        ).fetchone()
        block_rows = connection.execute(
            f"""
            SELECT
                blocks.height,
                blocks.timestamp,
                blocks.miner_id,
                blocks.miner_reward_address,
                blocks.block_hash,
                blocks.result_hash,
                blocks.range_start,
                blocks.range_end,
                blocks.reward,
                COALESCE(blocks.difficulty, protocol_params.difficulty, 0) AS difficulty,
                tasks.compute_ms AS compute_ms,
                blocks.total_task_ms,
                blocks.total_block_ms,
                blocks.validation_ms,
                COALESCE(protocol_params.segment_size, blocks.range_end - blocks.range_start + 1) AS segment_size,
                rewards.status AS reward_status,
                rewards.matures_at_height AS matures_at_height,
                rewards.matured_at AS matured_at
            FROM blocks
            LEFT JOIN protocol_params ON protocol_params.id = blocks.protocol_params_id
            LEFT JOIN tasks ON tasks.task_id = blocks.task_id
            LEFT JOIN rewards ON rewards.block_height = blocks.height
                AND rewards.miner_id = blocks.miner_id
                AND ABS(COALESCE(rewards.amount, 0) - COALESCE(blocks.reward, 0)) <= ?
            WHERE {where_sql}
            ORDER BY blocks.height DESC
            LIMIT ?
            """,
            (ECONOMIC_AUDIT_TOLERANCE, *params, block_limit),
        ).fetchall()
        account = get_balance(normalized_wallet) if normalized_wallet.startswith("PI") else None
        current_height = _latest_block_height(connection)

    miners = [enrich_miner(row_to_dict(row)) for row in miner_rows]
    recent_blocks = [_mining_metric_from_row(row) for row in block_rows]
    avg_compute_ms = blocks_or_zero(
        aggregate["avg_compute_ms"] or aggregate["avg_total_task_ms"] or aggregate["avg_total_block_ms"]
    )
    avg_task_ms = blocks_or_zero(aggregate["avg_total_task_ms"] or aggregate["avg_total_block_ms"])
    segment_size = recent_blocks[0]["segment_size"] if recent_blocks else 0
    avg_work_rate = round(segment_size / (avg_compute_ms / 1000), 4) if segment_size and avg_compute_ms > 0 else 0.0
    online_miners = sum(1 for miner in miners if miner.get("online_status") == "online")
    return {
        "query": search,
        "found": bool(miners or int(aggregate["accepted_blocks"] or 0) > 0 or account),
        "type": "reward_wallet" if normalized_wallet.startswith("PI") else "miner",
        "status": "online" if online_miners else ("known" if miners or account else "not_found"),
        "online_miners": online_miners,
        "miner_count": len(miners),
        "miners": miners,
        "account": account,
        "summary": {
            "accepted_blocks": int(aggregate["accepted_blocks"] or 0),
            "total_rewards": round(float(aggregate["total_rewards"] or 0), 8),
            "avg_compute_ms": round(avg_compute_ms, 2),
            "avg_total_task_ms": round(avg_task_ms, 2),
            "avg_total_block_ms": round(blocks_or_zero(aggregate["avg_total_block_ms"]), 2),
            "avg_difficulty": round(float(aggregate["avg_difficulty"] or 0), 8),
            "avg_work_rate_hps": avg_work_rate,
            "first_block_height": aggregate["first_block_height"],
            "latest_block_height": aggregate["latest_block_height"],
            "latest_block_at": aggregate["latest_block_at"],
            "block_maturity_depth": BLOCK_MATURITY_DEPTH,
            "current_height": current_height,
        },
        "recent_blocks": recent_blocks,
        "checked_at": utc_now(),
    }


def get_network_participation_status() -> dict[str, Any]:
    refresh_participant_liveness()
    with get_connection() as connection:
        params = _active_protocol_params(connection)
        counts = _node_counts(connection, params)
        latest_height = _latest_block_height(connection)
        latest_hash = _latest_block_hash(connection)
        revealed_waiting = int(
            connection.execute("SELECT COUNT(*) AS count FROM tasks WHERE status = 'revealed'").fetchone()["count"]
        )
        stuck_jobs = int(
            connection.execute("SELECT COUNT(*) AS count FROM validation_jobs WHERE status = 'pending'").fetchone()["count"]
        )
        eligible = counts["eligible_validators"]
        required = counts["required_validator_approvals"]
    blocking_reason = None
    if eligible <= 0:
        blocking_reason = "not_enough_online_validators"
    elif eligible < required:
        blocking_reason = "waiting_for_validators"
    return {
        "checked_at": utc_now(),
        "network_id": NETWORK_ID,
        "protocol_version": PROTOCOL_VERSION,
        "latest_block_height": latest_height,
        "latest_block_hash": latest_hash,
        "quorum": {
            "healthy": eligible >= required,
            "eligible_validators": eligible,
            "required_validator_approvals": required,
            "blocking_reason": blocking_reason,
        },
        "tasks": {
            "revealed_tasks_waiting": revealed_waiting,
            "stuck_validation_jobs": stuck_jobs,
        },
        "counts": counts,
    }


def set_validator_enabled(validator_id: str, enabled: bool) -> dict[str, Any]:
    reason = None if enabled else "disabled by operator"
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE validators
            SET enabled = ?, reason_if_not_eligible = ?, online_status = CASE WHEN ? = 0 THEN 'offline' ELSE online_status END
            WHERE validator_id = ?
            """,
            (1 if enabled else 0, reason, 1 if enabled else 0, validator_id),
        )
        if cursor.rowcount == 0:
            raise MiningError(404, "validator not found")
        row = connection.execute("SELECT * FROM validators WHERE validator_id = ?", (validator_id,)).fetchone()
    refresh_participant_liveness()
    return enrich_validator(row_to_dict(row))


def set_miner_enabled(miner_id: str, enabled: bool) -> dict[str, Any]:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE miners
            SET enabled = ?, online_status = CASE WHEN ? = 0 THEN 'offline' ELSE online_status END
            WHERE miner_id = ?
            """,
            (1 if enabled else 0, 1 if enabled else 0, miner_id),
        )
        if cursor.rowcount == 0:
            raise MiningError(404, "miner not found")
        row = connection.execute("SELECT * FROM miners WHERE miner_id = ?", (miner_id,)).fetchone()
    refresh_participant_liveness()
    return enrich_miner(row_to_dict(row))


def prune_stale_validators(older_than_seconds: int = PARTICIPANT_OFFLINE_SECONDS) -> dict[str, Any]:
    threshold = (utc_now_dt() - timedelta(seconds=max(1, older_than_seconds))).isoformat()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            DELETE FROM validators
            WHERE online_status = 'offline'
              AND COALESCE(last_heartbeat_at, registered_at) < ?
              AND accepted_jobs = 0
              AND rejected_jobs = 0
            """,
            (threshold,),
        )
        deleted = max(0, cursor.rowcount)
    return {"deleted": deleted, "older_than_seconds": older_than_seconds, "checked_at": utc_now()}


def prune_stale_miners(older_than_seconds: int = PARTICIPANT_OFFLINE_SECONDS) -> dict[str, Any]:
    threshold = (utc_now_dt() - timedelta(seconds=max(1, older_than_seconds))).isoformat()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            DELETE FROM miners
            WHERE online_status = 'offline'
              AND COALESCE(last_heartbeat_at, registered_at) < ?
              AND NOT EXISTS (SELECT 1 FROM blocks WHERE blocks.miner_id = miners.miner_id)
            """,
            (threshold,),
        )
        deleted = max(0, cursor.rowcount)
    return {"deleted": deleted, "older_than_seconds": older_than_seconds, "checked_at": utc_now()}


def _task_with_network_context(task: dict[str, Any] | None) -> dict[str, Any] | None:
    if task is None:
        return None
    task["network_id"] = NETWORK_ID
    task["chain_id"] = CHAIN_ID
    return task


def _task_for_next_response(connection: Any, task: dict[str, Any] | None) -> dict[str, Any] | None:
    if task is None:
        return None
    response = dict(task)
    if isinstance(response.get("selected_tx_hashes"), str):
        try:
            response["selected_tx_hashes"] = json.loads(response["selected_tx_hashes"])
        except (TypeError, ValueError):
            response["selected_tx_hashes"] = []
    if response.get("status") == "committed":
        commitment = connection.execute(
            "SELECT 1 FROM commitments WHERE task_id = ? AND miner_id = ?",
            (response["task_id"], response["miner_id"]),
        ).fetchone()
        if commitment is not None:
            response["resume_status"] = "committed"
            response["status"] = "assigned"
    return _task_with_network_context(response)


def _competitive_task_id(miner_id: str, assignment: dict[str, Any], params: dict[str, Any]) -> str:
    digest = sha256_text(
        canonical_json(
            {
                "mode": COMPETITIVE_ROUND_ASSIGNMENT_MODE,
                "network_id": NETWORK_ID,
                "chain_id": CHAIN_ID,
                "miner_id": miner_id,
                "height": int(assignment.get("round_height") or 0),
                "previous_hash": assignment.get("previous_hash"),
                "assignment_seed": assignment.get("assignment_seed"),
                "range_start": int(assignment["range_start"]),
                "range_end": int(assignment["range_end"]),
                "algorithm": params["algorithm"],
                "protocol_params_id": params["id"],
            }
        )
    )
    return f"task_{digest[:16]}"


def create_next_task(
    miner_id: str,
    *,
    public_key: str | None = None,
    name: str | None = None,
    reward_address: str | None = None,
) -> dict[str, Any] | None:
    started = now_perf()
    _ensure_replay_can_accept_work()
    refresh_participant_liveness()
    with get_connection() as connection:
        _maybe_expire_assigned_tasks(connection)
        miner = row_to_dict(connection.execute("SELECT * FROM miners WHERE miner_id = ?", (miner_id,)).fetchone())
        if miner is None and public_key:
            miner = _restore_miner_identity(connection, miner_id, public_key, name, reward_address)
        elif miner is not None and reward_address:
            _update_miner_reward_address(connection, miner_id, reward_address)
            miner = row_to_dict(connection.execute("SELECT * FROM miners WHERE miner_id = ?", (miner_id,)).fetchone())
        if miner is None:
            return None

        if miner["is_banned"]:
            raise MiningError(403, "miner is banned")
        if not bool(miner.get("enabled", 1)):
            raise MiningError(403, "miner is disabled")
        _maybe_update_miner_task_poll(connection, miner)

        cooldown_until = parse_iso(miner["cooldown_until"])
        if cooldown_until is not None and cooldown_until > utc_now_dt():
            raise MiningError(429, f"miner is in cooldown until {miner['cooldown_until']}")

        params = _active_protocol_params(connection)
        if MINING_TASK_MODE == COMPETITIVE_ROUND_ASSIGNMENT_MODE:
            resumable_task = _resume_expired_committed_competitive_task_for_miner(
                connection,
                miner_id,
                params,
            )
            if resumable_task is not None:
                return _task_for_next_response(connection, resumable_task)

        active_task = connection.execute(
            """
            SELECT * FROM tasks
            WHERE miner_id = ? AND status IN ('assigned', 'committed', 'revealed')
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (miner_id,),
        ).fetchone()
        if active_task is not None:
            task = row_to_dict(active_task)
            expires_at = parse_iso(task.get("expires_at"))
            if expires_at is not None and expires_at <= utc_now_dt():
                connection.execute("UPDATE tasks SET status = 'expired' WHERE task_id = ?", (task["task_id"],))
                release_selected_transactions(connection, task["task_id"], "task expired")
                task = None
            elif MINING_TASK_MODE == COMPETITIVE_ROUND_ASSIGNMENT_MODE and _expire_stale_competitive_task(
                connection,
                task,
                params,
            ):
                task = None
            if task is not None:
                RETARGET_MAX_PI_POSITION_value = _resolve_RETARGET_MAX_PI_POSITION(params)
                if int(task["range_end"]) > RETARGET_MAX_PI_POSITION_value:
                    raise MiningError(
                        409,
                        f"active task exceeds RETARGET_MAX_PI_POSITION={RETARGET_MAX_PI_POSITION_value}",
                    )
                return _task_for_next_response(connection, task)

        recent_assignments = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM tasks
            WHERE miner_id = ?
            AND created_at >= ?
            """,
            (miner_id, iso_ago(TASK_RATE_LIMIT_WINDOW_SECONDS)),
        ).fetchone()["count"]
        if recent_assignments >= TASK_RATE_LIMIT_MAX_ASSIGNMENTS:
            raise MiningError(429, "miner task assignment rate limit exceeded")

        active_count = connection.execute(
            "SELECT COUNT(*) AS count FROM tasks WHERE miner_id = ? AND status IN ('assigned', 'committed', 'revealed')",
            (miner_id,),
        ).fetchone()["count"]
        if active_count >= params["max_active_tasks_per_miner"]:
            raise MiningError(429, "miner has too many active tasks")

        if MINING_TASK_MODE != COMPETITIVE_ROUND_ASSIGNMENT_MODE:
            pooled_task = _claim_global_task_for_miner(connection, miner_id, params)
            if pooled_task is not None:
                return _task_for_next_response(connection, pooled_task)

        if MINING_TASK_MODE == COMPETITIVE_ROUND_ASSIGNMENT_MODE:
            assignment = _competitive_round_assignment(connection, params)
            if _competitive_round_has_pending_validation_job(connection, assignment.get("assignment_seed")):
                raise MiningError(429, "competitive round is waiting for validation; retry after next block")
            assignment_mode = COMPETITIVE_ROUND_ASSIGNMENT_MODE
            task_id = _competitive_task_id(miner_id, assignment, params)
            existing_round_task = row_to_dict(
                connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            )
            if existing_round_task is not None:
                revived_task = _reactivate_expired_competitive_task(
                    connection,
                    existing_round_task,
                    assignment,
                    params,
                )
                return _task_for_next_response(connection, revived_task or existing_round_task)
        else:
            task_id = f"task_{uuid.uuid4().hex[:16]}"
            assignment = _assign_pseudo_random_range(connection, miner_id, task_id, params)
            assignment_mode = params["range_assignment_mode"]
        assignment_ms = elapsed_ms(started)
        now = utc_now()
        expires_at = iso_at(_task_expiration_seconds_for_position(params, assignment["range_end"]))

        try:
            connection.execute(
                """
                INSERT INTO tasks (
                    task_id, miner_id, range_start, range_end, algorithm, status,
                    assignment_seed, assignment_mode, competitive_round_height,
                    competitive_round_previous_hash, assignment_ms, protocol_params_id,
                    created_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, 'assigned', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    miner_id,
                    assignment["range_start"],
                    assignment["range_end"],
                    params["algorithm"],
                    assignment["assignment_seed"],
                    assignment_mode,
                    assignment.get("round_height"),
                    assignment.get("previous_hash"),
                    assignment_ms,
                    params["id"],
                    now,
                    expires_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            if "tasks.task_id" not in str(exc):
                raise
            existing_task = row_to_dict(
                connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            )
            if existing_task is None:
                raise
            revived_task = _reactivate_expired_competitive_task(
                connection,
                existing_task,
                assignment,
                params,
            )
            return _task_for_next_response(connection, revived_task or existing_task)
        next_height = int(assignment.get("round_height") or (_latest_chain_tip_in_connection(connection)["height"] + 1))
        if assignment_mode == COMPETITIVE_ROUND_ASSIGNMENT_MODE:
            tx_snapshot = freeze_transactions_for_competitive_round_task(
                connection,
                task_id=task_id,
                block_height=next_height,
                assignment_seed=assignment.get("assignment_seed"),
                max_count=MAX_TRANSACTIONS_PER_BLOCK,
                timestamp=now,
            )
        else:
            tx_snapshot = freeze_transactions_for_task(
                connection,
                task_id=task_id,
                block_height=next_height,
                max_count=MAX_TRANSACTIONS_PER_BLOCK,
                timestamp=now,
            )
        if not tx_snapshot.get("reused"):
            print(
                json.dumps(
                    {
                        "event": "task_tx_snapshot_created",
                        "task_id": task_id,
                        "tx_count": tx_snapshot["tx_count"],
                        "tx_merkle_root": tx_snapshot["tx_merkle_root"],
                        "mempool_snapshot_id": tx_snapshot["snapshot_id"],
                        "tx_fee_total_units": tx_snapshot["tx_fee_total_units"],
                    },
                    sort_keys=True,
                )
            )
        row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        task = row_to_dict(row)
    return _task_for_next_response(connection, task)


def _restore_miner_identity(
    connection: Any,
    miner_id: str,
    public_key: str,
    name: str | None,
    reward_address: str | None = None,
) -> dict[str, Any] | None:
    miner_id = miner_id.strip()
    if not miner_id.startswith("miner_"):
        return None
    try:
        validate_public_key(public_key)
    except (RuntimeError, ValueError) as exc:
        raise MiningError(400, str(exc)) from exc
    reward_address = _normalize_reward_address(reward_address)
    timestamp = utc_now()
    connection.execute(
        """
        INSERT INTO miners (
            miner_id, name, public_key, reward_address, registered_at,
            last_seen_at, last_heartbeat_at, online_status, protocol_version, enabled
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'online', ?, 1)
        ON CONFLICT(miner_id) DO UPDATE SET
            name = COALESCE(NULLIF(excluded.name, ''), miners.name),
            public_key = COALESCE(miners.public_key, excluded.public_key),
            reward_address = COALESCE(excluded.reward_address, miners.reward_address),
            last_seen_at = excluded.last_seen_at,
            last_heartbeat_at = excluded.last_heartbeat_at,
            online_status = 'online',
            protocol_version = excluded.protocol_version
        """,
        (miner_id, (name or miner_id)[:80], public_key, reward_address, timestamp, timestamp, timestamp, PROTOCOL_VERSION),
    )
    _ensure_balance_account(connection, miner_id, "miner")
    if reward_address:
        _ensure_balance_account(connection, reward_address, "wallet")
    return row_to_dict(connection.execute("SELECT * FROM miners WHERE miner_id = ?", (miner_id,)).fetchone())


def _update_miner_reward_address(connection: Any, miner_id: str, reward_address: str) -> None:
    reward_address = _normalize_reward_address(reward_address)
    if not reward_address:
        return
    current = connection.execute("SELECT reward_address FROM miners WHERE miner_id = ?", (miner_id,)).fetchone()
    if current is not None and str(current["reward_address"] or "") == reward_address:
        return
    connection.execute("UPDATE miners SET reward_address = ? WHERE miner_id = ?", (reward_address, miner_id))
    _ensure_balance_account(connection, reward_address, "wallet")


def _maybe_update_miner_task_poll(connection: Any, miner: dict[str, Any]) -> None:
    now_dt = utc_now_dt()
    last_seen = parse_iso(miner.get("last_heartbeat_at") or miner.get("last_seen_at"))
    online_status = str(miner.get("online_status") or "")
    if (
        last_seen is not None
        and (now_dt - last_seen).total_seconds() < MINER_TASK_HEARTBEAT_MIN_INTERVAL_SECONDS
        and online_status == "online"
    ):
        return
    timestamp = now_dt.isoformat()
    connection.execute(
        """
        UPDATE miners
        SET last_seen_at = ?, last_heartbeat_at = ?, online_status = 'online'
        WHERE miner_id = ?
        """,
        (timestamp, timestamp, miner["miner_id"]),
    )


def _restore_validator_identity(
    connection: Any,
    validator_id: str,
    public_key: str,
    name: str | None,
    reward_address: str | None = None,
) -> dict[str, Any] | None:
    validator_id = validator_id.strip()
    if not validator_id.startswith("validator_"):
        return None
    try:
        validate_public_key(public_key)
    except (RuntimeError, ValueError) as exc:
        raise MiningError(400, str(exc)) from exc
    reward_address = _normalize_reward_address(reward_address)
    timestamp = utc_now()
    connection.execute(
        """
        INSERT INTO validators (
            validator_id, name, public_key, reward_address, registered_at,
            last_seen_at, last_heartbeat_at, online_status, sync_status, protocol_version, enabled, stake_locked
        )
        VALUES (?, ?, ?, ?, ?, ?, NULL, 'offline', 'unknown', ?, 1, ?)
        ON CONFLICT(validator_id) DO UPDATE SET
            name = COALESCE(NULLIF(excluded.name, ''), validators.name),
            public_key = COALESCE(validators.public_key, excluded.public_key),
            reward_address = COALESCE(excluded.reward_address, validators.reward_address),
            last_seen_at = excluded.last_seen_at,
            protocol_version = excluded.protocol_version
        """,
        (
            validator_id,
            (name or validator_id)[:80],
            public_key,
            reward_address,
            timestamp,
            timestamp,
            PROTOCOL_VERSION,
            VALIDATOR_REGISTRATION_STAKE,
        ),
    )
    _ensure_balance_account(connection, validator_id, "validator")
    if reward_address:
        _ensure_balance_account(connection, reward_address, "wallet")
    return row_to_dict(connection.execute("SELECT * FROM validators WHERE validator_id = ?", (validator_id,)).fetchone())


def _update_validator_reward_address(connection: Any, validator_id: str, reward_address: str) -> None:
    reward_address = _normalize_reward_address(reward_address)
    if not reward_address:
        return
    connection.execute("UPDATE validators SET reward_address = ? WHERE validator_id = ?", (reward_address, validator_id))
    _ensure_balance_account(connection, reward_address, "wallet")


def _claim_global_task_for_miner(
    connection: Any,
    miner_id: str,
    params: dict[str, Any],
) -> dict[str, Any] | None:
    timestamp = utc_now()
    RETARGET_MAX_PI_POSITION_value = _resolve_RETARGET_MAX_PI_POSITION(params)
    row = connection.execute(
        """
        SELECT *
        FROM tasks
        WHERE status IN ('pending', 'queued', 'available')
          AND COALESCE(NULLIF(algorithm, ''), ?) = ?
          AND range_end <= ?
          AND (expires_at IS NULL OR expires_at > ?)
          AND (
              miner_id = ?
              OR miner_id IS NULL
              OR miner_id = ''
              OR miner_id = 'global'
          )
        ORDER BY created_at ASC
        LIMIT 1
        """,
        (params["algorithm"], params["algorithm"], RETARGET_MAX_PI_POSITION_value, timestamp, miner_id),
    ).fetchone()
    if row is None:
        return None
    expires_at = iso_at(_task_expiration_seconds_for_position(params, row["range_end"]))
    connection.execute(
        """
        UPDATE tasks
        SET miner_id = ?,
            status = 'assigned',
            algorithm = COALESCE(NULLIF(algorithm, ''), ?),
            protocol_params_id = COALESCE(protocol_params_id, ?),
            assignment_mode = COALESCE(assignment_mode, ?),
            expires_at = ?,
            assignment_ms = COALESCE(assignment_ms, 0)
        WHERE task_id = ?
        """,
        (
            miner_id,
            params["algorithm"],
            params["id"],
            params["range_assignment_mode"],
            expires_at,
            row["task_id"],
        ),
    )
    next_height = _latest_chain_tip_in_connection(connection)["height"] + 1
    freeze_transactions_for_task(
        connection,
        task_id=row["task_id"],
        block_height=next_height,
        max_count=MAX_TRANSACTIONS_PER_BLOCK,
        timestamp=timestamp,
    )
    return row_to_dict(connection.execute("SELECT * FROM tasks WHERE task_id = ?", (row["task_id"],)).fetchone())


def _competitive_round_assignment(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    tip = _latest_chain_tip_in_connection(connection)
    next_height = int(tip["height"]) + 1
    window = _range_assignment_window(connection, params)
    segment_size = int(params["segment_size"])
    min_start = int(window["min_start"])
    max_start = int(window["max_start"])
    candidate_count = max(1, max_start - min_start + 1)
    assignment_seed = sha256_text(
        canonical_json(
            {
                "mode": COMPETITIVE_ROUND_ASSIGNMENT_MODE,
                "network_id": NETWORK_ID,
                "chain_id": CHAIN_ID,
                "height": next_height,
                "previous_hash": tip["block_hash"],
                "algorithm": params["algorithm"],
                "segment_size": segment_size,
                "protocol_params_id": params["id"],
            }
        )
    )
    range_start = None
    max_attempts = max(1, int(params.get("range_assignment_max_attempts") or RANGE_ASSIGNMENT_MAX_ATTEMPTS))
    for attempt in range(max_attempts):
        attempt_seed = assignment_seed if attempt == 0 else sha256_text(f"{assignment_seed}:{attempt}")
        candidate_start = min_start + (int(attempt_seed, 16) % candidate_count)
        candidate_end = candidate_start + segment_size - 1
        if not _range_start_is_accepted(connection, candidate_start, params["algorithm"]):
            range_start = candidate_start
            range_end = candidate_end
            break
    else:
        raise MiningError(409, "no assignable competitive range available in active window")
    assert range_start is not None
    range_end = range_start + segment_size - 1
    return {
        "range_start": range_start,
        "range_end": range_end,
        "assignment_seed": assignment_seed,
        "round_height": next_height,
        "previous_hash": tip["block_hash"],
    }


def _is_competitive_task(task: dict[str, Any] | None) -> bool:
    return bool(task and task.get("assignment_mode") == COMPETITIVE_ROUND_ASSIGNMENT_MODE)


def _task_competitive_round_height(task: dict[str, Any]) -> int | None:
    try:
        value = task.get("competitive_round_height")
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _block_anchor_for_task(connection: Any, task: dict[str, Any]) -> dict[str, Any]:
    tip = _latest_chain_tip_in_connection(connection)
    if _is_competitive_task(task):
        round_height = _task_competitive_round_height(task)
        previous_hash = str(task.get("competitive_round_previous_hash") or "").strip()
        if round_height is not None and previous_hash:
            return {
                "height": round_height,
                "previous_hash": previous_hash,
                "anchored": True,
                "tip": tip,
            }
    return {
        "height": int(tip["height"]) + 1,
        "previous_hash": tip["block_hash"],
        "anchored": False,
        "tip": tip,
    }


def _ensure_block_anchor_is_local_tip(anchor: dict[str, Any]) -> None:
    if not anchor.get("anchored"):
        return
    tip = anchor["tip"]
    expected_tip_height = int(anchor["height"]) - 1
    if int(tip["height"]) == expected_tip_height and str(tip["block_hash"]) == str(anchor["previous_hash"]):
        return
    raise ValidationFinalizationDeferred(
        "competitive round parent is not local chain tip: "
        f"expected height={expected_tip_height} hash={anchor['previous_hash']} "
        f"got height={tip['height']} hash={tip['block_hash']}"
    )


def _validation_block_elapsed_ms(task: dict[str, Any], timestamp: str) -> int | None:
    compute_ms = task.get("compute_ms")
    if compute_ms is not None:
        try:
            return max(0, int(compute_ms))
        except (TypeError, ValueError):
            pass
    return _elapsed_iso_ms(task.get("created_at"), timestamp)


def _competitive_round_has_earlier_pending_validation_job(connection: Any, job: dict[str, Any]) -> bool:
    if str(job.get("assignment_mode") or "") != COMPETITIVE_ROUND_ASSIGNMENT_MODE:
        return False
    assignment_seed = str(job.get("assignment_seed") or "").strip()
    if not assignment_seed:
        return False
    created_at = str(job.get("job_created_at") or job.get("created_at") or "")
    job_id = str(job.get("job_id") or "")
    if not created_at or not job_id:
        return False
    return (
        connection.execute(
            """
            SELECT 1
            FROM validation_jobs AS earlier_jobs
            JOIN tasks AS earlier_tasks ON earlier_tasks.task_id = earlier_jobs.task_id
            WHERE earlier_jobs.status IN ('pending', 'approved')
            AND earlier_tasks.status = 'revealed'
            AND earlier_tasks.assignment_mode = ?
            AND earlier_tasks.assignment_seed = ?
            AND earlier_jobs.job_id != ?
            AND (
                earlier_jobs.created_at < ?
                OR (
                    earlier_jobs.created_at = ?
                    AND earlier_jobs.job_id < ?
                )
            )
            LIMIT 1
            """,
            (
                COMPETITIVE_ROUND_ASSIGNMENT_MODE,
                assignment_seed,
                job_id,
                created_at,
                created_at,
                job_id,
            ),
        ).fetchone()
        is not None
    )


def _competitive_round_has_pending_validation_job(connection: Any, assignment_seed: str | None) -> bool:
    assignment_seed = str(assignment_seed or "").strip()
    if not assignment_seed:
        return False
    return (
        connection.execute(
            """
            SELECT 1
            FROM validation_jobs
            JOIN tasks ON tasks.task_id = validation_jobs.task_id
            WHERE validation_jobs.status IN ('pending', 'approved')
              AND tasks.status = 'revealed'
              AND tasks.assignment_mode = ?
              AND tasks.assignment_seed = ?
            LIMIT 1
            """,
            (COMPETITIVE_ROUND_ASSIGNMENT_MODE, assignment_seed),
        ).fetchone()
        is not None
    )


def _reactivate_expired_competitive_task(
    connection: Any,
    task: dict[str, Any],
    assignment: dict[str, Any] | None,
    params: dict[str, Any],
) -> dict[str, Any] | None:
    if str(task.get("status") or "") != "expired" or not _is_competitive_task(task):
        return None
    assignment_matches = assignment is not None and _expired_competitive_task_matches_assignment(task, assignment)
    if _competitive_round_winner(connection, task) is not None:
        return None
    if connection.execute("SELECT 1 FROM validation_jobs WHERE task_id = ? LIMIT 1", (task["task_id"],)).fetchone():
        return None

    has_commitment = (
        connection.execute("SELECT 1 FROM commitments WHERE task_id = ? LIMIT 1", (task["task_id"],)).fetchone()
        is not None
    )
    if not assignment_matches:
        if not has_commitment:
            return None
        if not _expired_competitive_task_targets_current_tip(connection, task):
            return None

    timestamp = utc_now()
    expires_at = iso_at(_task_expiration_seconds_for_position(params, task.get("range_end")))
    connection.execute(
        """
        UPDATE tasks
        SET status = ?,
            created_at = ?,
            expires_at = ?,
            submitted_at = NULL,
            stale_at = NULL,
            stale_reason = NULL,
            compute_ms = NULL
        WHERE task_id = ?
          AND status = 'expired'
        """,
        ("committed" if has_commitment else "assigned", timestamp, expires_at, task["task_id"]),
    )
    if not has_commitment:
        release_selected_transactions(connection, task["task_id"], "task reactivated", timestamp)
        freeze_transactions_for_competitive_round_task(
            connection,
            task_id=task["task_id"],
            block_height=int(assignment["round_height"]) if assignment is not None else int(task["competitive_round_height"]),
            assignment_seed=assignment.get("assignment_seed") if assignment is not None else task.get("assignment_seed"),
            max_count=MAX_TRANSACTIONS_PER_BLOCK,
            timestamp=timestamp,
        )
    return row_to_dict(connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task["task_id"],)).fetchone())


def _expired_competitive_task_matches_assignment(task: dict[str, Any], assignment: dict[str, Any]) -> bool:
    if str(task.get("assignment_seed") or "") != str(assignment.get("assignment_seed") or ""):
        return False
    if _task_competitive_round_height(task) != int(assignment.get("round_height") or 0):
        return False
    if str(task.get("competitive_round_previous_hash") or "") != str(assignment.get("previous_hash") or ""):
        return False
    return (
        int(task.get("range_start") or 0) == int(assignment.get("range_start") or 0)
        and int(task.get("range_end") or 0) == int(assignment.get("range_end") or 0)
    )


def _expired_competitive_task_targets_current_tip(connection: Any, task: dict[str, Any]) -> bool:
    round_height = _task_competitive_round_height(task)
    if round_height is None:
        return False
    tip = _latest_chain_tip_in_connection(connection)
    if round_height != int(tip["height"]) + 1:
        return False
    previous_hash = str(task.get("competitive_round_previous_hash") or "")
    return not previous_hash or previous_hash == str(tip["block_hash"])


def _resume_expired_committed_competitive_task_for_miner(
    connection: Any,
    miner_id: str,
    params: dict[str, Any],
) -> dict[str, Any] | None:
    rows = connection.execute(
        """
        SELECT tasks.*
        FROM tasks
        JOIN commitments ON commitments.task_id = tasks.task_id
        LEFT JOIN validation_jobs ON validation_jobs.task_id = tasks.task_id
        WHERE tasks.miner_id = ?
          AND tasks.status = 'expired'
          AND tasks.assignment_mode = ?
          AND validation_jobs.job_id IS NULL
        ORDER BY COALESCE(tasks.competitive_round_height, 0) DESC,
                 tasks.created_at DESC,
                 tasks.task_id DESC
        LIMIT 10
        """,
        (miner_id, COMPETITIVE_ROUND_ASSIGNMENT_MODE),
    ).fetchall()
    for row in rows:
        task = row_to_dict(row)
        if task is None:
            continue
        revived = _reactivate_expired_competitive_task(connection, task, None, params)
        if revived is not None:
            return revived
    return None


def _competitive_task_matches_current_round(connection: Any, task: dict[str, Any], params: dict[str, Any]) -> bool:
    if not _is_competitive_task(task):
        return True
    assignment = _competitive_round_assignment(connection, params)
    stored_round_height = _task_competitive_round_height(task)
    stored_previous_hash = task.get("competitive_round_previous_hash")
    if stored_round_height is not None and stored_round_height != int(assignment["round_height"]):
        return False
    if stored_previous_hash and str(stored_previous_hash) != str(assignment["previous_hash"]):
        return False
    return (
        str(task.get("assignment_seed") or "") == assignment["assignment_seed"]
        and int(task.get("range_start") or 0) == int(assignment["range_start"])
        and int(task.get("range_end") or 0) == int(assignment["range_end"])
    )


def _mark_competitive_task_stale(
    connection: Any,
    task_id: str,
    reason: str,
    timestamp: str | None = None,
) -> bool:
    timestamp = timestamp or utc_now()
    cursor = connection.execute(
        """
        UPDATE tasks
        SET status = 'stale',
            submitted_at = COALESCE(submitted_at, ?),
            stale_at = ?,
            stale_reason = ?
        WHERE task_id = ?
          AND status IN ('assigned', 'committed', 'revealed')
        """,
        (timestamp, timestamp, reason, task_id),
    )
    changed = max(0, cursor.rowcount) > 0
    release_selected_transactions(connection, task_id, reason, timestamp)
    connection.execute(
        """
        UPDATE validation_jobs
        SET status = 'rejected',
            result_reason = ?,
            completed_at = COALESCE(completed_at, ?),
            finalized_at = COALESCE(finalized_at, ?)
        WHERE task_id = ?
          AND status = 'pending'
        """,
        (reason, timestamp, timestamp, task_id),
    )
    return changed


def _close_obsolete_competitive_validation_jobs(connection: Any) -> int:
    rows = connection.execute(
        """
        SELECT tasks.*
        FROM validation_jobs
        JOIN tasks ON tasks.task_id = validation_jobs.task_id
        WHERE validation_jobs.status = 'pending'
          AND tasks.status IN ('assigned', 'committed', 'revealed')
          AND tasks.assignment_mode = ?
        GROUP BY tasks.task_id
        ORDER BY tasks.created_at ASC, tasks.task_id ASC
        """,
        (COMPETITIVE_ROUND_ASSIGNMENT_MODE,),
    ).fetchall()
    closed_jobs = 0
    for row in rows:
        task = row_to_dict(row)
        if task is None:
            continue
        reason: str | None = None
        winner = _competitive_round_winner(connection, task)
        if winner is not None and winner["task_id"] != task["task_id"]:
            reason = f"competitive round already won by {winner['task_id']} at block {winner['height']}"
        else:
            round_height = _task_competitive_round_height(task)
            if round_height is not None:
                canonical = row_to_dict(
                    connection.execute(
                        "SELECT height, task_id FROM blocks WHERE height = ?",
                        (round_height,),
                    ).fetchone()
                )
                if canonical is not None and canonical.get("task_id") != task["task_id"]:
                    reason = f"competitive round already won by {canonical['task_id']} at block {canonical['height']}"
            if reason is None:
                params = _protocol_params_for_task(connection, task)
                if not _competitive_task_matches_current_round(connection, task, params):
                    reason = "competitive round closed"
        if reason is None:
            continue
        pending_jobs = int(
            connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM validation_jobs
                WHERE task_id = ? AND status = 'pending'
                """,
                (task["task_id"],),
            ).fetchone()["count"]
        )
        if _mark_competitive_task_stale(connection, task["task_id"], reason):
            closed_jobs += pending_jobs
    return closed_jobs


def _competitive_round_pending_candidate(connection: Any, task: dict[str, Any]) -> dict[str, Any] | None:
    if not _is_competitive_task(task):
        return None
    assignment_seed = str(task.get("assignment_seed") or "").strip()
    if not assignment_seed:
        return None
    row = connection.execute(
        """
        SELECT validation_jobs.job_id, validation_jobs.task_id, validation_jobs.created_at
        FROM validation_jobs
        JOIN tasks ON tasks.task_id = validation_jobs.task_id
        WHERE validation_jobs.status = 'pending'
          AND tasks.status = 'revealed'
          AND tasks.assignment_mode = ?
          AND tasks.assignment_seed = ?
          AND validation_jobs.task_id != ?
        ORDER BY validation_jobs.created_at ASC, validation_jobs.job_id ASC
        LIMIT 1
        """,
        (COMPETITIVE_ROUND_ASSIGNMENT_MODE, assignment_seed, task["task_id"]),
    ).fetchone()
    return row_to_dict(row)


def _expire_stale_competitive_task(
    connection: Any,
    task: dict[str, Any],
    params: dict[str, Any],
    reason: str = "competitive round closed",
) -> bool:
    if not _is_competitive_task(task):
        return False
    if _competitive_task_matches_current_round(connection, task, params):
        return False
    return _mark_competitive_task_stale(connection, task["task_id"], reason)


def _competitive_round_winner(connection: Any, task: dict[str, Any]) -> dict[str, Any] | None:
    if not _is_competitive_task(task):
        return None
    assignment_seed = str(task.get("assignment_seed") or "").strip()
    if not assignment_seed:
        return None
    row = connection.execute(
        """
        SELECT blocks.height, blocks.task_id, blocks.block_hash, blocks.miner_id
        FROM blocks
        JOIN tasks ON tasks.task_id = blocks.task_id
        WHERE tasks.assignment_mode = ?
          AND tasks.assignment_seed = ?
        ORDER BY blocks.height ASC
        LIMIT 1
        """,
        (COMPETITIVE_ROUND_ASSIGNMENT_MODE, assignment_seed),
    ).fetchone()
    return row_to_dict(row)


def _ensure_competitive_task_can_finalize(
    connection: Any,
    task: dict[str, Any],
    params: dict[str, Any],
    *,
    require_current_round: bool = True,
) -> None:
    if not _is_competitive_task(task):
        return
    if require_current_round and _expire_stale_competitive_task(connection, task, params):
        raise TransactionExecutionError("competitive round closed")
    winner = _competitive_round_winner(connection, task)
    if winner is not None and winner["task_id"] != task["task_id"]:
        reason = f"competitive round already won by {winner['task_id']} at block {winner['height']}"
        _mark_competitive_task_stale(connection, task["task_id"], reason)
        raise TransactionExecutionError(reason)


def _close_competitive_round_after_block(
    connection: Any,
    task: dict[str, Any],
    block_height: int,
    timestamp: str,
) -> dict[str, Any]:
    if not _is_competitive_task(task):
        return {"closed": False, "stale_tasks": 0, "stale_task_ids": []}
    assignment_seed = str(task.get("assignment_seed") or "").strip()
    if not assignment_seed:
        return {"closed": False, "stale_tasks": 0, "stale_task_ids": []}
    rows = connection.execute(
        """
        SELECT task_id
        FROM tasks
        WHERE assignment_mode = ?
          AND assignment_seed = ?
          AND task_id != ?
          AND status IN ('assigned', 'committed', 'revealed')
        ORDER BY created_at ASC, task_id ASC
        """,
        (COMPETITIVE_ROUND_ASSIGNMENT_MODE, assignment_seed, task["task_id"]),
    ).fetchall()
    stale_task_ids = [row["task_id"] for row in rows]
    reason = f"competitive round won by {task['task_id']} at block {block_height}"
    for stale_task_id in stale_task_ids:
        _mark_competitive_task_stale(connection, stale_task_id, reason, timestamp)
    return {
        "closed": True,
        "assignment_seed": assignment_seed,
        "winner_task_id": task["task_id"],
        "winner_block_height": block_height,
        "stale_tasks": len(stale_task_ids),
        "stale_task_ids": stale_task_ids,
    }


def _stale_competitive_response(
    connection: Any,
    task_id: str,
    miner_id: str,
    result_hash: str,
    signature: str | None,
    segment: str,
    reason: str = "competitive round closed",
) -> dict[str, Any]:
    _mark_competitive_task_stale(connection, task_id, reason)
    if _miner_exists(connection, miner_id):
        _record_submission(connection, task_id, miner_id, result_hash, segment, signature, False, reason)
    return {
        "accepted": False,
        "status": "stale",
        "message": reason,
        "block": None,
        "validation": {"reason": reason},
    }


def submit_task(
    task_id: str,
    miner_id: str,
    result_hash: str,
    segment: str,
    signature: str | None,
    signed_at: str | None = None,
) -> dict[str, Any]:
    with get_connection() as connection:
        _expire_assigned_tasks(connection)
        task = row_to_dict(
            connection.execute(
                "SELECT * FROM tasks WHERE task_id = ? AND miner_id = ?",
                (task_id, miner_id),
            ).fetchone()
        )
        miner = row_to_dict(connection.execute("SELECT * FROM miners WHERE miner_id = ?", (miner_id,)).fetchone())

        if task is None:
            return _reject_in_connection(
                connection,
                "task not found for miner",
                task_id,
                miner_id,
                result_hash,
                {},
                PENALTY_INVALID_RESULT,
                signature,
                segment,
            )

        if miner is None:
            return _reject_in_connection(
                connection,
                "miner not found",
                task_id,
                miner_id,
                result_hash,
                {},
                PENALTY_INVALID_SIGNATURE,
                signature,
                segment,
            )

        if task["status"] == "stale":
            return _stale_competitive_response(
                connection,
                task_id,
                miner_id,
                result_hash,
                signature,
                segment,
                task.get("stale_reason") or "competitive round closed",
            )
        if task["status"] != "assigned":
            return _reject_in_connection(
                connection,
                "task is not active",
                task_id,
                miner_id,
                result_hash,
                {"task_status": task["status"]},
                PENALTY_DUPLICATE,
                signature,
                segment,
            )
        params = _protocol_params_for_task(connection, task)
        if MINING_TASK_MODE == COMPETITIVE_ROUND_ASSIGNMENT_MODE and _expire_stale_competitive_task(
            connection,
            task,
            params,
        ):
            return _stale_competitive_response(
                connection,
                task_id,
                miner_id,
                result_hash,
                signature,
                segment,
                "competitive round closed",
            )

        expires_at = parse_iso(task["expires_at"])
        if expires_at is not None and expires_at <= utc_now_dt():
            connection.execute("UPDATE tasks SET status = 'expired' WHERE task_id = ?", (task_id,))
            return _reject_in_connection(
                connection,
                "task expired",
                task_id,
                miner_id,
                result_hash,
                {},
                PENALTY_INVALID_RESULT,
                signature,
                segment,
            )

        existing_block = connection.execute(
            "SELECT height FROM blocks WHERE result_hash = ? OR task_id = ?",
            (result_hash, task_id),
        ).fetchone()
        if existing_block is not None:
            return _reject_in_connection(
                connection,
                "duplicate result or task submission",
                task_id,
                miner_id,
                result_hash,
                {},
                PENALTY_DUPLICATE,
                signature,
                segment,
            )

        signature_payload = build_submission_signature_payload(
            task_id=task_id,
            miner_id=miner_id,
            range_start=task["range_start"],
            range_end=task["range_end"],
            algorithm=task["algorithm"],
            result_hash=result_hash,
            signed_at=signed_at or "",
        )
        try:
            signature_valid = (
                bool(signature)
                and bool(signed_at)
                and verify_payload_signature(miner["public_key"], signature_payload, signature)
            )
        except (RuntimeError, ValueError):
            signature_valid = False

        if not signature_valid:
            connection.execute(
                "UPDATE tasks SET status = 'rejected', submitted_at = ? WHERE task_id = ?",
                (utc_now(), task_id),
            )
            return _reject_in_connection(
                connection,
                "invalid miner signature",
                task_id,
                miner_id,
                result_hash,
                {"signature_present": bool(signature), "signed_at_present": bool(signed_at)},
                PENALTY_INVALID_SIGNATURE,
                signature,
                segment,
            )

        validation = validate_submission(
            range_start=task["range_start"],
            range_end=task["range_end"],
            algorithm=task["algorithm"],
            submitted_segment=segment,
            submitted_hash=result_hash,
        )

        validation_payload = {
            "reason": validation.reason,
            "expected_hash": validation.expected_hash,
            "samples": validation.samples,
            "signature_present": bool(signature),
            "signed_at": signed_at,
        }

        if not validation.accepted:
            connection.execute(
                "UPDATE tasks SET status = 'rejected', submitted_at = ? WHERE task_id = ?",
                (utc_now(), task_id),
            )
            return _reject_in_connection(
                connection,
                validation.reason,
                task_id,
                miner_id,
                result_hash,
                validation_payload,
                PENALTY_INVALID_RESULT,
                signature,
                segment,
            )

        total_block_reward = calculate_reward(params)
        reward = calculate_miner_reward(params)
        difficulty = calculate_difficulty(params)
        tip = _latest_chain_tip_in_connection(connection)
        next_height = tip["height"] + 1
        previous_hash = tip["block_hash"]
        timestamp = utc_now()
        try:
            _ensure_competitive_task_can_finalize(connection, task, params)
        except TransactionExecutionError as exc:
            return _stale_competitive_response(
                connection,
                task_id,
                miner_id,
                result_hash,
                signature,
                segment,
                str(exc),
            )

        created_at = parse_iso(task.get("created_at"))
        total_block_ms = int((utc_now_dt() - created_at).total_seconds() * 1000) if created_at else None
        total_task_ms = total_block_ms

        block_transactions = load_snapshot_transactions(connection, task_id)
        tx_commitment = transaction_commitment(block_transactions)
        miner_reward_account, miner_reward_account_type = _reward_account_for_miner(connection, miner_id)

        block_payload = {
            "algorithm": task["algorithm"],
            "height": next_height,
            "miner_id": miner_id,
            "previous_hash": previous_hash,
            "range_end": task["range_end"],
            "range_start": task["range_start"],
            "result_hash": result_hash,
            "reward": reward,
            "difficulty": difficulty,
            "samples": validation.samples,
            "timestamp": timestamp,
            "protocol_params_id": params["id"],
            "protocol_version": params["protocol_version"],
            "total_block_ms": total_block_ms,
            "validation_mode": params["validation_mode"],
            "fraudulent": False,
            "fraud_reason": None,
            "fraud_detected_at": None,
        }
        if miner_reward_account_type == "wallet":
            block_payload["miner_reward_address"] = miner_reward_account
        if tx_commitment["tx_count"]:
            block_payload["tx_merkle_root"] = tx_commitment["tx_merkle_root"]
            block_payload["tx_count"] = tx_commitment["tx_count"]
            block_payload["tx_hashes"] = tx_commitment["tx_hashes"]
            block_payload["fee_reward"] = tx_commitment["fee_reward"]
        block_hash = hash_block(block_payload)

        connection.execute(
            """
            INSERT INTO blocks (
                height, previous_hash, miner_id, range_start, range_end, algorithm,
                result_hash, samples, timestamp, block_hash, reward, reward_units, tx_merkle_root,
                tx_count, tx_hashes, fee_reward, fee_reward_units, miner_reward_address, difficulty, task_id, protocol_params_id,
                protocol_version, validation_mode, total_task_ms, total_block_ms
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                next_height,
                previous_hash,
                miner_id,
                task["range_start"],
                task["range_end"],
                task["algorithm"],
                result_hash,
                json.dumps(validation.samples),
                timestamp,
                block_hash,
                reward,
                to_units(reward),
                tx_commitment["tx_merkle_root"],
                tx_commitment["tx_count"],
                json.dumps(tx_commitment["tx_hashes"], sort_keys=True),
                tx_commitment["fee_reward"],
                to_units(tx_commitment["fee_reward"]),
                block_payload.get("miner_reward_address"),
                difficulty,
                task_id,
                params["id"],
                params["protocol_version"],
                params["validation_mode"],
                total_task_ms,
                total_block_ms,
            ),
        )
        connection.execute(
            "UPDATE tasks SET status = 'accepted', submitted_at = ? WHERE task_id = ?",
            (timestamp, task_id),
        )
        _record_submission(connection, task_id, miner_id, result_hash, segment, signature, True, "accepted")
        reward_maturity = record_miner_block_reward(
            connection,
            miner_id=miner_id,
            account_id=miner_reward_account,
            account_type=miner_reward_account_type,
            block_height=next_height,
            amount=reward,
            reason="block accepted",
            related_id=task_id,
            description="miner block reward",
            timestamp=timestamp,
        )
        tx_execution = apply_block_transactions(
            connection,
            miner_id=miner_reward_account,
            miner_account_type=miner_reward_account_type,
            block_height=next_height,
            transactions=block_transactions,
            timestamp=timestamp,
        )
        record_science_reserve_for_block(connection, next_height, total_block_reward)
        record_scientific_development_treasury_for_block(connection, next_height, total_block_reward)
        competitive_round = _close_competitive_round_after_block(connection, task, next_height, timestamp)
        matured_rewards = mature_block_rewards(connection, current_height=next_height, timestamp=timestamp)
        _refresh_trust_score(connection, miner_id)
        _maybe_retarget_after_block(connection, next_height)
        _maybe_run_scheduled_retroactive_audit(connection, next_height)
        state_root = update_block_state_root(connection, next_height, timestamp)
        checkpoint = maybe_create_checkpoint_in_connection(connection, next_height)

        block = {
            "height": next_height,
            "previous_hash": previous_hash,
            "miner_id": miner_id,
            "range_start": task["range_start"],
            "range_end": task["range_end"],
            "algorithm": task["algorithm"],
            "result_hash": result_hash,
            "samples": validation.samples,
            "timestamp": timestamp,
            "block_hash": block_hash,
            "reward": reward,
            "miner_reward_address": block_payload.get("miner_reward_address"),
            "tx_merkle_root": tx_commitment["tx_merkle_root"],
            "tx_count": tx_commitment["tx_count"],
            "tx_hashes": tx_commitment["tx_hashes"],
            "fee_reward": tx_commitment["fee_reward"],
            "state_root": state_root,
            "checkpoint": checkpoint,
            "transactions": block_transactions,
            "transaction_execution": tx_execution,
            "reward_maturity": {**reward_maturity, **matured_rewards},
            "competitive_round": competitive_round,
            "difficulty": difficulty,
            "protocol_params_id": params["id"],
            "protocol_version": params["protocol_version"],
            "total_block_ms": total_block_ms,
            "validation_mode": params["validation_mode"],
        }
        block["consensus_proposal"] = record_local_block_proposal(connection, block, proposer_node_id=miner_id)

    return {
        "accepted": True,
        "status": "accepted",
        "message": "block accepted",
        "block": block,
        "validation": validation_payload,
    }


def _latest_chain_tip_in_connection(connection: Any) -> dict[str, Any]:
    latest_block = connection.execute(
        "SELECT height, block_hash FROM blocks ORDER BY height DESC LIMIT 1"
    ).fetchone()
    if latest_block is not None:
        return {"height": int(latest_block["height"]), "block_hash": latest_block["block_hash"]}
    snapshot_base = active_snapshot_base_in_connection(connection)
    if snapshot_base is not None and snapshot_base.get("state_applied"):
        return {"height": int(snapshot_base["height"]), "block_hash": snapshot_base["block_hash"]}
    return {"height": 0, "block_hash": GENESIS_HASH}


def commit_task(
    task_id: str,
    miner_id: str,
    result_hash: str,
    merkle_root: str,
    signature: str,
    signed_at: str,
    compute_ms: int | None = None,
    tx_merkle_root: str = "",
    mempool_snapshot_id: str | None = None,
    selected_tx_hashes_hash: str | None = None,
    tx_count: int = 0,
    tx_fee_total_units: int = 0,
) -> dict[str, Any]:
    started = now_perf()
    with get_connection() as connection:
        _expire_assigned_tasks(connection)
        task = row_to_dict(
            connection.execute(
                "SELECT * FROM tasks WHERE task_id = ? AND miner_id = ?",
                (task_id, miner_id),
            ).fetchone()
        )
        miner = row_to_dict(connection.execute("SELECT * FROM miners WHERE miner_id = ?", (miner_id,)).fetchone())

        if task is None:
            return _commit_rejected("task not found for miner")
        if miner is None:
            return _commit_rejected("miner not found")
        if task["status"] == "expired" and MINING_TASK_MODE == COMPETITIVE_ROUND_ASSIGNMENT_MODE:
            revived_task = _reactivate_expired_competitive_task(
                connection,
                task,
                None,
                _protocol_params_for_task(connection, task),
            )
            if revived_task is not None:
                task = revived_task
        if task["status"] == "committed":
            existing = row_to_dict(
                connection.execute(
                    "SELECT * FROM commitments WHERE task_id = ? AND miner_id = ?",
                    (task_id, miner_id),
                ).fetchone()
            )
            if existing is not None:
                if result_hash != existing["result_hash"] or merkle_root != existing["merkle_root"]:
                    return _commit_rejected("commitment mismatch")
                expected_root = str(existing.get("tx_merkle_root") or "")
                expected_snapshot_id = str(existing.get("mempool_snapshot_id") or "")
                expected_hash = str(existing.get("selected_tx_hashes_hash") or "")
                expected_count = int(existing.get("tx_count") or 0)
                expected_fee_units = int(existing.get("tx_fee_total_units") or 0)
                if (
                    (tx_merkle_root or "") != expected_root
                    or (mempool_snapshot_id or "") != expected_snapshot_id
                    or (selected_tx_hashes_hash or "") != expected_hash
                    or int(tx_count or 0) != expected_count
                    or int(tx_fee_total_units or 0) != expected_fee_units
                ):
                    return _commit_rejected("invalid_tx_commitment")
                payload = build_commit_signature_payload(
                    task_id=task_id,
                    miner_id=miner_id,
                    range_start=task["range_start"],
                    range_end=task["range_end"],
                    algorithm=task["algorithm"],
                    result_hash=result_hash,
                    merkle_root=merkle_root,
                    signed_at=signed_at,
                    tx_merkle_root=expected_root,
                    mempool_snapshot_id=expected_snapshot_id,
                    selected_tx_hashes_hash=expected_hash,
                    tx_count=expected_count,
                    tx_fee_total_units=expected_fee_units,
                    chain_id=CHAIN_ID,
                    network_id=NETWORK_ID,
                )
                try:
                    signature_valid = verify_payload_signature(miner["public_key"], payload, signature)
                except (RuntimeError, ValueError):
                    signature_valid = False
                if not signature_valid and expected_count == 0:
                    legacy_payload = build_commit_signature_payload(
                        task_id=task_id,
                        miner_id=miner_id,
                        range_start=task["range_start"],
                        range_end=task["range_end"],
                        algorithm=task["algorithm"],
                        result_hash=result_hash,
                        merkle_root=merkle_root,
                        signed_at=signed_at,
                    )
                    try:
                        signature_valid = verify_payload_signature(miner["public_key"], legacy_payload, signature)
                    except (RuntimeError, ValueError):
                        signature_valid = False
                if not signature_valid:
                    return _commit_rejected("invalid miner signature")
                return {
                    "accepted": True,
                    "status": "committed",
                    "message": "task already committed; reveal requested samples",
                    "challenge_seed": existing["challenge_seed"],
                    "samples": json.loads(existing["samples"]),
                }
            return _commit_rejected("commitment not found")
        if task["status"] == "stale":
            _record_submission(
                connection,
                task_id,
                miner_id,
                result_hash,
                "",
                signature,
                False,
                task.get("stale_reason") or "competitive round closed",
            )
            return _commit_stale(task.get("stale_reason") or "competitive round closed")
        if task["status"] != "assigned":
            return _commit_rejected(f"task is not active: {task['status']}")
        params = _protocol_params_for_task(connection, task)
        if MINING_TASK_MODE == COMPETITIVE_ROUND_ASSIGNMENT_MODE and _expire_stale_competitive_task(
            connection,
            task,
            params,
        ):
            _record_submission(connection, task_id, miner_id, result_hash, "", signature, False, "competitive round closed")
            return _commit_stale("competitive round closed")
        snapshot = get_task_tx_snapshot(connection, task_id)
        if snapshot is None:
            return _commit_rejected("tx snapshot not found for task")
        expected_root = str(snapshot["tx_merkle_root"] or "")
        expected_hash = str(snapshot["selected_tx_hashes_hash"] or "")
        expected_snapshot_id = str(snapshot["snapshot_id"] or "")
        expected_count = int(snapshot["tx_count"] or 0)
        expected_fee_units = int(snapshot["tx_fee_total_units"] or 0)
        if (
            (tx_merkle_root or "") != expected_root
            or (mempool_snapshot_id or "") != expected_snapshot_id
            or (selected_tx_hashes_hash or "") != expected_hash
            or int(tx_count or 0) != expected_count
            or int(tx_fee_total_units or 0) != expected_fee_units
        ):
            # Log the exact fields that mismatch to debug the invalid_tx_commitment error
            mismatches = []
            if (tx_merkle_root or "") != expected_root: mismatches.append("tx_merkle_root")
            if (mempool_snapshot_id or "") != expected_snapshot_id: mismatches.append("mempool_snapshot_id")
            if (selected_tx_hashes_hash or "") != expected_hash: mismatches.append("selected_tx_hashes_hash")
            if int(tx_count or 0) != expected_count: mismatches.append("tx_count")
            if int(tx_fee_total_units or 0) != expected_fee_units: mismatches.append("tx_fee_total_units")

            logger.warning(
                "invalid_tx_commitment details in commit_task: task_id=%s miner_id=%s mismatches=%s\n"
                "expected: root=%s, snapshot_id=%s, hashes_hash=%s, count=%s, fee_units=%s\n"
                "received: root=%s, snapshot_id=%s, hashes_hash=%s, count=%s, fee_units=%s",
                task_id, miner_id, mismatches,
                expected_root, expected_snapshot_id, expected_hash, expected_count, expected_fee_units,
                tx_merkle_root, mempool_snapshot_id, selected_tx_hashes_hash, tx_count, tx_fee_total_units
            )
            return _commit_rejected("invalid_tx_commitment")

        expires_at = parse_iso(task["expires_at"])
        if expires_at is not None and expires_at <= utc_now_dt():
            connection.execute("UPDATE tasks SET status = 'expired' WHERE task_id = ?", (task_id,))
            return _commit_rejected("task expired")

        duplicate = connection.execute(
            "SELECT 1 FROM blocks WHERE result_hash = ? OR task_id = ?",
            (result_hash, task_id),
        ).fetchone()
        if duplicate is not None:
            return _commit_rejected("duplicate result or task")

        payload = build_commit_signature_payload(
            task_id=task_id,
            miner_id=miner_id,
            range_start=task["range_start"],
            range_end=task["range_end"],
            algorithm=task["algorithm"],
            result_hash=result_hash,
            merkle_root=merkle_root,
            signed_at=signed_at,
            tx_merkle_root=expected_root,
            mempool_snapshot_id=expected_snapshot_id,
            selected_tx_hashes_hash=expected_hash,
            tx_count=expected_count,
            tx_fee_total_units=expected_fee_units,
            chain_id=CHAIN_ID,
            network_id=NETWORK_ID,
        )
        try:
            signature_valid = verify_payload_signature(miner["public_key"], payload, signature)
        except (RuntimeError, ValueError):
            signature_valid = False
        if not signature_valid and expected_count == 0:
            legacy_payload = build_commit_signature_payload(
                task_id=task_id,
                miner_id=miner_id,
                range_start=task["range_start"],
                range_end=task["range_end"],
                algorithm=task["algorithm"],
                result_hash=result_hash,
                merkle_root=merkle_root,
                signed_at=signed_at,
            )
            try:
                signature_valid = verify_payload_signature(miner["public_key"], legacy_payload, signature)
            except (RuntimeError, ValueError):
                signature_valid = False
        if not signature_valid:
            return _commit_rejected("invalid miner signature")

        previous_hash = _latest_block_hash(connection)
        challenge_seed = sha256_text(
            canonical_json(
                {
                    "merkle_root": merkle_root,
                    "previous_hash": previous_hash,
                    "result_hash": result_hash,
                    "task_id": task_id,
                }
            )
        )
        samples = _build_challenge_samples(
            task["range_start"],
            task["range_end"],
            challenge_seed,
            params["sample_count"],
        )

        connection.execute(
            """
            INSERT INTO commitments (
                task_id, miner_id, result_hash, merkle_root, challenge_seed,
                samples, tx_merkle_root, mempool_snapshot_id, selected_tx_hashes_hash,
                tx_count, tx_fee_total_units, signature, signed_at, commit_ms, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                miner_id,
                result_hash,
                merkle_root,
                challenge_seed,
                json.dumps(samples),
                expected_root,
                expected_snapshot_id,
                expected_hash,
                expected_count,
                expected_fee_units,
                signature,
                signed_at,
                elapsed_ms(started),
                utc_now(),
            ),
        )
        if compute_ms is not None:
            connection.execute("UPDATE tasks SET compute_ms = ? WHERE task_id = ?", (compute_ms, task_id))
        connection.execute("UPDATE tasks SET status = 'committed' WHERE task_id = ?", (task_id,))

    return {
        "accepted": True,
        "status": "committed",
        "message": "commit accepted; reveal requested samples",
        "challenge_seed": challenge_seed,
        "samples": samples,
    }


def reveal_task(
    task_id: str,
    miner_id: str,
    revealed_samples: list[dict[str, Any]],
    signature: str,
    signed_at: str,
    tx_merkle_root: str = "",
    mempool_snapshot_id: str | None = None,
    selected_tx_hashes_hash: str | None = None,
    tx_count: int = 0,
    tx_fee_total_units: int = 0,
) -> dict[str, Any]:
    with get_connection() as connection:
        _expire_assigned_tasks(connection)
        task = row_to_dict(
            connection.execute(
                "SELECT * FROM tasks WHERE task_id = ? AND miner_id = ?",
                (task_id, miner_id),
            ).fetchone()
        )
        miner = row_to_dict(connection.execute("SELECT * FROM miners WHERE miner_id = ?", (miner_id,)).fetchone())
        commitment = row_to_dict(connection.execute("SELECT * FROM commitments WHERE task_id = ?", (task_id,)).fetchone())

        if task is None or commitment is None:
            return _reject_in_connection(
                connection,
                "commitment not found",
                task_id,
                miner_id,
                "",
                {},
                PENALTY_INVALID_RESULT,
                signature,
                "",
            )
        if miner is None:
            return _reject_in_connection(
                connection,
                "miner not found",
                task_id,
                miner_id,
                commitment["result_hash"],
                {},
                PENALTY_INVALID_SIGNATURE,
                signature,
                "",
            )
        if task["status"] == "stale":
            return _stale_competitive_response(
                connection,
                task_id,
                miner_id,
                commitment["result_hash"],
                signature,
                "",
                task.get("stale_reason") or "competitive round closed",
            )
        if task["status"] in {"revealed", "accepted", "rejected"}:
            existing_job = row_to_dict(
                connection.execute(
                    "SELECT * FROM validation_jobs WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
            )
            if existing_job is not None:
                try:
                    existing_samples = json.loads(existing_job.get("samples") or "[]")
                except (TypeError, json.JSONDecodeError):
                    existing_samples = revealed_samples
                existing_status = existing_job.get("status")
                response_status = "validation_pending" if existing_status == "pending" else existing_status
                return {
                    "accepted": response_status != "rejected",
                    "status": response_status,
                    "message": "reveal already processed",
                    "block": None,
                    "validation": {
                        "job_id": existing_job["job_id"],
                        "challenge_seed": commitment["challenge_seed"],
                        "merkle_root": commitment["merkle_root"],
                        "samples": existing_samples,
                        "status": existing_status,
                    },
                }
        if task["status"] != "committed":
            return _reject_in_connection(
                connection,
                "task is not committed",
                task_id,
                miner_id,
                commitment["result_hash"],
                {"task_status": task["status"]},
                PENALTY_DUPLICATE,
                signature,
                "",
            )
        params = _protocol_params_for_task(connection, task)
        if MINING_TASK_MODE == COMPETITIVE_ROUND_ASSIGNMENT_MODE and _expire_stale_competitive_task(
            connection,
            task,
            params,
        ):
            return _stale_competitive_response(
                connection,
                task_id,
                miner_id,
                commitment["result_hash"],
                signature,
                "",
                "competitive round closed",
            )
        if MINING_TASK_MODE == COMPETITIVE_ROUND_ASSIGNMENT_MODE:
            pending_candidate = _competitive_round_pending_candidate(connection, task)
            if pending_candidate is not None:
                return {
                    "accepted": False,
                    "status": "competitive_round_waiting",
                    "message": f"competitive round already has pending candidate {pending_candidate['task_id']}",
                    "block": None,
                    "validation": {
                        "pending_task_id": pending_candidate["task_id"],
                        "pending_job_id": pending_candidate["job_id"],
                    },
                }
        snapshot = get_task_tx_snapshot(connection, task_id)
        if snapshot is None:
            return _reject_in_connection(
                connection,
                "tx_snapshot_mismatch",
                task_id,
                miner_id,
                commitment["result_hash"],
                {},
                PENALTY_INVALID_RESULT,
                signature,
                "",
            )
        expected_root = str(commitment.get("tx_merkle_root") if commitment.get("tx_merkle_root") is not None else snapshot["tx_merkle_root"] or "")
        expected_snapshot_id = str(commitment.get("mempool_snapshot_id") if commitment.get("mempool_snapshot_id") is not None else snapshot["snapshot_id"] or "")
        expected_hash = str(commitment.get("selected_tx_hashes_hash") if commitment.get("selected_tx_hashes_hash") is not None else snapshot["selected_tx_hashes_hash"] or "")
        expected_count = int(commitment.get("tx_count") if commitment.get("tx_count") is not None else snapshot["tx_count"] or 0)
        expected_fee_units = int(commitment.get("tx_fee_total_units") if commitment.get("tx_fee_total_units") is not None else snapshot["tx_fee_total_units"] or 0)
        if (
            (tx_merkle_root or "") != expected_root
            or (mempool_snapshot_id or "") != expected_snapshot_id
            or (selected_tx_hashes_hash or "") != expected_hash
            or int(tx_count or 0) != expected_count
            or int(tx_fee_total_units or 0) != expected_fee_units
        ):
            mismatches = []
            if (tx_merkle_root or "") != expected_root: mismatches.append("tx_merkle_root")
            if (mempool_snapshot_id or "") != expected_snapshot_id: mismatches.append("mempool_snapshot_id")
            if (selected_tx_hashes_hash or "") != expected_hash: mismatches.append("selected_tx_hashes_hash")
            if int(tx_count or 0) != expected_count: mismatches.append("tx_count")
            if int(tx_fee_total_units or 0) != expected_fee_units: mismatches.append("tx_fee_total_units")

            logger.warning(
                "invalid_tx_commitment details in reveal_task: task_id=%s miner_id=%s mismatches=%s\n"
                "expected: root=%s, snapshot_id=%s, hashes_hash=%s, count=%s, fee_units=%s\n"
                "received: root=%s, snapshot_id=%s, hashes_hash=%s, count=%s, fee_units=%s",
                task_id, miner_id, mismatches,
                expected_root, expected_snapshot_id, expected_hash, expected_count, expected_fee_units,
                tx_merkle_root, mempool_snapshot_id, selected_tx_hashes_hash, tx_count, tx_fee_total_units
            )
            return _reject_in_connection(
                connection,
                "invalid_tx_commitment",
                task_id,
                miner_id,
                commitment["result_hash"],
                {"expected_tx_merkle_root": expected_root, "received_tx_merkle_root": tx_merkle_root},
                PENALTY_INVALID_RESULT,
                signature,
                "",
            )

        payload = build_reveal_signature_payload(
            task_id=task_id,
            miner_id=miner_id,
            merkle_root=commitment["merkle_root"],
            challenge_seed=commitment["challenge_seed"],
            signed_at=signed_at,
            tx_merkle_root=expected_root,
            mempool_snapshot_id=expected_snapshot_id,
            selected_tx_hashes_hash=expected_hash,
        )
        try:
            signature_valid = verify_payload_signature(miner["public_key"], payload, signature)
        except (RuntimeError, ValueError):
            signature_valid = False
        if not signature_valid and expected_count == 0:
            legacy_payload = build_reveal_signature_payload(
                task_id=task_id,
                miner_id=miner_id,
                merkle_root=commitment["merkle_root"],
                challenge_seed=commitment["challenge_seed"],
                signed_at=signed_at,
            )
            try:
                signature_valid = verify_payload_signature(miner["public_key"], legacy_payload, signature)
            except (RuntimeError, ValueError):
                signature_valid = False
        if not signature_valid:
            connection.execute(
                "UPDATE tasks SET status = 'rejected', submitted_at = ? WHERE task_id = ?",
                (utc_now(), task_id),
            )
            return _reject_in_connection(
                connection,
                "invalid reveal signature",
                task_id,
                miner_id,
                commitment["result_hash"],
                {"signature_present": bool(signature)},
                PENALTY_INVALID_SIGNATURE,
                signature,
                "",
            )

        requested_samples = json.loads(commitment["samples"])
        requested_positions = {sample["position"] for sample in requested_samples}
        revealed_positions = {sample["position"] for sample in revealed_samples}
        if requested_positions != revealed_positions:
            return _reject_in_connection(
                connection,
                "revealed samples do not match requested positions",
                task_id,
                miner_id,
                commitment["result_hash"],
                {"requested_positions": sorted(requested_positions), "revealed_positions": sorted(revealed_positions)},
                PENALTY_INVALID_RESULT,
                signature,
                "",
            )

        existing_job = row_to_dict(connection.execute("SELECT * FROM validation_jobs WHERE task_id = ?", (task_id,)).fetchone())
        if existing_job is None:
            job_id = f"job_{uuid.uuid4().hex[:16]}"
            job_created_at = utc_now()
            snapshot_transactions = load_snapshot_transactions(connection, task_id)
            tx_hashes = snapshot["selected_tx_hashes"]
            connection.execute(
                """
                INSERT INTO validation_jobs (
                    job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
                    samples, tx_merkle_root, mempool_snapshot_id, selected_tx_hashes_hash,
                    tx_count, tx_fee_total_units, tx_hashes_json, transactions_json,
                    status, job_created_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    job_id,
                    task_id,
                    miner_id,
                    commitment["result_hash"],
                    commitment["merkle_root"],
                    commitment["challenge_seed"],
                    json.dumps(revealed_samples),
                    expected_root,
                    expected_snapshot_id,
                    expected_hash,
                    expected_count,
                    expected_fee_units,
                    json.dumps(tx_hashes, sort_keys=True),
                    json.dumps(snapshot_transactions, sort_keys=True),
                    job_created_at,
                    job_created_at,
                ),
            )
            logger.info(
                "validation job created task_id=%s job_id=%s tx_count=%s tx_merkle_root=%s mempool_snapshot_id=%s tx_fee_total_units=%s",
                task_id,
                job_id,
                expected_count,
                expected_root,
                expected_snapshot_id,
                expected_fee_units,
            )
            validation_expires_at = iso_at(_task_expiration_seconds_for_position(params, task["range_end"]))
            connection.execute(
                "UPDATE tasks SET status = 'revealed', expires_at = ? WHERE task_id = ?",
                (validation_expires_at, task_id),
            )
        else:
            job_id = existing_job["job_id"]

    return {
        "accepted": True,
        "status": "validation_pending",
        "message": "reveal accepted; waiting for external validator",
        "block": None,
        "validation": {
            "job_id": job_id,
            "challenge_seed": commitment["challenge_seed"],
            "merkle_root": commitment["merkle_root"],
            "samples": revealed_samples,
        },
    }


def get_validation_job(
    validator_id: str,
    *,
    public_key: str | None = None,
    name: str | None = None,
    reward_address: str | None = None,
) -> dict[str, Any] | None:
    _ensure_replay_can_accept_work()
    refresh_participant_liveness()
    with get_connection() as connection:
        _release_timed_out_validation_assignments(connection)
        validator = row_to_dict(connection.execute("SELECT * FROM validators WHERE validator_id = ?", (validator_id,)).fetchone())
        if validator is None and public_key:
            validator = _restore_validator_identity(connection, validator_id, public_key, name, reward_address)
        elif validator is not None and reward_address:
            _update_validator_reward_address(connection, validator_id, reward_address)
            validator = row_to_dict(connection.execute("SELECT * FROM validators WHERE validator_id = ?", (validator_id,)).fetchone())
        if validator is None:
            return None
        if validator["is_banned"]:
            raise MiningError(403, "validator is banned")
        if not bool(validator.get("enabled", 1)):
            raise MiningError(403, validator.get("reason_if_not_eligible") or "validator is disabled")
        cooldown_until = parse_iso(validator["cooldown_until"])
        if cooldown_until is not None and cooldown_until > utc_now_dt():
            raise MiningError(429, f"validator is in cooldown until {validator['cooldown_until']}")
        if _validator_eligibility_stake(validator) < MIN_VALIDATOR_STAKE:
            raise MiningError(403, _validator_min_stake_reason())
        if float(validator["trust_score"]) < VALIDATOR_MIN_TRUST_SCORE:
            raise MiningError(403, "validator trust score is below the minimum required")
        if not str(validator.get("node_id") or "").strip() or not str(validator.get("advertised_address") or "").strip():
            raise MiningError(403, "validator node heartbeat required")
        connection.execute(
            """
            UPDATE validators
            SET last_seen_at = ?
            WHERE validator_id = ?
            """,
            (utc_now(), validator_id),
        )
        validator = row_to_dict(connection.execute("SELECT * FROM validators WHERE validator_id = ?", (validator_id,)).fetchone())
        if str(validator.get("online_status") or "") != "online":
            raise MiningError(403, validator.get("reason_if_not_eligible") or "validator node heartbeat required")
        if str(validator.get("sync_status") or "unknown") == "out_of_sync":
            raise MiningError(403, "validator is out_of_sync")
        if str(validator.get("protocol_version") or PROTOCOL_VERSION) != PROTOCOL_VERSION:
            raise MiningError(403, "validator protocol version is incompatible")

        _promote_tasks_for_pending_validation_jobs(connection)
        candidate_rows = connection.execute(
            """
            SELECT validation_jobs.*, tasks.range_start, tasks.range_end, tasks.algorithm
                 , tasks.protocol_params_id
                 , (
                    SELECT COUNT(*)
                    FROM validation_votes
                    WHERE validation_votes.job_id = validation_jobs.job_id
                    AND validation_votes.approved = 1
                 ) AS approval_count
                 , (
                    SELECT COUNT(*)
                    FROM validation_votes
                    WHERE validation_votes.job_id = validation_jobs.job_id
                 ) AS vote_count
            FROM validation_jobs
            JOIN tasks ON tasks.task_id = validation_jobs.task_id
            WHERE validation_jobs.status = 'pending'
            AND tasks.status = 'revealed'
            AND (
                COALESCE(tasks.assignment_mode, '') != ?
                OR NOT EXISTS (
                    SELECT 1
                    FROM validation_jobs AS earlier_jobs
                    JOIN tasks AS earlier_tasks ON earlier_tasks.task_id = earlier_jobs.task_id
                    WHERE earlier_jobs.status = 'pending'
                    AND earlier_tasks.status = 'revealed'
                    AND earlier_tasks.assignment_mode = ?
                    AND earlier_tasks.assignment_seed = tasks.assignment_seed
                    AND (
                        earlier_jobs.created_at < validation_jobs.created_at
                        OR (
                            earlier_jobs.created_at = validation_jobs.created_at
                            AND earlier_jobs.job_id < validation_jobs.job_id
                        )
                    )
                )
            )
            AND NOT EXISTS (
                SELECT 1
                FROM validation_votes
                WHERE validation_votes.job_id = validation_jobs.job_id
                AND validation_votes.validator_id = ?
            )
            ORDER BY approval_count DESC, vote_count DESC, validation_jobs.created_at ASC
            LIMIT 20
            """,
            (COMPETITIVE_ROUND_ASSIGNMENT_MODE, COMPETITIVE_ROUND_ASSIGNMENT_MODE, validator_id),
        ).fetchall()

        job = None
        selection_meta = None
        for candidate_row in candidate_rows:
            candidate = row_to_dict(candidate_row)
            params = _protocol_params_for_task(connection, candidate)
            job = candidate
            selection_meta = _validator_selection_metadata_for_job(connection, candidate, params, validator_id)
            break

        if job is None:
            return None
        assigned_at = utc_now()
        connection.execute(
            """
            UPDATE validation_jobs
            SET assigned_validator_id = ?, assigned_at = ?, blocking_reason = NULL
            WHERE job_id = ?
            """,
            (validator_id, assigned_at, job["job_id"]),
        )
        job["assigned_validator_id"] = validator_id
        job["assigned_at"] = assigned_at
        job["selection_score"] = selection_meta["selection_score"] if selection_meta else None
        job["selection_rank"] = selection_meta["selection_rank"] if selection_meta else None
        counts = _validation_vote_counts(connection, job["job_id"])
        params = _protocol_params_for_task(connection, job)
        required = _effective_required_validator_approvals(connection, params)
        logger.info(
            "validation job visible to validator job_id=%s task_id=%s validator_id=%s approvals=%s/%s",
            job["job_id"],
            job["task_id"],
            validator_id,
            counts["approvals"],
            required,
        )

    job["samples"] = json.loads(job["samples"])
    job["selected_tx_hashes"] = json.loads(job.get("tx_hashes_json") or "[]")
    job["transactions"] = json.loads(job.get("transactions_json") or "[]")
    job["approvals"] = counts["approvals"]
    job["rejections"] = counts["rejections"]
    job["required_approvals"] = required
    job["required_rejections"] = required
    return job


def _release_timed_out_validation_assignments(connection: Any) -> int:
    threshold = iso_ago(VALIDATION_JOB_ASSIGNMENT_TIMEOUT_SECONDS)
    cursor = connection.execute(
        """
        UPDATE validation_jobs
        SET assigned_validator_id = NULL,
            assigned_at = NULL,
            assignment_failures = assignment_failures + 1,
            blocking_reason = 'assigned_validator_timeout'
        WHERE status = 'pending'
          AND assigned_validator_id IS NOT NULL
          AND assigned_at IS NOT NULL
          AND assigned_at < ?
        """,
        (threshold,),
    )
    return max(0, cursor.rowcount)


def _validator_selection_metadata_for_job(
    connection: Any,
    job: dict[str, Any],
    params: dict[str, Any],
    validator_id: str,
) -> dict[str, Any] | None:
    eligible = _eligible_validator_rows(connection)
    required = _effective_required_validator_approvals(connection, params)
    pool_size = min(len(eligible), max(required, required * VALIDATOR_SELECTION_POOL_MULTIPLIER))
    scored: list[dict[str, Any]] = []
    for validator in eligible:
        metrics = _validator_selection_metrics(connection, validator)
        jitter = _selection_jitter(job["challenge_seed"], validator["validator_id"])
        scored.append(
            {
                "validator_id": validator["validator_id"],
                "selection_score": metrics["selection_score"],
                "selection_weight": round(metrics["selection_score"] + jitter, 8),
                "recent_validation_votes": metrics["recent_validation_votes"],
                "availability_score": metrics["availability_score"],
            }
        )
    scored.sort(key=lambda item: (-item["selection_weight"], item["validator_id"]))
    for index, item in enumerate(scored, start=1):
        if item["validator_id"] == validator_id:
            item["selection_rank"] = index
            item["selection_pool_size"] = pool_size
            return item
    return None


def _refresh_validation_job_timing(
    connection: Any,
    *,
    job_id: str,
    counts: dict[str, int],
    required: int,
    received_at: str,
) -> None:
    job = row_to_dict(connection.execute("SELECT * FROM validation_jobs WHERE job_id = ?", (job_id,)).fetchone())
    if job is None:
        return
    created_at = job.get("job_created_at") or job.get("created_at")
    total_votes = counts["approvals"] + counts["rejections"]

    updates: dict[str, Any] = {}
    if total_votes >= 1 and not job.get("first_vote_at"):
        first_vote = connection.execute(
            """
            SELECT created_at
            FROM validation_votes
            WHERE job_id = ?
            ORDER BY created_at ASC, id ASC
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()
        first_vote_at = first_vote["created_at"] if first_vote else received_at
        updates["first_vote_at"] = first_vote_at
        updates["waiting_for_first_vote_ms"] = _elapsed_iso_ms(created_at, first_vote_at)

    if total_votes >= 2 and not job.get("second_vote_at"):
        second_vote = connection.execute(
            """
            SELECT created_at
            FROM validation_votes
            WHERE job_id = ?
            ORDER BY created_at ASC, id ASC
            LIMIT 1 OFFSET 1
            """,
            (job_id,),
        ).fetchone()
        second_vote_at = second_vote["created_at"] if second_vote else received_at
        updates["second_vote_at"] = second_vote_at

    quorum_reached = counts["approvals"] >= required or counts["rejections"] >= required
    if quorum_reached and not job.get("quorum_reached_at"):
        updates["quorum_reached_at"] = received_at
        updates["waiting_for_quorum_ms"] = _elapsed_iso_ms(created_at, received_at)

    if not updates:
        return
    assignments = ", ".join(f"{column} = ?" for column in updates)
    connection.execute(
        f"UPDATE validation_jobs SET {assignments} WHERE job_id = ?",
        (*updates.values(), job_id),
    )


def _mark_validation_job_finalized(
    connection: Any,
    *,
    job_id: str,
    finalized_at: str,
) -> None:
    job = row_to_dict(connection.execute("SELECT * FROM validation_jobs WHERE job_id = ?", (job_id,)).fetchone())
    quorum_reached_at = (job or {}).get("quorum_reached_at") or finalized_at
    connection.execute(
        """
        UPDATE validation_jobs
        SET finalized_at = ?,
            finalization_ms = ?
        WHERE job_id = ?
        """,
        (finalized_at, _elapsed_iso_ms(quorum_reached_at, finalized_at), job_id),
    )


def _block_timestamp_for_validation_job(connection: Any, validation_job_id: str | None) -> str:
    if not validation_job_id:
        return utc_now()
    row = connection.execute(
        "SELECT job_created_at, created_at FROM validation_jobs WHERE job_id = ?",
        (validation_job_id,),
    ).fetchone()
    if row is None:
        return utc_now()
    for column in ("job_created_at", "created_at"):
        value = row[column]
        if not value:
            continue
        try:
            parsed = parse_iso(str(value))
        except (TypeError, ValueError):
            continue
        if parsed is not None:
            return str(value)
    return utc_now()


def submit_validation_result(
    job_id: str,
    validator_id: str,
    approved: bool,
    reason: str,
    signature: str,
    signed_at: str,
) -> dict[str, Any]:
    started = now_perf()
    with get_connection() as connection:
        job = row_to_dict(
            connection.execute(
                """
                SELECT validation_jobs.*, tasks.range_start, tasks.range_end, tasks.algorithm
                     , tasks.protocol_params_id
                FROM validation_jobs
                JOIN tasks ON tasks.task_id = validation_jobs.task_id
                WHERE validation_jobs.job_id = ?
                """,
                (job_id,),
            ).fetchone()
        )
        validator = row_to_dict(connection.execute("SELECT * FROM validators WHERE validator_id = ?", (validator_id,)).fetchone())
        if job is None:
            raise MiningError(404, "validation job not found")
        if validator is None:
            raise MiningError(404, "validator not found")
        if validator["is_banned"]:
            raise MiningError(403, "validator is banned")
        cooldown_until = parse_iso(validator["cooldown_until"])
        if cooldown_until is not None and cooldown_until > utc_now_dt():
            raise MiningError(429, f"validator is in cooldown until {validator['cooldown_until']}")
        if job["status"] != "pending":
            counts = _validation_vote_counts(connection, job_id)
            params = _protocol_params_for_task(connection, job)
            required = _effective_required_validator_approvals(connection, params)
            return {
                "accepted": False,
                "status": job["status"],
                "message": "validation job already completed",
                "block": None,
                "approvals": counts["approvals"],
                "rejections": counts["rejections"],
                "required_approvals": required,
                "required_rejections": required,
            }
        def existing_vote_response() -> dict[str, Any]:
            counts = _validation_vote_counts(connection, job_id)
            params = _protocol_params_for_task(connection, job)
            required = _effective_required_validator_approvals(connection, params)
            finalization = _finalize_pending_validation_job_from_existing_votes(
                connection,
                job=job,
                counts=counts,
                required=required,
            )
            if finalization is not None and finalization.get("status") == "approved":
                return {
                    "accepted": True,
                    "status": "approved",
                    "message": "block accepted by validator quorum",
                    "block": finalization.get("block"),
                    "finality_certificate": finalization.get("finality_certificate"),
                    "approvals": counts["approvals"],
                    "rejections": counts["rejections"],
                    "required_approvals": required,
                    "required_rejections": required,
                }
            return {
                "accepted": False,
                "status": "already_voted",
                "message": (
                    str(finalization.get("message"))
                    if finalization is not None and finalization.get("message")
                    else "validator already submitted a vote for this job"
                ),
                "block": None,
                "approvals": counts["approvals"],
                "rejections": counts["rejections"],
                "required_approvals": required,
                "required_rejections": required,
            }

        existing_vote = connection.execute(
            "SELECT 1 FROM validation_votes WHERE job_id = ? AND validator_id = ?",
            (job_id, validator_id),
        ).fetchone()
        if existing_vote is not None:
            return existing_vote_response()

        payload = build_validation_result_signature_payload(
            job_id=job_id,
            validator_id=validator_id,
            task_id=job["task_id"],
            approved=approved,
            reason=reason,
            signed_at=signed_at,
        )
        try:
            signature_valid = verify_payload_signature(validator["public_key"], payload, signature)
        except (RuntimeError, ValueError):
            signature_valid = False
        if not signature_valid:
            _apply_validator_penalty(connection, validator_id, "invalid validator signature")
            connection.commit()
            raise MiningError(400, "invalid validator signature")

        task = row_to_dict(connection.execute("SELECT * FROM tasks WHERE task_id = ?", (job["task_id"],)).fetchone())
        samples = json.loads(job["samples"])
        validation_ms = elapsed_ms(started)
        received_at = utc_now()
        submit_result_latency_ms = _elapsed_iso_ms(signed_at, received_at)
        params = _protocol_params_for_task(connection, task)
        try:
            connection.execute(
                """
                INSERT INTO validation_votes (
                    job_id, task_id, validator_id, approved, reason, signature,
                    signed_at, validation_ms, submit_result_latency_ms, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    job["task_id"],
                    validator_id,
                    int(approved),
                    reason,
                    signature,
                    signed_at,
                    validation_ms,
                    submit_result_latency_ms,
                    received_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            if "validation_votes.job_id, validation_votes.validator_id" in str(exc):
                logger.info(
                    "duplicate validation vote ignored job_id=%s validator_id=%s",
                    job_id,
                    validator_id,
                )
                return existing_vote_response()
            raise
        _record_validator_completed_vote(connection, validator_id, approved, validation_ms)
        counts = _validation_vote_counts(connection, job_id)
        required = _effective_required_validator_approvals(connection, params)
        _refresh_validation_job_timing(
            connection,
            job_id=job_id,
            counts=counts,
            required=required,
            received_at=received_at,
        )
        logger.info(
            "validation vote received job_id=%s task_id=%s validator_id=%s approved=%s approvals=%s/%s rejections=%s/%s",
            job_id,
            job["task_id"],
            validator_id,
            approved,
            counts["approvals"],
            required,
            counts["rejections"],
            required,
        )

        if approved and counts["approvals"] >= required:
            quorum_at = utc_now()
            logger.info(
                "validation quorum reached job_id=%s task_id=%s approvals=%s/%s",
                job_id,
                job["task_id"],
                counts["approvals"],
                required,
            )
            duplicate_block = connection.execute(
                "SELECT height FROM blocks WHERE result_hash = ? OR task_id = ?",
                (job["result_hash"], job["task_id"]),
            ).fetchone()
            if duplicate_block is not None:
                finalized_at = utc_now()
                reason_text = f"duplicate competitive result already accepted at block {duplicate_block['height']}"
                connection.execute(
                    "UPDATE tasks SET status = 'rejected', submitted_at = ? WHERE task_id = ?",
                    (finalized_at, job["task_id"]),
                )
                release_selected_transactions(connection, job["task_id"], reason_text, finalized_at)
                _mark_validation_job_finalized(connection, job_id=job_id, finalized_at=finalized_at)
                connection.execute(
                    """
                    UPDATE validation_jobs
                    SET status = 'rejected', assigned_validator_id = ?, result_reason = ?,
                        validator_signature = ?, validation_ms = ?, completed_at = ?
                    WHERE job_id = ?
                    """,
                    (validator_id, reason_text, signature, validation_ms, finalized_at, job_id),
                )
                return {
                    "accepted": False,
                    "status": "rejected",
                    "message": reason_text,
                    "block": None,
                    "approvals": counts["approvals"],
                    "rejections": counts["rejections"],
                    "required_approvals": required,
                    "required_rejections": required,
                }
            savepoint_name = "validation_block_finalization"
            connection.execute(f"SAVEPOINT {savepoint_name}")
            try:
                block = _accept_block_in_connection(
                    connection=connection,
                    task=task,
                    miner_id=job["miner_id"],
                    result_hash=job["result_hash"],
                    merkle_root=job["merkle_root"],
                    samples=samples,
                    signature=signature,
                    submission_reason=f"external validation approved by {validator_id}",
                    validation_ms=validation_ms,
                    params=params,
                    validation_job_id=job_id,
                )
            except ValidationFinalizationDeferred as exc:
                connection.execute(f"ROLLBACK TO {savepoint_name}")
                connection.execute(f"RELEASE {savepoint_name}")
                logger.warning(
                    "validation finalization deferred job_id=%s task_id=%s reason=%s",
                    job_id,
                    job["task_id"],
                    exc,
                )
                return {
                    "accepted": True,
                    "status": "validation_pending",
                    "message": str(exc),
                    "block": None,
                    "approvals": counts["approvals"],
                    "rejections": counts["rejections"],
                    "required_approvals": required,
                    "required_rejections": required,
                }
            except TransactionExecutionError as exc:
                connection.execute(f"ROLLBACK TO {savepoint_name}")
                connection.execute(f"RELEASE {savepoint_name}")
                finalized_at = utc_now()
                raw_reason = str(exc)
                is_competitive_stale = raw_reason.startswith("competitive round")
                reason_text = raw_reason if is_competitive_stale else f"transaction finalization failed: {exc}"
                logger.error(
                    "validation finalization failed job_id=%s task_id=%s reason=%s",
                    job_id,
                    job["task_id"],
                    reason_text,
                )
                current_task = row_to_dict(
                    connection.execute("SELECT status FROM tasks WHERE task_id = ?", (job["task_id"],)).fetchone()
                )
                if is_competitive_stale:
                    _mark_competitive_task_stale(connection, job["task_id"], reason_text, finalized_at)
                elif current_task is None or current_task.get("status") != "stale":
                    connection.execute(
                        "UPDATE tasks SET status = 'rejected', submitted_at = ? WHERE task_id = ?",
                        (finalized_at, job["task_id"]),
                    )
                    release_selected_transactions(connection, job["task_id"], reason_text, finalized_at)
                _mark_validation_job_finalized(connection, job_id=job_id, finalized_at=finalized_at)
                connection.execute(
                    """
                    UPDATE validation_jobs
                    SET status = 'rejected', assigned_validator_id = ?, result_reason = ?,
                        validator_signature = ?, validation_ms = ?, completed_at = ?
                    WHERE job_id = ?
                    """,
                    (validator_id, reason_text, signature, validation_ms, finalized_at, job_id),
                )
                return {
                    "accepted": False,
                    "status": "stale" if is_competitive_stale else "rejected",
                    "message": reason_text,
                    "block": None,
                    "approvals": counts["approvals"],
                    "rejections": counts["rejections"],
                    "required_approvals": required,
                    "required_rejections": required,
                }
            connection.execute(f"RELEASE {savepoint_name}")
            finalized_at = utc_now()
            _mark_validation_job_finalized(connection, job_id=job_id, finalized_at=finalized_at)
            connection.execute(
                """
                UPDATE validation_jobs
                SET status = 'approved', assigned_validator_id = ?, result_reason = ?,
                    validator_signature = ?, validation_ms = ?, completed_at = ?
                WHERE job_id = ?
                """,
                (validator_id, reason, signature, validation_ms, finalized_at, job_id),
            )
            finality_certificate = _create_finality_certificate(
                connection,
                block=block,
                job_id=job_id,
                required_approvals=required,
                created_at=finalized_at,
            )
            logger.info(
                "validation task finalized job_id=%s task_id=%s block_height=%s finalization_ms=%s",
                job_id,
                job["task_id"],
                block.get("height") if isinstance(block, dict) else None,
                _elapsed_iso_ms(quorum_at, finalized_at),
            )
            return {
                "accepted": True,
                "status": "approved",
                "message": "block accepted by validator quorum",
                "block": block,
                "finality_certificate": finality_certificate,
                "approvals": counts["approvals"],
                "rejections": counts["rejections"],
                "required_approvals": required,
                "required_rejections": required,
            }

        if not approved and counts["rejections"] >= required:
            finalized_at = utc_now()
            connection.execute(
                "UPDATE tasks SET status = 'rejected', submitted_at = ? WHERE task_id = ?",
                (finalized_at, job["task_id"]),
            )
            _mark_validation_job_finalized(connection, job_id=job_id, finalized_at=finalized_at)
            connection.execute(
                """
                UPDATE validation_jobs
                SET status = 'rejected', assigned_validator_id = ?, result_reason = ?,
                    validator_signature = ?, validation_ms = ?, completed_at = ?
                WHERE job_id = ?
                """,
                (validator_id, reason, signature, validation_ms, finalized_at, job_id),
            )
            _apply_penalty(connection, job["miner_id"], job["task_id"], PENALTY_INVALID_RESULT, reason)
            return {
                "accepted": True,
                "status": "rejected",
                "message": "validation rejected task by validator quorum",
                "block": None,
                "approvals": counts["approvals"],
                "rejections": counts["rejections"],
                "required_approvals": required,
                "required_rejections": required,
            }

    return {
        "accepted": True,
        "status": "validation_pending",
        "message": "validator vote recorded; waiting for quorum",
        "block": None,
        "approvals": counts["approvals"],
        "rejections": counts["rejections"],
        "required_approvals": required,
        "required_rejections": required,
    }


def _create_finality_certificate(
    connection: Any,
    *,
    block: dict[str, Any],
    job_id: str,
    required_approvals: int,
    created_at: str,
) -> dict[str, Any]:
    existing = row_to_dict(
        connection.execute(
            "SELECT * FROM finality_certificates WHERE block_height = ? OR job_id = ?",
            (int(block["height"]), job_id),
        ).fetchone()
    )
    if existing is not None:
        return _decode_finality_certificate(existing) or {}

    job = row_to_dict(connection.execute("SELECT * FROM validation_jobs WHERE job_id = ?", (job_id,)).fetchone())
    if job is None:
        raise MiningError(500, "validation job not found while creating finality certificate")

    vote_rows = connection.execute(
        """
        SELECT
            validation_votes.job_id,
            validation_votes.task_id,
            validation_votes.validator_id,
            validation_votes.approved,
            validation_votes.reason,
            validation_votes.signature,
            validation_votes.signed_at,
            validation_votes.validation_ms,
            validation_votes.submit_result_latency_ms,
            validation_votes.created_at,
            validators.name AS validator_name,
            validators.public_key AS validator_public_key,
            validators.reward_address AS validator_reward_address
        FROM validation_votes
        LEFT JOIN validators ON validators.validator_id = validation_votes.validator_id
        WHERE validation_votes.job_id = ?
          AND validation_votes.approved = 1
        ORDER BY validation_votes.created_at ASC, validation_votes.validator_id ASC
        """,
        (job_id,),
    ).fetchall()
    votes = [row_to_dict(row) for row in vote_rows]
    approval_count = len(votes)
    if approval_count < int(required_approvals):
        raise MiningError(500, "not enough validator approvals to create finality certificate")

    vote_payloads = []
    for vote in votes:
        signature_payload = build_validation_result_signature_payload(
            job_id=vote["job_id"],
            validator_id=vote["validator_id"],
            task_id=vote["task_id"],
            approved=True,
            reason=vote["reason"],
            signed_at=vote["signed_at"],
        )
        vote_payloads.append(
            {
                "validator_id": vote["validator_id"],
                "validator_name": vote.get("validator_name"),
                "validator_public_key": vote.get("validator_public_key"),
                "validator_reward_address": vote.get("validator_reward_address"),
                "approved": True,
                "reason": vote["reason"],
                "signature": vote["signature"],
                "signed_at": vote["signed_at"],
                "signature_payload": signature_payload,
                "validation_ms": vote.get("validation_ms"),
                "submit_result_latency_ms": vote.get("submit_result_latency_ms"),
                "voted_at": vote["created_at"],
            }
        )

    task_id = job["task_id"]
    protocol_params_id = block.get("protocol_params_id")
    payload = {
        "version": "picoin-finality-v1",
        "network_id": NETWORK_ID,
        "chain_id": CHAIN_ID,
        "protocol_version": block.get("protocol_version") or PROTOCOL_VERSION,
        "protocol_params_id": protocol_params_id,
        "block": {
            "height": int(block["height"]),
            "block_hash": block["block_hash"],
            "previous_hash": block["previous_hash"],
            "state_root": block.get("state_root"),
            "miner_id": block["miner_id"],
            "task_id": task_id,
            "result_hash": block["result_hash"],
            "merkle_root": block.get("merkle_root"),
            "tx_merkle_root": block.get("tx_merkle_root"),
            "tx_count": int(block.get("tx_count") or 0),
            "tx_fee_total_units": int(job.get("tx_fee_total_units") or 0),
            "selected_tx_hashes_hash": job.get("selected_tx_hashes_hash"),
        },
        "validation": {
            "job_id": job_id,
            "challenge_seed": job["challenge_seed"],
            "sample_count": len(json.loads(job.get("samples") or "[]")),
            "required_approvals": int(required_approvals),
            "approval_count": approval_count,
            "status": "approved",
        },
    }
    votes_json = canonical_json(vote_payloads)
    payload_json = canonical_json(payload)
    certificate_hash = sha256_text(canonical_json({"payload": payload, "votes": vote_payloads}))

    connection.execute(
        """
        INSERT INTO finality_certificates (
            block_height, block_hash, task_id, job_id, miner_id, network_id, chain_id,
            protocol_version, protocol_params_id, required_approvals, approval_count,
            certificate_hash, payload_json, votes_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(block["height"]),
            block["block_hash"],
            task_id,
            job_id,
            block["miner_id"],
            NETWORK_ID,
            str(CHAIN_ID),
            payload["protocol_version"],
            protocol_params_id,
            int(required_approvals),
            approval_count,
            certificate_hash,
            payload_json,
            votes_json,
            created_at,
        ),
    )
    stored = row_to_dict(
        connection.execute("SELECT * FROM finality_certificates WHERE block_height = ?", (int(block["height"]),)).fetchone()
    )
    return _decode_finality_certificate(stored) or {}


def get_block_finality_certificate(height: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM finality_certificates WHERE block_height = ?",
            (int(height),),
        ).fetchone()
    return _decode_finality_certificate(row_to_dict(row))


def get_blocks(limit: int | None = None) -> list[dict[str, Any]]:
    with get_connection() as connection:
        if limit is not None:
            rows = connection.execute(
                "SELECT * FROM blocks ORDER BY height DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
            return [_decode_block(row_to_dict(row)) for row in reversed(rows)]
        rows = connection.execute("SELECT * FROM blocks ORDER BY height ASC").fetchall()
        return [_decode_block(row_to_dict(row)) for row in rows]


def get_task_status(task_id: str) -> dict[str, Any] | None:
    task_id = str(task_id or "").strip()
    if not task_id:
        return None

    with get_connection() as connection:
        _expire_assigned_tasks(connection)
        task = row_to_dict(connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone())
        if task is None:
            return None

        block = _decode_block(row_to_dict(connection.execute("SELECT * FROM blocks WHERE task_id = ?", (task_id,)).fetchone()))
        job = row_to_dict(
            connection.execute(
                """
                SELECT *
                FROM validation_jobs
                WHERE task_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()
        )

        validation: dict[str, Any] | None = None
        finality_certificate: dict[str, Any] | None = None
        if block is not None:
            finality_certificate = _decode_finality_certificate(
                row_to_dict(
                    connection.execute(
                        """
                        SELECT *
                        FROM finality_certificates
                        WHERE block_height = ? OR block_hash = ? OR task_id = ?
                        ORDER BY block_height DESC
                        LIMIT 1
                        """,
                        (int(block["height"]), block["block_hash"], task_id),
                    ).fetchone()
                )
            )
            if finality_certificate is not None and (
                job is None or str(job.get("job_id") or "") != str(finality_certificate.get("job_id") or "")
            ):
                certificate_job = row_to_dict(
                    connection.execute(
                        "SELECT * FROM validation_jobs WHERE job_id = ?",
                        (finality_certificate.get("job_id"),),
                    ).fetchone()
                )
                if certificate_job is not None:
                    job = certificate_job
        if job is not None:
            vote_counts = _validation_vote_counts(connection, job["job_id"])
            params = _protocol_params_for_task(connection, {**task, **job})
            required = _effective_required_validator_approvals(connection, params)
            status = job["status"]
            result_reason = job.get("result_reason")
            approvals = vote_counts["approvals"]
            rejections = vote_counts["rejections"]
            total_votes = approvals + rejections
            completed_at = job.get("completed_at")
            finalized_at = job.get("finalized_at")
            if block is not None:
                same_result = str(job.get("result_hash") or "") == str(block.get("result_hash") or "")
                if finality_certificate is not None:
                    status = "approved"
                    result_reason = (
                        job.get("result_reason")
                        if job.get("status") == "approved" and job.get("result_reason")
                        else "block accepted by finality certificate"
                    )
                    approvals = max(approvals, int(finality_certificate.get("approval_count") or 0))
                    required = int(finality_certificate.get("required_approvals") or required)
                    total_votes = max(total_votes, approvals)
                    completed_at = completed_at or finality_certificate.get("created_at")
                    finalized_at = finalized_at or finality_certificate.get("created_at")
                elif same_result and approvals >= required:
                    status = "approved"
                    result_reason = (
                        job.get("result_reason")
                        if job.get("status") == "approved" and job.get("result_reason")
                        else "block accepted with validator quorum"
                    )
            validation = {
                "job_id": job["job_id"],
                "status": status,
                "result_reason": result_reason,
                "approvals": approvals,
                "rejections": rejections,
                "total_votes": total_votes,
                "required_approvals": required,
                "required_rejections": required,
                "created_at": job.get("created_at"),
                "completed_at": completed_at,
                "finalized_at": finalized_at,
            }
        elif finality_certificate is not None:
            validation = {
                "job_id": finality_certificate["job_id"],
                "status": "approved",
                "result_reason": "block accepted by finality certificate",
                "approvals": int(finality_certificate.get("approval_count") or 0),
                "rejections": 0,
                "total_votes": int(finality_certificate.get("approval_count") or 0),
                "required_approvals": int(finality_certificate.get("required_approvals") or 0),
                "required_rejections": int(finality_certificate.get("required_approvals") or 0),
                "created_at": finality_certificate.get("created_at"),
                "completed_at": finality_certificate.get("created_at"),
                "finalized_at": finality_certificate.get("created_at"),
            }

        task_status = str(task.get("status") or "")
        status = task_status
        message = f"task status is {task_status}"
        if block is not None:
            status = "accepted"
            message = "block accepted"
        elif task_status == "revealed" and validation is not None and validation["status"] == "pending":
            status = "validation_pending"
            message = "reveal accepted; waiting for external validator"
        elif validation is not None and validation["status"] in {"approved", "rejected", "expired"}:
            if validation["status"] == "approved":
                status = "accepted" if block is not None else task_status
            elif validation["status"] == "expired":
                status = "expired"
            elif validation["status"] == "rejected" and task_status not in {"stale", "expired"}:
                status = "rejected"
            message = validation.get("result_reason") or message

        return {
            "task_id": task["task_id"],
            "miner_id": task["miner_id"],
            "status": status,
            "task_status": task_status,
            "message": message,
            "assignment_seed": task.get("assignment_seed"),
            "assignment_mode": task.get("assignment_mode"),
            "competitive_round_height": task.get("competitive_round_height"),
            "range_start": task.get("range_start"),
            "range_end": task.get("range_end"),
            "algorithm": task.get("algorithm"),
            "created_at": task.get("created_at"),
            "expires_at": task.get("expires_at"),
            "submitted_at": task.get("submitted_at"),
            "block": block,
            "validation": validation,
        }


def get_block(height: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM blocks WHERE height = ?", (height,)).fetchone()
    return _decode_block(row_to_dict(row))


def get_stats() -> dict[str, Any]:
    return _cached_status_payload("stats", STATUS_ENDPOINT_CACHE_SECONDS, _get_stats_uncached)


def _get_stats_uncached() -> dict[str, Any]:
    with get_connection() as connection:
        _maybe_expire_assigned_tasks(connection)
        miners = connection.execute("SELECT COUNT(*) AS count FROM miners").fetchone()["count"]
        tasks = connection.execute("SELECT COUNT(*) AS count FROM tasks").fetchone()["count"]
        pending = connection.execute("SELECT COUNT(*) AS count FROM tasks WHERE status = 'assigned'").fetchone()["count"]
        expired = connection.execute("SELECT COUNT(*) AS count FROM tasks WHERE status = 'expired'").fetchone()["count"]
        blocks = connection.execute("SELECT COUNT(*) AS count, COALESCE(SUM(reward), 0) AS rewards FROM blocks").fetchone()
        validator_rewards = connection.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS rewards
            FROM ledger_entries
            WHERE entry_type = 'validator_reward'
            """
        ).fetchone()["rewards"]
        audit_rewards = connection.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS rewards
            FROM ledger_entries
            WHERE entry_type = 'retroactive_audit_reward'
            """
        ).fetchone()["rewards"]
        science_rewards = connection.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS rewards
            FROM ledger_entries
            WHERE entry_type = 'science_reserve_accrual'
            """
        ).fetchone()["rewards"]
        treasury_rewards = connection.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS rewards
            FROM ledger_entries
            WHERE entry_type = 'scientific_development_treasury_accrual'
            """
        ).fetchone()["rewards"]
        rejected = connection.execute("SELECT COUNT(*) AS count FROM submissions WHERE accepted = 0").fetchone()["count"]
        latest = connection.execute("SELECT block_hash FROM blocks ORDER BY height DESC LIMIT 1").fetchone()
        supply = _supply_snapshot(connection)
    return {
        "miners": miners,
        "tasks": tasks,
        "pending_tasks": pending,
        "expired_tasks": expired,
        "accepted_blocks": blocks["count"],
        "rejected_submissions": rejected,
        "total_rewards": blocks["rewards"],
        "total_validator_rewards": round(float(validator_rewards), 8),
        "total_audit_rewards": round(float(audit_rewards), 8),
        "total_science_reserve_rewards": round(float(science_rewards), 8),
        "total_scientific_development_rewards": round(float(treasury_rewards), 8),
        "total_minted_rewards": round(
            float(blocks["rewards"])
            + float(validator_rewards)
            + float(audit_rewards)
            + float(science_rewards)
            + float(treasury_rewards),
            8,
        ),
        "circulating_supply": supply["circulating_supply"],
        "genesis_balance": supply["genesis_balance"],
        "latest_block_hash": GENESIS_HASH if latest is None else latest["block_hash"],
    }


def get_balance(account_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM balances WHERE account_id = ?", (account_id,)).fetchone()
        immature = immature_reward_total_for_account(connection, account_id)
    result = row_to_dict(row)
    if result is None:
        if immature["immature_reward_count"] == 0:
            return None
        result = {
            "account_id": account_id,
            "account_type": "unknown",
            "balance": 0.0,
            "balance_units": 0,
            "updated_at": None,
        }
    result["available_balance"] = round(float(result.get("balance") or 0), 8)
    result.update(immature)
    result["total_balance"] = round(result["available_balance"] + float(immature["immature_rewards"]), 8)
    return result


def get_balances(limit: int = 100) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM balances
            ORDER BY balance DESC, account_id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def request_faucet(account_id: str, account_type: str = "miner", amount: float | None = None) -> dict[str, Any]:
    if NETWORK_ID != "local":
        raise MiningError(403, "direct faucet credits are only allowed on local networks; submit a signed faucet transaction")
    if NETWORK_ID not in FAUCET_ALLOWED_NETWORKS:
        raise MiningError(403, f"faucet is disabled on network '{NETWORK_ID}'")
    if account_type not in {"miner", "validator", "wallet"}:
        raise MiningError(400, "account_type must be miner, validator or wallet")

    faucet_amount = FAUCET_DEFAULT_AMOUNT if amount is None else round(float(amount), 8)
    if faucet_amount <= 0:
        raise MiningError(400, "faucet amount must be positive")
    if faucet_amount > FAUCET_MAX_AMOUNT:
        raise MiningError(400, f"faucet amount exceeds max {FAUCET_MAX_AMOUNT}")

    with get_connection() as connection:
        if account_type in {"miner", "validator"}:
            table_name = "miners" if account_type == "miner" else "validators"
            id_column = "miner_id" if account_type == "miner" else "validator_id"
            account = connection.execute(
                f"SELECT 1 FROM {table_name} WHERE {id_column} = ?",
                (account_id,),
            ).fetchone()
            if account is None:
                raise MiningError(404, f"{account_type} account not found")

        recent_requests = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM ledger_entries
            WHERE account_id = ?
            AND entry_type = 'faucet_credit'
            AND created_at >= ?
            """,
            (account_id, iso_ago(FAUCET_RATE_LIMIT_WINDOW_SECONDS)),
        ).fetchone()["count"]
        if recent_requests >= FAUCET_RATE_LIMIT_MAX_REQUESTS:
            raise MiningError(429, "faucet rate limit exceeded for account")

        genesis_balance = connection.execute(
            "SELECT balance FROM balances WHERE account_id = ?",
            (GENESIS_ACCOUNT_ID,),
        ).fetchone()
        if genesis_balance is None or float(genesis_balance["balance"]) < faucet_amount:
            raise MiningError(409, "genesis faucet balance is insufficient")

        _apply_ledger_entry(
            connection,
            account_id=GENESIS_ACCOUNT_ID,
            account_type="genesis",
            amount=-faucet_amount,
            entry_type="faucet_debit",
            related_id=account_id,
            description=f"{NETWORK_ID} faucet debit for {account_type}",
        )
        _apply_ledger_entry(
            connection,
            account_id=account_id,
            account_type=account_type,
            amount=faucet_amount,
            entry_type="faucet_credit",
            related_id=GENESIS_ACCOUNT_ID,
            description=f"{NETWORK_ID} faucet credit",
        )
        balance = connection.execute(
            "SELECT balance FROM balances WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        next_genesis_balance = connection.execute(
            "SELECT balance FROM balances WHERE account_id = ?",
            (GENESIS_ACCOUNT_ID,),
        ).fetchone()

    return {
        "account_id": account_id,
        "account_type": account_type,
        "amount": faucet_amount,
        "balance": round(float(balance["balance"]), 8),
        "genesis_balance": round(float(next_genesis_balance["balance"]), 8),
        "message": f"{NETWORK_ID} faucet credit applied",
    }


def get_ledger_entries(account_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    with get_connection() as connection:
        if account_id is None:
            rows = connection.execute(
                "SELECT * FROM ledger_entries ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT * FROM ledger_entries
                WHERE account_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (account_id, limit),
            ).fetchall()
    return [row_to_dict(row) for row in rows]


def get_audit_summary() -> dict[str, Any]:
    with get_connection() as connection:
        supply = _supply_snapshot(connection)
        blocks = connection.execute("SELECT COUNT(*) AS count FROM blocks").fetchone()["count"]
        pending_jobs = connection.execute("SELECT COUNT(*) AS count FROM validation_jobs WHERE status = 'pending'").fetchone()["count"]
        validators = connection.execute(
            f"""
            SELECT
                COUNT(*) AS validator_count,
                COALESCE(SUM(stake_locked), 0) AS locked_stake,
                COALESCE(SUM(slashed_amount), 0) AS slashed_stake,
                COALESCE(SUM(CASE WHEN is_banned = 0 AND {VALIDATOR_ELIGIBILITY_STAKE_FIELD} >= ? AND trust_score >= ? THEN 1 ELSE 0 END), 0) AS eligible_count
            FROM validators
            """,
            (MIN_VALIDATOR_STAKE, VALIDATOR_MIN_TRUST_SCORE),
        ).fetchone()
    return {
        "genesis_supply": GENESIS_SUPPLY,
        "circulating_supply": supply["circulating_supply"],
        "genesis_balance": supply["genesis_balance"],
        "total_miner_balances": supply["miner_balances"],
        "total_validator_balances": supply["validator_balances"],
        "total_locked_validator_stake": round(float(validators["locked_stake"]), 8),
        "total_slashed_validator_stake": round(float(validators["slashed_stake"]), 8),
        "accepted_blocks": blocks,
        "pending_validation_jobs": pending_jobs,
        "validator_count": validators["validator_count"],
        "eligible_validator_count": validators["eligible_count"],
    }


def get_full_economic_audit() -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    with get_connection() as connection:
        _expire_assigned_tasks(connection)
        protocol = _active_protocol_params(connection)
        supply = _supply_snapshot(connection)
        balance_rows = connection.execute("SELECT * FROM balances ORDER BY account_id ASC").fetchall()
        balance_count = len(balance_rows)
        actual_total_balances = _sum_query(connection, "SELECT COALESCE(SUM(balance), 0) AS total FROM balances")
        actual_balances_by_type = _rows_to_float_map(
            connection.execute(
                """
                SELECT account_type, COALESCE(SUM(balance), 0) AS total
                FROM balances
                GROUP BY account_type
                """
            ).fetchall(),
            "account_type",
            "total",
        )
        ledger_entry_count = int(
            connection.execute("SELECT COUNT(*) AS count FROM ledger_entries").fetchone()["count"]
        )
        ledger_total_amount = _sum_query(connection, "SELECT COALESCE(SUM(amount), 0) AS total FROM ledger_entries")
        ledger_by_type = _rows_to_float_map(
            connection.execute(
                """
                SELECT entry_type, COALESCE(SUM(amount), 0) AS total
                FROM ledger_entries
                GROUP BY entry_type
                """
            ).fetchall(),
            "entry_type",
            "total",
        )
        ledger_by_account_type = _rows_to_float_map(
            connection.execute(
                """
                SELECT account_type, COALESCE(SUM(amount), 0) AS total
                FROM ledger_entries
                GROUP BY account_type
                """
            ).fetchall(),
            "account_type",
            "total",
        )
        account_mismatches = _account_balance_mismatches(connection)
        snapshot_base = active_snapshot_base_in_connection(connection)
        snapshot_base_total = (
            round(float(snapshot_base.get("total_balance") or 0), 8)
            if snapshot_base and snapshot_base.get("state_applied")
            else 0.0
        )
        snapshot_base_height = (
            int(snapshot_base.get("height") or 0)
            if snapshot_base and snapshot_base.get("state_applied")
            else 0
        )
        economic_base_total = snapshot_base_total if snapshot_base_total > 0 else GENESIS_SUPPLY

        block_rewards = _sum_query(connection, "SELECT COALESCE(SUM(reward), 0) AS total FROM blocks")
        accepted_blocks = int(connection.execute("SELECT COUNT(*) AS count FROM blocks").fetchone()["count"])
        snapshot_pending_reward_rows = connection.execute(
            """
            SELECT COUNT(*) AS count, COALESCE(SUM(amount), 0) AS total
            FROM rewards
            WHERE block_height <= ?
            """,
            (snapshot_base_height,),
        ).fetchone()
        snapshot_pending_reward_count = int(snapshot_pending_reward_rows["count"] or 0)
        snapshot_pending_reward_total = round(float(snapshot_pending_reward_rows["total"] or 0), 8)
        mature_block_rewards_total = _sum_query(
            connection,
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM rewards
            WHERE COALESCE(status, 'mature') = 'mature'
            """,
        )
        immature_block_rewards = _sum_query(
            connection,
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM rewards
            WHERE status = 'immature'
            """,
        )
        validator_rewards = _sum_query(
            connection,
            "SELECT COALESCE(SUM(amount), 0) AS total FROM ledger_entries WHERE entry_type = 'validator_reward'",
        )
        audit_rewards = _sum_query(
            connection,
            "SELECT COALESCE(SUM(amount), 0) AS total FROM ledger_entries WHERE entry_type = 'retroactive_audit_reward'",
        )
        science_reserve_rewards = _sum_query(
            connection,
            "SELECT COALESCE(SUM(amount), 0) AS total FROM ledger_entries WHERE entry_type = 'science_reserve_accrual'",
        )
        scientific_development_rewards = _sum_query(
            connection,
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM ledger_entries
            WHERE entry_type = 'scientific_development_treasury_accrual'
            """,
        )
        reward_rows = connection.execute(
            "SELECT COUNT(*) AS count, COALESCE(SUM(amount), 0) AS total FROM rewards"
        ).fetchone()
        reward_count = int(reward_rows["count"])
        rewards_table_total = round(float(reward_rows["total"]), 8)
        expected_rewards_table_total = round(block_rewards + snapshot_pending_reward_total, 8)
        expected_reward_count = accepted_blocks + snapshot_pending_reward_count
        ledger_block_rewards = _sum_query(
            connection,
            "SELECT COALESCE(SUM(amount), 0) AS total FROM ledger_entries WHERE entry_type = 'block_reward'",
        )

        validators = connection.execute(
            """
            SELECT
                COUNT(*) AS count,
                COALESCE(SUM(stake_locked), 0) AS stake_locked,
                COALESCE(SUM(wallet_stake_locked), 0) AS wallet_stake_locked,
                COALESCE(SUM(slashed_amount), 0) AS slashed_amount
            FROM validators
            """
        ).fetchone()
        validator_stake_locked = round(float(validators["stake_locked"]), 8)
        validator_wallet_stake_locked = round(float(validators["wallet_stake_locked"]), 8)
        legacy_validator_stake_locked = round(max(0.0, validator_stake_locked - validator_wallet_stake_locked), 8)
        validator_slashed_amount = round(float(validators["slashed_amount"]), 8)
        ledger_validator_stake_locks = _sum_query(
            connection,
            "SELECT COALESCE(SUM(amount), 0) AS total FROM ledger_entries WHERE entry_type = 'validator_stake_lock'",
        )
        ledger_validator_stake_unlocks = _sum_query(
            connection,
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM ledger_entries
            WHERE entry_type = 'validator_stake_unlock'
            AND account_type = 'validator'
            """,
        )
        ledger_validator_slashes = _sum_query(
            connection,
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM ledger_entries
            WHERE entry_type IN ('validator_slash', 'validator_fraud_slash')
            AND account_type = 'validator'
            """,
        )
        ledger_genesis_slashes = _sum_query(
            connection,
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM ledger_entries
            WHERE entry_type IN ('validator_slash', 'validator_fraud_slash')
            AND account_type = 'genesis'
            """,
        )

    expected_total_balances = round(
        economic_base_total
        + mature_block_rewards_total
        + validator_rewards
        + audit_rewards
        + science_reserve_rewards
        + scientific_development_rewards,
        8,
    )
    expected_ledger_total = expected_total_balances
    expected_validator_wallet_stake_locked = round(
        ledger_validator_stake_locks + ledger_validator_stake_unlocks + ledger_validator_slashes,
        8,
    )
    expected_validator_stake_locked = round(legacy_validator_stake_locked + expected_validator_wallet_stake_locked, 8)

    _audit_equal(
        issues,
        code="total_balances_mismatch",
        message="sum(balances) must equal genesis supply plus mature miner, validator, audit, science reserve and scientific treasury rewards",
        expected=expected_total_balances,
        actual=actual_total_balances,
    )
    _audit_equal(
        issues,
        code="ledger_total_mismatch",
        message="sum(ledger_entries.amount) must equal genesis supply plus mature miner, validator, audit, science reserve and scientific treasury rewards",
        expected=expected_ledger_total,
        actual=ledger_total_amount,
    )
    _audit_equal(
        issues,
        code="rewards_table_mismatch",
        message="rewards table total must equal accepted block rewards plus imported pre-snapshot pending rewards",
        expected=expected_rewards_table_total,
        actual=rewards_table_total,
    )
    _audit_equal(
        issues,
        code="ledger_block_rewards_mismatch",
        message="block_reward ledger entries must equal mature block rewards",
        expected=mature_block_rewards_total,
        actual=ledger_block_rewards,
    )
    _audit_equal(
        issues,
        code="validator_stake_mismatch",
        message="validator stake_locked must equal stake locks minus validator-side slashes",
        expected=expected_validator_stake_locked,
        actual=validator_stake_locked,
    )
    _audit_equal(
        issues,
        code="validator_wallet_stake_mismatch",
        message="validator wallet_stake_locked must equal wallet stake locks minus unlocks and slashes",
        expected=expected_validator_wallet_stake_locked,
        actual=validator_wallet_stake_locked,
    )
    _audit_equal(
        issues,
        code="validator_slash_mismatch",
        message="validator slashed_amount must match validator-side slash ledger entries",
        expected=validator_slashed_amount,
        actual=round(abs(ledger_validator_slashes), 8),
    )
    _audit_equal(
        issues,
        code="genesis_slash_mismatch",
        message="genesis slash credits must match validator slashed_amount",
        expected=validator_slashed_amount,
        actual=ledger_genesis_slashes,
    )
    if NETWORK_PROFILE.name == "mainnet" and legacy_validator_stake_locked > ECONOMIC_AUDIT_TOLERANCE:
        issues.append(
            {
                "code": "mainnet_legacy_validator_stake",
                "severity": "error",
                "message": "mainnet validator collateral must be wallet-backed stake",
                "details": {
                    "legacy_unbacked_stake_locked": legacy_validator_stake_locked,
                    "wallet_stake_locked": validator_wallet_stake_locked,
                },
            }
        )

    if expected_reward_count != reward_count:
        issues.append(
            {
                "code": "reward_count_mismatch",
                "severity": "error",
                "message": "accepted block count plus imported pre-snapshot pending rewards must match reward row count",
                "details": {
                    "accepted_blocks": accepted_blocks,
                    "snapshot_pending_reward_rows": snapshot_pending_reward_count,
                    "expected_reward_rows": expected_reward_count,
                    "reward_rows": reward_count,
                },
            }
        )

    for mismatch in account_mismatches:
        issues.append(
            {
                "code": "account_balance_mismatch",
                "severity": "error",
                "message": "account balance does not match sum of its ledger entries",
                "details": mismatch,
            }
        )

    return {
        "valid": not issues,
        "network_id": NETWORK_ID,
        "protocol_version": protocol["protocol_version"],
        "checked_at": utc_now(),
        "tolerance": ECONOMIC_AUDIT_TOLERANCE,
        "supply": {
            "genesis_supply": GENESIS_SUPPLY,
            "economic_base_total": economic_base_total,
            "active_snapshot_base": snapshot_base,
            "expected_total_balances": expected_total_balances,
            "actual_total_balances": actual_total_balances,
            "circulating_supply": supply["circulating_supply"],
            "genesis_balance": supply["genesis_balance"],
            "miner_balances": supply["miner_balances"],
            "validator_balances": supply["validator_balances"],
            "balances_by_account_type": actual_balances_by_type,
        },
        "ledger": {
            "entry_count": ledger_entry_count,
            "total_amount": ledger_total_amount,
            "expected_total_amount": expected_ledger_total,
            "by_entry_type": ledger_by_type,
            "by_account_type": ledger_by_account_type,
            "account_mismatch_count": len(account_mismatches),
        },
        "rewards": {
            "accepted_blocks": accepted_blocks,
            "block_reward_total": block_rewards,
            "snapshot_pending_reward_total": snapshot_pending_reward_total,
            "snapshot_pending_reward_rows": snapshot_pending_reward_count,
            "expected_rewards_table_total": expected_rewards_table_total,
            "mature_block_reward_total": mature_block_rewards_total,
            "immature_block_reward_total": immature_block_rewards,
            "block_maturity_depth": BLOCK_MATURITY_DEPTH,
            "validator_reward_total": validator_rewards,
            "audit_reward_total": audit_rewards,
            "science_reserve_total": science_reserve_rewards,
            "scientific_development_treasury_total": scientific_development_rewards,
            "total_minted_rewards": round(
                block_rewards
                + validator_rewards
                + audit_rewards
                + science_reserve_rewards
                + scientific_development_rewards,
                8,
            ),
            "reward_rows": reward_count,
            "rewards_table_total": rewards_table_total,
            "ledger_block_reward_total": ledger_block_rewards,
        },
        "validators": {
            "validator_count": int(validators["count"]),
            "stake_locked": validator_stake_locked,
            "expected_stake_locked": expected_validator_stake_locked,
            "wallet_stake_locked": validator_wallet_stake_locked,
            "expected_wallet_stake_locked": expected_validator_wallet_stake_locked,
            "legacy_unbacked_stake_locked": legacy_validator_stake_locked,
            "eligibility_stake_source": VALIDATOR_ELIGIBILITY_STAKE_SOURCE,
            "ledger_validator_stake_locks": ledger_validator_stake_locks,
            "ledger_validator_stake_unlocks": ledger_validator_stake_unlocks,
            "slashed_amount": validator_slashed_amount,
            "ledger_validator_slashes": ledger_validator_slashes,
            "ledger_genesis_slashes": ledger_genesis_slashes,
        },
        "issues": issues,
    }


def repair_missing_block_rewards() -> dict[str, Any]:
    repaired_heights: set[int] = set()
    miners_restored: set[str] = set()
    rewards_inserted = 0
    ledger_entries_inserted = 0
    state_roots_updated = 0
    checkpoints_updated = 0
    timestamp = utc_now()
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                blocks.height,
                blocks.miner_id,
                blocks.miner_reward_address,
                blocks.reward,
                blocks.task_id,
                blocks.timestamp,
                rewards.id AS reward_id,
                rewards.status AS reward_status,
                rewards.related_id AS reward_related_id,
                ledger_entries.id AS ledger_entry_id
            FROM blocks
            LEFT JOIN rewards
                ON rewards.block_height = blocks.height
               AND rewards.miner_id = blocks.miner_id
               AND ABS(rewards.amount - blocks.reward) <= ?
            LEFT JOIN ledger_entries
                ON ledger_entries.account_id = COALESCE(blocks.miner_reward_address, blocks.miner_id)
               AND ledger_entries.entry_type = 'block_reward'
               AND ABS(ledger_entries.amount - blocks.reward) <= ?
               AND (
                   ledger_entries.related_id = COALESCE(rewards.related_id, blocks.task_id)
                   OR ledger_entries.block_height = blocks.height
               )
            WHERE rewards.id IS NULL
               OR (COALESCE(rewards.status, 'mature') = 'mature' AND ledger_entries.id IS NULL)
            ORDER BY blocks.height ASC
            """,
            (ECONOMIC_AUDIT_TOLERANCE, ECONOMIC_AUDIT_TOLERANCE),
        ).fetchall()
        for row in rows:
            height = int(row["height"])
            reward = round(float(row["reward"] or 0), 8)
            if reward <= 0:
                continue
            if _ensure_historical_miner(connection, str(row["miner_id"]), row["timestamp"] or timestamp):
                miners_restored.add(str(row["miner_id"]))
            if row["reward_id"] is None:
                repair_account = row["miner_reward_address"] or row["miner_id"]
                repair_account_type = "wallet" if row["miner_reward_address"] else "miner"
                connection.execute(
                    """
                    INSERT INTO rewards (
                        miner_id, block_height, amount, amount_units, account_id, account_type,
                        status, matures_at_height, matured_at, related_id, reason, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'mature', ?, ?, ?, 'block reward repair', ?)
                    """,
                    (
                        row["miner_id"],
                        height,
                        reward,
                        to_units(reward),
                        repair_account,
                        repair_account_type,
                        height,
                        row["timestamp"] or timestamp,
                        row["task_id"],
                        row["timestamp"] or timestamp,
                    ),
                )
                rewards_inserted += 1
            if row["ledger_entry_id"] is None:
                repair_account = row["miner_reward_address"] or row["miner_id"]
                repair_account_type = "wallet" if row["miner_reward_address"] else "miner"
                balance_row = connection.execute(
                    "SELECT balance FROM balances WHERE account_id = ?",
                    (repair_account,),
                ).fetchone()
                if balance_row is not None:
                    connection.execute(
                        "UPDATE balances SET balance_units = ? WHERE account_id = ?",
                        (to_units(balance_row["balance"]), repair_account),
                    )
                _apply_ledger_entry(
                    connection,
                    account_id=repair_account,
                    account_type=repair_account_type,
                    amount=reward,
                    entry_type="block_reward",
                    block_height=height,
                    related_id=row["task_id"],
                    description="miner block reward repair",
                    timestamp=row["timestamp"] or timestamp,
                )
                ledger_entries_inserted += 1
            repaired_heights.add(height)

        if repaired_heights:
            first_height = min(repaired_heights)
            block_rows = connection.execute(
                """
                SELECT height, timestamp
                FROM blocks
                WHERE height >= ?
                ORDER BY height ASC
                """,
                (first_height,),
            ).fetchall()
            for block in block_rows:
                update_block_state_root(connection, int(block["height"]), block["timestamp"])
                state_roots_updated += 1
            checkpoint_rows = connection.execute(
                """
                SELECT height
                FROM canonical_checkpoints
                WHERE height >= ?
                ORDER BY height ASC
                """,
                (first_height,),
            ).fetchall()
            for checkpoint in checkpoint_rows:
                create_canonical_checkpoint_in_connection(
                    connection,
                    int(checkpoint["height"]),
                    trusted=True,
                    source="repair",
                )
                checkpoints_updated += 1

    audit = get_full_economic_audit()
    chain = verify_chain()
    return {
        "status": "ok" if audit["valid"] and chain["valid"] else "needs_attention",
        "repaired_blocks": len(repaired_heights),
        "repaired_heights": sorted(repaired_heights),
        "miners_restored": len(miners_restored),
        "restored_miner_ids": sorted(miners_restored),
        "rewards_inserted": rewards_inserted,
        "ledger_entries_inserted": ledger_entries_inserted,
        "state_roots_updated": state_roots_updated,
        "checkpoints_updated": checkpoints_updated,
        "audit_valid": audit["valid"],
        "audit_issues": audit["issues"],
        "chain_valid": chain["valid"],
        "chain_issues": chain["issues"],
    }


def _ensure_historical_miner(connection: Any, miner_id: str, timestamp: str) -> bool:
    existing = connection.execute("SELECT 1 FROM miners WHERE miner_id = ?", (miner_id,)).fetchone()
    if existing is not None:
        return False
    connection.execute(
        """
        INSERT INTO miners (miner_id, name, public_key, registered_at)
        VALUES (?, ?, NULL, ?)
        """,
        (miner_id, f"historical:{miner_id}"[:80], timestamp),
    )
    return True


def cleanup_expired_tasks() -> dict[str, Any]:
    with get_connection() as connection:
        result = _expire_assigned_tasks(connection)
    return {
        **result,
        "message": "expired tasks cleanup completed",
    }


def get_performance_stats() -> dict[str, Any]:
    with get_connection() as connection:
        blocks = connection.execute(
            """
            SELECT
                COUNT(*) AS accepted_blocks,
                COALESCE(AVG(total_task_ms), 0) AS avg_total_task_ms,
                COALESCE(AVG(validation_ms), 0) AS avg_validation_ms
            FROM blocks
            """
        ).fetchone()
        tasks = connection.execute(
            """
            SELECT
                COALESCE(AVG(compute_ms), 0) AS avg_compute_ms,
                COALESCE(AVG(assignment_ms), 0) AS avg_assignment_ms
            FROM tasks
            """
        ).fetchone()
        commitments = connection.execute(
            "SELECT COALESCE(AVG(commit_ms), 0) AS avg_commit_ms FROM commitments"
        ).fetchone()
        pending_jobs = connection.execute(
            "SELECT COUNT(*) AS count FROM validation_jobs WHERE status = 'pending'"
        ).fetchone()["count"]

    return {
        "accepted_blocks": blocks["accepted_blocks"],
        "avg_compute_ms": round(blocks_or_zero(tasks["avg_compute_ms"]), 2),
        "avg_assignment_ms": round(blocks_or_zero(tasks["avg_assignment_ms"]), 2),
        "avg_commit_ms": round(blocks_or_zero(commitments["avg_commit_ms"]), 2),
        "avg_validation_ms": round(blocks_or_zero(blocks["avg_validation_ms"]), 2),
        "avg_total_task_ms": round(blocks_or_zero(blocks["avg_total_task_ms"]), 2),
        "pending_validation_jobs": pending_jobs,
        **pi_cache_info(),
    }


def get_health_status() -> dict[str, Any]:
    return _cached_status_payload(
        f"health:{database_module.DATABASE_PATH}",
        HEALTH_ENDPOINT_CACHE_SECONDS,
        _get_health_status_uncached,
    )


def _get_health_status_uncached() -> dict[str, Any]:
    checked_at = utc_now_dt()
    issues: list[str] = []
    database = {"connected": False}
    snapshot_base: dict[str, Any] | None = None
    replay_status: dict[str, Any] = {
        "sync_status": "healthy",
        "replay_stalled": False,
        "replay_last_progress_at": None,
        "replay_last_imported_height": 0,
        "replay_consecutive_failures": 0,
        "divergence_detected": False,
        "divergence_reason": None,
        "auto_recovery_active": False,
    }

    try:
        with get_connection() as connection:
            connection.execute("SELECT 1").fetchone()
            maintenance = _maybe_expire_assigned_tasks(connection)
            params = _active_protocol_params(connection)
            latest_height = _latest_block_height(connection)
            latest_hash = _latest_block_hash(connection)
            snapshot_base = active_snapshot_base_in_connection(connection)
            miner_counts = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(CASE WHEN online_status = 'online' THEN 1 ELSE 0 END), 0) AS online,
                    COALESCE(SUM(CASE WHEN online_status = 'stale' THEN 1 ELSE 0 END), 0) AS stale,
                    COALESCE(SUM(CASE WHEN online_status = 'offline' THEN 1 ELSE 0 END), 0) AS offline
                FROM miners
                """
            ).fetchone()
            miners = int(miner_counts["total"])
            online_miners = int(miner_counts["online"])
            validators = connection.execute("SELECT COUNT(*) AS total FROM validators").fetchone()
            active_protocol = params is not None
            eligible_validators = len(_eligible_validator_rows(connection))
            required_approvals = _effective_required_validator_approvals(connection, params)
            database = {
                "connected": True,
                "active_protocol": active_protocol,
                "miners": miners,
                "online_miners": online_miners,
                "stale_miners": int(miner_counts["stale"]),
                "offline_miners": int(miner_counts["offline"]),
                "validators": int(validators["total"]),
                "eligible_validators": eligible_validators,
                "maintenance": maintenance,
            }
    except Exception as exc:
        issues.append(f"database unavailable: {exc}")
        params = None
        latest_height = 0
        latest_hash = GENESIS_HASH
        miners = 0
        online_miners = 0
        required_approvals = REQUIRED_VALIDATOR_APPROVALS
        eligible_validators = 0
        active_protocol = False

    local_height = latest_height
    local_hash = latest_hash
    if snapshot_base is not None and int(snapshot_base.get("height") or 0) > latest_height:
        latest_height = int(snapshot_base["height"])
        latest_hash = snapshot_base["block_hash"]

    if database["connected"] and snapshot_base is not None and int(snapshot_base.get("height") or 0) > local_height:
        chain = {
            "valid": True,
            "checked_blocks": 0,
            "latest_block_hash": latest_hash,
            "issues": [],
        }
    else:
        if database["connected"]:
            chain = _basic_chain_health(local_height, local_hash)
        else:
            chain = {
                "valid": False,
                "checked_blocks": 0,
                "latest_block_hash": latest_hash,
                "issues": [{"reason": "database unavailable"}],
            }
    include_audit = os.getenv("PICOIN_HEALTH_INCLUDE_AUDIT", "0").strip().lower() in {"1", "true", "yes", "on"}
    audit = (
        _basic_audit_health()
        if database["connected"] and include_audit
        else {"valid": bool(database["connected"]), "skipped": True, "detail": "use /audit/full for economic audit"}
    )

    if not chain["valid"]:
        issues.append("chain verification failed")
    if include_audit and not audit["valid"]:
        issues.append("economic audit has issues")
    try:
        from app.services.consensus import get_replay_status

        replay_status = get_replay_status()
    except Exception as exc:
        issues.append(f"replay status unavailable: {exc}")
    sync_status = str(replay_status.get("sync_status") or "healthy")
    if sync_status in {"stalled", "divergent"}:
        issues.append(f"replay {sync_status}")
    if replay_status.get("divergence_reason"):
        issues.append(str(replay_status["divergence_reason"])[:180])
    readiness = build_node_readiness(
        database_connected=bool(database["connected"]),
        active_protocol=active_protocol,
        chain_valid=bool(chain["valid"]),
        sync_status=sync_status,
        divergence_detected=bool(replay_status.get("divergence_detected")),
        miners=miners,
        online_miners=online_miners,
        eligible_validators=eligible_validators,
        required_validator_approvals=required_approvals,
    )
    local_quorum_types = {"full", "bootstrap", "miner"}
    if active_protocol and NODE_TYPE in local_quorum_types and readiness["reasons"]["block_finalize_ready"]:
        quorum_reason = f"eligible validators {eligible_validators} below required quorum {required_approvals}"
        if quorum_reason in readiness["reasons"]["block_finalize_ready"]:
            issues.append("not enough eligible validators for quorum")

    can_assign_tasks = bool(readiness["task_assign_ready"])
    mining_ready = bool(readiness["mining_ready"])
    status = "ok" if not issues else "degraded"

    protocol_version = params["protocol_version"] if params is not None else PROTOCOL_VERSION
    return {
        "status": status,
        "project": PROJECT_NAME,
        "protocol_version": protocol_version,
        "network_id": NETWORK_ID,
        "checked_at": checked_at.isoformat(),
        "started_at": NODE_STARTED_AT.isoformat(),
        "uptime_seconds": max(0, int((checked_at - NODE_STARTED_AT).total_seconds())),
        "database": database,
        "chain": {
            "valid": bool(chain["valid"]),
            "checked_blocks": int(chain["checked_blocks"]),
            "issue_count": len(chain["issues"]),
        },
        "audit": audit,
        "latest_block_height": latest_height,
        "latest_block_hash": latest_hash,
        "local_block_height": local_height,
        "local_block_hash": local_hash,
        "sync_status": sync_status,
        "replay_stalled": bool(replay_status.get("replay_stalled")),
        "replay_last_progress_at": replay_status.get("replay_last_progress_at"),
        "replay_last_imported_height": int(replay_status.get("replay_last_imported_height") or 0),
        "replay_consecutive_failures": int(replay_status.get("replay_consecutive_failures") or 0),
        "divergence_detected": bool(replay_status.get("divergence_detected")),
        "divergence_reason": replay_status.get("divergence_reason"),
        "auto_recovery_active": bool(replay_status.get("auto_recovery_active")),
        "node_role": readiness["node_role"],
        "readiness": readiness,
        "read_ready": bool(readiness["read_ready"]),
        "tx_submit_ready": bool(readiness["tx_submit_ready"]),
        "task_assign_ready": bool(readiness["task_assign_ready"]),
        "validation_job_ready": bool(readiness["validation_job_ready"]),
        "block_finalize_ready": bool(readiness["block_finalize_ready"]),
        "can_assign_tasks": can_assign_tasks,
        "mining_ready": mining_ready,
        "issues": issues,
    }


def _basic_chain_health(latest_height: int, latest_hash: str) -> dict[str, Any]:
    """Lightweight recent-tip check for public monitoring; full replay stays on /chain/verify."""
    try:
        limit = int(os.getenv("PICOIN_HEALTH_CHAIN_CHECK_BLOCKS", "64"))
    except ValueError:
        limit = 64
    limit = max(1, min(limit, 512))
    issues: list[dict[str, Any]] = []
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT height, previous_hash, block_hash
            FROM blocks
            ORDER BY height DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    blocks = [row_to_dict(row) for row in rows]
    if latest_height > 0 and not blocks:
        issues.append({"height": latest_height, "reason": "latest block missing"})
    if blocks:
        newest = blocks[0]
        if int(newest["height"]) != latest_height:
            issues.append({"height": newest["height"], "reason": f"expected latest height {latest_height}"})
        if newest["block_hash"] != latest_hash:
            issues.append({"height": newest["height"], "reason": "latest block hash mismatch"})
    by_height = {int(block["height"]): block for block in blocks}
    for block in blocks:
        height = int(block["height"])
        if height <= 1:
            if block["previous_hash"] != GENESIS_HASH:
                issues.append({"height": height, "reason": "genesis previous_hash mismatch"})
            continue
        parent = by_height.get(height - 1)
        if parent is not None and block["previous_hash"] != parent["block_hash"]:
            issues.append({"height": height, "reason": "previous_hash does not match recent parent"})
    return {
        "valid": not issues,
        "checked_blocks": len(blocks),
        "latest_block_hash": latest_hash,
        "issues": issues,
    }


def get_node_status() -> dict[str, Any]:
    checked_at = utc_now_dt()
    with get_connection() as connection:
        params = _active_protocol_params(connection)
        latest_height = _latest_block_height(connection)
        latest_hash = _latest_block_hash(connection)
        counts = _node_counts(connection, params)
        supply = _supply_snapshot(connection)

    chain = verify_chain()
    audit = _basic_audit_health()
    performance = get_performance_stats()
    protocol = _protocol_payload(params)
    mining_ready = counts["online_miners"] > 0 and counts["eligible_validators"] >= protocol["required_validator_approvals"]

    return {
        "project": PROJECT_NAME,
        "protocol_version": protocol["protocol_version"],
        "network_id": NETWORK_ID,
        "started_at": NODE_STARTED_AT.isoformat(),
        "checked_at": checked_at.isoformat(),
        "uptime_seconds": max(0, int((checked_at - NODE_STARTED_AT).total_seconds())),
        "latest_block_height": latest_height,
        "latest_block_hash": latest_hash,
        "chain_valid": bool(chain["valid"]),
        "audit_valid": bool(audit["valid"]),
        "mining_ready": mining_ready,
        "counts": counts,
        "protocol": {
            "algorithm": protocol["algorithm"],
            "validation_mode": protocol["validation_mode"],
            "required_validator_approvals": protocol["required_validator_approvals"],
            "difficulty": protocol["difficulty"],
            "reward_per_block": protocol["reward_per_block"],
            "faucet_enabled": protocol["faucet_enabled"],
        },
        "performance": {
            "avg_total_task_ms": performance["avg_total_task_ms"],
            "avg_validation_ms": performance["avg_validation_ms"],
            "pending_validation_jobs": performance["pending_validation_jobs"],
        },
        "economy": {
            "circulating_supply": supply["circulating_supply"],
            "genesis_balance": supply["genesis_balance"],
            "miner_balances": supply["miner_balances"],
            "validator_balances": supply["validator_balances"],
            "science_balances": supply["science_balances"],
            "scientific_development_balances": supply["scientific_development_balances"],
        },
    }


def get_recent_events(limit: int = 30) -> list[dict[str, Any]]:
    with get_connection() as connection:
        events: list[dict[str, Any]] = []
        events.extend(_block_events(connection, limit))
        events.extend(_validator_vote_events(connection, limit))
        events.extend(_faucet_events(connection, limit))
        events.extend(_penalty_events(connection, limit))
        events.extend(_retarget_events(connection, limit))
        events.extend(_retroactive_audit_events(connection, limit))
        events.extend(science_events_for_node(connection, limit))

    events.sort(key=lambda event: parse_iso(event["created_at"]) or NODE_STARTED_AT, reverse=True)
    return events[:limit]


def get_retroactive_audits(limit: int = 20) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM retroactive_audits
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_decode_retroactive_audit(row_to_dict(row)) for row in rows]


def run_retroactive_audit(block_height: int | None = None, sample_multiplier: int = 2) -> dict[str, Any]:
    with get_connection() as connection:
        audit = _run_retroactive_audit_in_connection(
            connection,
            block_height=block_height,
            sample_multiplier=sample_multiplier,
            automatic=False,
        )

    decoded = _decode_retroactive_audit(audit)
    return {"accepted": bool(decoded["passed"]), "audit": decoded}


def get_protocol() -> dict[str, Any]:
    with get_connection() as connection:
        params = _active_protocol_params(connection)
    return _protocol_payload(params)


def get_protocol_history() -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM protocol_params ORDER BY id DESC"
        ).fetchall()
    return [_protocol_params_payload(row_to_dict(row)) for row in rows]


def get_difficulty_status() -> dict[str, Any]:
    with get_connection() as connection:
        params = _active_protocol_params(connection)
        current_height = _latest_block_height(connection)
        last_retarget_height = _last_retarget_height(connection)
        epoch_rows = _retarget_epoch_rows(connection, last_retarget_height)
        assignment_window = _range_assignment_window(connection, params)

    blocks_since_retarget = max(0, current_height - last_retarget_height)
    average_ms = _average_epoch_ms(epoch_rows) if epoch_rows else None
    required_epoch_blocks = max(RETARGET_EPOCH_BLOCKS, RETARGET_WINDOW_BLOCKS)
    blocks_until_ready = max(0, required_epoch_blocks - len(epoch_rows))
    next_range_start = min(
        int(assignment_window["frontier"]) + 1,
        int(assignment_window["RETARGET_MAX_PI_POSITION"]),
    )
    required_task_expiration_seconds = int(get_dynamic_expiration(next_range_start))
    effective_task_expiration_seconds = _task_expiration_seconds_for_position(params, next_range_start)
    return {
        "enabled": True,
        "epoch_blocks": RETARGET_EPOCH_BLOCKS,
        "epoch_blocks_required": required_epoch_blocks,
        "retarget_window_blocks": RETARGET_WINDOW_BLOCKS,
        "target_block_ms": RETARGET_TARGET_BLOCK_MS,
        "tolerance": RETARGET_TOLERANCE,
        "current_height": current_height,
        "last_retarget_height": last_retarget_height,
        "current_epoch_block_count": len(epoch_rows),
        "current_epoch_average_ms": average_ms,
        "blocks_since_retarget": blocks_since_retarget,
        "blocks_until_ready": blocks_until_ready,
        "blocks_until_next_epoch": blocks_until_ready,
        "active_difficulty": calculate_difficulty(params),
        "min_difficulty": RETARGET_MIN_DIFFICULTY,
        "max_difficulty": RETARGET_MAX_DIFFICULTY,
        "active_reward_per_block": calculate_reward(params),
        "configured_max_pi_position": params["max_pi_position"],
        "effective_max_pi_position": assignment_window["effective_max_pi_position"],
        "RETARGET_MAX_PI_POSITION": assignment_window["RETARGET_MAX_PI_POSITION"],
        "next_range_start": next_range_start,
        "active_task_expiration_seconds": int(params["task_expiration_seconds"]),
        "required_task_expiration_seconds": required_task_expiration_seconds,
        "effective_task_expiration_seconds": effective_task_expiration_seconds,
        "range_frontier": assignment_window["frontier"],
        "range_start_min": assignment_window["min_start"],
        "range_start_max": assignment_window["max_start"],
        "range_window_index": assignment_window["window_index"],
        "range_window_size": assignment_window["window_size"],
    }


def preview_retarget(force: bool = False) -> dict[str, Any]:
    with get_connection() as connection:
        return _public_retarget_preview(_retarget_preview(connection, force=force))


def get_retarget_history(limit: int = 20) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM retarget_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def run_retarget(force: bool = False) -> dict[str, Any]:
    with get_connection() as connection:
        current_height = _latest_block_height(connection)
        event = _maybe_retarget_after_block(connection, current_height, force=force)
        params = _active_protocol_params(connection)

    return {
        "retargeted": event is not None,
        "status": "retargeted" if event is not None else "waiting",
        "message": "retarget epoch applied" if event is not None else "not enough accepted blocks for retarget",
        "event": event,
        "protocol": _protocol_payload(params),
    }


def _protocol_payload(params: dict[str, Any]) -> dict[str, Any]:
    RETARGET_MAX_PI_POSITION_value = _resolve_RETARGET_MAX_PI_POSITION(params)
    return {
        "project": PROJECT_NAME,
        "protocol_version": params["protocol_version"],
        "network_id": NETWORK_ID,
        "chain_id": CHAIN_ID,
        "node_role": NODE_ROLE,
        "node_capabilities": node_capabilities_for_role(),
        "algorithm": params["algorithm"],
        "validation_mode": params["validation_mode"],
        "mining_task_mode": MINING_TASK_MODE,
        "required_validator_approvals": params["required_validator_approvals"],
        "range_assignment_mode": params["range_assignment_mode"],
        "max_pi_position": params["max_pi_position"],
        "range_assignment_max_attempts": params["range_assignment_max_attempts"],
        "range_start_window_size": RANGE_START_WINDOW_SIZE,
        "range_window_retire_occupancy": RANGE_WINDOW_RETIRE_OCCUPANCY,
        "range_window_max_age_blocks": RANGE_WINDOW_MAX_AGE_BLOCKS,
        "range_window_lookahead_multiplier": RANGE_WINDOW_LOOKAHEAD_MULTIPLIER,
        "segment_size": params["segment_size"],
        "sample_count": params["sample_count"],
        "task_expiration_seconds": params["task_expiration_seconds"],
        "max_active_tasks_per_miner": params["max_active_tasks_per_miner"],
        "block_maturity_depth": BLOCK_MATURITY_DEPTH,
        "base_reward": params["base_reward"],
        "difficulty": calculate_difficulty(params),
        "target_block_time_ms": params.get("target_block_time_ms") or RETARGET_TARGET_BLOCK_MS,
        "RETARGET_MAX_PI_POSITION": RETARGET_MAX_PI_POSITION_value,
        "retarget_reason": params.get("retarget_reason"),
        "retarget_source_window": _retarget_source_window(params),
        "retarget_source_details": params.get("retarget_source_details"),
        "previous_protocol_params_id": params.get("previous_protocol_params_id"),
        "reward_per_block": calculate_reward(params),
        "proof_of_pi_reward_per_block": calculate_miner_reward(params),
        "proof_of_pi_reward_percent": PROOF_OF_PI_REWARD_PERCENT,
        "science_compute_reward_percent": SCIENCE_COMPUTE_REWARD_PERCENT_OF_BLOCK,
        "science_compute_reserve_per_block": reward_units_to_float(
            science_reserve_units_from_total(total_block_reward_units(params))
        ),
        "science_reserve_account_id": SCIENCE_RESERVE_ACCOUNT_ID,
        "science_base_monthly_quota_units": SCIENCE_BASE_MONTHLY_QUOTA_UNITS,
        "validator_auditor_reward_percent": VALIDATOR_AUDITOR_REWARD_PERCENT,
        "validator_reward_percent": VALIDATOR_REWARD_PERCENT_OF_BLOCK,
        "validator_reward_pool_per_block": calculate_validator_reward_pool(params),
        "scientific_development_reward_percent": SCIENTIFIC_DEVELOPMENT_REWARD_PERCENT_OF_BLOCK,
        "scientific_development_treasury_per_block": calculate_scientific_development_treasury_reward(params),
        "scientific_development_treasury_account_id": SCIENTIFIC_DEVELOPMENT_TREASURY_ACCOUNT_ID,
        "scientific_development_treasury_wallet": SCIENTIFIC_DEVELOPMENT_TREASURY_WALLET,
        "scientific_development_governance_wallet": SCIENTIFIC_DEVELOPMENT_GOVERNANCE_WALLET,
        "scientific_development_unlock_interval_days": SCIENTIFIC_DEVELOPMENT_UNLOCK_INTERVAL_DAYS,
        "retroactive_audit_interval_blocks": RETROACTIVE_AUDIT_INTERVAL_BLOCKS,
        "retroactive_audit_sample_multiplier": RETROACTIVE_AUDIT_SAMPLE_MULTIPLIER,
        "retroactive_audit_reward_percent": RETROACTIVE_AUDIT_REWARD_PERCENT_OF_BLOCK,
        "retroactive_audit_reward_per_audit": round(
            calculate_reward(params) * RETROACTIVE_AUDIT_REWARD_PERCENT_OF_BLOCK,
            8,
        ),
        "fraud_miner_penalty_points": FRAUD_MINER_PENALTY_POINTS,
        "fraud_validator_invalid_results": FRAUD_VALIDATOR_INVALID_RESULTS,
        "fraud_cooldown_seconds": FRAUD_COOLDOWN_SECONDS,
        "faucet_enabled": NETWORK_ID in FAUCET_ALLOWED_NETWORKS,
        "min_validator_stake": MIN_VALIDATOR_STAKE,
        "validator_eligibility_stake_field": VALIDATOR_ELIGIBILITY_STAKE_FIELD,
        "validator_eligibility_stake_source": VALIDATOR_ELIGIBILITY_STAKE_SOURCE,
        "validator_selection_mode": VALIDATOR_SELECTION_MODE,
        "penalty_invalid_result": PENALTY_INVALID_RESULT,
        "penalty_duplicate": PENALTY_DUPLICATE,
        "penalty_invalid_signature": PENALTY_INVALID_SIGNATURE,
        "cooldown_after_rejections": COOLDOWN_AFTER_REJECTIONS,
        "cooldown_seconds": COOLDOWN_SECONDS,
    }


def _protocol_params_payload(params: dict[str, Any]) -> dict[str, Any]:
    payload = {
        key: value
        for key, value in params.items()
        if str(key).upper() != "RETARGET_MAX_PI_POSITION"
    }
    payload["RETARGET_MAX_PI_POSITION"] = _resolve_RETARGET_MAX_PI_POSITION(params)
    payload["active"] = bool(payload["active"])
    payload["difficulty"] = calculate_difficulty(payload)
    payload["target_block_time_ms"] = payload.get("target_block_time_ms") or RETARGET_TARGET_BLOCK_MS
    payload["retarget_source_window"] = _retarget_source_window(payload)
    payload["reward_per_block"] = calculate_reward(payload)
    return payload


def _retarget_source_window(params: dict[str, Any]) -> int | None:
    value = params.get("retarget_source_window")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            parsed = json.loads(str(value))
            return int(parsed.get("epoch_block_count") or RETARGET_WINDOW_BLOCKS)
        except (TypeError, ValueError, json.JSONDecodeError, AttributeError):
            return RETARGET_WINDOW_BLOCKS


def verify_chain() -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    blocks = get_blocks()
    snapshot_base = active_snapshot_base()
    previous_hash = GENESIS_HASH
    expected_start = 1
    if snapshot_base is not None and snapshot_base.get("state_applied") and blocks:
        first_height = int(blocks[0]["height"])
        if first_height == int(snapshot_base["height"]) + 1:
            previous_hash = snapshot_base["block_hash"]
            expected_start = first_height
    seen_result_hashes: set[str] = set()

    for expected_height, block in enumerate(blocks, start=expected_start):
        height = block["height"]
        if height != expected_height:
            issues.append({"height": height, "reason": f"expected height {expected_height}"})
        if block["previous_hash"] != previous_hash:
            issues.append({"height": height, "reason": "previous_hash does not match prior block"})

        if block["result_hash"] in seen_result_hashes:
            issues.append({"height": height, "reason": "duplicate result_hash"})
        seen_result_hashes.add(block["result_hash"])

        current_payload = _block_payload(block, include_protocol=True)
        legacy_payload = _block_payload(block, include_protocol=False)
        fraud_payload = dict(current_payload)
        fraud_payload["fraudulent"] = bool(block.get("fraudulent", False))
        fraud_payload["fraud_reason"] = block.get("fraud_reason")
        fraud_payload["fraud_detected_at"] = block.get("fraud_detected_at")
        legacy_fraud_payload = dict(legacy_payload)
        legacy_fraud_payload["fraudulent"] = bool(block.get("fraudulent", False))
        legacy_fraud_payload["fraud_reason"] = block.get("fraud_reason")
        legacy_fraud_payload["fraud_detected_at"] = block.get("fraud_detected_at")
        valid_hashes = {
            hash_block(current_payload),
            hash_block(legacy_payload),
            hash_block(fraud_payload),
            hash_block(legacy_fraud_payload),
        }
        if block["block_hash"] not in valid_hashes and _matches_imported_proposal_payload(block):
            valid_hashes.add(block["block_hash"])
        if block["block_hash"] not in valid_hashes:
            issues.append({"height": height, "reason": "block_hash does not match block payload"})
        if block.get("state_root"):
            with get_connection() as connection:
                try:
                    expected_state_root = calculate_state_root(connection, height, block.get("timestamp"))
                except ValueError as exc:
                    issues.append({"height": height, "reason": "state_root does not match ledger replay", "detail": str(exc)})
                    expected_state_root = None
            if expected_state_root is not None and block["state_root"] != expected_state_root:
                issues.append({"height": height, "reason": "state_root does not match ledger replay"})

        previous_hash = block["block_hash"]

    return {
        "valid": not issues,
        "checked_blocks": len(blocks),
        "latest_block_hash": previous_hash if blocks else (snapshot_base["block_hash"] if snapshot_base else previous_hash),
        "issues": issues,
    }


def _matches_imported_proposal_payload(block: dict[str, Any]) -> bool:
    """Accept a foreign proposal payload when local FK ids were normalized during replay."""
    with get_connection() as connection:
        proposal_rows = connection.execute(
            """
            SELECT payload
            FROM consensus_block_proposals
            WHERE block_hash = ? AND height = ?
            ORDER BY updated_at DESC, created_at DESC
            """,
            (block["block_hash"], block["height"]),
        ).fetchall()
        header_rows = connection.execute(
            """
            SELECT payload
            FROM network_block_headers
            WHERE block_hash = ? AND height = ?
            ORDER BY received_at DESC
            """,
            (block["block_hash"], block["height"]),
        ).fetchall()
    for row in [*proposal_rows, *header_rows]:
        try:
            proposal_block = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            continue
        if proposal_block.get("block_hash") != block["block_hash"]:
            continue
        proposal_hashes = {
            hash_block(_block_payload(proposal_block, include_protocol=True)),
            hash_block(_block_payload(proposal_block, include_protocol=False)),
        }
        fraud_payload = _block_payload(proposal_block, include_protocol=True)
        fraud_payload["fraudulent"] = bool(proposal_block.get("fraudulent", False))
        fraud_payload["fraud_reason"] = proposal_block.get("fraud_reason")
        fraud_payload["fraud_detected_at"] = proposal_block.get("fraud_detected_at")
        proposal_hashes.add(hash_block(fraud_payload))
        legacy_fraud_payload = _block_payload(proposal_block, include_protocol=False)
        legacy_fraud_payload["fraudulent"] = bool(proposal_block.get("fraudulent", False))
        legacy_fraud_payload["fraud_reason"] = proposal_block.get("fraud_reason")
        legacy_fraud_payload["fraud_detected_at"] = proposal_block.get("fraud_detected_at")
        proposal_hashes.add(hash_block(legacy_fraud_payload))
        if block["block_hash"] in proposal_hashes:
            return True
    return False


def _reject_in_connection(
    connection: Any,
    reason: str,
    task_id: str,
    miner_id: str,
    result_hash: str,
    validation: dict[str, Any],
    penalty_points: int,
    signature: str | None,
    segment: str,
) -> dict[str, Any]:
    release_selected_transactions(connection, task_id, reason)
    existing_rejection = connection.execute(
        """
        SELECT 1
        FROM rejected_submissions
        WHERE task_id = ?
          AND miner_id = ?
          AND result_hash = ?
          AND reason = ?
        LIMIT 1
        """,
        (task_id, miner_id, result_hash, reason),
    ).fetchone()
    if existing_rejection is None:
        connection.execute(
            """
            INSERT INTO rejected_submissions (task_id, miner_id, result_hash, reason, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (task_id, miner_id, result_hash, reason, utc_now()),
        )
    if _miner_exists(connection, miner_id):
        _record_submission(connection, task_id, miner_id, result_hash, segment, signature, False, reason)
        _apply_penalty(connection, miner_id, task_id, penalty_points, reason)
    return {
        "accepted": False,
        "status": "rejected",
        "message": reason,
        "block": None,
        "validation": validation,
    }


def _expire_assigned_tasks(connection: Any) -> dict[str, int]:
    released_assignments = _release_timed_out_validation_assignments(connection)
    closed_competitive_jobs = _close_obsolete_competitive_validation_jobs(connection)
    expired_rows = connection.execute(
        """
        SELECT task_id
        FROM tasks
        WHERE status IN ('assigned', 'committed')
        AND expires_at IS NOT NULL
        AND expires_at <= ?
        ORDER BY task_id ASC
        """,
        (utc_now(),),
    ).fetchall()
    revealed_candidates = connection.execute(
        """
        SELECT task_id
        FROM tasks
        WHERE status = 'revealed'
        AND expires_at IS NOT NULL
        AND expires_at <= ?
        ORDER BY task_id ASC
        """,
        (utc_now(),),
    ).fetchall()
    expirable_revealed_task_ids = [
        row["task_id"]
        for row in revealed_candidates
        if not _revealed_task_has_quorum_path(connection, row["task_id"])
    ]
    task_cursor = connection.execute(
        """
        UPDATE tasks
        SET status = 'expired'
        WHERE status IN ('assigned', 'committed')
        AND expires_at IS NOT NULL
        AND expires_at <= ?
        """,
        (utc_now(),),
    )
    expired_task_ids = [row["task_id"] for row in expired_rows] + expirable_revealed_task_ids
    revealed_expired_count = 0
    job_expired_count = 0
    if expirable_revealed_task_ids:
        placeholders = ", ".join("?" for _ in expirable_revealed_task_ids)
        revealed_cursor = connection.execute(
            f"""
            UPDATE tasks
            SET status = 'expired'
            WHERE status = 'revealed'
            AND task_id IN ({placeholders})
            """,
            tuple(expirable_revealed_task_ids),
        )
        revealed_expired_count = max(0, revealed_cursor.rowcount)
    if expired_task_ids:
        placeholders = ", ".join("?" for _ in expired_task_ids)
        job_cursor = connection.execute(
            f"""
            UPDATE validation_jobs
            SET status = 'expired', completed_at = ?
            WHERE status = 'pending'
            AND task_id IN ({placeholders})
            """,
            (utc_now(), *expired_task_ids),
        )
        job_expired_count = max(0, job_cursor.rowcount)
    for task_id in expired_task_ids:
        release_selected_transactions(connection, task_id, "task expired")
    return {
        "expired_tasks": max(0, task_cursor.rowcount) + revealed_expired_count,
        "expired_validation_jobs": job_expired_count,
        "released_validation_assignments": released_assignments,
        "closed_competitive_validation_jobs": closed_competitive_jobs,
    }


def _maybe_expire_assigned_tasks(connection: Any) -> dict[str, Any]:
    global _EXPIRED_TASK_CLEANUP_LAST_RUN_MONOTONIC
    min_interval = max(0, int(EXPIRED_TASK_CLEANUP_MIN_INTERVAL_SECONDS))
    monotonic_now = time.monotonic()
    if monotonic_now - _EXPIRED_TASK_CLEANUP_LAST_RUN_MONOTONIC < min_interval:
        return {
            "expired_tasks": 0,
            "expired_validation_jobs": 0,
            "released_validation_assignments": 0,
            "closed_competitive_validation_jobs": 0,
            "skipped": "recent",
        }
    if not _EXPIRED_TASK_CLEANUP_LOCK.acquire(blocking=False):
        return {
            "expired_tasks": 0,
            "expired_validation_jobs": 0,
            "released_validation_assignments": 0,
            "closed_competitive_validation_jobs": 0,
            "skipped": "already_running",
        }
    try:
        monotonic_now = time.monotonic()
        if monotonic_now - _EXPIRED_TASK_CLEANUP_LAST_RUN_MONOTONIC < min_interval:
            return {
                "expired_tasks": 0,
                "expired_validation_jobs": 0,
                "released_validation_assignments": 0,
                "closed_competitive_validation_jobs": 0,
                "skipped": "recent",
            }
        result = _expire_assigned_tasks(connection)
        _EXPIRED_TASK_CLEANUP_LAST_RUN_MONOTONIC = monotonic_now
        return result
    finally:
        _EXPIRED_TASK_CLEANUP_LOCK.release()


def _revealed_task_has_quorum_path(connection: Any, task_id: str) -> bool:
    job = row_to_dict(
        connection.execute(
            """
            SELECT validation_jobs.*, tasks.protocol_params_id
            FROM validation_jobs
            JOIN tasks ON tasks.task_id = validation_jobs.task_id
            WHERE validation_jobs.task_id = ?
            AND validation_jobs.status = 'pending'
            ORDER BY validation_jobs.created_at DESC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
    )
    if job is None:
        return False
    params = _protocol_params_for_task(connection, job)
    required = _effective_required_validator_approvals(connection, params)
    counts = _validation_vote_counts(connection, job["job_id"])
    if counts["approvals"] >= required or counts["rejections"] >= required:
        return True
    if counts["approvals"] == 0 and counts["rejections"] == 0 and not job.get("assigned_validator_id"):
        return False
    voted_rows = connection.execute(
        "SELECT validator_id FROM validation_votes WHERE job_id = ?",
        (job["job_id"],),
    ).fetchall()
    voted_validator_ids = {row["validator_id"] for row in voted_rows}
    eligible_unvoted = [
        validator
        for validator in _eligible_validator_rows(connection)
        if validator["validator_id"] not in voted_validator_ids
    ]
    return bool(eligible_unvoted)


def _record_submission(
    connection: Any,
    task_id: str,
    miner_id: str,
    result_hash: str,
    segment: str,
    signature: str | None,
    accepted: bool,
    reason: str,
) -> None:
    connection.execute(
        """
        INSERT INTO submissions (
            task_id, miner_id, result_hash, segment_length, signature,
            accepted, reason, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (task_id, miner_id, result_hash, len(segment), signature, int(accepted), reason, utc_now()),
    )


def calculate_validator_reward_pool(params: dict[str, Any]) -> float:
    return reward_units_to_float(validator_reward_pool_units(params))


def calculate_miner_reward(params: dict[str, Any]) -> float:
    return reward_units_to_float(miner_reward_units(params))


def calculate_scientific_development_treasury_reward(params: dict[str, Any]) -> float:
    return reward_units_to_float(scientific_development_units_from_total(total_block_reward_units(params)))


def _validator_reward_total(account_ids: list[str | None]) -> float:
    normalized = [account_id for account_id in account_ids if account_id]
    if not normalized:
        return 0.0
    placeholders = ",".join("?" for _ in normalized)
    with get_connection() as connection:
        row = connection.execute(
            f"""
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM ledger_entries
            WHERE account_id IN ({placeholders})
            AND entry_type = 'validator_reward'
            """,
            tuple(normalized),
        ).fetchone()
    return round(float(row["total"]), 8)


def _approved_validator_ids_for_job(connection: Any, job_id: str) -> list[str]:
    rows = connection.execute(
        """
        SELECT validator_id
        FROM validation_votes
        WHERE job_id = ?
        AND approved = 1
        ORDER BY created_at ASC, id ASC
        """,
        (job_id,),
    ).fetchall()
    return [row["validator_id"] for row in rows]


def _apply_validator_rewards(
    connection: Any,
    *,
    job_id: str,
    block_height: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    validator_ids = _approved_validator_ids_for_job(connection, job_id)
    pool_units = validator_reward_pool_units(params)
    pool = reward_units_to_float(pool_units)
    if not validator_ids or pool_units <= 0:
        return {"pool": 0.0, "per_validator": 0.0, "validator_ids": []}

    per_validator_units = pool_units // len(validator_ids)
    distributed_units = 0
    reward_addresses: dict[str, str] = {}
    for index, validator_id in enumerate(validator_ids, start=1):
        amount_units = per_validator_units
        if index == len(validator_ids):
            amount_units = pool_units - distributed_units
        distributed_units += amount_units
        amount = reward_units_to_float(amount_units)
        reward_account, reward_account_type = _reward_account_for_validator(connection, validator_id)
        if reward_account_type == "wallet":
            reward_addresses[validator_id] = reward_account
        _apply_ledger_entry(
            connection,
            account_id=reward_account,
            account_type=reward_account_type,
            amount=amount,
            entry_type="validator_reward",
            block_height=block_height,
            related_id=job_id,
            description="additional validator reward",
        )

    return {
        "pool": pool,
        "per_validator": reward_units_to_float(per_validator_units),
        "validator_ids": validator_ids,
        "reward_addresses": reward_addresses,
    }


def _selected_validators_for_job(
    connection: Any,
    job: dict[str, Any],
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    eligible = _eligible_validator_rows(connection)
    required = _effective_required_validator_approvals(connection, params)
    pool_size = min(len(eligible), max(required, required * VALIDATOR_SELECTION_POOL_MULTIPLIER))
    scored: list[dict[str, Any]] = []
    for validator in eligible:
        metrics = _validator_selection_metrics(connection, validator)
        jitter = _selection_jitter(job["challenge_seed"], validator["validator_id"])
        scored.append(
            {
                "validator_id": validator["validator_id"],
                "selection_score": metrics["selection_score"],
                "selection_weight": round(metrics["selection_score"] + jitter, 8),
                "recent_validation_votes": metrics["recent_validation_votes"],
                "availability_score": metrics["availability_score"],
            }
        )

    scored.sort(key=lambda item: (-item["selection_weight"], item["validator_id"]))
    selected = scored[:pool_size]
    for index, item in enumerate(selected, start=1):
        item["selection_rank"] = index
    return selected


def _eligible_validator_rows(connection: Any) -> list[dict[str, Any]]:
    rows = connection.execute(
        f"""
        SELECT *
        FROM validators
        WHERE is_banned = 0
        AND enabled = 1
        AND online_status = 'online'
        AND sync_status != 'out_of_sync'
        AND COALESCE(node_id, '') != ''
        AND COALESCE(advertised_address, '') != ''
        AND protocol_version = ?
        AND {VALIDATOR_ELIGIBILITY_STAKE_FIELD} >= ?
        AND trust_score >= ?
        """,
        (PROTOCOL_VERSION, MIN_VALIDATOR_STAKE, VALIDATOR_MIN_TRUST_SCORE),
    ).fetchall()
    eligible: list[dict[str, Any]] = []
    now = utc_now_dt()
    for row in rows:
        validator = row_to_dict(row)
        cooldown_until = parse_iso(validator["cooldown_until"])
        if cooldown_until is not None and cooldown_until > now:
            continue
        eligible.append(validator)
    return eligible


def _validator_selection_metrics(connection: Any, validator: dict[str, Any]) -> dict[str, Any]:
    trust_score = max(0.0, min(1.0, float(validator.get("trust_score") or 0.0)))
    stake_locked = max(0.0, _validator_eligibility_stake(validator))
    stake_score = min(1.0, stake_locked / (MIN_VALIDATOR_STAKE * 2))

    last_seen_at = parse_iso(validator.get("last_seen_at"))
    availability_score = 0.5
    if last_seen_at is not None and last_seen_at >= utc_now_dt() - timedelta(seconds=VALIDATOR_AVAILABILITY_WINDOW_SECONDS):
        availability_score = 1.0

    recent_votes = int(
        connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM validation_votes
            WHERE validator_id = ?
            AND created_at >= ?
            """,
            (validator["validator_id"], iso_ago(VALIDATOR_ROTATION_WINDOW_SECONDS)),
        ).fetchone()["count"]
    )
    rotation_score = 1 / (1 + recent_votes)
    selection_score = (
        (trust_score * VALIDATOR_SELECTION_TRUST_WEIGHT)
        + (stake_score * VALIDATOR_SELECTION_STAKE_WEIGHT)
        + (availability_score * VALIDATOR_SELECTION_AVAILABILITY_WEIGHT)
        + (rotation_score * VALIDATOR_SELECTION_ROTATION_WEIGHT)
    )
    return {
        "selection_score": round(selection_score, 6),
        "selection_weight": round(selection_score, 6),
        "recent_validation_votes": recent_votes,
        "availability_score": round(availability_score, 6),
    }


def _selection_jitter(seed: str, validator_id: str) -> float:
    digest = sha256_text(canonical_json({"seed": seed, "validator_id": validator_id}))
    return (int(digest[:8], 16) / 0xFFFFFFFF) / 1_000_000


def _node_counts(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    eligible_validators = len(_eligible_validator_rows(connection))
    miners = connection.execute(
        """
        SELECT
            COUNT(*) AS total,
            COALESCE(SUM(CASE WHEN online_status = 'online' THEN 1 ELSE 0 END), 0) AS online,
            COALESCE(SUM(CASE WHEN online_status = 'stale' THEN 1 ELSE 0 END), 0) AS stale,
            COALESCE(SUM(CASE WHEN online_status = 'offline' THEN 1 ELSE 0 END), 0) AS offline,
            COALESCE(SUM(CASE WHEN enabled = 0 THEN 1 ELSE 0 END), 0) AS disabled
        FROM miners
        """
    ).fetchone()
    validators = connection.execute(
        """
        SELECT
            COUNT(*) AS total,
            COALESCE(SUM(CASE WHEN is_banned = 0 AND enabled = 1 THEN 1 ELSE 0 END), 0) AS active,
            COALESCE(SUM(CASE WHEN online_status = 'online' THEN 1 ELSE 0 END), 0) AS online,
            COALESCE(SUM(CASE WHEN online_status = 'stale' THEN 1 ELSE 0 END), 0) AS stale,
            COALESCE(SUM(CASE WHEN online_status = 'offline' THEN 1 ELSE 0 END), 0) AS offline,
            COALESCE(SUM(CASE WHEN sync_status = 'out_of_sync' THEN 1 ELSE 0 END), 0) AS out_of_sync
        FROM validators
        """
    ).fetchone()
    tasks = connection.execute(
        """
        SELECT
            COUNT(*) AS total,
            COALESCE(SUM(CASE WHEN status = 'assigned' THEN 1 ELSE 0 END), 0) AS assigned,
            COALESCE(SUM(CASE WHEN status = 'committed' THEN 1 ELSE 0 END), 0) AS committed,
            COALESCE(SUM(CASE WHEN status = 'revealed' THEN 1 ELSE 0 END), 0) AS revealed,
            COALESCE(SUM(CASE WHEN status = 'accepted' THEN 1 ELSE 0 END), 0) AS accepted,
            COALESCE(SUM(CASE WHEN status = 'expired' THEN 1 ELSE 0 END), 0) AS expired,
            COALESCE(SUM(CASE WHEN status = 'stale' THEN 1 ELSE 0 END), 0) AS stale
        FROM tasks
        """
    ).fetchone()
    validation_jobs = connection.execute(
        """
        SELECT
            COUNT(*) AS total,
            COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0) AS pending,
            COALESCE(SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END), 0) AS approved,
            COALESCE(SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END), 0) AS rejected,
            COALESCE(SUM(CASE WHEN status = 'expired' THEN 1 ELSE 0 END), 0) AS expired
        FROM validation_jobs
        """
    ).fetchone()
    return {
        "miners": int(miners["total"]),
        "online_miners": int(miners["online"]),
        "stale_miners": int(miners["stale"]),
        "offline_miners": int(miners["offline"]),
        "disabled_miners": int(miners["disabled"]),
        "validators": int(validators["total"]),
        "active_validators": int(validators["active"]),
        "online_validators": int(validators["online"]),
        "stale_validators": int(validators["stale"]),
        "offline_validators": int(validators["offline"]),
        "out_of_sync_validators": int(validators["out_of_sync"]),
        "eligible_validators": eligible_validators,
        "required_validator_approvals": _effective_required_validator_approvals(connection, params),
        "blocks": _latest_block_height(connection),
        "tasks": {
            "total": int(tasks["total"]),
            "assigned": int(tasks["assigned"]),
            "committed": int(tasks["committed"]),
            "revealed": int(tasks["revealed"]),
            "accepted": int(tasks["accepted"]),
            "expired": int(tasks["expired"]),
            "stale": int(tasks["stale"]),
        },
        "validation_jobs": {
            "total": int(validation_jobs["total"]),
            "pending": int(validation_jobs["pending"]),
            "approved": int(validation_jobs["approved"]),
            "rejected": int(validation_jobs["rejected"]),
            "expired": int(validation_jobs["expired"]),
        },
    }


def _basic_audit_health() -> dict[str, Any]:
    with get_connection() as connection:
        mismatches = _account_balance_mismatches(connection)
        ledger_total = _sum_query(connection, "SELECT COALESCE(SUM(amount), 0) AS total FROM ledger_entries")
        balance_total = _sum_query(connection, "SELECT COALESCE(SUM(balance), 0) AS total FROM balances")
    return {
        "valid": not mismatches and _money_equal(ledger_total, balance_total),
        "balance_mismatches": len(mismatches),
        "ledger_total": ledger_total,
        "balance_total": balance_total,
    }


def _event(
    *,
    event_id: str,
    event_type: str,
    title: str,
    message: str,
    severity: str,
    created_at: str,
    related_id: str | None = None,
    block_height: int | None = None,
    actor_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": event_id,
        "type": event_type,
        "title": title,
        "message": message,
        "severity": severity,
        "created_at": created_at,
        "related_id": related_id,
        "block_height": block_height,
        "actor_id": actor_id,
        "metadata": metadata or {},
    }


def _block_events(connection: Any, limit: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT height, miner_id, block_hash, reward, difficulty, timestamp
        FROM blocks
        ORDER BY height DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        _event(
            event_id=f"block:{row['height']}",
            event_type="block_accepted",
            title="Bloque aceptado",
            message=f"height {row['height']} minado por {row['miner_id']}",
            severity="info",
            created_at=row["timestamp"],
            related_id=row["block_hash"],
            block_height=int(row["height"]),
            actor_id=row["miner_id"],
            metadata={"reward": row["reward"], "difficulty": row["difficulty"]},
        )
        for row in rows
    ]


def _validator_vote_events(connection: Any, limit: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, job_id, task_id, validator_id, approved, reason, validation_ms, created_at
        FROM validation_votes
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        _event(
            event_id=f"vote:{row['id']}",
            event_type="validator_vote",
            title="Voto de validador",
            message=f"{row['validator_id']} {'aprobo' if row['approved'] else 'rechazo'} {row['job_id']}",
            severity="info" if row["approved"] else "warn",
            created_at=row["created_at"],
            related_id=row["job_id"],
            actor_id=row["validator_id"],
            metadata={
                "task_id": row["task_id"],
                "approved": bool(row["approved"]),
                "reason": row["reason"],
                "validation_ms": row["validation_ms"],
            },
        )
        for row in rows
    ]


def _faucet_events(connection: Any, limit: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, account_id, account_type, amount, created_at
        FROM ledger_entries
        WHERE entry_type = 'faucet_credit'
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        _event(
            event_id=f"faucet:{row['id']}",
            event_type="faucet_credit",
            title="Faucet",
            message=f"{row['amount']} acreditado a {row['account_id']}",
            severity="info",
            created_at=row["created_at"],
            related_id=row["account_id"],
            actor_id=row["account_id"],
            metadata={"account_type": row["account_type"], "amount": row["amount"]},
        )
        for row in rows
    ]


def _penalty_events(connection: Any, limit: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, miner_id, task_id, points, reason, created_at
        FROM penalties
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        _event(
            event_id=f"penalty:{row['id']}",
            event_type="penalty",
            title="Penalizacion",
            message=f"{row['points']} puntos a {row['miner_id']}",
            severity="bad",
            created_at=row["created_at"],
            related_id=row["task_id"],
            actor_id=row["miner_id"],
            metadata={"points": row["points"], "reason": row["reason"]},
        )
        for row in rows
    ]


def _retarget_events(connection: Any, limit: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, action, old_difficulty, new_difficulty, adjustment_factor, reason, created_at
        FROM retarget_events
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        _event(
            event_id=f"retarget:{row['id']}",
            event_type="retarget",
            title="Retarget",
            message=f"{row['action']} dificultad {row['old_difficulty']} -> {row['new_difficulty']}",
            severity="info",
            created_at=row["created_at"],
            related_id=str(row["id"]),
            metadata={
                "adjustment_factor": row["adjustment_factor"],
                "reason": row["reason"],
            },
        )
        for row in rows
    ]


def _retroactive_audit_events(connection: Any, limit: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, block_height, sample_count, passed, reason, automatic, reward, fraud_detected, created_at
        FROM retroactive_audits
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        _event(
            event_id=f"retro_audit:{row['id']}",
            event_type="retroactive_audit",
            title="Auditoria retroactiva automatica" if row["automatic"] else "Auditoria retroactiva",
            message=f"bloque {row['block_height']} con {row['sample_count']} samples: {row['reason']}",
            severity="info" if row["passed"] else "bad",
            created_at=row["created_at"],
            related_id=str(row["id"]),
            block_height=int(row["block_height"]),
            metadata={
                "sample_count": row["sample_count"],
                "passed": bool(row["passed"]),
                "automatic": bool(row["automatic"]),
                "reward": row["reward"],
                "fraud_detected": bool(row["fraud_detected"]),
            },
        )
        for row in rows
    ]


def _sum_query(connection: Any, query: str) -> float:
    row = connection.execute(query).fetchone()
    return round(float(row["total"]), 8)


def _rows_to_float_map(rows: list[Any], key_column: str, value_column: str) -> dict[str, float]:
    return {str(row[key_column]): round(float(row[value_column]), 8) for row in rows}


def _account_balance_mismatches(connection: Any) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            balances.account_id,
            balances.account_type,
            balances.balance AS balance,
            COALESCE(SUM(ledger_entries.amount), 0) AS ledger_balance
        FROM balances
        LEFT JOIN ledger_entries ON ledger_entries.account_id = balances.account_id
        GROUP BY balances.account_id, balances.account_type, balances.balance
        ORDER BY balances.account_id ASC
        """
    ).fetchall()
    mismatches: list[dict[str, Any]] = []
    for row in rows:
        balance = round(float(row["balance"]), 8)
        ledger_balance = round(float(row["ledger_balance"]), 8)
        if not _money_equal(balance, ledger_balance):
            mismatches.append(
                {
                    "account_id": row["account_id"],
                    "account_type": row["account_type"],
                    "balance": balance,
                    "ledger_balance": ledger_balance,
                    "delta": round(balance - ledger_balance, 8),
                }
            )
    return mismatches


def _money_equal(left: float, right: float) -> bool:
    return abs(round(float(left) - float(right), 8)) <= ECONOMIC_AUDIT_TOLERANCE


def _audit_equal(
    issues: list[dict[str, Any]],
    *,
    code: str,
    message: str,
    expected: float,
    actual: float,
) -> None:
    expected_value = round(float(expected), 8)
    actual_value = round(float(actual), 8)
    if _money_equal(expected_value, actual_value):
        return
    issues.append(
        {
            "code": code,
            "severity": "error",
            "message": message,
            "details": {
                "expected": expected_value,
                "actual": actual_value,
                "delta": round(actual_value - expected_value, 8),
            },
        }
    )


def get_balance_amount(account_id: str) -> float:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT balance FROM balances WHERE account_id = ?",
            (account_id,),
        ).fetchone()
    return 0.0 if row is None else round(float(row["balance"]), 8)


def _ensure_balance_account(connection: Any, account_id: str, account_type: str) -> None:
    connection.execute(
        """
        INSERT INTO balances (account_id, account_type, balance, balance_units, updated_at)
        VALUES (?, ?, 0, 0, ?)
        ON CONFLICT(account_id) DO NOTHING
        """,
        (account_id, account_type, utc_now()),
    )


def _reward_account_for_miner(connection: Any, miner_id: str) -> tuple[str, str]:
    row = connection.execute("SELECT reward_address FROM miners WHERE miner_id = ?", (miner_id,)).fetchone()
    reward_address = row["reward_address"] if row is not None else None
    if reward_address and is_valid_address(reward_address):
        return reward_address, "wallet"
    return miner_id, "miner"


def _reward_account_for_validator(connection: Any, validator_id: str) -> tuple[str, str]:
    row = connection.execute("SELECT reward_address FROM validators WHERE validator_id = ?", (validator_id,)).fetchone()
    reward_address = row["reward_address"] if row is not None else None
    if reward_address and is_valid_address(reward_address):
        return reward_address, "wallet"
    return validator_id, "validator"


def _apply_ledger_entry(
    connection: Any,
    *,
    account_id: str,
    account_type: str,
    amount: float,
    entry_type: str,
    block_height: int | None = None,
    related_id: str | None = None,
    description: str | None = None,
    timestamp: str | None = None,
) -> None:
    _ensure_balance_account(connection, account_id, account_type)
    current = connection.execute(
        "SELECT balance, balance_units FROM balances WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    amount_units = to_units(amount)
    current_units = units_from_db(current["balance"], current["balance_units"])
    balance_after_units = current_units + amount_units
    balance_after = units_to_float(balance_after_units)
    timestamp = timestamp or utc_now()
    connection.execute(
        "UPDATE balances SET balance = ?, balance_units = ?, updated_at = ? WHERE account_id = ?",
        (balance_after, balance_after_units, timestamp, account_id),
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


def _supply_snapshot(connection: Any) -> dict[str, float]:
    row = connection.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN account_id = ? THEN balance ELSE 0 END), 0) AS genesis_balance,
            COALESCE(SUM(CASE WHEN account_type = 'wallet' THEN balance ELSE 0 END), 0) AS wallet_balances,
            COALESCE(SUM(CASE WHEN account_type = 'miner' THEN balance ELSE 0 END), 0) AS miner_balances,
            COALESCE(SUM(CASE WHEN account_type = 'validator' THEN balance ELSE 0 END), 0) AS validator_balances,
            COALESCE(SUM(CASE WHEN account_type = 'audit' THEN balance ELSE 0 END), 0) AS audit_balances,
            COALESCE(SUM(CASE WHEN account_type IN ('science_reserve', 'science_worker') THEN balance ELSE 0 END), 0) AS science_balances,
            COALESCE(SUM(CASE WHEN account_type IN ('scientific_development_treasury', 'treasury_wallet') THEN balance ELSE 0 END), 0) AS scientific_development_balances
        FROM balances
        """,
        (GENESIS_ACCOUNT_ID,),
    ).fetchone()
    genesis_balance = round(float(row["genesis_balance"]), 8)
    wallet_balances = round(float(row["wallet_balances"]), 8)
    miner_balances = round(float(row["miner_balances"]), 8)
    validator_balances = round(float(row["validator_balances"]), 8)
    audit_balances = round(float(row["audit_balances"]), 8)
    science_balances = round(float(row["science_balances"]), 8)
    scientific_development_balances = round(float(row["scientific_development_balances"]), 8)
    return {
        "genesis_balance": genesis_balance,
        "wallet_balances": wallet_balances,
        "miner_balances": miner_balances,
        "validator_balances": validator_balances,
        "audit_balances": audit_balances,
        "science_balances": science_balances,
        "scientific_development_balances": scientific_development_balances,
        "circulating_supply": round(
            wallet_balances
            + miner_balances
            + validator_balances
            + audit_balances
            + science_balances
            + scientific_development_balances,
            8,
        ),
    }


def _miner_exists(connection: Any, miner_id: str) -> bool:
    row = connection.execute("SELECT 1 FROM miners WHERE miner_id = ?", (miner_id,)).fetchone()
    return row is not None


def _apply_penalty(
    connection: Any,
    miner_id: str,
    task_id: str,
    points: int,
    reason: str,
    force_cooldown_seconds: int | None = None,
) -> None:
    existing_penalty = connection.execute(
        """
        SELECT 1
        FROM penalties
        WHERE miner_id = ?
          AND task_id = ?
          AND reason = ?
        LIMIT 1
        """,
        (miner_id, task_id, reason),
    ).fetchone()
    if existing_penalty is not None:
        if force_cooldown_seconds is not None:
            connection.execute(
                "UPDATE miners SET cooldown_until = ? WHERE miner_id = ?",
                (iso_at(force_cooldown_seconds), miner_id),
            )
        _refresh_trust_score(connection, miner_id)
        return
    connection.execute(
        """
        INSERT INTO penalties (miner_id, task_id, points, reason, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (miner_id, task_id, points, reason, utc_now()),
    )
    total_penalty_points = connection.execute(
        "SELECT COALESCE(SUM(points), 0) AS points FROM penalties WHERE miner_id = ?",
        (miner_id,),
    ).fetchone()["points"]
    if total_penalty_points >= COOLDOWN_AFTER_REJECTIONS:
        connection.execute(
            "UPDATE miners SET cooldown_until = ? WHERE miner_id = ?",
            (iso_at(COOLDOWN_SECONDS), miner_id),
        )
    if force_cooldown_seconds is not None:
        connection.execute(
            "UPDATE miners SET cooldown_until = ? WHERE miner_id = ?",
            (iso_at(force_cooldown_seconds), miner_id),
        )
    _refresh_trust_score(connection, miner_id)


def _refresh_trust_score(connection: Any, miner_id: str) -> None:
    accepted = connection.execute(
        "SELECT COUNT(*) AS count FROM blocks WHERE miner_id = ?",
        (miner_id,),
    ).fetchone()["count"]
    penalty_points = connection.execute(
        "SELECT COALESCE(SUM(points), 0) AS points FROM penalties WHERE miner_id = ?",
        (miner_id,),
    ).fetchone()["points"]
    denominator = accepted + penalty_points
    trust_score = 1.0 if denominator == 0 else accepted / denominator
    connection.execute(
        "UPDATE miners SET trust_score = ? WHERE miner_id = ?",
        (round(trust_score, 4), miner_id),
    )


def _apply_validator_penalty(connection: Any, validator_id: str, reason: str) -> None:
    validator = connection.execute(
        "SELECT stake_locked, wallet_stake_locked FROM validators WHERE validator_id = ?",
        (validator_id,),
    ).fetchone()
    slash_amount = 0.0
    wallet_slash_amount = 0.0
    if validator is not None:
        slash_amount = min(float(validator["stake_locked"]), VALIDATOR_SLASH_INVALID_SIGNATURE)
        wallet_slash_amount = min(float(validator["wallet_stake_locked"] or 0), slash_amount)
    connection.execute(
        """
        UPDATE validators
        SET invalid_results = invalid_results + 1,
            stake_locked = MAX(0, stake_locked - ?),
            wallet_stake_locked = MAX(0, wallet_stake_locked - ?),
            slashed_amount = slashed_amount + ?,
            last_seen_at = ?
        WHERE validator_id = ?
        """,
        (slash_amount, wallet_slash_amount, slash_amount, utc_now(), validator_id),
    )
    row = connection.execute(
        "SELECT invalid_results FROM validators WHERE validator_id = ?",
        (validator_id,),
    ).fetchone()
    invalid_results = int(row["invalid_results"])
    if invalid_results >= VALIDATOR_COOLDOWN_AFTER_INVALID_RESULTS:
        connection.execute(
            "UPDATE validators SET cooldown_until = ? WHERE validator_id = ?",
            (iso_at(VALIDATOR_COOLDOWN_SECONDS), validator_id),
        )
    if invalid_results >= VALIDATOR_BAN_AFTER_INVALID_RESULTS:
        connection.execute(
            "UPDATE validators SET is_banned = 1 WHERE validator_id = ?",
            (validator_id,),
        )
    _refresh_validator_trust_score(connection, validator_id)


def _refresh_validator_trust_score(connection: Any, validator_id: str) -> None:
    row = connection.execute(
        """
        SELECT accepted_jobs, rejected_jobs, invalid_results
        FROM validators
        WHERE validator_id = ?
        """,
        (validator_id,),
    ).fetchone()
    if row is None:
        return
    completed = int(row["accepted_jobs"]) + int(row["rejected_jobs"])
    invalid_weight = int(row["invalid_results"]) * VALIDATOR_PENALTY_INVALID_SIGNATURE
    trust_score = (completed + 1) / (completed + 1 + invalid_weight)
    connection.execute(
        "UPDATE validators SET trust_score = ? WHERE validator_id = ?",
        (round(trust_score, 4), validator_id),
    )


def _record_validator_completed_vote(
    connection: Any,
    validator_id: str,
    approved: bool,
    validation_ms: int,
) -> None:
    column = "accepted_jobs" if approved else "rejected_jobs"
    connection.execute(
        f"""
        UPDATE validators
        SET {column} = {column} + 1,
            total_validation_ms = total_validation_ms + ?,
            last_seen_at = ?
        WHERE validator_id = ?
        """,
        (validation_ms, utc_now(), validator_id),
    )
    _refresh_validator_trust_score(connection, validator_id)


def _validation_vote_counts(connection: Any, job_id: str) -> dict[str, int]:
    row = connection.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN approved = 1 THEN 1 ELSE 0 END), 0) AS approvals,
            COALESCE(SUM(CASE WHEN approved = 0 THEN 1 ELSE 0 END), 0) AS rejections
        FROM validation_votes
        WHERE job_id = ?
        """,
        (job_id,),
    ).fetchone()
    return {"approvals": int(row["approvals"]), "rejections": int(row["rejections"])}


def _run_retroactive_audit_in_connection(
    connection: Any,
    *,
    block_height: int | None,
    sample_multiplier: int,
    automatic: bool,
    trigger_height: int | None = None,
    trigger_timestamp: str | None = None,
) -> dict[str, Any]:
    if block_height is None:
        if automatic:
            row = _select_scheduled_retroactive_audit_block(connection, trigger_height)
        else:
            row = connection.execute(
                "SELECT * FROM blocks ORDER BY RANDOM() LIMIT 1"
            ).fetchone()
    else:
        row = connection.execute(
            "SELECT * FROM blocks WHERE height = ?",
            (block_height,),
        ).fetchone()
    block = _decode_block(row_to_dict(row))
    if block is None:
        raise MiningError(404, "block not found for retroactive audit")

    params = _protocol_params_for_block(connection, block)
    base_samples = int(params["sample_count"])
    sample_count = min(block["range_end"] - block["range_start"] + 1, base_samples * sample_multiplier)
    timestamp = trigger_timestamp if automatic and trigger_timestamp else utc_now()
    if automatic:
        audit_seed = _scheduled_retroactive_audit_seed(block, sample_count, trigger_height, timestamp)
    else:
        audit_seed = sha256_text(
            canonical_json(
                {
                    "audit_id": uuid.uuid4().hex,
                    "automatic": automatic,
                    "block_hash": block["block_hash"],
                    "block_height": block["height"],
                    "created_at": timestamp,
                    "sample_count": sample_count,
                }
            )
        )
    existing = connection.execute(
        "SELECT * FROM retroactive_audits WHERE audit_seed = ?",
        (audit_seed,),
    ).fetchone()
    if existing is not None:
        return row_to_dict(existing)
    segment = calculate_pi_segment(block["range_start"], block["range_end"], block["algorithm"])
    actual_hash = hash_result(segment, block["range_start"], block["range_end"], block["algorithm"])
    requested_samples = _build_challenge_samples(
        block["range_start"],
        block["range_end"],
        audit_seed,
        sample_count,
    )
    samples = [
        {
            "position": sample["position"],
            "digit": segment[sample["position"] - block["range_start"]],
        }
        for sample in requested_samples
    ]
    passed = actual_hash == block["result_hash"]
    reason = "accepted" if passed else "fraud detected: result_hash mismatch"
    reward = _apply_retroactive_audit_reward(connection, block, params, audit_seed) if automatic else 0.0
    if not passed:
        if block.get("fraudulent"):
            reason = "fraud confirmed: result_hash mismatch"
        else:
            _mark_block_fraudulent(connection, block, reason, timestamp)
            _apply_fraud_penalties(connection, block, reason)

    cursor = connection.execute(
        """
        INSERT INTO retroactive_audits (
            block_height, block_hash, audit_seed, sample_count, samples,
            expected_hash, actual_hash, passed, reason, automatic, reward,
            reward_account_id, fraud_detected, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            block["height"],
            block["block_hash"],
            audit_seed,
            sample_count,
            json.dumps(samples),
            block["result_hash"],
            actual_hash,
            int(passed),
            reason,
            int(automatic),
            reward,
            RETROACTIVE_AUDIT_REWARD_ACCOUNT_ID if reward > 0 else None,
            int(not passed),
            timestamp,
        ),
    )
    return row_to_dict(
        connection.execute(
            "SELECT * FROM retroactive_audits WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    )


def _apply_retroactive_audit_reward(
    connection: Any,
    block: dict[str, Any],
    params: dict[str, Any],
    audit_seed: str,
) -> float:
    reward = round(calculate_reward(params) * RETROACTIVE_AUDIT_REWARD_PERCENT_OF_BLOCK, 8)
    if reward <= 0:
        return 0.0
    _apply_ledger_entry(
        connection,
        account_id=RETROACTIVE_AUDIT_REWARD_ACCOUNT_ID,
        account_type="audit",
        amount=reward,
        entry_type="retroactive_audit_reward",
        block_height=block["height"],
        related_id=audit_seed,
        description="scheduled retroactive audit reward",
    )
    return reward


def _select_scheduled_retroactive_audit_block(connection: Any, trigger_height: int | None) -> Any:
    max_height = int(trigger_height or 0)
    if max_height <= 0:
        latest = connection.execute("SELECT COALESCE(MAX(height), 0) AS height FROM blocks").fetchone()
        max_height = int((latest["height"] if latest else 0) or 0)
    count_row = connection.execute(
        "SELECT COUNT(*) AS count FROM blocks WHERE height <= ?",
        (max_height,),
    ).fetchone()
    count = int((count_row["count"] if count_row else 0) or 0)
    if count <= 0:
        return None
    trigger = connection.execute(
        "SELECT block_hash, previous_hash, timestamp FROM blocks WHERE height = ?",
        (max_height,),
    ).fetchone()
    seed = sha256_text(
        canonical_json(
            {
                "chain_id": CHAIN_ID,
                "network_id": NETWORK_ID,
                "trigger_block_hash": trigger["block_hash"] if trigger else None,
                "trigger_height": max_height,
                "trigger_previous_hash": trigger["previous_hash"] if trigger else None,
                "type": "scheduled_retroactive_audit_selection",
            }
        )
    )
    offset = int(seed[:16], 16) % count
    return connection.execute(
        """
        SELECT *
        FROM blocks
        WHERE height <= ?
        ORDER BY height ASC
        LIMIT 1 OFFSET ?
        """,
        (max_height, offset),
    ).fetchone()


def _scheduled_retroactive_audit_seed(
    block: dict[str, Any],
    sample_count: int,
    trigger_height: int | None,
    trigger_timestamp: str,
) -> str:
    return sha256_text(
        canonical_json(
            {
                "automatic": True,
                "audited_block_hash": block["block_hash"],
                "audited_block_height": block["height"],
                "chain_id": CHAIN_ID,
                "created_at": trigger_timestamp,
                "network_id": NETWORK_ID,
                "sample_count": sample_count,
                "trigger_height": int(trigger_height or 0),
                "type": "scheduled_retroactive_audit",
            }
        )
    )


def _mark_block_fraudulent(connection: Any, block: dict[str, Any], reason: str, detected_at: str) -> None:
    connection.execute(
        """
        UPDATE blocks
        SET fraudulent = 1,
            fraud_reason = ?,
            fraud_detected_at = ?
        WHERE height = ?
        """,
        (reason, detected_at, block["height"]),
    )


def _apply_fraud_penalties(connection: Any, block: dict[str, Any], reason: str) -> None:
    _apply_penalty(
        connection,
        block["miner_id"],
        block["task_id"],
        FRAUD_MINER_PENALTY_POINTS,
        reason,
        force_cooldown_seconds=FRAUD_COOLDOWN_SECONDS,
    )
    validator_ids = connection.execute(
        """
        SELECT DISTINCT validator_id
        FROM validation_votes
        WHERE task_id = ?
        AND approved = 1
        """,
        (block["task_id"],),
    ).fetchall()
    for row in validator_ids:
        _apply_validator_fraud_penalty(connection, row["validator_id"], reason)


def _apply_validator_fraud_penalty(connection: Any, validator_id: str, reason: str) -> None:
    validator = connection.execute(
        "SELECT stake_locked, wallet_stake_locked FROM validators WHERE validator_id = ?",
        (validator_id,),
    ).fetchone()
    slash_amount = 0.0
    wallet_slash_amount = 0.0
    if validator is not None:
        slash_amount = min(float(validator["stake_locked"]), VALIDATOR_SLASH_INVALID_SIGNATURE)
        wallet_slash_amount = min(float(validator["wallet_stake_locked"] or 0), slash_amount)
    connection.execute(
        """
        UPDATE validators
        SET invalid_results = invalid_results + ?,
            stake_locked = MAX(0, stake_locked - ?),
            wallet_stake_locked = MAX(0, wallet_stake_locked - ?),
            slashed_amount = slashed_amount + ?,
            cooldown_until = ?,
            last_seen_at = ?
        WHERE validator_id = ?
        """,
        (
            FRAUD_VALIDATOR_INVALID_RESULTS,
            slash_amount,
            wallet_slash_amount,
            slash_amount,
            iso_at(FRAUD_COOLDOWN_SECONDS),
            utc_now(),
            validator_id,
        ),
    )
    invalid_results = int(
        connection.execute(
            "SELECT invalid_results FROM validators WHERE validator_id = ?",
            (validator_id,),
        ).fetchone()["invalid_results"]
    )
    if invalid_results >= VALIDATOR_BAN_AFTER_INVALID_RESULTS:
        connection.execute(
            "UPDATE validators SET is_banned = 1 WHERE validator_id = ?",
            (validator_id,),
        )
    _refresh_validator_trust_score(connection, validator_id)


def _maybe_run_scheduled_retroactive_audit(connection: Any, current_height: int) -> dict[str, Any] | None:
    if current_height <= 0 or current_height % RETROACTIVE_AUDIT_INTERVAL_BLOCKS != 0:
        return None
    trigger = connection.execute(
        "SELECT timestamp FROM blocks WHERE height = ?",
        (current_height,),
    ).fetchone()
    return _run_retroactive_audit_in_connection(
        connection,
        block_height=None,
        sample_multiplier=RETROACTIVE_AUDIT_SAMPLE_MULTIPLIER,
        automatic=True,
        trigger_height=current_height,
        trigger_timestamp=trigger["timestamp"] if trigger else None,
    )


def _decode_block(block: dict[str, Any] | None) -> dict[str, Any] | None:
    if block is None:
        return None
    block["samples"] = json.loads(block["samples"])
    block["tx_hashes"] = json.loads(block.get("tx_hashes") or "[]")
    block["tx_count"] = int(block.get("tx_count") or 0)
    block["fee_reward"] = round(float(block.get("fee_reward") or 0), 8)
    block["fraudulent"] = bool(block.get("fraudulent", 0))
    block["parent_hash"] = block.get("parent_hash") or block.get("previous_hash")
    block["branch_id"] = block.get("branch_id") or "canonical"
    block["branch_status"] = block.get("branch_status") or "canonical"
    block["ancestor_height"] = (
        int(block["ancestor_height"])
        if block.get("ancestor_height") is not None
        else max(0, int(block.get("height") or 0) - 1)
    )
    block["ancestor_hash"] = block.get("ancestor_hash") or block.get("previous_hash")
    block["selected_at"] = block.get("selected_at") or block.get("timestamp")
    return block


def _decode_finality_certificate(certificate: dict[str, Any] | None) -> dict[str, Any] | None:
    if certificate is None:
        return None
    payload = json.loads(certificate.get("payload_json") or "{}")
    votes = json.loads(certificate.get("votes_json") or "[]")
    return {
        "block_height": int(certificate["block_height"]),
        "block_hash": certificate["block_hash"],
        "task_id": certificate["task_id"],
        "job_id": certificate["job_id"],
        "miner_id": certificate["miner_id"],
        "network_id": certificate["network_id"],
        "chain_id": payload.get("chain_id", certificate["chain_id"]),
        "protocol_version": certificate["protocol_version"],
        "protocol_params_id": certificate.get("protocol_params_id"),
        "required_approvals": int(certificate["required_approvals"]),
        "approval_count": int(certificate["approval_count"]),
        "certificate_hash": certificate["certificate_hash"],
        "payload": payload,
        "votes": votes,
        "created_at": certificate["created_at"],
    }


def _decode_retroactive_audit(audit: dict[str, Any] | None) -> dict[str, Any] | None:
    if audit is None:
        return None
    audit["samples"] = json.loads(audit["samples"])
    audit["passed"] = bool(audit["passed"])
    audit["automatic"] = bool(audit.get("automatic", 0))
    audit["fraud_detected"] = bool(audit.get("fraud_detected", 0))
    return audit


def _block_payload(block: dict[str, Any], include_protocol: bool) -> dict[str, Any]:
    payload = {
        "algorithm": block["algorithm"],
        "height": block["height"],
        "miner_id": block["miner_id"],
        "previous_hash": block["previous_hash"],
        "range_end": block["range_end"],
        "range_start": block["range_start"],
        "result_hash": block["result_hash"],
        "reward": block["reward"],
        "samples": block["samples"],
        "timestamp": block["timestamp"],
    }
    if block.get("difficulty") is not None:
        payload["difficulty"] = block["difficulty"]
    if block.get("protocol_params_id") is not None:
        payload["protocol_params_id"] = block["protocol_params_id"]
    if block.get("total_block_ms") is not None:
        payload["total_block_ms"] = int(block["total_block_ms"])
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


def _commit_rejected(reason: str) -> dict[str, Any]:
    return {
        "accepted": False,
        "status": "rejected",
        "message": reason,
        "challenge_seed": None,
        "samples": [],
    }


def _commit_stale(reason: str) -> dict[str, Any]:
    return {
        "accepted": False,
        "status": "stale",
        "message": reason,
        "challenge_seed": None,
        "samples": [],
    }


def _latest_block_hash(connection: Any) -> str:
    latest = connection.execute("SELECT block_hash FROM blocks ORDER BY height DESC LIMIT 1").fetchone()
    return GENESIS_HASH if latest is None else latest["block_hash"]


def _latest_block_height(connection: Any) -> int:
    latest = connection.execute("SELECT COALESCE(MAX(height), 0) AS height FROM blocks").fetchone()
    return int(latest["height"])


def _last_retarget_height(connection: Any) -> int:
    latest = connection.execute(
        "SELECT COALESCE(MAX(epoch_end_height), 0) AS height FROM retarget_events"
    ).fetchone()
    return int(latest["height"])


def _active_protocol_params(connection: Any) -> dict[str, Any]:
    params = row_to_dict(
        connection.execute(
            "SELECT * FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    )
    if params is None:
        raise MiningError(500, "active protocol params not found")
    params["active"] = bool(params["active"])
    params["RETARGET_MAX_PI_POSITION"] = _resolve_RETARGET_MAX_PI_POSITION(params)
    return params


def _protocol_params_by_id(connection: Any, protocol_params_id: int) -> dict[str, Any] | None:
    params = row_to_dict(
        connection.execute(
            "SELECT * FROM protocol_params WHERE id = ?",
            (protocol_params_id,),
        ).fetchone()
    )
    if params is not None:
        params["active"] = bool(params["active"])
        params["RETARGET_MAX_PI_POSITION"] = _resolve_RETARGET_MAX_PI_POSITION(params)
    return params


def _resolve_RETARGET_MAX_PI_POSITION(params: dict[str, Any]) -> int:
    configured_value = None
    for key, value in params.items():
        if str(key).upper() == "RETARGET_MAX_PI_POSITION":
            configured_value = value
            break
    try:
        value = int(configured_value or RETARGET_MAX_PI_POSITION)
    except (TypeError, ValueError):
        value = RETARGET_MAX_PI_POSITION
    return max(1, value)


def _protocol_params_for_task(connection: Any, task: dict[str, Any]) -> dict[str, Any]:
    protocol_params_id = task.get("protocol_params_id")
    if protocol_params_id is not None:
        params = _protocol_params_by_id(connection, protocol_params_id)
        if params is not None:
            return params
    return _active_protocol_params(connection)


def _protocol_params_for_block(connection: Any, block: dict[str, Any]) -> dict[str, Any]:
    protocol_params_id = block.get("protocol_params_id")
    if protocol_params_id is not None:
        params = _protocol_params_by_id(connection, protocol_params_id)
        if params is not None:
            return params
    return _active_protocol_params(connection)


def _assign_pseudo_random_range(
    connection: Any,
    miner_id: str,
    task_id: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    window = _range_assignment_window(connection, params)
    min_start = window["min_start"]
    max_start = window["max_start"]
    RETARGET_MAX_PI_POSITION_value = window["RETARGET_MAX_PI_POSITION"]
    if max_start < min_start:
        raise MiningError(
            503,
            f"no assignable range available below RETARGET_MAX_PI_POSITION={RETARGET_MAX_PI_POSITION_value}",
        )

    previous_hash = _latest_block_hash(connection)
    task_counter = connection.execute("SELECT COUNT(*) AS count FROM tasks").fetchone()["count"] + 1
    candidate_count = max_start - min_start + 1

    for nonce in range(params["range_assignment_max_attempts"]):
        assignment_seed = sha256_text(
            canonical_json(
                {
                    "algorithm": params["algorithm"],
                    "effective_max_pi_position": window["effective_max_pi_position"],
                    "min_start": min_start,
                    "miner_id": miner_id,
                    "nonce": nonce,
                    "previous_hash": previous_hash,
                    "segment_size": params["segment_size"],
                    "task_counter": task_counter,
                    "task_id": task_id,
                }
            )
        )
        range_start = min_start + (int(assignment_seed, 16) % candidate_count)
        range_end = range_start + params["segment_size"] - 1
        if range_end > RETARGET_MAX_PI_POSITION_value:
            continue
        if _range_is_assignable(connection, range_start, range_end, params["algorithm"]):
            return {
                "range_start": range_start,
                "range_end": range_end,
                "assignment_seed": assignment_seed,
            }

    fallback_seed = sha256_text(
        canonical_json(
            {
                "algorithm": params["algorithm"],
                "effective_max_pi_position": window["effective_max_pi_position"],
                "fallback": True,
                "min_start": min_start,
                "miner_id": miner_id,
                "previous_hash": previous_hash,
                "segment_size": params["segment_size"],
                "task_counter": task_counter,
                "task_id": task_id,
            }
        )
    )
    for range_start in range(min_start, max_start + 1):
        range_end = range_start + params["segment_size"] - 1
        if range_end > RETARGET_MAX_PI_POSITION_value:
            continue
        if _range_is_assignable(connection, range_start, range_end, params["algorithm"]):
            return {
                "range_start": range_start,
                "range_end": range_end,
                "assignment_seed": fallback_seed,
            }

    raise MiningError(
        503,
        f"could not assign a non-overlapping range below RETARGET_MAX_PI_POSITION={RETARGET_MAX_PI_POSITION_value}",
    )


def _range_assignment_window(connection: Any, params: dict[str, Any]) -> dict[str, int]:
    segment_size = int(params["segment_size"])
    configured_limit = max(int(params["max_pi_position"]), segment_size)
    RETARGET_MAX_PI_POSITION_value = _resolve_RETARGET_MAX_PI_POSITION(params)
    window_size = max(int(RANGE_START_WINDOW_SIZE), segment_size)
    lookahead_window = max(window_size, segment_size * int(RANGE_WINDOW_LOOKAHEAD_MULTIPLIER))
    frontier = _accepted_range_frontier(connection, params["algorithm"])
    latest_height = _latest_block_height(connection)
    frontier_window_index = max(0, (max(frontier, 1) - 1) // window_size)
    first_window_index = _first_unretired_range_window(
        connection,
        params["algorithm"],
        window_size,
        latest_height,
        frontier_window_index,
    )
    min_start = first_window_index * window_size + 1
    current_window_end = (first_window_index + 1) * window_size
    effective_max_pi_position = min(
        RETARGET_MAX_PI_POSITION_value,
        max(configured_limit, current_window_end, frontier + lookahead_window),
    )
    max_start = effective_max_pi_position - segment_size + 1
    return {
        "RETARGET_MAX_PI_POSITION": int(RETARGET_MAX_PI_POSITION_value),
        "effective_max_pi_position": int(effective_max_pi_position),
        "frontier": int(frontier),
        "lookahead_window": int(lookahead_window),
        "max_start": int(max_start),
        "min_start": int(min_start),
        "window_index": int(first_window_index),
        "window_size": int(window_size),
    }


def _accepted_range_frontier(connection: Any, algorithm: str) -> int:
    row = connection.execute(
        """
        SELECT MAX(range_end) AS frontier
        FROM (
            SELECT range_end FROM blocks WHERE algorithm = ?
            UNION ALL
            SELECT range_end FROM tasks WHERE algorithm = ? AND status = 'accepted'
        )
        """,
        (algorithm, algorithm),
    ).fetchone()
    return int(row["frontier"] or 0)


def _latest_block_height(connection: Any) -> int:
    row = connection.execute("SELECT MAX(height) AS height FROM blocks").fetchone()
    return int(row["height"] or 0)


def _first_unretired_range_window(
    connection: Any,
    algorithm: str,
    window_size: int,
    latest_height: int,
    frontier_window_index: int,
) -> int:
    threshold = max(0.0, min(1.0, float(RANGE_WINDOW_RETIRE_OCCUPANCY)))
    max_age_blocks = max(1, int(RANGE_WINDOW_MAX_AGE_BLOCKS))
    for window_index in range(frontier_window_index + 1):
        window_start = window_index * window_size + 1
        window_end = (window_index + 1) * window_size
        used = connection.execute(
            """
            SELECT COUNT(DISTINCT range_start) AS count
            FROM tasks
            WHERE algorithm = ?
              AND status IN ('assigned', 'committed', 'revealed', 'accepted')
              AND range_start BETWEEN ? AND ?
            """,
            (algorithm, window_start, window_end),
        ).fetchone()["count"]
        occupancy = float(used or 0) / float(window_size)
        opened = connection.execute(
            """
            SELECT MIN(height) AS height
            FROM blocks
            WHERE algorithm = ? AND range_start BETWEEN ? AND ?
            """,
            (algorithm, window_start, window_end),
        ).fetchone()
        first_height = opened["height"] if opened else None
        age = 0 if first_height is None else max(0, latest_height - int(first_height) + 1)
        if occupancy < threshold and age < max_age_blocks:
            return window_index
    return frontier_window_index + 1


def _range_is_assignable(connection: Any, range_start: int, range_end: int, algorithm: str) -> bool:
    return (
        not _range_start_is_protected(connection, range_start, algorithm)
        and not _range_overlaps_active_task(connection, range_start, range_end, algorithm)
    )


def _range_start_is_protected(connection: Any, range_start: int, algorithm: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM tasks
        WHERE algorithm = ?
        AND status IN ('assigned', 'committed', 'revealed', 'accepted')
        AND range_start = ?
        LIMIT 1
        """,
        (algorithm, range_start),
    ).fetchone()
    return row is not None


def _range_start_is_accepted(connection: Any, range_start: int, algorithm: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM (
            SELECT range_start, algorithm
            FROM tasks
            WHERE status = 'accepted'
            UNION ALL
            SELECT range_start, algorithm
            FROM blocks
        )
        WHERE algorithm = ?
          AND range_start = ?
        LIMIT 1
        """,
        (algorithm, range_start),
    ).fetchone()
    return row is not None


def _range_overlaps_active_task(connection: Any, range_start: int, range_end: int, algorithm: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM tasks
        WHERE algorithm = ?
        AND status IN ('assigned', 'committed', 'revealed')
        AND range_start <= ?
        AND range_end >= ?
        LIMIT 1
        """,
        (algorithm, range_end, range_start),
    ).fetchone()
    return row is not None


def _build_challenge_samples(
    range_start: int,
    range_end: int,
    challenge_seed: str,
    requested_sample_count: int,
) -> list[dict[str, int]]:
    length = range_end - range_start + 1
    sample_count = min(requested_sample_count, length)
    randomizer = random.Random(challenge_seed)
    offsets = sorted(randomizer.sample(range(length), sample_count))
    return [{"position": range_start + offset} for offset in offsets]


def _retarget_epoch_rows(connection: Any, last_height: int) -> list[Any]:
    return connection.execute(
        """
        SELECT *
        FROM (
        SELECT
            blocks.height,
            blocks.range_start,
            blocks.range_end,
            COALESCE(blocks.total_task_ms, blocks.total_block_ms, ?) AS total_task_ms,
            COALESCE(blocks.validation_ms, 0) AS validation_ms,
            COALESCE(blocks.total_block_ms, blocks.total_task_ms, ?) AS total_block_ms,
            COALESCE(blocks.difficulty, protocol_params.difficulty, 0) AS difficulty,
            COALESCE(protocol_params.segment_size, blocks.range_end - blocks.range_start + 1) AS segment_size,
            COALESCE(protocol_params.sample_count, 8) AS sample_count
        FROM blocks
        LEFT JOIN protocol_params ON protocol_params.id = blocks.protocol_params_id
        WHERE blocks.height > ?
          AND COALESCE(blocks.total_block_ms, blocks.total_task_ms, 0) > 0
        ORDER BY blocks.height DESC
        LIMIT ?
        )
        ORDER BY height ASC
        """,
        (
            RETARGET_TARGET_BLOCK_MS,
            RETARGET_TARGET_BLOCK_MS,
            last_height,
            RETARGET_WINDOW_BLOCKS,
        ),
    ).fetchall()


def _average_epoch_ms(epoch_rows: list[Any]) -> float:
    return round(sum(float(row["total_block_ms"]) for row in epoch_rows) / len(epoch_rows), 2)


def _retarget_protocol_params_from_history(
    params: dict[str, Any],
    history: list[dict[str, Any]],
    next_range_start: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    return DifficultyService.calculate_next_protocol_params(history, params, next_range_start)


def _upsert_difficulty_bucket_metrics(connection: Any, history: list[dict[str, Any]], updated_at: str) -> None:
    for metric in DifficultyService.bucket_metrics(history):
        connection.execute(
            """
            INSERT INTO difficulty_bucket_metrics (
                bucket_id, range_start_min, range_start_max, avg_task_ms,
                avg_validation_ms, avg_total_block_ms, avg_segment_size,
                avg_sample_count, avg_difficulty, samples_seen, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(bucket_id) DO UPDATE SET
                range_start_min = excluded.range_start_min,
                range_start_max = excluded.range_start_max,
                avg_task_ms = excluded.avg_task_ms,
                avg_validation_ms = excluded.avg_validation_ms,
                avg_total_block_ms = excluded.avg_total_block_ms,
                avg_segment_size = excluded.avg_segment_size,
                avg_sample_count = excluded.avg_sample_count,
                avg_difficulty = excluded.avg_difficulty,
                samples_seen = excluded.samples_seen,
                updated_at = excluded.updated_at
            """,
            (
                metric["bucket_id"],
                metric["range_start_min"],
                metric["range_start_max"],
                metric["avg_task_ms"],
                metric["avg_validation_ms"],
                metric["avg_total_block_ms"],
                metric["avg_segment_size"],
                metric["avg_sample_count"],
                metric["avg_difficulty"],
                metric["samples_seen"],
                updated_at,
            ),
        )


def _retarget_preview(connection: Any, force: bool = False) -> dict[str, Any]:
    current_height = _latest_block_height(connection)
    last_height = _last_retarget_height(connection)
    params = _active_protocol_params(connection)
    epoch_rows = _retarget_epoch_rows(connection, last_height)
    epoch_count = len(epoch_rows)
    average_block_ms = _average_epoch_ms(epoch_rows) if epoch_rows else None
    required_epoch_blocks = max(RETARGET_EPOCH_BLOCKS, RETARGET_WINDOW_BLOCKS)
    ready = bool(epoch_rows) and (force or epoch_count >= required_epoch_blocks)
    next_params = dict(params)
    meta = {
        "action": "wait",
        "reason": "not enough accepted blocks for retarget",
        "adjustment_ratio": 1.0,
        "adjustment_factor": 1.0,
    }
    if ready:
        history = [row_to_dict(row) for row in epoch_rows]
        assignment_window = _range_assignment_window(connection, params)
        next_range_start_for_preview = min(
            int(assignment_window["frontier"]) + 1,
            int(assignment_window["RETARGET_MAX_PI_POSITION"]),
        )
        next_params, meta = _retarget_protocol_params_from_history(params, history, next_range_start_for_preview)

    status = "ready" if ready else "waiting"
    if not epoch_rows:
        status = "empty"

    return {
        "ready": ready,
        "status": status,
        "message": "retarget can be applied" if ready else "not enough accepted blocks for retarget",
        "current_height": current_height,
        "last_retarget_height": last_height,
        "epoch_start_height": int(epoch_rows[0]["height"]) if epoch_rows else None,
        "epoch_end_height": int(epoch_rows[-1]["height"]) if epoch_rows else None,
        "epoch_block_count": epoch_count,
        "epoch_blocks_required": required_epoch_blocks,
        "blocks_until_ready": max(0, required_epoch_blocks - epoch_count),
        "average_block_ms": average_block_ms,
        "target_block_ms": RETARGET_TARGET_BLOCK_MS,
        "tolerance": RETARGET_TOLERANCE,
        "min_difficulty": RETARGET_MIN_DIFFICULTY,
        "max_difficulty": RETARGET_MAX_DIFFICULTY,
        "action": meta["action"],
        "reason": meta["reason"],
        "adjustment_factor": meta["adjustment_factor"],
        "avg_task_ms": meta.get("avg_task_ms"),
        "avg_validation_ms": meta.get("avg_validation_ms"),
        "mining_ratio": meta.get("mining_ratio"),
        "validation_ratio": meta.get("validation_ratio"),
        "old_difficulty": calculate_difficulty(params),
        "new_difficulty": calculate_difficulty(next_params),
        "current_protocol": _protocol_payload(params),
        "proposed_protocol": _protocol_payload(next_params),
        "_current_params": params,
        "_proposed_params": next_params,
        "_meta": meta,
    }


def _public_retarget_preview(preview: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in preview.items() if not key.startswith("_")}


def _maybe_retarget_after_block(connection: Any, current_height: int, force: bool = False) -> dict[str, Any] | None:
    if current_height <= 0:
        return None

    preview = _retarget_preview(connection, force=force)
    if not preview["ready"]:
        return None

    epoch_rows = _retarget_epoch_rows(connection, preview["last_retarget_height"])
    history = [row_to_dict(row) for row in epoch_rows]
    params = preview["_current_params"]
    next_params = preview["_proposed_params"]
    meta = preview["_meta"]
    average_block_ms = preview["average_block_ms"]
    if not epoch_rows:
        return None
    old_difficulty = calculate_difficulty(params)
    new_difficulty = calculate_difficulty(next_params)
    previous_params_id = params["id"]
    new_params_id = previous_params_id
    created_at = utc_now()
    _upsert_difficulty_bucket_metrics(connection, history, created_at)

    if meta["action"] != "keep":
        connection.execute("UPDATE protocol_params SET active = 0 WHERE active = 1")
        source_window = json.dumps(
            {
                "epoch_start_height": int(epoch_rows[0]["height"]),
                "epoch_end_height": int(epoch_rows[-1]["height"]),
                "epoch_block_count": len(epoch_rows),
                "bucket": meta.get("bucket"),
                "avg_total_block_ms": meta.get("avg_total_block_ms"),
                "avg_task_ms": meta.get("avg_task_ms"),
                "avg_validation_ms": meta.get("avg_validation_ms"),
                "mining_ratio": meta.get("mining_ratio"),
                "validation_ratio": meta.get("validation_ratio"),
                "old_task_expiration_seconds": meta.get("old_task_expiration_seconds"),
                "new_task_expiration_seconds": meta.get("new_task_expiration_seconds"),
                "task_expiration_changed": meta.get("task_expiration_changed"),
            },
            sort_keys=True,
        )
        cursor = connection.execute(
            """
            INSERT INTO protocol_params (
                protocol_version, algorithm, validation_mode, required_validator_approvals,
                range_assignment_mode, max_pi_position, range_assignment_max_attempts,
                segment_size, sample_count, task_expiration_seconds,
                max_active_tasks_per_miner, base_reward, difficulty,
                target_block_time_ms, RETARGET_MAX_PI_POSITION,
                retarget_reason, retarget_source_window,
                retarget_source_details, previous_protocol_params_id, active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                PROTOCOL_VERSION,
                next_params["algorithm"],
                next_params["validation_mode"],
                next_params["required_validator_approvals"],
                next_params["range_assignment_mode"],
                next_params["max_pi_position"],
                next_params["range_assignment_max_attempts"],
                next_params["segment_size"],
                next_params["sample_count"],
                next_params["task_expiration_seconds"],
                next_params["max_active_tasks_per_miner"],
                next_params["base_reward"],
                next_params["difficulty"],
                RETARGET_TARGET_BLOCK_MS,
                next_params.get("RETARGET_MAX_PI_POSITION") or RETARGET_MAX_PI_POSITION,
                meta["reason"],
                len(epoch_rows),
                source_window,
                previous_params_id,
            ),
        )
        new_params_id = cursor.lastrowid

    epoch_start_height = int(epoch_rows[0]["height"])
    epoch_end_height = int(epoch_rows[-1]["height"])
    cursor = connection.execute(
        """
        INSERT INTO retarget_events (
            previous_protocol_params_id, new_protocol_params_id, epoch_start_height,
            epoch_end_height, epoch_block_count, average_block_ms, target_block_ms,
            old_difficulty, new_difficulty, adjustment_factor, action, reason, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            previous_params_id,
            new_params_id,
            epoch_start_height,
            epoch_end_height,
            len(epoch_rows),
            round(average_block_ms, 2),
            RETARGET_TARGET_BLOCK_MS,
            old_difficulty,
            new_difficulty,
            meta["adjustment_factor"],
            meta["action"],
            meta["reason"],
            created_at,
        ),
    )
    logger.info(
        "difficulty retarget action=%s old_difficulty=%s new_difficulty=%s old_segment_size=%s new_segment_size=%s old_sample_count=%s new_sample_count=%s avg_total_block_ms=%s avg_task_ms=%s avg_validation_ms=%s mining_ratio=%s validation_ratio=%s bucket=%s reason=%s",
        meta["action"],
        old_difficulty,
        new_difficulty,
        meta.get("old_segment_size"),
        meta.get("new_segment_size"),
        meta.get("old_sample_count"),
        meta.get("new_sample_count"),
        meta.get("avg_total_block_ms"),
        meta.get("avg_task_ms"),
        meta.get("avg_validation_ms"),
        meta.get("mining_ratio"),
        meta.get("validation_ratio"),
        meta.get("bucket"),
        meta.get("reason"),
    )
    return row_to_dict(connection.execute("SELECT * FROM retarget_events WHERE id = ?", (cursor.lastrowid,)).fetchone())


def _validate_revealed_samples(
    task: dict[str, Any],
    commitment: dict[str, Any],
    requested_samples: list[dict[str, Any]],
    revealed_samples: list[dict[str, Any]],
) -> dict[str, Any]:
    revealed_by_position = {sample["position"]: sample for sample in revealed_samples}
    accepted_samples: list[dict[str, Any]] = []

    for requested in requested_samples:
        position = requested["position"]
        revealed = revealed_by_position.get(position)
        if revealed is None:
            return {"accepted": False, "reason": f"missing sample at position {position}", "samples": accepted_samples}

        digit = str(revealed["digit"]).upper()
        expected_digit = calculate_pi_segment(position, position, task["algorithm"])
        if digit != expected_digit:
            return {"accepted": False, "reason": f"sample digit mismatch at position {position}", "samples": accepted_samples}

        proof = revealed.get("proof", [])
        if not verify_merkle_proof(position, digit, proof, commitment["merkle_root"]):
            return {"accepted": False, "reason": f"invalid Merkle proof at position {position}", "samples": accepted_samples}

        accepted_samples.append({"position": position, "digit": digit, "proof": proof})

    return {"accepted": True, "reason": "accepted", "samples": accepted_samples}


def _accept_block_in_connection(
    *,
    connection: Any,
    task: dict[str, Any],
    miner_id: str,
    result_hash: str,
    merkle_root: str | None,
    samples: list[dict[str, Any]],
    signature: str,
    submission_reason: str,
    validation_ms: int | None = None,
    params: dict[str, Any] | None = None,
    validation_job_id: str | None = None,
) -> dict[str, Any]:
    existing = row_to_dict(
        connection.execute("SELECT * FROM blocks WHERE task_id = ?", (task["task_id"],)).fetchone()
    )
    if existing is not None:
        block = _decode_block(existing)
        block["already_finalized"] = True
        logger.info(
            "validation finalization skipped existing block task_id=%s block_height=%s",
            task["task_id"],
            block.get("height"),
        )
        return block

    if params is None:
        params = _protocol_params_for_task(connection, task)
    anchor = _block_anchor_for_task(connection, task)
    _ensure_block_anchor_is_local_tip(anchor)
    _ensure_competitive_task_can_finalize(connection, task, params, require_current_round=False)
    total_block_reward = calculate_reward(params)
    reward = calculate_miner_reward(params)
    difficulty = calculate_difficulty(params)
    next_height = int(anchor["height"])
    previous_hash = str(anchor["previous_hash"])
    timestamp = _block_timestamp_for_validation_job(connection, validation_job_id)
    total_task_ms = _validation_block_elapsed_ms(task, timestamp)
    total_block_ms = total_task_ms
    block_transactions = load_snapshot_transactions(connection, task["task_id"])
    tx_commitment = transaction_commitment(block_transactions)
    if (
        tx_commitment["tx_merkle_root"] != (task.get("tx_merkle_root") or "")
        or tx_commitment["selected_tx_hashes_hash"] != (task.get("selected_tx_hashes_hash") or selected_tx_hashes_hash([]))
        or int(tx_commitment["tx_count"]) != int(task.get("tx_count") or 0)
        or int(tx_commitment["tx_fee_total_units"]) != int(task.get("tx_fee_total_units") or 0)
    ):
        raise TransactionExecutionError(f"tx snapshot mismatch for task {task['task_id']}")
    miner_reward_account, miner_reward_account_type = _reward_account_for_miner(connection, miner_id)

    block_payload = {
        "algorithm": task["algorithm"],
        "height": next_height,
        "miner_id": miner_id,
        "previous_hash": previous_hash,
        "range_end": task["range_end"],
        "range_start": task["range_start"],
        "result_hash": result_hash,
        "reward": reward,
        "difficulty": difficulty,
        "samples": samples,
        "timestamp": timestamp,
        "protocol_params_id": params["id"],
        "protocol_version": params["protocol_version"],
        "total_block_ms": total_block_ms,
        "validation_mode": params["validation_mode"],
    }
    if miner_reward_account_type == "wallet":
        block_payload["miner_reward_address"] = miner_reward_account
    if merkle_root:
        block_payload["merkle_root"] = merkle_root
    if tx_commitment["tx_count"]:
        block_payload["tx_merkle_root"] = tx_commitment["tx_merkle_root"]
        block_payload["tx_count"] = tx_commitment["tx_count"]
        block_payload["tx_hashes"] = tx_commitment["tx_hashes"]
        block_payload["fee_reward"] = tx_commitment["fee_reward"]
    block_hash = hash_block(block_payload)

    connection.execute(
        """
        INSERT INTO blocks (
            height, previous_hash, miner_id, range_start, range_end, algorithm,
            result_hash, merkle_root, samples, timestamp, block_hash, reward, reward_units, tx_merkle_root,
            tx_count, tx_hashes, fee_reward, fee_reward_units, miner_reward_address, difficulty, task_id, protocol_params_id,
            protocol_version, validation_mode, total_task_ms, total_block_ms, validation_ms
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            next_height,
            previous_hash,
            miner_id,
            task["range_start"],
            task["range_end"],
            task["algorithm"],
            result_hash,
            merkle_root,
            json.dumps(samples),
            timestamp,
            block_hash,
            reward,
            to_units(reward),
            tx_commitment["tx_merkle_root"],
            tx_commitment["tx_count"],
            json.dumps(tx_commitment["tx_hashes"], sort_keys=True),
            tx_commitment["fee_reward"],
            to_units(tx_commitment["fee_reward"]),
            block_payload.get("miner_reward_address"),
            difficulty,
            task["task_id"],
            params["id"],
            params["protocol_version"],
            params["validation_mode"],
            total_task_ms,
            total_block_ms,
            validation_ms,
        ),
    )
    connection.execute(
        "UPDATE tasks SET status = 'accepted', submitted_at = ? WHERE task_id = ?",
        (timestamp, task["task_id"]),
    )
    _record_submission(connection, task["task_id"], miner_id, result_hash, "", signature, True, submission_reason)
    reward_maturity = record_miner_block_reward(
        connection,
        miner_id=miner_id,
        account_id=miner_reward_account,
        account_type=miner_reward_account_type,
        block_height=next_height,
        amount=reward,
        reason="block accepted",
        related_id=task["task_id"],
        description="miner block reward",
        timestamp=timestamp,
    )
    tx_execution = apply_block_transactions(
        connection,
        miner_id=miner_reward_account,
        miner_account_type=miner_reward_account_type,
        block_height=next_height,
        transactions=block_transactions,
        timestamp=timestamp,
    )
    record_science_reserve_for_block(connection, next_height, total_block_reward)
    record_scientific_development_treasury_for_block(connection, next_height, total_block_reward)
    validator_reward = {"pool": 0.0, "per_validator": 0.0, "validator_ids": []}
    if validation_job_id is not None:
        validator_reward = _apply_validator_rewards(
            connection,
            job_id=validation_job_id,
            block_height=next_height,
            params=params,
        )
    competitive_round = _close_competitive_round_after_block(connection, task, next_height, timestamp)
    matured_rewards = mature_block_rewards(connection, current_height=next_height, timestamp=timestamp)
    _refresh_trust_score(connection, miner_id)
    _maybe_retarget_after_block(connection, next_height)
    _maybe_run_scheduled_retroactive_audit(connection, next_height)
    state_root = update_block_state_root(connection, next_height, timestamp)
    checkpoint = maybe_create_checkpoint_in_connection(connection, next_height)

    block = {
        "height": next_height,
        "previous_hash": previous_hash,
        "miner_id": miner_id,
        "range_start": task["range_start"],
        "range_end": task["range_end"],
        "algorithm": task["algorithm"],
        "result_hash": result_hash,
        "merkle_root": merkle_root,
        "samples": samples,
        "timestamp": timestamp,
        "block_hash": block_hash,
        "reward": reward,
        "miner_reward_address": block_payload.get("miner_reward_address"),
        "tx_merkle_root": tx_commitment["tx_merkle_root"],
        "tx_count": tx_commitment["tx_count"],
        "tx_hashes": tx_commitment["tx_hashes"],
        "fee_reward": tx_commitment["fee_reward"],
        "state_root": state_root,
        "checkpoint": checkpoint,
        "transactions": block_transactions,
        "transaction_execution": tx_execution,
        "validator_reward": validator_reward,
        "reward_maturity": {**reward_maturity, **matured_rewards},
        "competitive_round": competitive_round,
        "difficulty": difficulty,
        "protocol_params_id": params["id"],
        "protocol_version": params["protocol_version"],
        "validation_mode": params["validation_mode"],
        "total_task_ms": total_task_ms,
        "total_block_ms": total_block_ms,
        "validation_ms": validation_ms,
        "fraudulent": False,
        "fraud_reason": None,
        "fraud_detected_at": None,
    }
    block["consensus_proposal"] = record_local_block_proposal(connection, block, proposer_node_id=miner_id)
    return block


def blocks_or_zero(value: Any) -> float:
    return 0.0 if value is None else float(value)
