import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.crypto import hash_block, sha256_text
from app.core.settings import DEFAULT_REWARD, PI_ALGORITHM, TASK_SEGMENT_SIZE, TASK_START_OFFSET
from app.db.database import get_connection, row_to_dict
from validator.proof import validate_submission


GENESIS_HASH = "0" * 64


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def register_miner(name: str, public_key: str | None = None) -> dict[str, Any]:
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
            "SELECT COUNT(*) AS count FROM rejected_submissions WHERE miner_id = ?",
            (miner["miner_id"],),
        ).fetchone()
    miner["accepted_blocks"] = accepted_blocks["count"]
    miner["total_rewards"] = accepted_blocks["rewards"]
    miner["rejected_submissions"] = rejected["count"]
    return miner


def create_next_task(miner_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        miner = connection.execute("SELECT miner_id FROM miners WHERE miner_id = ?", (miner_id,)).fetchone()
        if miner is None:
            return None

        latest_task = connection.execute("SELECT MAX(range_end) AS max_end FROM tasks").fetchone()
        previous_end = latest_task["max_end"] if latest_task and latest_task["max_end"] is not None else TASK_START_OFFSET - 1
        range_start = previous_end + 1
        range_end = range_start + TASK_SEGMENT_SIZE - 1
        task_id = f"task_{uuid.uuid4().hex[:16]}"

        connection.execute(
            """
            INSERT INTO tasks (task_id, miner_id, range_start, range_end, algorithm, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'assigned', ?)
            """,
            (task_id, miner_id, range_start, range_end, PI_ALGORITHM, utc_now()),
        )
        row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    return row_to_dict(row)


def submit_task(task_id: str, miner_id: str, result_hash: str, segment: str, signature: str | None) -> dict[str, Any]:
    with get_connection() as connection:
        task = row_to_dict(
            connection.execute(
                "SELECT * FROM tasks WHERE task_id = ? AND miner_id = ?",
                (task_id, miner_id),
            ).fetchone()
        )

        if task is None:
            return _rejected("task not found for miner", task_id, miner_id, result_hash, {})

        if task["status"] != "assigned":
            return _rejected("task was already submitted", task_id, miner_id, result_hash, {"task_status": task["status"]})

        existing_block = connection.execute(
            "SELECT height FROM blocks WHERE result_hash = ? OR task_id = ?",
            (result_hash, task_id),
        ).fetchone()
        if existing_block is not None:
            return _rejected("duplicate result or task submission", task_id, miner_id, result_hash, {})

        expected_signature = sha256_text(f"{miner_id}:{task_id}:{result_hash}")
        if signature is not None and signature != expected_signature:
            return _rejected(
                "invalid miner signature",
                task_id,
                miner_id,
                result_hash,
                {"signature_present": True},
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
        }

        if not validation.accepted:
            connection.execute(
                "UPDATE tasks SET status = 'rejected', submitted_at = ? WHERE task_id = ?",
                (utc_now(), task_id),
            )
            connection.execute(
                """
                INSERT INTO rejected_submissions (task_id, miner_id, result_hash, reason, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (task_id, miner_id, result_hash, validation.reason, utc_now()),
            )
            return {
                "accepted": False,
                "status": "rejected",
                "message": validation.reason,
                "block": None,
                "validation": validation_payload,
            }

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
        }
        block_hash = hash_block(block_payload)

        connection.execute(
            """
            INSERT INTO blocks (
                height, previous_hash, miner_id, range_start, range_end, algorithm,
                result_hash, samples, timestamp, block_hash, reward, task_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        connection.execute(
            "UPDATE tasks SET status = 'accepted', submitted_at = ? WHERE task_id = ?",
            (timestamp, task_id),
        )
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


def _rejected(
    reason: str,
    task_id: str,
    miner_id: str,
    result_hash: str,
    validation: dict[str, Any],
) -> dict[str, Any]:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO rejected_submissions (task_id, miner_id, result_hash, reason, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (task_id, miner_id, result_hash, reason, utc_now()),
        )
    return {
        "accepted": False,
        "status": "rejected",
        "message": reason,
        "block": None,
        "validation": validation,
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
        miners = connection.execute("SELECT COUNT(*) AS count FROM miners").fetchone()["count"]
        tasks = connection.execute("SELECT COUNT(*) AS count FROM tasks").fetchone()["count"]
        pending = connection.execute("SELECT COUNT(*) AS count FROM tasks WHERE status = 'assigned'").fetchone()["count"]
        blocks = connection.execute("SELECT COUNT(*) AS count, COALESCE(SUM(reward), 0) AS rewards FROM blocks").fetchone()
        rejected = connection.execute("SELECT COUNT(*) AS count FROM rejected_submissions").fetchone()["count"]
        latest = connection.execute("SELECT block_hash FROM blocks ORDER BY height DESC LIMIT 1").fetchone()
    return {
        "miners": miners,
        "tasks": tasks,
        "pending_tasks": pending,
        "accepted_blocks": blocks["count"],
        "rejected_submissions": rejected,
        "total_rewards": blocks["rewards"],
        "latest_block_hash": GENESIS_HASH if latest is None else latest["block_hash"],
    }


def _decode_block(block: dict[str, Any] | None) -> dict[str, Any] | None:
    if block is None:
        return None
    block["samples"] = json.loads(block["samples"])
    return block
