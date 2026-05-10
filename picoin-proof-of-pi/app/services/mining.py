import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.crypto import canonical_json, hash_block, sha256_text
from app.core.merkle import verify_merkle_proof
from app.core.pi import calculate_pi_segment
from app.core.signatures import (
    build_commit_signature_payload,
    build_reveal_signature_payload,
    build_submission_signature_payload,
    validate_public_key,
    verify_payload_signature,
)
from app.core.settings import (
    COOLDOWN_AFTER_REJECTIONS,
    COOLDOWN_SECONDS,
    DEFAULT_REWARD,
    MAX_ACTIVE_TASKS_PER_MINER,
    MAX_PI_POSITION,
    PENALTY_DUPLICATE,
    PENALTY_INVALID_RESULT,
    PENALTY_INVALID_SIGNATURE,
    PI_ALGORITHM,
    PROJECT_NAME,
    RANGE_ASSIGNMENT_MAX_ATTEMPTS,
    RANGE_ASSIGNMENT_MODE,
    PROTOCOL_VERSION,
    SAMPLE_COUNT,
    TASK_EXPIRATION_SECONDS,
    TASK_SEGMENT_SIZE,
    VALIDATION_MODE,
)
from app.db.database import get_connection, row_to_dict
from validator.proof import validate_submission


GENESIS_HASH = "0" * 64


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


def parse_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


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
        row = connection.execute("SELECT * FROM miners WHERE miner_id = ?", (miner_id,)).fetchone()
    return enrich_miner(row_to_dict(row))


def get_miner(miner_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM miners WHERE miner_id = ?", (miner_id,)).fetchone()
    miner = row_to_dict(row)
    if miner is None:
        return None
    return enrich_miner(miner)


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
    miner["is_banned"] = bool(miner["is_banned"])
    return miner


def create_next_task(miner_id: str) -> dict[str, Any] | None:
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
            WHERE miner_id = ? AND status IN ('assigned', 'committed')
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (miner_id,),
        ).fetchone()
        if active_task is not None:
            return row_to_dict(active_task)

        active_count = connection.execute(
            "SELECT COUNT(*) AS count FROM tasks WHERE miner_id = ? AND status IN ('assigned', 'committed')",
            (miner_id,),
        ).fetchone()["count"]
        if active_count >= MAX_ACTIVE_TASKS_PER_MINER:
            raise MiningError(429, "miner has too many active tasks")

        task_id = f"task_{uuid.uuid4().hex[:16]}"
        assignment = _assign_pseudo_random_range(connection, miner_id, task_id)
        now = utc_now()
        expires_at = iso_at(TASK_EXPIRATION_SECONDS)

        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                assignment_seed, assignment_mode, created_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?, 'assigned', ?, ?, ?, ?)
            """,
            (
                task_id,
                miner_id,
                assignment["range_start"],
                assignment["range_end"],
                PI_ALGORITHM,
                assignment["assignment_seed"],
                RANGE_ASSIGNMENT_MODE,
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
            "reward": DEFAULT_REWARD,
            "samples": validation.samples,
            "timestamp": timestamp,
            "protocol_version": PROTOCOL_VERSION,
            "validation_mode": VALIDATION_MODE,
        }
        block_hash = hash_block(block_payload)

        connection.execute(
            """
            INSERT INTO blocks (
                height, previous_hash, miner_id, range_start, range_end, algorithm,
                result_hash, samples, timestamp, block_hash, reward, task_id,
                protocol_version, validation_mode
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                DEFAULT_REWARD,
                task_id,
                PROTOCOL_VERSION,
                VALIDATION_MODE,
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
            (miner_id, next_height, DEFAULT_REWARD, timestamp),
        )
        _refresh_trust_score(connection, miner_id)

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
            "reward": DEFAULT_REWARD,
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
        samples = _build_challenge_samples(task["range_start"], task["range_end"], challenge_seed)

        connection.execute(
            """
            INSERT INTO commitments (
                task_id, miner_id, result_hash, merkle_root, challenge_seed,
                samples, signature, signed_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                utc_now(),
            ),
        )
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
        validation = _validate_revealed_samples(task, commitment, requested_samples, revealed_samples)
        validation_payload = {
            "reason": validation["reason"],
            "samples": validation["samples"],
            "challenge_seed": commitment["challenge_seed"],
            "merkle_root": commitment["merkle_root"],
        }

        if not validation["accepted"]:
            connection.execute(
                "UPDATE tasks SET status = 'rejected', submitted_at = ? WHERE task_id = ?",
                (utc_now(), task_id),
            )
            return _reject_in_connection(
                connection,
                validation["reason"],
                task_id,
                miner_id,
                commitment["result_hash"],
                validation_payload,
                PENALTY_INVALID_RESULT,
                signature,
                "",
            )

        block = _accept_block_in_connection(
            connection=connection,
            task=task,
            miner_id=miner_id,
            result_hash=commitment["result_hash"],
            merkle_root=commitment["merkle_root"],
            samples=validation["samples"],
            signature=signature,
            submission_reason="commit_reveal accepted",
        )

    return {
        "accepted": True,
        "status": "accepted",
        "message": "block accepted",
        "block": block,
        "validation": validation_payload,
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
        rejected = connection.execute("SELECT COUNT(*) AS count FROM submissions WHERE accepted = 0").fetchone()["count"]
        latest = connection.execute("SELECT block_hash FROM blocks ORDER BY height DESC LIMIT 1").fetchone()
    return {
        "miners": miners,
        "tasks": tasks,
        "pending_tasks": pending,
        "expired_tasks": expired,
        "accepted_blocks": blocks["count"],
        "rejected_submissions": rejected,
        "total_rewards": blocks["rewards"],
        "latest_block_hash": GENESIS_HASH if latest is None else latest["block_hash"],
    }


def get_protocol() -> dict[str, Any]:
    return {
        "project": PROJECT_NAME,
        "protocol_version": PROTOCOL_VERSION,
        "algorithm": PI_ALGORITHM,
        "validation_mode": VALIDATION_MODE,
        "range_assignment_mode": RANGE_ASSIGNMENT_MODE,
        "max_pi_position": MAX_PI_POSITION,
        "range_assignment_max_attempts": RANGE_ASSIGNMENT_MAX_ATTEMPTS,
        "segment_size": TASK_SEGMENT_SIZE,
        "sample_count": SAMPLE_COUNT,
        "task_expiration_seconds": TASK_EXPIRATION_SECONDS,
        "max_active_tasks_per_miner": MAX_ACTIVE_TASKS_PER_MINER,
        "reward_per_block": DEFAULT_REWARD,
        "penalty_invalid_result": PENALTY_INVALID_RESULT,
        "penalty_duplicate": PENALTY_DUPLICATE,
        "penalty_invalid_signature": PENALTY_INVALID_SIGNATURE,
        "cooldown_after_rejections": COOLDOWN_AFTER_REJECTIONS,
        "cooldown_seconds": COOLDOWN_SECONDS,
    }


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


def _expire_assigned_tasks(connection: Any) -> None:
    connection.execute(
        """
        UPDATE tasks
        SET status = 'expired'
        WHERE status IN ('assigned', 'committed')
        AND expires_at IS NOT NULL
        AND expires_at <= ?
        """,
        (utc_now(),),
    )


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


def _decode_block(block: dict[str, Any] | None) -> dict[str, Any] | None:
    if block is None:
        return None
    block["samples"] = json.loads(block["samples"])
    return block


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


def _assign_pseudo_random_range(connection: Any, miner_id: str, task_id: str) -> dict[str, Any]:
    max_start = MAX_PI_POSITION - TASK_SEGMENT_SIZE + 1
    if max_start < 1:
        raise MiningError(500, "MAX_PI_POSITION must be >= TASK_SEGMENT_SIZE")

    previous_hash = _latest_block_hash(connection)
    task_counter = connection.execute("SELECT COUNT(*) AS count FROM tasks").fetchone()["count"] + 1

    for nonce in range(RANGE_ASSIGNMENT_MAX_ATTEMPTS):
        assignment_seed = sha256_text(
            canonical_json(
                {
                    "algorithm": PI_ALGORITHM,
                    "max_pi_position": MAX_PI_POSITION,
                    "miner_id": miner_id,
                    "nonce": nonce,
                    "previous_hash": previous_hash,
                    "segment_size": TASK_SEGMENT_SIZE,
                    "task_counter": task_counter,
                    "task_id": task_id,
                }
            )
        )
        range_start = (int(assignment_seed, 16) % max_start) + 1
        range_end = range_start + TASK_SEGMENT_SIZE - 1
        if not _range_overlaps_protected_task(connection, range_start, range_end):
            return {
                "range_start": range_start,
                "range_end": range_end,
                "assignment_seed": assignment_seed,
            }

    raise MiningError(503, "could not assign a non-overlapping range")


def _range_overlaps_protected_task(connection: Any, range_start: int, range_end: int) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM tasks
        WHERE algorithm = ?
        AND status IN ('assigned', 'committed', 'accepted')
        AND range_start <= ?
        AND range_end >= ?
        LIMIT 1
        """,
        (PI_ALGORITHM, range_end, range_start),
    ).fetchone()
    return row is not None


def _build_challenge_samples(range_start: int, range_end: int, challenge_seed: str) -> list[dict[str, int]]:
    length = range_end - range_start + 1
    sample_count = min(SAMPLE_COUNT, length)
    randomizer = random.Random(challenge_seed)
    offsets = sorted(randomizer.sample(range(length), sample_count))
    return [{"position": range_start + offset} for offset in offsets]


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
) -> dict[str, Any]:
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
        "reward": DEFAULT_REWARD,
        "samples": samples,
        "timestamp": timestamp,
        "protocol_version": PROTOCOL_VERSION,
        "validation_mode": VALIDATION_MODE,
    }
    if merkle_root:
        block_payload["merkle_root"] = merkle_root
    block_hash = hash_block(block_payload)

    connection.execute(
        """
        INSERT INTO blocks (
            height, previous_hash, miner_id, range_start, range_end, algorithm,
            result_hash, merkle_root, samples, timestamp, block_hash, reward, task_id,
            protocol_version, validation_mode
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            DEFAULT_REWARD,
            task["task_id"],
            PROTOCOL_VERSION,
            VALIDATION_MODE,
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
        (miner_id, next_height, DEFAULT_REWARD, timestamp),
    )
    _refresh_trust_score(connection, miner_id)

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
        "reward": DEFAULT_REWARD,
        "protocol_version": PROTOCOL_VERSION,
        "validation_mode": VALIDATION_MODE,
    }
