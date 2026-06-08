from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.settings import (
    MIN_VALIDATOR_STAKE,
    NODE_ROLE,
    PROTOCOL_VERSION,
    REQUIRED_VALIDATOR_APPROVALS,
    VALIDATOR_ELIGIBILITY_STAKE_FIELD,
    VALIDATOR_MIN_TRUST_SCORE,
)
from app.db.database import get_connection


WRITE_ROLES = {"write_candidate", "validator_node", "pool_node"}


def node_capabilities_for_role(role: str | None = None) -> dict[str, bool]:
    node_role = (role or NODE_ROLE).strip().lower()
    write_enabled = node_role in WRITE_ROLES
    return {
        "read": True,
        "tx_submit": write_enabled,
        "task_assign": write_enabled,
        "validation_job": write_enabled,
        "block_finalize": write_enabled,
    }


def _eligible_validator_count(connection: Any) -> int:
    rows = connection.execute(
        f"""
        SELECT cooldown_until
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
    now = datetime.now(timezone.utc)
    eligible = 0
    for row in rows:
        cooldown_until = _parse_iso(row["cooldown_until"])
        if cooldown_until is not None and cooldown_until > now:
            continue
        eligible += 1
    return eligible


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_node_readiness(
    *,
    database_connected: bool,
    active_protocol: bool,
    chain_valid: bool,
    sync_status: str,
    divergence_detected: bool,
    miners: int = 0,
    online_miners: int | None = None,
    eligible_validators: int | None = None,
    required_validator_approvals: int | None = None,
) -> dict[str, Any]:
    required = int(required_validator_approvals or REQUIRED_VALIDATOR_APPROVALS)
    eligible = int(eligible_validators or 0)
    online = int(online_miners if online_miners is not None else miners)
    role_capabilities = node_capabilities_for_role()
    replay_ready = sync_status not in {"stalled", "divergent"} and not divergence_detected
    quorum_ready = eligible >= required

    read_reasons: list[str] = []
    if not database_connected:
        read_reasons.append("database unavailable")
    if not active_protocol:
        read_reasons.append("active protocol unavailable")
    if not chain_valid:
        read_reasons.append("chain verification failed")
    if not replay_ready:
        read_reasons.append(f"replay {sync_status}")

    read_ready = not read_reasons

    tx_reasons = list(read_reasons)
    if not role_capabilities["tx_submit"]:
        tx_reasons.append(f"node role {NODE_ROLE} does not submit transactions")

    quorum_reasons = list(tx_reasons)
    if not quorum_ready:
        quorum_reasons.append(
            f"eligible validators {eligible} below required quorum {required}"
        )

    task_reasons = list(quorum_reasons)
    if not role_capabilities["task_assign"]:
        task_reasons.append(f"node role {NODE_ROLE} does not assign mining tasks")

    validation_reasons = list(quorum_reasons)
    if not role_capabilities["validation_job"]:
        validation_reasons.append(f"node role {NODE_ROLE} does not create validation jobs")

    finality_reasons = list(quorum_reasons)
    if not role_capabilities["block_finalize"]:
        finality_reasons.append(f"node role {NODE_ROLE} does not finalize blocks")

    readiness = {
        "node_role": NODE_ROLE,
        "role_capabilities": role_capabilities,
        "read_ready": read_ready,
        "tx_submit_ready": not tx_reasons,
        "task_assign_ready": not task_reasons,
        "validation_job_ready": not validation_reasons,
        "block_finalize_ready": not finality_reasons,
        "mining_ready": not task_reasons and online > 0,
        "requirements": {
            "database_connected": database_connected,
            "active_protocol": active_protocol,
            "chain_valid": chain_valid,
            "sync_status": sync_status,
            "divergence_detected": divergence_detected,
            "miners": int(miners),
            "online_miners": online,
            "eligible_validators": eligible,
            "required_validator_approvals": required,
        },
        "reasons": {
            "read_ready": read_reasons,
            "tx_submit_ready": tx_reasons,
            "task_assign_ready": task_reasons,
            "validation_job_ready": validation_reasons,
            "block_finalize_ready": finality_reasons,
        },
    }
    return readiness


def get_node_readiness(
    *,
    chain_valid: bool = True,
    sync_status: str = "healthy",
    divergence_detected: bool = False,
) -> dict[str, Any]:
    try:
        with get_connection() as connection:
            params = connection.execute(
                "SELECT required_validator_approvals FROM protocol_params WHERE active = 1 LIMIT 1"
            ).fetchone()
            miners = int(connection.execute("SELECT COUNT(*) AS count FROM miners").fetchone()["count"])
            active_protocol = params is not None
            eligible_validators = _eligible_validator_count(connection)
            required = int(params["required_validator_approvals"] if params else REQUIRED_VALIDATOR_APPROVALS)
        return build_node_readiness(
            database_connected=True,
            active_protocol=active_protocol,
            chain_valid=chain_valid,
            sync_status=sync_status,
            divergence_detected=divergence_detected,
            miners=miners,
            eligible_validators=eligible_validators,
            required_validator_approvals=required,
        )
    except Exception as exc:
        readiness = build_node_readiness(
            database_connected=False,
            active_protocol=False,
            chain_valid=False,
            sync_status=sync_status,
            divergence_detected=divergence_detected,
        )
        readiness["error"] = str(exc)
        return readiness
