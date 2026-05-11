import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.crypto import canonical_json, hash_block, hash_result, sha256_text
from app.core.difficulty import calculate_difficulty, calculate_reward, propose_retarget_params
from app.core.merkle import verify_merkle_proof
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
    COOLDOWN_AFTER_REJECTIONS,
    COOLDOWN_SECONDS,
    FAUCET_ALLOWED_NETWORKS,
    FAUCET_DEFAULT_AMOUNT,
    FAUCET_MAX_AMOUNT,
    FAUCET_RATE_LIMIT_MAX_REQUESTS,
    FAUCET_RATE_LIMIT_WINDOW_SECONDS,
    GENESIS_ACCOUNT_ID,
    GENESIS_SUPPLY,
    MIN_VALIDATOR_STAKE,
    NETWORK_ID,
    PENALTY_DUPLICATE,
    PENALTY_INVALID_RESULT,
    PENALTY_INVALID_SIGNATURE,
    PROJECT_NAME,
    PROTOCOL_VERSION,
    RETARGET_EPOCH_BLOCKS,
    RETARGET_TARGET_BLOCK_MS,
    RETARGET_TOLERANCE,
    TASK_RATE_LIMIT_MAX_ASSIGNMENTS,
    TASK_RATE_LIMIT_WINDOW_SECONDS,
    VALIDATOR_BAN_AFTER_INVALID_RESULTS,
    VALIDATOR_AVAILABILITY_WINDOW_SECONDS,
    VALIDATOR_COOLDOWN_AFTER_INVALID_RESULTS,
    VALIDATOR_COOLDOWN_SECONDS,
    VALIDATOR_MIN_TRUST_SCORE,
    VALIDATOR_PENALTY_INVALID_SIGNATURE,
    VALIDATOR_ROTATION_WINDOW_SECONDS,
    VALIDATOR_REWARD_PERCENT_OF_BLOCK,
    VALIDATOR_SELECTION_AVAILABILITY_WEIGHT,
    VALIDATOR_SELECTION_MODE,
    VALIDATOR_SELECTION_POOL_MULTIPLIER,
    VALIDATOR_SELECTION_ROTATION_WEIGHT,
    VALIDATOR_SELECTION_STAKE_WEIGHT,
    VALIDATOR_SELECTION_TRUST_WEIGHT,
    VALIDATOR_SLASH_INVALID_SIGNATURE,
    VALIDATION_MODE,
)
from app.db.database import get_connection, row_to_dict
from validator.proof import validate_submission


GENESIS_HASH = "0" * 64
ECONOMIC_AUDIT_TOLERANCE = 0.000001


class MiningError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail


def utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def utc_now() -> str:
    return utc_now_dt().isoformat()


def iso_at(seconds_from_now: int) -> str:
    return (utc_now_dt() + timedelta(seconds=seconds_from_now)).isoformat()


def iso_ago(seconds_before_now: int) -> str:
    return (utc_now_dt() - timedelta(seconds=seconds_before_now)).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


NODE_STARTED_AT = utc_now_dt()


def register_miner(name: str, public_key: str | None = None) -> dict[str, Any]:
    if public_key is None:
        raise MiningError(400, "public_key is required")
    try:
        validate_public_key(public_key)
    except (RuntimeError, ValueError) as exc:
        raise MiningError(400, str(exc)) from exc

    miner_id = f"miner_{uuid.uuid4().hex[:16]}"
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO miners (miner_id, name, public_key, registered_at) VALUES (?, ?, ?, ?)",
            (miner_id, name, public_key, utc_now()),
        )
        _ensure_balance_account(connection, miner_id, "miner")
        row = connection.execute("SELECT * FROM miners WHERE miner_id = ?", (miner_id,)).fetchone()
    return enrich_miner(row_to_dict(row))


def get_miner(miner_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM miners WHERE miner_id = ?", (miner_id,)).fetchone()
    miner = row_to_dict(row)
    if miner is None:
        return None
    return enrich_miner(miner)


def register_validator(name: str, public_key: str) -> dict[str, Any]:
    try:
        validate_public_key(public_key)
    except (RuntimeError, ValueError) as exc:
        raise MiningError(400, str(exc)) from exc

    validator_id = f"validator_{uuid.uuid4().hex[:16]}"
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO validators (validator_id, name, public_key, registered_at) VALUES (?, ?, ?, ?)",
            (validator_id, name, public_key, utc_now()),
        )
        _ensure_balance_account(connection, validator_id, "validator")
        _apply_ledger_entry(
            connection,
            account_id=GENESIS_ACCOUNT_ID,
            account_type="genesis",
            amount=-MIN_VALIDATOR_STAKE,
            entry_type="validator_stake_grant",
            related_id=validator_id,
            description="simulated validator stake funded from genesis",
        )
        _apply_ledger_entry(
            connection,
            account_id=validator_id,
            account_type="validator",
            amount=MIN_VALIDATOR_STAKE,
            entry_type="validator_stake_lock",
            related_id=validator_id,
            description="simulated validator stake locked",
        )
        row = connection.execute("SELECT * FROM validators WHERE validator_id = ?", (validator_id,)).fetchone()
    return enrich_validator(row_to_dict(row))


def get_validator(validator_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM validators WHERE validator_id = ?", (validator_id,)).fetchone()
    return enrich_validator(row_to_dict(row))


def get_validators(limit: int = 100, eligible_only: bool = False) -> list[dict[str, Any]]:
    where = ""
    params: tuple[Any, ...] = ()
    if eligible_only:
        where = "WHERE is_banned = 0 AND stake_locked >= ? AND trust_score >= ?"
        params = (MIN_VALIDATOR_STAKE, VALIDATOR_MIN_TRUST_SCORE)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT * FROM validators
            {where}
            ORDER BY trust_score DESC, stake_locked DESC, accepted_jobs DESC, registered_at ASC
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
    validator["balance"] = get_balance_amount(validator["validator_id"])
    validator["is_banned"] = bool(validator["is_banned"])
    validator["total_rewards"] = _validator_reward_total(validator["validator_id"])
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
    miner["balance"] = get_balance_amount(miner["miner_id"])
    miner["is_banned"] = bool(miner["is_banned"])
    return miner


def create_next_task(miner_id: str) -> dict[str, Any] | None:
    started = now_perf()
    with get_connection() as connection:
        _expire_assigned_tasks(connection)
        miner = row_to_dict(connection.execute("SELECT * FROM miners WHERE miner_id = ?", (miner_id,)).fetchone())
        if miner is None:
            return None

        if miner["is_banned"]:
            raise MiningError(403, "miner is banned")

        cooldown_until = parse_iso(miner["cooldown_until"])
        if cooldown_until is not None and cooldown_until > utc_now_dt():
            raise MiningError(429, f"miner is in cooldown until {miner['cooldown_until']}")

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
            return row_to_dict(active_task)

        params = _active_protocol_params(connection)
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

        task_id = f"task_{uuid.uuid4().hex[:16]}"
        assignment = _assign_pseudo_random_range(connection, miner_id, task_id, params)
        assignment_ms = elapsed_ms(started)
        now = utc_now()
        expires_at = iso_at(params["task_expiration_seconds"])

        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                assignment_seed, assignment_mode, assignment_ms, protocol_params_id,
                created_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?, 'assigned', ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                miner_id,
                assignment["range_start"],
                assignment["range_end"],
                params["algorithm"],
                assignment["assignment_seed"],
                params["range_assignment_mode"],
                assignment_ms,
                params["id"],
                now,
                expires_at,
            ),
        )
        row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    return row_to_dict(row)


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

        params = _protocol_params_for_task(connection, task)
        reward = calculate_reward(params)
        difficulty = calculate_difficulty(params)
        latest_block = connection.execute(
            "SELECT height, block_hash FROM blocks ORDER BY height DESC LIMIT 1"
        ).fetchone()
        next_height = 1 if latest_block is None else latest_block["height"] + 1
        previous_hash = GENESIS_HASH if latest_block is None else latest_block["block_hash"]
        timestamp = utc_now()

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
            "validation_mode": params["validation_mode"],
        }
        block_hash = hash_block(block_payload)

        connection.execute(
            """
            INSERT INTO blocks (
                height, previous_hash, miner_id, range_start, range_end, algorithm,
                result_hash, samples, timestamp, block_hash, reward, difficulty, task_id,
                protocol_params_id, protocol_version, validation_mode
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                difficulty,
                task_id,
                params["id"],
                params["protocol_version"],
                params["validation_mode"],
            ),
        )
        connection.execute(
            "UPDATE tasks SET status = 'accepted', submitted_at = ? WHERE task_id = ?",
            (timestamp, task_id),
        )
        _record_submission(connection, task_id, miner_id, result_hash, segment, signature, True, "accepted")
        connection.execute(
            """
            INSERT INTO rewards (miner_id, block_height, amount, reason, created_at)
            VALUES (?, ?, ?, 'block accepted', ?)
            """,
            (miner_id, next_height, reward, timestamp),
        )
        _apply_ledger_entry(
            connection,
            account_id=miner_id,
            account_type="miner",
            amount=reward,
            entry_type="block_reward",
            block_height=next_height,
            related_id=task_id,
            description="miner block reward",
        )
        _refresh_trust_score(connection, miner_id)
        _maybe_retarget_after_block(connection, next_height)

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
            "difficulty": difficulty,
            "protocol_params_id": params["id"],
            "protocol_version": params["protocol_version"],
            "validation_mode": params["validation_mode"],
        }

    return {
        "accepted": True,
        "status": "accepted",
        "message": "block accepted",
        "block": block,
        "validation": validation_payload,
    }


def commit_task(
    task_id: str,
    miner_id: str,
    result_hash: str,
    merkle_root: str,
    signature: str,
    signed_at: str,
    compute_ms: int | None = None,
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
        if task["status"] == "committed":
            existing = row_to_dict(connection.execute("SELECT * FROM commitments WHERE task_id = ?", (task_id,)).fetchone())
            if existing is not None:
                return {
                    "accepted": True,
                    "status": "committed",
                    "message": "task already committed",
                    "challenge_seed": existing["challenge_seed"],
                    "samples": json.loads(existing["samples"]),
                }
        if task["status"] != "assigned":
            return _commit_rejected(f"task is not active: {task['status']}")

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
        )
        try:
            signature_valid = verify_payload_signature(miner["public_key"], payload, signature)
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
        params = _active_protocol_params(connection)
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
                samples, signature, signed_at, commit_ms, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                miner_id,
                result_hash,
                merkle_root,
                challenge_seed,
                json.dumps(samples),
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

        payload = build_reveal_signature_payload(
            task_id=task_id,
            miner_id=miner_id,
            merkle_root=commitment["merkle_root"],
            challenge_seed=commitment["challenge_seed"],
            signed_at=signed_at,
        )
        try:
            signature_valid = verify_payload_signature(miner["public_key"], payload, signature)
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
            connection.execute(
                """
                INSERT INTO validation_jobs (
                    job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
                    samples, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    job_id,
                    task_id,
                    miner_id,
                    commitment["result_hash"],
                    commitment["merkle_root"],
                    commitment["challenge_seed"],
                    json.dumps(revealed_samples),
                    utc_now(),
                ),
            )
            connection.execute("UPDATE tasks SET status = 'revealed' WHERE task_id = ?", (task_id,))
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


def get_validation_job(validator_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        validator = row_to_dict(connection.execute("SELECT * FROM validators WHERE validator_id = ?", (validator_id,)).fetchone())
        if validator is None:
            return None
        if validator["is_banned"]:
            raise MiningError(403, "validator is banned")
        cooldown_until = parse_iso(validator["cooldown_until"])
        if cooldown_until is not None and cooldown_until > utc_now_dt():
            raise MiningError(429, f"validator is in cooldown until {validator['cooldown_until']}")
        if float(validator["stake_locked"]) < MIN_VALIDATOR_STAKE:
            raise MiningError(403, "validator stake is below the minimum required")
        if float(validator["trust_score"]) < VALIDATOR_MIN_TRUST_SCORE:
            raise MiningError(403, "validator trust score is below the minimum required")
        connection.execute(
            "UPDATE validators SET last_seen_at = ? WHERE validator_id = ?",
            (utc_now(), validator_id),
        )

        candidate_rows = connection.execute(
            """
            SELECT validation_jobs.*, tasks.range_start, tasks.range_end, tasks.algorithm
                 , tasks.protocol_params_id
            FROM validation_jobs
            JOIN tasks ON tasks.task_id = validation_jobs.task_id
            WHERE validation_jobs.status = 'pending'
            AND NOT EXISTS (
                SELECT 1
                FROM validation_votes
                WHERE validation_votes.job_id = validation_jobs.job_id
                AND validation_votes.validator_id = ?
            )
            ORDER BY (
                SELECT COUNT(*)
                FROM validation_votes
                WHERE validation_votes.job_id = validation_jobs.job_id
            ) ASC, validation_jobs.created_at ASC
            LIMIT 20
            """,
            (validator_id,),
        ).fetchall()

        job = None
        selection_meta = None
        for candidate_row in candidate_rows:
            candidate = row_to_dict(candidate_row)
            params = _protocol_params_for_task(connection, candidate)
            selected = _selected_validators_for_job(connection, candidate, params)
            match = next((item for item in selected if item["validator_id"] == validator_id), None)
            if match is not None:
                job = candidate
                selection_meta = match
                break

        if job is None:
            return None
        job["assigned_validator_id"] = validator_id
        job["selection_score"] = selection_meta["selection_score"] if selection_meta else None
        job["selection_rank"] = selection_meta["selection_rank"] if selection_meta else None
        counts = _validation_vote_counts(connection, job["job_id"])
        params = _protocol_params_for_task(connection, job)

    job["samples"] = json.loads(job["samples"])
    job["approvals"] = counts["approvals"]
    job["rejections"] = counts["rejections"]
    job["required_approvals"] = params["required_validator_approvals"]
    job["required_rejections"] = params["required_validator_approvals"]
    return job


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
            return {
                "accepted": False,
                "status": job["status"],
                "message": "validation job already completed",
                "block": None,
                "approvals": counts["approvals"],
                "rejections": counts["rejections"],
                "required_approvals": params["required_validator_approvals"],
                "required_rejections": params["required_validator_approvals"],
            }
        existing_vote = connection.execute(
            "SELECT 1 FROM validation_votes WHERE job_id = ? AND validator_id = ?",
            (job_id, validator_id),
        ).fetchone()
        if existing_vote is not None:
            counts = _validation_vote_counts(connection, job_id)
            params = _protocol_params_for_task(connection, job)
            return {
                "accepted": False,
                "status": "already_voted",
                "message": "validator already submitted a vote for this job",
                "block": None,
                "approvals": counts["approvals"],
                "rejections": counts["rejections"],
                "required_approvals": params["required_validator_approvals"],
                "required_rejections": params["required_validator_approvals"],
            }

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
        params = _protocol_params_for_task(connection, task)
        connection.execute(
            """
            INSERT INTO validation_votes (
                job_id, task_id, validator_id, approved, reason, signature,
                signed_at, validation_ms, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                utc_now(),
            ),
        )
        _record_validator_completed_vote(connection, validator_id, approved, validation_ms)
        counts = _validation_vote_counts(connection, job_id)
        required = params["required_validator_approvals"]

        if approved and counts["approvals"] >= required:
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
            connection.execute(
                """
                UPDATE validation_jobs
                SET status = 'approved', assigned_validator_id = ?, result_reason = ?,
                    validator_signature = ?, validation_ms = ?, completed_at = ?
                WHERE job_id = ?
                """,
                (validator_id, reason, signature, validation_ms, utc_now(), job_id),
            )
            return {
                "accepted": True,
                "status": "approved",
                "message": "block accepted by validator quorum",
                "block": block,
                "approvals": counts["approvals"],
                "rejections": counts["rejections"],
                "required_approvals": required,
                "required_rejections": required,
            }

        if not approved and counts["rejections"] >= required:
            connection.execute(
                "UPDATE tasks SET status = 'rejected', submitted_at = ? WHERE task_id = ?",
                (utc_now(), job["task_id"]),
            )
            connection.execute(
                """
                UPDATE validation_jobs
                SET status = 'rejected', assigned_validator_id = ?, result_reason = ?,
                    validator_signature = ?, validation_ms = ?, completed_at = ?
                WHERE job_id = ?
                """,
                (validator_id, reason, signature, validation_ms, utc_now(), job_id),
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


def get_blocks() -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute("SELECT * FROM blocks ORDER BY height ASC").fetchall()
    return [_decode_block(row_to_dict(row)) for row in rows]


def get_block(height: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM blocks WHERE height = ?", (height,)).fetchone()
    return _decode_block(row_to_dict(row))


def get_stats() -> dict[str, Any]:
    with get_connection() as connection:
        _expire_assigned_tasks(connection)
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
        "total_minted_rewards": round(float(blocks["rewards"]) + float(validator_rewards), 8),
        "circulating_supply": supply["circulating_supply"],
        "genesis_balance": supply["genesis_balance"],
        "latest_block_hash": GENESIS_HASH if latest is None else latest["block_hash"],
    }


def get_balance(account_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM balances WHERE account_id = ?", (account_id,)).fetchone()
    return row_to_dict(row)


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
    if NETWORK_ID not in FAUCET_ALLOWED_NETWORKS:
        raise MiningError(403, f"faucet is disabled on network '{NETWORK_ID}'")
    if account_type not in {"miner", "validator"}:
        raise MiningError(400, "account_type must be miner or validator")

    faucet_amount = FAUCET_DEFAULT_AMOUNT if amount is None else round(float(amount), 8)
    if faucet_amount <= 0:
        raise MiningError(400, "faucet amount must be positive")
    if faucet_amount > FAUCET_MAX_AMOUNT:
        raise MiningError(400, f"faucet amount exceeds max {FAUCET_MAX_AMOUNT}")

    table_name = "miners" if account_type == "miner" else "validators"
    id_column = "miner_id" if account_type == "miner" else "validator_id"

    with get_connection() as connection:
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
            description=f"local testnet faucet debit for {account_type}",
        )
        _apply_ledger_entry(
            connection,
            account_id=account_id,
            account_type=account_type,
            amount=faucet_amount,
            entry_type="faucet_credit",
            related_id=GENESIS_ACCOUNT_ID,
            description="local testnet faucet credit",
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
        "message": "local testnet faucet credit applied",
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
            """
            SELECT
                COUNT(*) AS validator_count,
                COALESCE(SUM(stake_locked), 0) AS locked_stake,
                COALESCE(SUM(slashed_amount), 0) AS slashed_stake,
                COALESCE(SUM(CASE WHEN is_banned = 0 AND stake_locked >= ? AND trust_score >= ? THEN 1 ELSE 0 END), 0) AS eligible_count
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

        block_rewards = _sum_query(connection, "SELECT COALESCE(SUM(reward), 0) AS total FROM blocks")
        validator_rewards = _sum_query(
            connection,
            "SELECT COALESCE(SUM(amount), 0) AS total FROM ledger_entries WHERE entry_type = 'validator_reward'",
        )
        reward_rows = connection.execute(
            "SELECT COUNT(*) AS count, COALESCE(SUM(amount), 0) AS total FROM rewards"
        ).fetchone()
        reward_count = int(reward_rows["count"])
        rewards_table_total = round(float(reward_rows["total"]), 8)
        ledger_block_rewards = _sum_query(
            connection,
            "SELECT COALESCE(SUM(amount), 0) AS total FROM ledger_entries WHERE entry_type = 'block_reward'",
        )
        accepted_blocks = int(connection.execute("SELECT COUNT(*) AS count FROM blocks").fetchone()["count"])

        validators = connection.execute(
            """
            SELECT
                COUNT(*) AS count,
                COALESCE(SUM(stake_locked), 0) AS stake_locked,
                COALESCE(SUM(slashed_amount), 0) AS slashed_amount
            FROM validators
            """
        ).fetchone()
        validator_stake_locked = round(float(validators["stake_locked"]), 8)
        validator_slashed_amount = round(float(validators["slashed_amount"]), 8)
        ledger_validator_stake_locks = _sum_query(
            connection,
            "SELECT COALESCE(SUM(amount), 0) AS total FROM ledger_entries WHERE entry_type = 'validator_stake_lock'",
        )
        ledger_validator_slashes = _sum_query(
            connection,
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM ledger_entries
            WHERE entry_type = 'validator_slash'
            AND account_type = 'validator'
            """,
        )
        ledger_genesis_slashes = _sum_query(
            connection,
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM ledger_entries
            WHERE entry_type = 'validator_slash'
            AND account_type = 'genesis'
            """,
        )

    expected_total_balances = round(GENESIS_SUPPLY + block_rewards + validator_rewards, 8)
    expected_ledger_total = expected_total_balances
    expected_validator_stake_locked = round(ledger_validator_stake_locks + ledger_validator_slashes, 8)

    _audit_equal(
        issues,
        code="total_balances_mismatch",
        message="sum(balances) must equal genesis supply plus minted miner and validator rewards",
        expected=expected_total_balances,
        actual=actual_total_balances,
    )
    _audit_equal(
        issues,
        code="ledger_total_mismatch",
        message="sum(ledger_entries.amount) must equal genesis supply plus minted miner and validator rewards",
        expected=expected_ledger_total,
        actual=ledger_total_amount,
    )
    _audit_equal(
        issues,
        code="rewards_table_mismatch",
        message="rewards table total must equal accepted block rewards",
        expected=block_rewards,
        actual=rewards_table_total,
    )
    _audit_equal(
        issues,
        code="ledger_block_rewards_mismatch",
        message="block_reward ledger entries must equal accepted block rewards",
        expected=block_rewards,
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

    if accepted_blocks != reward_count:
        issues.append(
            {
                "code": "reward_count_mismatch",
                "severity": "error",
                "message": "accepted block count must match reward row count",
                "details": {"accepted_blocks": accepted_blocks, "reward_rows": reward_count},
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
            "validator_reward_total": validator_rewards,
            "total_minted_rewards": round(block_rewards + validator_rewards, 8),
            "reward_rows": reward_count,
            "rewards_table_total": rewards_table_total,
            "ledger_block_reward_total": ledger_block_rewards,
        },
        "validators": {
            "validator_count": int(validators["count"]),
            "stake_locked": validator_stake_locked,
            "expected_stake_locked": expected_validator_stake_locked,
            "slashed_amount": validator_slashed_amount,
            "ledger_validator_slashes": ledger_validator_slashes,
            "ledger_genesis_slashes": ledger_genesis_slashes,
        },
        "issues": issues,
    }


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
    checked_at = utc_now_dt()
    issues: list[str] = []
    database = {"connected": False}

    try:
        with get_connection() as connection:
            connection.execute("SELECT 1").fetchone()
            params = _active_protocol_params(connection)
            latest_height = _latest_block_height(connection)
            latest_hash = _latest_block_hash(connection)
            miners = int(connection.execute("SELECT COUNT(*) AS count FROM miners").fetchone()["count"])
            validators = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(CASE WHEN is_banned = 0 AND stake_locked >= ? AND trust_score >= ? THEN 1 ELSE 0 END), 0) AS eligible
                FROM validators
                """,
                (MIN_VALIDATOR_STAKE, VALIDATOR_MIN_TRUST_SCORE),
            ).fetchone()
            active_protocol = params is not None
            required_approvals = int(params["required_validator_approvals"])
            eligible_validators = int(validators["eligible"])
            database = {
                "connected": True,
                "active_protocol": active_protocol,
                "miners": miners,
                "validators": int(validators["total"]),
                "eligible_validators": eligible_validators,
            }
    except Exception as exc:
        issues.append(f"database unavailable: {exc}")
        params = None
        latest_height = 0
        latest_hash = GENESIS_HASH
        miners = 0
        required_approvals = REQUIRED_VALIDATOR_APPROVALS
        eligible_validators = 0
        active_protocol = False

    chain = verify_chain() if database["connected"] else {
        "valid": False,
        "checked_blocks": 0,
        "latest_block_hash": latest_hash,
        "issues": [{"reason": "database unavailable"}],
    }
    audit = _basic_audit_health() if database["connected"] else {"valid": False, "issues": 1}

    if not chain["valid"]:
        issues.append("chain verification failed")
    if not audit["valid"]:
        issues.append("economic audit has issues")
    if active_protocol and eligible_validators < required_approvals:
        issues.append("not enough eligible validators for quorum")

    can_assign_tasks = bool(database["connected"] and active_protocol)
    mining_ready = bool(can_assign_tasks and miners > 0 and eligible_validators >= required_approvals)
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
        "can_assign_tasks": can_assign_tasks,
        "mining_ready": mining_ready,
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
    mining_ready = counts["miners"] > 0 and counts["eligible_validators"] >= protocol["required_validator_approvals"]

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
        if block_height is None:
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
        timestamp = utc_now()
        audit_seed = sha256_text(
            canonical_json(
                {
                    "audit_id": uuid.uuid4().hex,
                    "block_hash": block["block_hash"],
                    "block_height": block["height"],
                    "created_at": timestamp,
                    "sample_count": sample_count,
                }
            )
        )
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
        reason = "accepted" if passed else "result_hash mismatch"
        cursor = connection.execute(
            """
            INSERT INTO retroactive_audits (
                block_height, block_hash, audit_seed, sample_count, samples,
                expected_hash, actual_hash, passed, reason, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                timestamp,
            ),
        )
        audit = row_to_dict(
            connection.execute(
                "SELECT * FROM retroactive_audits WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
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

    blocks_since_retarget = max(0, current_height - last_retarget_height)
    average_ms = _average_epoch_ms(epoch_rows) if epoch_rows else None
    return {
        "enabled": True,
        "epoch_blocks": RETARGET_EPOCH_BLOCKS,
        "target_block_ms": RETARGET_TARGET_BLOCK_MS,
        "tolerance": RETARGET_TOLERANCE,
        "current_height": current_height,
        "last_retarget_height": last_retarget_height,
        "current_epoch_block_count": len(epoch_rows),
        "current_epoch_average_ms": average_ms,
        "blocks_until_next_epoch": max(0, RETARGET_EPOCH_BLOCKS - blocks_since_retarget),
        "active_difficulty": calculate_difficulty(params),
        "active_reward_per_block": calculate_reward(params),
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
    return {
        "project": PROJECT_NAME,
        "protocol_version": params["protocol_version"],
        "network_id": NETWORK_ID,
        "algorithm": params["algorithm"],
        "validation_mode": params["validation_mode"],
        "required_validator_approvals": params["required_validator_approvals"],
        "range_assignment_mode": params["range_assignment_mode"],
        "max_pi_position": params["max_pi_position"],
        "range_assignment_max_attempts": params["range_assignment_max_attempts"],
        "segment_size": params["segment_size"],
        "sample_count": params["sample_count"],
        "task_expiration_seconds": params["task_expiration_seconds"],
        "max_active_tasks_per_miner": params["max_active_tasks_per_miner"],
        "base_reward": params["base_reward"],
        "difficulty": calculate_difficulty(params),
        "reward_per_block": calculate_reward(params),
        "validator_reward_percent": VALIDATOR_REWARD_PERCENT_OF_BLOCK,
        "validator_reward_pool_per_block": calculate_validator_reward_pool(params),
        "faucet_enabled": NETWORK_ID in FAUCET_ALLOWED_NETWORKS,
        "validator_selection_mode": VALIDATOR_SELECTION_MODE,
        "penalty_invalid_result": PENALTY_INVALID_RESULT,
        "penalty_duplicate": PENALTY_DUPLICATE,
        "penalty_invalid_signature": PENALTY_INVALID_SIGNATURE,
        "cooldown_after_rejections": COOLDOWN_AFTER_REJECTIONS,
        "cooldown_seconds": COOLDOWN_SECONDS,
    }


def _protocol_params_payload(params: dict[str, Any]) -> dict[str, Any]:
    payload = dict(params)
    payload["active"] = bool(payload["active"])
    payload["difficulty"] = calculate_difficulty(payload)
    payload["reward_per_block"] = calculate_reward(payload)
    return payload


def verify_chain() -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    blocks = get_blocks()
    previous_hash = GENESIS_HASH
    seen_ranges: list[tuple[int, int, str]] = []
    seen_result_hashes: set[str] = set()

    for expected_height, block in enumerate(blocks, start=1):
        height = block["height"]
        if height != expected_height:
            issues.append({"height": height, "reason": f"expected height {expected_height}"})
        if block["previous_hash"] != previous_hash:
            issues.append({"height": height, "reason": "previous_hash does not match prior block"})

        for seen_start, seen_end, seen_algorithm in seen_ranges:
            same_algorithm = seen_algorithm == block["algorithm"]
            overlaps = seen_start <= block["range_end"] and seen_end >= block["range_start"]
            if same_algorithm and overlaps:
                issues.append({"height": height, "reason": "range overlaps a previous block"})
                break
        seen_ranges.append((block["range_start"], block["range_end"], block["algorithm"]))

        if block["result_hash"] in seen_result_hashes:
            issues.append({"height": height, "reason": "duplicate result_hash"})
        seen_result_hashes.add(block["result_hash"])

        current_payload = _block_payload(block, include_protocol=True)
        legacy_payload = _block_payload(block, include_protocol=False)
        if hash_block(current_payload) != block["block_hash"] and hash_block(legacy_payload) != block["block_hash"]:
            issues.append({"height": height, "reason": "block_hash does not match block payload"})

        previous_hash = block["block_hash"]

    return {
        "valid": not issues,
        "checked_blocks": len(blocks),
        "latest_block_hash": previous_hash,
        "issues": issues,
    }


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
    task_cursor = connection.execute(
        """
        UPDATE tasks
        SET status = 'expired'
        WHERE status IN ('assigned', 'committed', 'revealed')
        AND expires_at IS NOT NULL
        AND expires_at <= ?
        """,
        (utc_now(),),
    )
    job_cursor = connection.execute(
        """
        UPDATE validation_jobs
        SET status = 'expired', completed_at = ?
        WHERE status = 'pending'
        AND task_id IN (
            SELECT task_id
            FROM tasks
            WHERE status = 'expired'
        )
        """,
        (utc_now(),),
    )
    return {
        "expired_tasks": max(0, task_cursor.rowcount),
        "expired_validation_jobs": max(0, job_cursor.rowcount),
    }


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
    return round(calculate_reward(params) * VALIDATOR_REWARD_PERCENT_OF_BLOCK, 8)


def _validator_reward_total(validator_id: str) -> float:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM ledger_entries
            WHERE account_id = ?
            AND entry_type = 'validator_reward'
            """,
            (validator_id,),
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
    pool = calculate_validator_reward_pool(params)
    if not validator_ids or pool <= 0:
        return {"pool": 0.0, "per_validator": 0.0, "validator_ids": []}

    per_validator = round(pool / len(validator_ids), 8)
    distributed = 0.0
    for index, validator_id in enumerate(validator_ids, start=1):
        amount = per_validator
        if index == len(validator_ids):
            amount = round(pool - distributed, 8)
        distributed = round(distributed + amount, 8)
        _apply_ledger_entry(
            connection,
            account_id=validator_id,
            account_type="validator",
            amount=amount,
            entry_type="validator_reward",
            block_height=block_height,
            related_id=job_id,
            description="additional validator reward",
        )

    return {
        "pool": pool,
        "per_validator": per_validator,
        "validator_ids": validator_ids,
    }


def _selected_validators_for_job(
    connection: Any,
    job: dict[str, Any],
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    eligible = _eligible_validator_rows(connection)
    required = int(params["required_validator_approvals"])
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
        """
        SELECT *
        FROM validators
        WHERE is_banned = 0
        AND stake_locked >= ?
        AND trust_score >= ?
        """,
        (MIN_VALIDATOR_STAKE, VALIDATOR_MIN_TRUST_SCORE),
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
    stake_locked = max(0.0, float(validator.get("stake_locked") or 0.0))
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
    validators = connection.execute(
        """
        SELECT
            COUNT(*) AS total,
            COALESCE(SUM(CASE WHEN is_banned = 0 THEN 1 ELSE 0 END), 0) AS active,
            COALESCE(SUM(CASE WHEN is_banned = 0 AND stake_locked >= ? AND trust_score >= ? THEN 1 ELSE 0 END), 0) AS eligible
        FROM validators
        """,
        (MIN_VALIDATOR_STAKE, VALIDATOR_MIN_TRUST_SCORE),
    ).fetchone()
    tasks = connection.execute(
        """
        SELECT
            COUNT(*) AS total,
            COALESCE(SUM(CASE WHEN status = 'assigned' THEN 1 ELSE 0 END), 0) AS assigned,
            COALESCE(SUM(CASE WHEN status = 'committed' THEN 1 ELSE 0 END), 0) AS committed,
            COALESCE(SUM(CASE WHEN status = 'revealed' THEN 1 ELSE 0 END), 0) AS revealed,
            COALESCE(SUM(CASE WHEN status = 'accepted' THEN 1 ELSE 0 END), 0) AS accepted,
            COALESCE(SUM(CASE WHEN status = 'expired' THEN 1 ELSE 0 END), 0) AS expired
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
        "miners": int(connection.execute("SELECT COUNT(*) AS count FROM miners").fetchone()["count"]),
        "validators": int(validators["total"]),
        "active_validators": int(validators["active"]),
        "eligible_validators": int(validators["eligible"]),
        "required_validator_approvals": int(params["required_validator_approvals"]),
        "blocks": _latest_block_height(connection),
        "tasks": {
            "total": int(tasks["total"]),
            "assigned": int(tasks["assigned"]),
            "committed": int(tasks["committed"]),
            "revealed": int(tasks["revealed"]),
            "accepted": int(tasks["accepted"]),
            "expired": int(tasks["expired"]),
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
        SELECT id, block_height, sample_count, passed, reason, created_at
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
            title="Auditoria retroactiva",
            message=f"bloque {row['block_height']} con {row['sample_count']} samples: {row['reason']}",
            severity="info" if row["passed"] else "bad",
            created_at=row["created_at"],
            related_id=str(row["id"]),
            block_height=int(row["block_height"]),
            metadata={"sample_count": row["sample_count"], "passed": bool(row["passed"])},
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
        INSERT INTO balances (account_id, account_type, balance, updated_at)
        VALUES (?, ?, 0, ?)
        ON CONFLICT(account_id) DO NOTHING
        """,
        (account_id, account_type, utc_now()),
    )


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
) -> None:
    _ensure_balance_account(connection, account_id, account_type)
    current = connection.execute(
        "SELECT balance FROM balances WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    balance_after = round(float(current["balance"]) + float(amount), 8)
    timestamp = utc_now()
    connection.execute(
        "UPDATE balances SET balance = ?, updated_at = ? WHERE account_id = ?",
        (balance_after, timestamp, account_id),
    )
    connection.execute(
        """
        INSERT INTO ledger_entries (
            account_id, account_type, amount, balance_after, entry_type,
            block_height, related_id, description, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account_id,
            account_type,
            round(float(amount), 8),
            balance_after,
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
            COALESCE(SUM(CASE WHEN account_type = 'miner' THEN balance ELSE 0 END), 0) AS miner_balances,
            COALESCE(SUM(CASE WHEN account_type = 'validator' THEN balance ELSE 0 END), 0) AS validator_balances
        FROM balances
        """,
        (GENESIS_ACCOUNT_ID,),
    ).fetchone()
    genesis_balance = round(float(row["genesis_balance"]), 8)
    miner_balances = round(float(row["miner_balances"]), 8)
    validator_balances = round(float(row["validator_balances"]), 8)
    return {
        "genesis_balance": genesis_balance,
        "miner_balances": miner_balances,
        "validator_balances": validator_balances,
        "circulating_supply": round(miner_balances + validator_balances, 8),
    }


def _miner_exists(connection: Any, miner_id: str) -> bool:
    row = connection.execute("SELECT 1 FROM miners WHERE miner_id = ?", (miner_id,)).fetchone()
    return row is not None


def _apply_penalty(connection: Any, miner_id: str, task_id: str, points: int, reason: str) -> None:
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
        "SELECT stake_locked FROM validators WHERE validator_id = ?",
        (validator_id,),
    ).fetchone()
    slash_amount = 0.0
    if validator is not None:
        slash_amount = min(float(validator["stake_locked"]), VALIDATOR_SLASH_INVALID_SIGNATURE)
    connection.execute(
        """
        UPDATE validators
        SET invalid_results = invalid_results + 1,
            stake_locked = MAX(0, stake_locked - ?),
            slashed_amount = slashed_amount + ?,
            last_seen_at = ?
        WHERE validator_id = ?
        """,
        (slash_amount, slash_amount, utc_now(), validator_id),
    )
    if slash_amount > 0:
        _apply_ledger_entry(
            connection,
            account_id=validator_id,
            account_type="validator",
            amount=-slash_amount,
            entry_type="validator_slash",
            related_id=validator_id,
            description=reason,
        )
        _apply_ledger_entry(
            connection,
            account_id=GENESIS_ACCOUNT_ID,
            account_type="genesis",
            amount=slash_amount,
            entry_type="validator_slash",
            related_id=validator_id,
            description=reason,
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


def _decode_block(block: dict[str, Any] | None) -> dict[str, Any] | None:
    if block is None:
        return None
    block["samples"] = json.loads(block["samples"])
    return block


def _decode_retroactive_audit(audit: dict[str, Any] | None) -> dict[str, Any] | None:
    if audit is None:
        return None
    audit["samples"] = json.loads(audit["samples"])
    audit["passed"] = bool(audit["passed"])
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
    if block.get("merkle_root"):
        payload["merkle_root"] = block["merkle_root"]
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
    return params


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
    max_start = params["max_pi_position"] - params["segment_size"] + 1
    if max_start < 1:
        raise MiningError(500, "max_pi_position must be >= segment_size")

    previous_hash = _latest_block_hash(connection)
    task_counter = connection.execute("SELECT COUNT(*) AS count FROM tasks").fetchone()["count"] + 1

    for nonce in range(params["range_assignment_max_attempts"]):
        assignment_seed = sha256_text(
            canonical_json(
                {
                    "algorithm": params["algorithm"],
                    "max_pi_position": params["max_pi_position"],
                    "miner_id": miner_id,
                    "nonce": nonce,
                    "previous_hash": previous_hash,
                    "segment_size": params["segment_size"],
                    "task_counter": task_counter,
                    "task_id": task_id,
                }
            )
        )
        range_start = (int(assignment_seed, 16) % max_start) + 1
        range_end = range_start + params["segment_size"] - 1
        if not _range_overlaps_protected_task(connection, range_start, range_end, params["algorithm"]):
            return {
                "range_start": range_start,
                "range_end": range_end,
                "assignment_seed": assignment_seed,
            }

    raise MiningError(503, "could not assign a non-overlapping range")


def _range_overlaps_protected_task(connection: Any, range_start: int, range_end: int, algorithm: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM tasks
        WHERE algorithm = ?
        AND status IN ('assigned', 'committed', 'revealed', 'accepted')
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
        SELECT height, COALESCE(total_task_ms, ?) AS total_task_ms
        FROM blocks
        WHERE height > ?
        ORDER BY height ASC
        LIMIT ?
        """,
        (RETARGET_TARGET_BLOCK_MS, last_height, RETARGET_EPOCH_BLOCKS),
    ).fetchall()


def _average_epoch_ms(epoch_rows: list[Any]) -> float:
    return round(sum(float(row["total_task_ms"]) for row in epoch_rows) / len(epoch_rows), 2)


def _retarget_preview(connection: Any, force: bool = False) -> dict[str, Any]:
    current_height = _latest_block_height(connection)
    last_height = _last_retarget_height(connection)
    params = _active_protocol_params(connection)
    epoch_rows = _retarget_epoch_rows(connection, last_height)
    epoch_count = len(epoch_rows)
    average_block_ms = _average_epoch_ms(epoch_rows) if epoch_rows else None
    ready = bool(epoch_rows) and (force or epoch_count >= RETARGET_EPOCH_BLOCKS)
    next_params = dict(params)
    meta = {
        "action": "wait",
        "reason": "not enough accepted blocks for retarget",
        "adjustment_factor": 1.0,
    }
    if ready and average_block_ms is not None:
        next_params, meta = propose_retarget_params(params, average_block_ms)

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
        "epoch_blocks_required": RETARGET_EPOCH_BLOCKS,
        "blocks_until_ready": max(0, RETARGET_EPOCH_BLOCKS - epoch_count),
        "average_block_ms": average_block_ms,
        "target_block_ms": RETARGET_TARGET_BLOCK_MS,
        "tolerance": RETARGET_TOLERANCE,
        "action": meta["action"],
        "reason": meta["reason"],
        "adjustment_factor": meta["adjustment_factor"],
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

    if meta["action"] != "keep":
        connection.execute("UPDATE protocol_params SET active = 0 WHERE active = 1")
        cursor = connection.execute(
            """
            INSERT INTO protocol_params (
                protocol_version, algorithm, validation_mode, required_validator_approvals,
                range_assignment_mode, max_pi_position, range_assignment_max_attempts,
                segment_size, sample_count, task_expiration_seconds,
                max_active_tasks_per_miner, base_reward, active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
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
            utc_now(),
        ),
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
    if params is None:
        params = _protocol_params_for_task(connection, task)
    reward = calculate_reward(params)
    difficulty = calculate_difficulty(params)
    latest_block = connection.execute(
        "SELECT height, block_hash FROM blocks ORDER BY height DESC LIMIT 1"
    ).fetchone()
    next_height = 1 if latest_block is None else latest_block["height"] + 1
    previous_hash = GENESIS_HASH if latest_block is None else latest_block["block_hash"]
    timestamp = utc_now()
    created_at = parse_iso(task.get("created_at"))
    total_task_ms = None
    if created_at is not None:
        total_task_ms = max(0, round((utc_now_dt() - created_at).total_seconds() * 1000))

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
        "validation_mode": params["validation_mode"],
    }
    if merkle_root:
        block_payload["merkle_root"] = merkle_root
    block_hash = hash_block(block_payload)

    connection.execute(
        """
        INSERT INTO blocks (
            height, previous_hash, miner_id, range_start, range_end, algorithm,
            result_hash, merkle_root, samples, timestamp, block_hash, reward, difficulty,
            task_id, protocol_params_id, protocol_version, validation_mode, total_task_ms, validation_ms
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            difficulty,
            task["task_id"],
            params["id"],
            params["protocol_version"],
            params["validation_mode"],
            total_task_ms,
            validation_ms,
        ),
    )
    connection.execute(
        "UPDATE tasks SET status = 'accepted', submitted_at = ? WHERE task_id = ?",
        (timestamp, task["task_id"]),
    )
    _record_submission(connection, task["task_id"], miner_id, result_hash, "", signature, True, submission_reason)
    connection.execute(
        """
        INSERT INTO rewards (miner_id, block_height, amount, reason, created_at)
        VALUES (?, ?, ?, 'block accepted', ?)
        """,
        (miner_id, next_height, reward, timestamp),
    )
    _apply_ledger_entry(
        connection,
        account_id=miner_id,
        account_type="miner",
        amount=reward,
        entry_type="block_reward",
        block_height=next_height,
        related_id=task["task_id"],
        description="miner block reward",
    )
    validator_reward = {"pool": 0.0, "per_validator": 0.0, "validator_ids": []}
    if validation_job_id is not None:
        validator_reward = _apply_validator_rewards(
            connection,
            job_id=validation_job_id,
            block_height=next_height,
            params=params,
        )
    _refresh_trust_score(connection, miner_id)
    _maybe_retarget_after_block(connection, next_height)

    return {
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
        "validator_reward": validator_reward,
        "difficulty": difficulty,
        "protocol_params_id": params["id"],
        "protocol_version": params["protocol_version"],
        "validation_mode": params["validation_mode"],
        "total_task_ms": total_task_ms,
        "validation_ms": validation_ms,
    }


def blocks_or_zero(value: Any) -> float:
    return 0.0 if value is None else float(value)
