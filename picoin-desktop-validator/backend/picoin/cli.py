import argparse
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import uvicorn

from app.tools.bootstrap_testnet import main as bootstrap_testnet_main
from app.tools.reset_testnet import main as reset_testnet_main
from app.tools.run_testnet_cycle import main as run_testnet_cycle_main
from app.tools.run_testnet_multi_miner import main as run_testnet_multi_miner_main
from miner.client import command_mine, command_register as miner_register, command_stats as miner_stats
from validator.client import command_register as validator_register, command_validate


def load_env_file(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()

from app.core.crypto import canonical_json, sha256_text
from app.core.network_profiles import MAINNET_PROFILE
from app.core.settings import (
    CHAIN_ID,
    DATABASE_PATH,
    FAUCET_DEFAULT_AMOUNT,
    GENESIS_HASH,
    MAINNET_RETARGET_MAX_PI_POSITION,
    NETWORK_ID,
    PROJECT_NAME,
    PROTOCOL_VERSION,
    REPLAY_BACKLOG_THRESHOLD,
    REPLAY_BATCH_SIZE,
    get_dynamic_expiration,
)
from app.core.signatures import sign_payload
from app.services.consensus import consensus_vote_payload
from app.services.genesis import (
    genesis_allocation_summary,
    genesis_allocations_hash,
    load_genesis_allocations,
    validate_mainnet_genesis_allocations,
)
from app.services.state import (
    balance_snapshot,
    calculate_state_root,
    import_canonical_snapshot,
    restore_imported_snapshot_state,
    validate_snapshot_document,
    validator_snapshot,
)
from app.services.wallet import address_from_public_key, address_matches_public_key, create_wallet, is_valid_address, sign_transaction


DEFAULT_SERVER_URL = os.getenv("PICOIN_SERVER", "http://127.0.0.1:8000")
DEFAULT_HOST = os.getenv("PICOIN_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.getenv("PICOIN_PORT", "8000"))
DEFAULT_SCIENCE_ADDRESS = os.getenv("PICOIN_SCIENCE_ADDRESS", "local-science-user")
DEFAULT_WALLET_PATH = Path(os.getenv("PICOIN_WALLET_PATH", str(Path.home() / ".picoin" / "wallets" / "default.json")))


def http_timeout_seconds() -> float:
    value = os.getenv("PICOIN_HTTP_TIMEOUT_SECONDS") or os.getenv("PICOIN_SMOKE_TIMEOUT") or "20"
    try:
        return max(1.0, float(value))
    except ValueError:
        return 20.0


def normalize_server_url(server: str) -> str:
    return server.rstrip("/")


def print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def get_json(server_url: str, path: str) -> Any:
    response = requests.get(f"{normalize_server_url(server_url)}{path}", timeout=http_timeout_seconds())
    response.raise_for_status()
    return response.json()


def post_json(server_url: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    response = requests.post(f"{normalize_server_url(server_url)}{path}", json=payload, timeout=http_timeout_seconds())
    response.raise_for_status()
    return response.json()


def command_node_start(args: argparse.Namespace) -> int:
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )
    return 0


def command_node_status(args: argparse.Namespace) -> int:
    server_url = normalize_server_url(args.server)
    print_json(
        {
            "root": get_json(server_url, "/"),
            "health": get_json(server_url, "/health"),
            "status": get_json(server_url, "/node/status"),
            "events": get_json(server_url, "/events?limit=5"),
        }
    )
    return 0


def command_node_audit(args: argparse.Namespace) -> int:
    print_json(get_json(args.server, "/audit/full"))
    return 0


def command_node_repair_rewards(args: argparse.Namespace) -> int:
    print_json(post_json(args.server, "/maintenance/repair-block-rewards"))
    return 0


def command_node_protocol(args: argparse.Namespace) -> int:
    print_json(get_json(args.server, "/protocol"))
    return 0


def command_node_peers(args: argparse.Namespace) -> int:
    print_json(get_json(args.server, f"/node/peers?include_stale={str(args.include_stale).lower()}"))
    return 0


def command_node_discover_peers(args: argparse.Namespace) -> int:
    print_json(post_json(args.server, f"/node/peers/discover?limit={int(args.limit)}"))
    return 0


def command_node_sync_status(args: argparse.Namespace) -> int:
    print_json(get_json(args.server, "/node/sync-status"))
    return 0


def command_node_validation_health(args: argparse.Namespace) -> int:
    path = f"/validation/jobs/health?stale_after_seconds={int(args.stale_after_seconds)}&limit={int(args.limit)}"
    payload = get_json(args.server, path)
    print_json(payload)
    return 0 if payload.get("healthy") else 1


def command_node_doctor(args: argparse.Namespace) -> int:
    server_url = normalize_server_url(args.server)
    checks: list[dict[str, Any]] = []
    payloads: dict[str, Any] = {}

    def record(name: str, ok: bool, detail: str, severity: str = "error") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "severity": severity})

    try:
        health = get_json(server_url, "/health")
        payloads["health"] = health
        record("api_health", health.get("status") == "ok", f"status={health.get('status')}")
        record("database", bool(health.get("database", {}).get("connected")), "database connected")
        record("chain", bool(health.get("chain", {}).get("valid")), "chain validation")
        record("audit", bool(health.get("audit", {}).get("valid")), "ledger audit")
        record("mining_ready", bool(health.get("mining_ready")), "node can assign mining tasks", "warning")
    except requests.RequestException as exc:
        record("api_health", False, f"cannot reach /health: {exc}")

    try:
        status = get_json(server_url, "/node/status")
        payloads["status"] = status
        record("node_status", True, f"node_id={status.get('node_id', 'unknown')}")
    except requests.RequestException as exc:
        record("node_status", False, f"cannot reach /node/status: {exc}")

    try:
        sync = get_json(server_url, "/node/sync-status")
        payloads["sync"] = sync
        latest_height = sync.get("latest_block_height", 0)
        record("sync_status", True, f"height={latest_height}")
        peer_total = sync.get("peer_counts", {}).get("total", 0)
        peer_connected = sync.get("peer_counts", {}).get("connected", 0)
        peer_ok = peer_connected > 0 or not args.require_peers
        record("peers", peer_ok, f"connected={peer_connected}, total={peer_total}", "warning")
    except requests.RequestException as exc:
        record("sync_status", False, f"cannot reach /node/sync-status: {exc}")

    try:
        checkpoint = get_json(server_url, "/node/checkpoints/latest")
        payloads["latest_checkpoint"] = checkpoint
        if checkpoint:
            record("latest_checkpoint", True, f"height={checkpoint.get('height')}")
        else:
            record("latest_checkpoint", not args.require_checkpoint, "no canonical checkpoint yet", "warning")
    except requests.RequestException as exc:
        record("latest_checkpoint", False, f"cannot reach /node/checkpoints/latest: {exc}", "warning")

    failures = [check for check in checks if not check["ok"] and check["severity"] == "error"]
    warnings = [check for check in checks if not check["ok"] and check["severity"] == "warning"]
    output = {
        "server": server_url,
        "status": "fail" if failures else "warn" if warnings else "ok",
        "checks": checks,
        "summary": {
            "errors": len(failures),
            "warnings": len(warnings),
            "checked": len(checks),
        },
    }
    if args.verbose:
        output["payloads"] = payloads
    print_json(output)
    return 1 if failures else 0


def command_node_reconcile(args: argparse.Namespace) -> int:
    path = f"/node/reconcile?limit={args.limit}"
    if args.peer:
        path = f"{path}&peer_address={args.peer}"
    print_json(post_json(args.server, path))
    return 0


def command_node_catch_up(args: argparse.Namespace) -> int:
    server_url = normalize_server_url(args.server)
    peer_url = normalize_server_url(args.peer) if args.peer else None
    rounds: list[dict[str, Any]] = []
    initial_sync = get_json(server_url, "/node/sync-status")
    final_sync = initial_sync
    final_audit: dict[str, Any] = {}
    peer_sync: dict[str, Any] | None = None

    for round_number in range(1, args.max_rounds + 1):
        sync_before = get_json(server_url, "/node/sync-status")
        replay_status = get_json(server_url, "/replay/status")
        queue_size = int(replay_status.get("queue_size") or sync_before.get("pending_replay_blocks") or 0)
        replay_active = bool(replay_status.get("active"))
        skip_reconcile = replay_active or queue_size > args.replay_backlog_threshold
        if skip_reconcile:
            reconcile = {
                "attempted": 0,
                "blocks_imported": 0,
                "proposals_imported": 0,
                "transactions_imported": 0,
                "errors": 0,
                "results": [],
                "skipped": True,
                "reason": "replay active" if replay_active else "replay backlog above threshold",
            }
        else:
            path = f"/node/reconcile?limit={args.reconcile_limit}"
            if peer_url:
                path = f"{path}&peer_address={peer_url}"
            reconcile = post_json(server_url, path)
        if replay_active:
            replay = {"status": "active", "imported": 0, "headers_imported": 0, "headers_skipped": 0, "errors": []}
        else:
            replay_limit = min(args.replay_limit, args.replay_batch_size)
            replay = post_json(server_url, f"/consensus/replay?limit={replay_limit}")
        final_sync = get_json(server_url, "/node/sync-status")
        pending_after = int(final_sync.get("pending_replay_blocks") or 0)
        if pending_after == 0 or round_number == args.max_rounds:
            final_audit = get_json(server_url, "/audit/full")
        else:
            final_audit = {
                "valid": False,
                "issues": [{"code": "replay_backlog_draining", "pending_replay_blocks": pending_after}],
            }
        if peer_url:
            peer_sync = get_json(peer_url, "/node/sync-status")
        reconcile_results = reconcile.get("results") or []
        blocks_seen = sum(int(item.get("blocks_seen", 0)) for item in reconcile_results)
        peer_height = (
            int(peer_sync.get("effective_latest_block_height", peer_sync.get("latest_block_height", 0)) or 0)
            if peer_sync
            else None
        )
        peer_hash = (
            (peer_sync.get("effective_latest_block_hash") or peer_sync.get("latest_block_hash"))
            if peer_sync
            else None
        )
        local_height = int(final_sync.get("effective_latest_block_height", final_sync.get("latest_block_height", 0)) or 0)
        local_hash = final_sync.get("effective_latest_block_hash") or final_sync.get("latest_block_hash")
        round_summary = {
            "round": round_number,
            "reconcile": {
                "skipped": bool(reconcile.get("skipped", False)),
                "reason": reconcile.get("reason"),
                "blocks_seen": blocks_seen,
                "blocks_imported": reconcile.get("blocks_imported", 0),
                "proposals_imported": reconcile.get("proposals_imported", 0),
                "transactions_imported": reconcile.get("transactions_imported", 0),
                "errors": reconcile.get("errors", 0),
            },
            "replay": {
                "imported": replay.get("imported", 0),
                "headers_imported": replay.get("headers_imported", 0),
                "headers_skipped": replay.get("headers_skipped", 0),
                "headers_skipped_pre_snapshot": replay.get("headers_skipped_pre_snapshot", 0),
                "normalized": replay.get("normalized", 0),
                "queue_size": replay.get("queue_size", replay.get("replay_queue_size", queue_size)),
                "active": replay.get("active", replay_active),
                "avg_ms": replay.get("replay_avg_ms"),
                "blocks_per_second": replay.get("replay_blocks_per_second"),
                "errors": replay.get("errors", []),
            },
            "local_block_height": final_sync.get("local_block_height", final_sync.get("latest_block_height", 0)),
            "snapshot_height": final_sync.get("snapshot_height", 0),
            "effective_latest_block_height": final_sync.get(
                "effective_latest_block_height", final_sync.get("latest_block_height", 0)
            ),
            "catch_up_start_height": final_sync.get("catch_up_start_height", 0),
            "height": local_height,
            "latest_block_hash": local_hash,
            "peer_height": peer_height,
            "peer_block_hash": peer_hash,
            "matches_peer": peer_sync is None or (local_height == peer_height and local_hash == peer_hash),
            "pending_replay_blocks": final_sync.get("pending_replay_blocks", 0),
            "audit_valid": bool(final_audit.get("valid")),
        }
        rounds.append(round_summary)
        if (
            round_summary["pending_replay_blocks"] == 0
            and round_summary["audit_valid"]
            and not round_summary["replay"]["errors"]
            and blocks_seen < args.reconcile_limit
            and int(round_summary["replay"]["headers_imported"]) < args.replay_limit
            and round_summary["matches_peer"]
        ):
            break

    peer_matches = True
    if peer_sync is not None:
        final_height = int(final_sync.get("effective_latest_block_height", final_sync.get("latest_block_height", 0)) or 0)
        final_hash = final_sync.get("effective_latest_block_hash") or final_sync.get("latest_block_hash")
        peer_height = int(peer_sync.get("effective_latest_block_height", peer_sync.get("latest_block_height", 0)) or 0)
        peer_hash = peer_sync.get("effective_latest_block_hash") or peer_sync.get("latest_block_hash")
        peer_matches = (
            final_sync.get("network_id") == peer_sync.get("network_id")
            and final_sync.get("chain_id") == peer_sync.get("chain_id")
            and final_sync.get("genesis_hash") == peer_sync.get("genesis_hash")
            and final_height == peer_height
            and final_hash == peer_hash
        )
    healthy = final_sync.get("pending_replay_blocks", 0) == 0 and bool(final_audit.get("valid")) and peer_matches
    output = {
        "server": server_url,
        "peer": peer_url,
        "status": "ok" if healthy else "needs_attention",
        "initial_height": initial_sync.get("effective_latest_block_height", initial_sync.get("latest_block_height", 0)),
        "initial_local_block_height": initial_sync.get("local_block_height", initial_sync.get("latest_block_height", 0)),
        "initial_snapshot_height": initial_sync.get("snapshot_height", 0),
        "final_height": final_sync.get("effective_latest_block_height", final_sync.get("latest_block_height", 0)),
        "final_local_block_height": final_sync.get("local_block_height", final_sync.get("latest_block_height", 0)),
        "final_snapshot_height": final_sync.get("snapshot_height", 0),
        "final_block_hash": final_sync.get("effective_latest_block_hash") or final_sync.get("latest_block_hash"),
        "peer_height": (
            peer_sync.get("effective_latest_block_height", peer_sync.get("latest_block_height"))
            if peer_sync
            else None
        ),
        "peer_block_hash": (
            peer_sync.get("effective_latest_block_hash") or peer_sync.get("latest_block_hash")
            if peer_sync
            else None
        ),
        "peer_matches": peer_matches,
        "network_matches_peer": None if peer_sync is None else final_sync.get("network_id") == peer_sync.get("network_id"),
        "chain_matches_peer": None if peer_sync is None else final_sync.get("chain_id") == peer_sync.get("chain_id"),
        "genesis_matches_peer": None if peer_sync is None else final_sync.get("genesis_hash") == peer_sync.get("genesis_hash"),
        "pending_replay_blocks": final_sync.get("pending_replay_blocks", 0),
        "catch_up_start_height": final_sync.get("catch_up_start_height", 0),
        "headers_skipped_pre_snapshot": sum(
            int((round_item.get("replay") or {}).get("headers_skipped_pre_snapshot") or 0) for round_item in rounds
        ),
        "audit_valid": bool(final_audit.get("valid")),
        "audit_issues": final_audit.get("issues", []),
        "rounds": rounds,
    }
    print_json(output)
    return 0 if output["status"] == "ok" else 1


def command_node_report(args: argparse.Namespace) -> int:
    server_url = normalize_server_url(args.server)
    peer_url = normalize_server_url(args.peer) if args.peer else None
    checks: list[dict[str, Any]] = []
    payloads: dict[str, Any] = {}

    def add_check(name: str, ok: bool, detail: str, severity: str = "error") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "severity": severity})

    health = get_json(server_url, "/health")
    sync = get_json(server_url, "/node/sync-status")
    audit = get_json(server_url, "/audit/full")
    consensus = get_json(server_url, "/consensus/status")
    reserve = get_json(server_url, "/reserve/status")
    treasury = get_json(server_url, "/treasury/status")
    payloads.update(
        {
            "health": health,
            "sync": sync,
            "audit": audit,
            "consensus": consensus,
            "reserve": reserve,
            "treasury": treasury,
        }
    )

    add_check("api_health", health.get("status") == "ok", f"status={health.get('status')}")
    add_check("database", bool(health.get("database", {}).get("connected")), "database connected")
    add_check("chain", bool(health.get("chain", {}).get("valid")), "chain validation")
    add_check("audit", bool(audit.get("valid")), f"issues={len(audit.get('issues', []))}")
    add_check(
        "pending_replay",
        int(sync.get("pending_replay_blocks", 0)) == 0,
        f"pending_replay_blocks={sync.get('pending_replay_blocks', 0)}",
    )
    add_check(
        "peers",
        int(sync.get("peer_counts", {}).get("connected", 0)) > 0 or not args.require_peers,
        f"connected={sync.get('peer_counts', {}).get('connected', 0)}, total={sync.get('peer_counts', {}).get('total', 0)}",
        "warning",
    )
    consensus_counts = consensus.get("proposals", {})
    add_check(
        "consensus_backlog",
        int(consensus_counts.get("pending_missing_ancestors", 0)) == 0,
        f"pending_missing_ancestors={consensus_counts.get('pending_missing_ancestors', 0)}",
    )
    add_check(
        "fork_choice",
        int(consensus.get("fork_group_count", 0) or 0) == 0,
        (
            f"fork_groups={consensus.get('fork_group_count', 0)}, "
            f"competing_proposals={consensus.get('competing_proposal_count', 0)}"
        ),
        "warning",
    )
    reserve_status = str(reserve.get("status") or "")
    add_check(
        "science_reserve_locked",
        reserve_status in {"RESERVE_LOCKED", "L2_PENDING", "EMERGENCY_PAUSED"},
        f"status={reserve_status}, payouts_enabled={reserve.get('payouts_enabled')}",
        "warning",
    )
    add_check(
        "treasury_accounting",
        float(treasury.get("locked_balance", 0) or 0) >= 0 and float(treasury.get("unlocked_balance", 0) or 0) >= 0,
        f"locked={treasury.get('locked_balance')}, unlocked={treasury.get('unlocked_balance')}",
    )

    peer_sync: dict[str, Any] | None = None
    if peer_url:
        peer_sync = get_json(peer_url, "/node/sync-status")
        payloads["peer_sync"] = peer_sync
        local_height = int(sync.get("effective_latest_block_height", sync.get("latest_block_height", 0)) or 0)
        local_hash = sync.get("effective_latest_block_hash") or sync.get("latest_block_hash")
        peer_height = int(peer_sync.get("effective_latest_block_height", peer_sync.get("latest_block_height", 0)) or 0)
        peer_hash = peer_sync.get("effective_latest_block_hash") or peer_sync.get("latest_block_hash")
        add_check("network_match", sync.get("network_id") == peer_sync.get("network_id"), f"peer={peer_sync.get('network_id')}")
        add_check("chain_match", sync.get("chain_id") == peer_sync.get("chain_id"), f"peer={peer_sync.get('chain_id')}")
        add_check(
            "genesis_match",
            sync.get("genesis_hash") == peer_sync.get("genesis_hash"),
            f"peer={peer_sync.get('genesis_hash')}",
        )
        add_check(
            "height_match",
            local_height == peer_height,
            f"local={local_height}, peer={peer_height}",
        )
        add_check(
            "block_hash_match",
            local_hash == peer_hash,
            f"local={local_hash}, peer={peer_hash}",
        )

    failures = [check for check in checks if not check["ok"] and check["severity"] == "error"]
    warnings = [check for check in checks if not check["ok"] and check["severity"] == "warning"]
    output = {
        "server": server_url,
        "peer": peer_url,
        "status": "fail" if failures else "warn" if warnings else "ok",
        "height": sync.get("effective_latest_block_height", sync.get("latest_block_height", 0)),
        "block_hash": sync.get("effective_latest_block_hash") or sync.get("latest_block_hash"),
        "network_id": sync.get("network_id"),
        "chain_id": sync.get("chain_id"),
        "checks": checks,
        "summary": {"errors": len(failures), "warnings": len(warnings), "checked": len(checks)},
    }
    if args.verbose:
        output["payloads"] = payloads
    print_json(output)
    return 1 if failures else 0


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_matches(value: Any, expected: float, tolerance: float = 0.00000001) -> bool:
    parsed = _optional_float(value)
    return parsed is not None and abs(parsed - expected) <= tolerance


def _audit_issue_codes(audit: dict[str, Any]) -> set[str]:
    codes: set[str] = set()
    for issue in audit.get("issues", []) or []:
        if isinstance(issue, dict):
            code = issue.get("code")
            if code:
                codes.add(str(code))
        elif issue:
            codes.add(str(issue))
    return codes


def command_node_mainnet_preflight(args: argparse.Namespace) -> int:
    server_url = normalize_server_url(args.server)
    peer_url = normalize_server_url(args.peer) if args.peer else None
    checks: list[dict[str, Any]] = []
    payloads: dict[str, Any] = {}

    def add_check(name: str, ok: bool, detail: str, severity: str = "error") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "severity": severity})

    health = get_json(server_url, "/health")
    protocol = get_json(server_url, "/protocol")
    sync = get_json(server_url, "/node/sync-status")
    audit = get_json(server_url, "/audit/full")
    validators = get_json(server_url, "/validators/status")
    mempool = get_json(server_url, "/mempool/status")
    consensus = get_json(server_url, "/consensus/status")
    validation_health = get_json(server_url, "/validation/jobs/health")
    difficulty = get_json(server_url, "/difficulty")
    payloads.update(
        {
            "health": health,
            "protocol": protocol,
            "sync": sync,
            "audit": audit,
            "validators": validators,
            "mempool": mempool,
            "consensus": consensus,
            "validation_health": validation_health,
            "difficulty": difficulty,
        }
    )

    add_check("api_health", health.get("status") == "ok", f"status={health.get('status')}")
    add_check("database", bool(health.get("database", {}).get("connected")), "database connected")
    add_check("chain", bool(health.get("chain", {}).get("valid")), "chain validation")
    add_check("audit_valid", bool(audit.get("valid")), f"issues={len(audit.get('issues', []) or [])}")
    add_check("network_id", protocol.get("network_id") == MAINNET_PROFILE.network_id, f"network_id={protocol.get('network_id')}")
    add_check("chain_id", protocol.get("chain_id") == MAINNET_PROFILE.chain_id, f"chain_id={protocol.get('chain_id')}")
    add_check(
        "protocol_version",
        str(protocol.get("protocol_version")) == MAINNET_PROFILE.protocol_version,
        f"protocol_version={protocol.get('protocol_version')}",
    )
    add_check("faucet_disabled", protocol.get("faucet_enabled") is False, f"faucet_enabled={protocol.get('faucet_enabled')}")
    add_check(
        "validator_quorum_frozen",
        _optional_int(protocol.get("required_validator_approvals")) == MAINNET_PROFILE.required_validator_approvals,
        f"required_validator_approvals={protocol.get('required_validator_approvals')}",
    )
    retarget_max_pi_position = _optional_int(protocol.get("RETARGET_MAX_PI_POSITION"))
    difficulty_retarget_max = _optional_int(difficulty.get("RETARGET_MAX_PI_POSITION"))
    add_check(
        "pi_depth_cap_frozen",
        retarget_max_pi_position == MAINNET_RETARGET_MAX_PI_POSITION
        and (difficulty_retarget_max is None or difficulty_retarget_max == MAINNET_RETARGET_MAX_PI_POSITION),
        (
            f"protocol_RETARGET_MAX_PI_POSITION={protocol.get('RETARGET_MAX_PI_POSITION')}, "
            f"difficulty_RETARGET_MAX_PI_POSITION={difficulty.get('RETARGET_MAX_PI_POSITION')}"
        ),
    )
    protocol_task_expiration = _optional_int(protocol.get("task_expiration_seconds"))
    active_task_expiration = _optional_int(difficulty.get("active_task_expiration_seconds"))
    required_task_expiration = _optional_int(difficulty.get("required_task_expiration_seconds"))
    effective_task_expiration = _optional_int(difficulty.get("effective_task_expiration_seconds"))
    dynamic_expiration_cap = int(get_dynamic_expiration(MAINNET_RETARGET_MAX_PI_POSITION))
    task_expiration_floor_ok = (
        protocol_task_expiration is not None
        and active_task_expiration is not None
        and required_task_expiration is not None
        and effective_task_expiration is not None
        and protocol_task_expiration >= int(get_dynamic_expiration(1))
        and active_task_expiration == protocol_task_expiration
        and effective_task_expiration >= required_task_expiration
        and effective_task_expiration <= dynamic_expiration_cap
    )
    add_check(
        "dynamic_task_expiration",
        task_expiration_floor_ok,
        (
            f"protocol={protocol.get('task_expiration_seconds')}, "
            f"active={difficulty.get('active_task_expiration_seconds')}, "
            f"required={difficulty.get('required_task_expiration_seconds')}, "
            f"effective={difficulty.get('effective_task_expiration_seconds')}, "
            f"cap={dynamic_expiration_cap}"
        ),
    )
    add_check(
        "validator_eligibility_wallet_backed",
        protocol.get("validator_eligibility_stake_field") == "wallet_stake_locked"
        and protocol.get("validator_eligibility_stake_source") == "wallet",
        (
            f"field={protocol.get('validator_eligibility_stake_field')}, "
            f"source={protocol.get('validator_eligibility_stake_source')}"
        ),
    )
    treasury_wallet = protocol.get("scientific_development_treasury_wallet")
    governance_wallet = protocol.get("scientific_development_governance_wallet")
    add_check(
        "treasury_wallet_canonical",
        is_valid_address(treasury_wallet),
        f"treasury_wallet={treasury_wallet}",
    )
    add_check(
        "governance_wallet_canonical",
        is_valid_address(governance_wallet),
        f"governance_wallet={governance_wallet}",
    )
    add_check(
        "treasury_governance_wallets_distinct",
        bool(treasury_wallet) and bool(governance_wallet) and treasury_wallet != governance_wallet,
        f"treasury_wallet={treasury_wallet}, governance_wallet={governance_wallet}",
    )

    reward_checks = {
        "proof_of_pi_reward_percent": MAINNET_PROFILE.proof_of_pi_reward_percent,
        "validator_reward_percent": MAINNET_PROFILE.validator_reward_percent,
        "science_compute_reward_percent": MAINNET_PROFILE.science_compute_reward_percent,
        "scientific_development_reward_percent": MAINNET_PROFILE.scientific_development_reward_percent,
    }
    for field, expected in reward_checks.items():
        add_check(field, _float_matches(protocol.get(field), expected), f"{field}={protocol.get(field)}, expected={expected}")
    reward_sum = sum(_optional_float(protocol.get(field)) or 0.0 for field in reward_checks)
    add_check("reward_percent_sum", abs(reward_sum - 1.0) <= 0.00000001, f"sum={round(reward_sum, 8)}")
    add_check(
        "retroactive_audit_no_emission",
        _float_matches(protocol.get("retroactive_audit_reward_percent"), 0.0)
        and _float_matches(protocol.get("retroactive_audit_reward_per_audit"), 0.0),
        (
            f"percent={protocol.get('retroactive_audit_reward_percent')}, "
            f"per_audit={protocol.get('retroactive_audit_reward_per_audit')}"
        ),
    )

    issue_codes = _audit_issue_codes(audit)
    add_check(
        "no_legacy_mainnet_validator_stake",
        "mainnet_legacy_validator_stake" not in issue_codes,
        f"issue_codes={sorted(issue_codes)}",
    )

    required_approvals = _optional_int(validators.get("required_validator_approvals")) or MAINNET_PROFILE.required_validator_approvals
    eligible_count = _optional_int(validators.get("eligible_validators")) or 0
    add_check(
        "validator_quorum_available",
        required_approvals > 0 and eligible_count >= required_approvals,
        f"eligible={eligible_count}, required={required_approvals}",
    )

    validator_rows = validators.get("validators", []) or []
    eligible_validators = [validator for validator in validator_rows if validator.get("eligible") is True]
    min_stake = _optional_float(protocol.get("min_validator_stake")) or MAINNET_PROFILE.min_validator_stake
    missing_wallet_stake = []
    unhealthy_validators = []
    rewardless_validators = []
    for validator in eligible_validators:
        validator_id = validator.get("validator_id")
        eligibility_stake = _optional_float(validator.get("eligibility_stake"))
        if eligibility_stake is None:
            eligibility_stake = _optional_float(validator.get("wallet_stake_locked"))
        if eligibility_stake is None or eligibility_stake + 0.00000001 < min_stake:
            missing_wallet_stake.append(validator_id)
        if validator.get("eligibility_stake_source") not in {None, "wallet"}:
            missing_wallet_stake.append(validator_id)
        if validator.get("online_status") != "online" or validator.get("sync_status") != "synced":
            unhealthy_validators.append(validator_id)
        if not validator.get("reward_address"):
            rewardless_validators.append(validator_id)
    add_check(
        "eligible_validators_wallet_staked",
        not missing_wallet_stake and bool(eligible_validators),
        f"missing_or_legacy={missing_wallet_stake}, eligible_checked={len(eligible_validators)}",
    )
    add_check(
        "eligible_validators_healthy",
        not unhealthy_validators and bool(eligible_validators),
        f"unhealthy={unhealthy_validators}, eligible_checked={len(eligible_validators)}",
    )
    add_check(
        "eligible_validators_reward_addresses",
        not rewardless_validators and bool(eligible_validators),
        f"missing_reward_address={rewardless_validators}",
        "warning",
    )

    replay = sync.get("replay", {}) or {}
    add_check(
        "pending_replay_clear",
        int(sync.get("pending_replay_blocks", 0) or 0) == 0
        and int(replay.get("queue_size", 0) or 0) == 0
        and int(replay.get("finalized_queue_size", 0) or 0) == 0
        and int(replay.get("header_queue_size", 0) or 0) == 0,
        (
            f"pending_replay_blocks={sync.get('pending_replay_blocks', 0)}, "
            f"queue={replay.get('queue_size', 0)}, finalized={replay.get('finalized_queue_size', 0)}, "
            f"headers={replay.get('header_queue_size', 0)}"
        ),
    )
    consensus_counts = consensus.get("proposals", {}) or sync.get("consensus", {}) or {}
    add_check(
        "consensus_backlog_clear",
        int(consensus_counts.get("pending_missing_ancestors", 0) or 0) == 0,
        f"pending_missing_ancestors={consensus_counts.get('pending_missing_ancestors', 0)}",
    )
    add_check(
        "no_fork_groups",
        int(consensus.get("fork_group_count", 0) or 0) == 0
        and int(consensus.get("competing_proposal_count", 0) or 0) == 0,
        (
            f"fork_groups={consensus.get('fork_group_count', 0)}, "
            f"competing_proposals={consensus.get('competing_proposal_count', 0)}"
        ),
        "warning",
    )
    mempool_pending = int(mempool.get("pending_count", 0) or 0)
    mempool_selected = int(mempool.get("selected_count", 0) or 0)
    mempool_empty = mempool_pending == 0 and mempool_selected == 0
    add_check(
        "mempool_empty",
        mempool_empty,
        f"pending={mempool_pending}, selected={mempool_selected}",
        "warning" if args.allow_mempool else "error",
    )
    add_check(
        "validation_jobs_not_stuck",
        bool(validation_health.get("healthy", False)),
        (
            f"pending={validation_health.get('pending_count', 0)}, "
            f"stuck={validation_health.get('stuck_count', 0)}, "
            f"counts={validation_health.get('counts', {})}"
        ),
    )

    peer_sync: dict[str, Any] | None = None
    if peer_url:
        peer_sync = get_json(peer_url, "/node/sync-status")
        payloads["peer_sync"] = peer_sync
        local_height = int(sync.get("effective_latest_block_height", sync.get("latest_block_height", 0)) or 0)
        local_hash = sync.get("effective_latest_block_hash") or sync.get("latest_block_hash")
        peer_height = int(peer_sync.get("effective_latest_block_height", peer_sync.get("latest_block_height", 0)) or 0)
        peer_hash = peer_sync.get("effective_latest_block_hash") or peer_sync.get("latest_block_hash")
        add_check("peer_network_match", sync.get("network_id") == peer_sync.get("network_id"), f"peer={peer_sync.get('network_id')}")
        add_check("peer_chain_match", sync.get("chain_id") == peer_sync.get("chain_id"), f"peer={peer_sync.get('chain_id')}")
        add_check(
            "peer_genesis_match",
            sync.get("genesis_hash") == peer_sync.get("genesis_hash"),
            f"peer={peer_sync.get('genesis_hash')}",
        )
        add_check("peer_height_match", local_height == peer_height, f"local={local_height}, peer={peer_height}")
        add_check("peer_block_hash_match", local_hash == peer_hash, f"local={local_hash}, peer={peer_hash}")

    failures = [check for check in checks if not check["ok"] and check["severity"] == "error"]
    warnings = [check for check in checks if not check["ok"] and check["severity"] == "warning"]
    output = {
        "server": server_url,
        "peer": peer_url,
        "status": "fail" if failures else "warn" if warnings else "ok",
        "network_id": protocol.get("network_id"),
        "chain_id": protocol.get("chain_id"),
        "protocol_version": protocol.get("protocol_version"),
        "height": sync.get("effective_latest_block_height", sync.get("latest_block_height", 0)),
        "block_hash": sync.get("effective_latest_block_hash") or sync.get("latest_block_hash"),
        "eligible_validators": eligible_count,
        "required_validator_approvals": required_approvals,
        "checks": checks,
        "summary": {"errors": len(failures), "warnings": len(warnings), "checked": len(checks)},
    }
    if args.verbose:
        output["payloads"] = payloads
    print_json(output)
    return 1 if failures else 0


def command_node_compare(args: argparse.Namespace) -> int:
    server_url = normalize_server_url(args.server)
    peer_url = normalize_server_url(args.peer)
    local = get_json(server_url, "/node/sync-status")
    peer = get_json(peer_url, "/node/sync-status")
    local_height = int(local.get("effective_latest_block_height", local.get("latest_block_height", 0)) or 0)
    local_hash = local.get("effective_latest_block_hash") or local.get("latest_block_hash")
    peer_height = int(peer.get("effective_latest_block_height", peer.get("latest_block_height", 0)) or 0)
    peer_hash = peer.get("effective_latest_block_hash") or peer.get("latest_block_hash")
    comparisons = {
        "network_id": local.get("network_id") == peer.get("network_id"),
        "chain_id": local.get("chain_id") == peer.get("chain_id"),
        "genesis_hash": local.get("genesis_hash") == peer.get("genesis_hash"),
        "height": local_height == peer_height,
        "block_hash": local_hash == peer_hash,
    }
    output = {
        "server": server_url,
        "peer": peer_url,
        "status": "ok" if all(comparisons.values()) else "mismatch",
        "comparisons": comparisons,
        "local": {
            "height": local_height,
            "block_hash": local_hash,
            "local_block_height": local.get("latest_block_height", 0),
            "local_block_hash": local.get("latest_block_hash"),
            "network_id": local.get("network_id"),
            "chain_id": local.get("chain_id"),
            "genesis_hash": local.get("genesis_hash"),
            "pending_replay_blocks": local.get("pending_replay_blocks", 0),
        },
        "peer_state": {
            "height": peer_height,
            "block_hash": peer_hash,
            "local_block_height": peer.get("latest_block_height", 0),
            "local_block_hash": peer.get("latest_block_hash"),
            "network_id": peer.get("network_id"),
            "chain_id": peer.get("chain_id"),
            "genesis_hash": peer.get("genesis_hash"),
            "pending_replay_blocks": peer.get("pending_replay_blocks", 0),
        },
    }
    print_json(output)
    return 0 if output["status"] == "ok" else 1


def _first_peer_block(peer_url: str, height: int) -> dict[str, Any] | None:
    payload = get_json(peer_url, f"/node/sync/blocks?from_height={height - 1}&limit=1")
    blocks = payload.get("blocks") or []
    for block in blocks:
        if int(block.get("height") or 0) == height:
            return block
    return None


def _diff_json_values(left: Any, right: Any, path: str = "$") -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            child_path = f"{path}.{key}"
            if key not in left:
                diffs.append({"path": child_path, "local": None, "remote": right[key], "reason": "missing_local"})
            elif key not in right:
                diffs.append({"path": child_path, "local": left[key], "remote": None, "reason": "missing_remote"})
            else:
                diffs.extend(_diff_json_values(left[key], right[key], child_path))
        return diffs
    if isinstance(left, list) and isinstance(right, list):
        max_len = max(len(left), len(right))
        for index in range(max_len):
            child_path = f"{path}[{index}]"
            if index >= len(left):
                diffs.append({"path": child_path, "local": None, "remote": right[index], "reason": "missing_local"})
            elif index >= len(right):
                diffs.append({"path": child_path, "local": left[index], "remote": None, "reason": "missing_remote"})
            else:
                diffs.extend(_diff_json_values(left[index], right[index], child_path))
        return diffs
    if left != right:
        diffs.append({"path": path, "local": left, "remote": right, "reason": "value_mismatch"})
    return diffs


def command_node_compare_block_payloads(args: argparse.Namespace) -> int:
    server_url = normalize_server_url(args.server)
    peer_url = normalize_server_url(args.peer)
    remote_block = _first_peer_block(peer_url, args.height)
    if remote_block is None:
        print_json(
            {
                "status": "error",
                "server": server_url,
                "peer": peer_url,
                "height": args.height,
                "error": "remote block not found",
            }
        )
        return 1
    local_debug = get_json(server_url, f"/consensus/debug/block/{args.height}")
    remote_debug = post_json(server_url, "/consensus/debug/hash", remote_block)
    local_payload = local_debug.get("normalized_payload")
    remote_payload = remote_debug.get("normalized_payload")
    diffs = _diff_json_values(local_payload, remote_payload) if local_payload is not None else []
    output = {
        "status": "ok" if remote_debug.get("matched") and not diffs else "mismatch",
        "server": server_url,
        "peer": peer_url,
        "height": args.height,
        "local": {
            "source": local_debug.get("source"),
            "expected_hash": local_debug.get("expected_hash"),
            "computed_hash": local_debug.get("computed_hash"),
            "matched": local_debug.get("matched"),
            "matched_variant": local_debug.get("matched_variant"),
        },
        "remote": {
            "expected_hash": remote_debug.get("expected_hash"),
            "computed_hash": remote_debug.get("computed_hash"),
            "matched": remote_debug.get("matched"),
            "matched_variant": remote_debug.get("matched_variant"),
        },
        "diffs": diffs[: args.max_diffs],
        "diff_count": len(diffs),
    }
    print_json(output)
    return 0 if output["status"] == "ok" else 1


def command_debug_replay_check(args: argparse.Namespace) -> int:
    server_url = normalize_server_url(args.server)
    params = f"from_height={args.from_height}&to_height={args.to_height}"
    if args.peer:
        params += f"&peer={args.peer}"
    report = get_json(server_url, f"/debug/replay/divergence?{params}")
    print_json(report)
    return 0 if report.get("status") == "ok" else 1


def command_node_checkpoint_list(args: argparse.Namespace) -> int:
    print_json(get_json(args.server, f"/node/checkpoints?limit={args.limit}"))
    return 0


def command_node_checkpoint_latest(args: argparse.Namespace) -> int:
    print_json(get_json(args.server, "/node/checkpoints/latest"))
    return 0


def command_node_checkpoint_create(args: argparse.Namespace) -> int:
    query = f"trusted={str(args.trusted).lower()}&source={args.source}"
    if args.height is not None:
        query = f"height={args.height}&{query}"
    print_json(post_json(args.server, f"/node/checkpoints?{query}"))
    return 0


def command_node_checkpoint_verify(args: argparse.Namespace) -> int:
    print_json(post_json(args.server, f"/node/checkpoints/{args.height}/verify"))
    return 0


def command_node_checkpoint_export(args: argparse.Namespace) -> int:
    path = "/node/snapshots/export"
    if args.height is not None:
        path = f"{path}?height={args.height}"
    snapshot = get_json(args.server, path)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
    print_json(snapshot)
    return 0


def command_node_checkpoint_import(args: argparse.Namespace) -> int:
    snapshot = json.loads(args.file.read_text(encoding="utf-8"))
    print_json(post_json(args.server, "/node/snapshots/import", {"snapshot": snapshot, "source": args.source}))
    return 0


def command_node_checkpoint_imports(args: argparse.Namespace) -> int:
    print_json(get_json(args.server, f"/node/snapshots/imports?limit={args.limit}"))
    return 0


def command_node_checkpoint_activate(args: argparse.Namespace) -> int:
    print_json(post_json(args.server, f"/node/snapshots/{args.snapshot_hash}/activate"))
    return 0


def command_node_checkpoint_apply(args: argparse.Namespace) -> int:
    print_json(post_json(args.server, f"/node/snapshots/{args.snapshot_hash}/apply"))
    return 0


def command_node_checkpoint_restore_peer(args: argparse.Namespace) -> int:
    server_url = normalize_server_url(args.server)
    peer_url = normalize_server_url(args.peer)
    path = "/node/snapshots/export"
    if args.height is not None:
        path = f"{path}?height={args.height}"
    snapshot = get_json(peer_url, path)
    imported = post_json(server_url, "/node/snapshots/import", {"snapshot": snapshot, "source": args.source})
    snapshot_hash = imported.get("snapshot", {}).get("snapshot_hash") or snapshot.get("checkpoint", {}).get("snapshot_hash")
    restored = post_json(server_url, f"/node/snapshots/{snapshot_hash}/restore")
    output = {
        "status": "ok" if restored.get("applied") else "fail",
        "server": server_url,
        "peer": peer_url,
        "height": restored.get("height"),
        "snapshot_hash": snapshot_hash,
        "import": imported,
        "restore": restored,
    }
    print_json(output)
    return 0 if output["status"] == "ok" else 1


def command_node_checkpoint_restore_sqlite(args: argparse.Namespace) -> int:
    snapshot = _snapshot_from_sqlite(args.file, args.height)
    validation = validate_snapshot_document(snapshot)
    if not validation["valid"]:
        raise SystemExit(f"invalid backup snapshot: {', '.join(validation['issues'])}")
    backup_path = None
    if args.backup_current:
        backup_path = args.backup_current
        if backup_path.is_dir():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_path = backup_path / f"pre-snapshot-restore-{stamp}.sqlite3"
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        if DATABASE_PATH.exists():
            shutil.copy2(DATABASE_PATH, backup_path)
    imported = import_canonical_snapshot(snapshot, source=args.source)
    restored = restore_imported_snapshot_state(imported["snapshot"]["snapshot_hash"])
    print_json(
        {
            "status": "ok" if restored.get("applied") else "fail",
            "source_file": str(args.file),
            "backup_current": str(backup_path) if backup_path else None,
            "height": restored.get("height"),
            "snapshot_hash": imported["snapshot"]["snapshot_hash"],
            "restore": restored,
        }
    )
    return 0 if restored.get("applied") else 1


def _snapshot_from_sqlite(path: Path, height: int | None) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"SQLite backup not found: {path}")
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        if height is None:
            latest = connection.execute("SELECT COALESCE(MAX(height), 0) AS height FROM blocks").fetchone()
            height = int(latest["height"] if latest else 0)
        block = connection.execute(
            "SELECT height, previous_hash, block_hash, state_root, timestamp FROM blocks WHERE height = ?",
            (height,),
        ).fetchone()
        if block is None:
            raise SystemExit(f"block height {height} not found in {path}")
        state_root = block["state_root"] or calculate_state_root(connection, height, block["timestamp"])
        balances = balance_snapshot(connection, height, block["timestamp"])
        account_types = {
            row["account_id"]: row["account_type"]
            for row in connection.execute("SELECT account_id, account_type FROM balances").fetchall()
        }
        export_balances = [
            {
                **item,
                "account_type": account_types.get(item["account_id"], _infer_account_type_for_cli_snapshot(item["account_id"])),
            }
            for item in balances
        ]
        balances_hash = sha256_text(canonical_json({"height": height, "balances": balances}))
        validators = validator_snapshot(connection)
        validators_hash = sha256_text(canonical_json({"height": height, "validators": validators}))
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
        total_balance = round(total_balance_units / 1_000_000, 8)
        checkpoint = {
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
            "validators_hash": validators_hash,
            "validators_count": len(validators),
            "ledger_entries_count": ledger_entries_count,
            "total_balance": total_balance,
            "total_balance_units": total_balance_units,
        }
        checkpoint["snapshot_hash"] = sha256_text(canonical_json(checkpoint))
        return {
            "snapshot_version": 1,
            "type": "picoin_canonical_snapshot",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "checkpoint": checkpoint,
            "balances": export_balances,
            "validators": validators,
        }
    finally:
        connection.close()


def _infer_account_type_for_cli_snapshot(account_id: str) -> str:
    if account_id == "genesis":
        return "genesis"
    if account_id.startswith("miner_"):
        return "miner"
    if account_id.startswith("validator_"):
        return "validator"
    if account_id.startswith("science_"):
        return "science_stake"
    if account_id == "science_compute_reserve":
        return "science_reserve"
    if account_id == "scientific_development_treasury":
        return "scientific_development_treasury"
    return "wallet"


def command_node_genesis_hash(args: argparse.Namespace) -> int:
    document = load_genesis_allocations(args.file)
    if args.mainnet:
        validate_mainnet_genesis_allocations(document)
    payload = {
        "genesis_hash": genesis_allocations_hash(document),
        **genesis_allocation_summary(document),
    }
    if args.mainnet:
        payload["mainnet_valid"] = True
    print_json(payload)
    return 0


def command_node_bootstrap_validator_stake(args: argparse.Namespace) -> int:
    from app.services.mainnet_bootstrap import MainnetBootstrapError, apply_bootstrap_validator_stakes

    try:
        print_json(apply_bootstrap_validator_stakes(args.file, dry_run=args.dry_run))
        return 0
    except MainnetBootstrapError as exc:
        print(f"Mainnet bootstrap error: {exc}")
        return 2


def command_wallet_create(args: argparse.Namespace) -> int:
    wallet = create_wallet(args.name, network_id=args.network, chain_id=args.chain_id)
    output = args.output or DEFAULT_WALLET_PATH
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(wallet, indent=2, sort_keys=True), encoding="utf-8")
    print_json(wallet)
    return 0


def _load_wallet_file(path: Path | None) -> dict[str, Any]:
    wallet_path = path or DEFAULT_WALLET_PATH
    return json.loads(wallet_path.read_text(encoding="utf-8"))


def command_wallet_import(args: argparse.Namespace) -> int:
    wallet = json.loads(args.file.read_text(encoding="utf-8"))
    if "address" not in wallet and "public_key" in wallet:
        wallet["address"] = address_from_public_key(wallet["public_key"])
    output = args.output or DEFAULT_WALLET_PATH
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(wallet, indent=2, sort_keys=True), encoding="utf-8")
    print_json({"status": "ok", "address": wallet.get("address"), "wallet": str(output)})
    return 0


def command_wallet_export(args: argparse.Namespace) -> int:
    print_json(_load_wallet_file(args.wallet))
    return 0


def command_wallet_address(args: argparse.Namespace) -> int:
    wallet = _load_wallet_file(args.wallet)
    print_json({"address": wallet.get("address") or address_from_public_key(wallet["public_key"])})
    return 0


def command_wallet_balance(args: argparse.Namespace) -> int:
    address = args.address
    if not address:
        address = _load_wallet_file(args.wallet).get("address")
    print_json(get_json(args.server, f"/accounts/{address}"))
    return 0


def command_wallet_history(args: argparse.Namespace) -> int:
    address = args.address
    if not address:
        address = _load_wallet_file(args.wallet).get("address")
    print_json(get_json(args.server, f"/accounts/{address}/history?limit={args.limit}"))
    return 0


def command_wallet_nonce(args: argparse.Namespace) -> int:
    print_json(get_json(args.server, f"/wallet/{args.address}/nonce"))
    return 0


def command_tx_send(args: argparse.Namespace) -> int:
    wallet = _load_wallet_file(args.wallet)
    payload = json.loads(args.payload) if args.payload else {}
    if getattr(args, "stake_type", None):
        payload = {**payload, "stake_type": args.stake_type}
    if getattr(args, "validator_id", None):
        payload = {**payload, "stake_type": "validator", "validator_id": args.validator_id}
    sender = args.sender or wallet["address"]
    nonce = args.nonce
    if nonce is None:
        nonce = int(get_json(args.server, f"/wallet/{sender}/nonce")["next_nonce"])
    tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type=args.type,
        sender=sender,
        recipient=args.to,
        amount=args.amount,
        nonce=nonce,
        fee=args.fee,
        payload=payload,
    )
    print_json(post_json(args.server, "/transactions/submit", tx))
    return 0


def command_tx_status(args: argparse.Namespace) -> int:
    print_json(get_json(args.server, f"/tx/{args.hash}"))
    return 0


def command_tx_mempool(args: argparse.Namespace) -> int:
    path = f"/mempool?limit={args.limit}"
    if args.status:
        path = f"{path}&status={args.status}"
    print_json(get_json(args.server, path))
    return 0


def command_consensus_status(args: argparse.Namespace) -> int:
    print_json(get_json(args.server, "/consensus/status"))
    return 0


def command_validators_list(args: argparse.Namespace) -> int:
    print_json(get_json(args.server, "/validators/status"))
    return 0


def command_validators_prune_stale(args: argparse.Namespace) -> int:
    print_json(post_json(args.server, f"/validators/prune-stale?older_than={args.older_than}"))
    return 0


def command_validators_enable(args: argparse.Namespace) -> int:
    print_json(post_json(args.server, f"/validators/{args.validator_id}/enable"))
    return 0


def command_validators_disable(args: argparse.Namespace) -> int:
    print_json(post_json(args.server, f"/validators/{args.validator_id}/disable"))
    return 0


def command_miners_list(args: argparse.Namespace) -> int:
    print_json(get_json(args.server, "/miners/status"))
    return 0


def command_miners_prune_stale(args: argparse.Namespace) -> int:
    print_json(post_json(args.server, f"/miners/prune-stale?older_than={args.older_than}"))
    return 0


def command_miners_enable(args: argparse.Namespace) -> int:
    print_json(post_json(args.server, f"/miners/{args.miner_id}/enable"))
    return 0


def command_miners_disable(args: argparse.Namespace) -> int:
    print_json(post_json(args.server, f"/miners/{args.miner_id}/disable"))
    return 0


def command_consensus_proposals(args: argparse.Namespace) -> int:
    path = f"/consensus/proposals?limit={args.limit}"
    if args.status:
        path = f"{path}&status={args.status}"
    print_json(get_json(args.server, path))
    return 0


def command_consensus_votes(args: argparse.Namespace) -> int:
    print_json(get_json(args.server, f"/consensus/proposals/{args.proposal_id}/votes"))
    return 0


def command_consensus_propose(args: argparse.Namespace) -> int:
    block = json.loads(args.block.read_text(encoding="utf-8"))
    print_json(
        post_json(
            args.server,
            "/consensus/proposals",
            {"block": block, "proposer_node_id": args.proposer},
        )
    )
    return 0


def command_consensus_vote(args: argparse.Namespace) -> int:
    identity = json.loads(args.identity.read_text(encoding="utf-8"))
    proposal = get_json(args.server, f"/consensus/proposals/{args.proposal_id}")
    signed_at = _utc_now()
    payload = consensus_vote_payload(
        proposal_id=args.proposal_id,
        block_hash=proposal["block_hash"],
        height=proposal["height"],
        validator_id=identity["validator_id"],
        approved=not args.reject,
        reason=args.reason,
        signed_at=signed_at,
    )
    signature = sign_payload(identity["private_key"], payload)
    print_json(
        post_json(
            args.server,
            f"/consensus/proposals/{args.proposal_id}/vote",
            {
                "validator_id": identity["validator_id"],
                "approved": not args.reject,
                "reason": args.reason,
                "signature": signature,
                "signed_at": signed_at,
            },
        )
    )
    return 0


def command_consensus_finalize(args: argparse.Namespace) -> int:
    print_json(post_json(args.server, f"/consensus/proposals/{args.proposal_id}/finalize"))
    return 0


def command_consensus_replay(args: argparse.Namespace) -> int:
    print_json(post_json(args.server, f"/consensus/replay?limit={args.limit}"))
    return 0


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def command_science_stake(args: argparse.Namespace) -> int:
    print_json(post_json(args.server, "/science/stake", {"address": args.address, "amount": args.amount}))
    return 0


def command_science_account(args: argparse.Namespace) -> int:
    print_json(get_json(args.server, f"/science/accounts/{args.address}"))
    return 0


def command_science_create_job(args: argparse.Namespace) -> int:
    print_json(
        post_json(
            args.server,
            "/science/jobs",
            {
                "requester_address": args.address,
                "job_type": args.type,
                "metadata_hash": args.metadata_hash,
                "storage_pointer": args.storage_pointer,
                "max_compute_units": args.max_compute_units,
                "reward_per_compute_unit": args.reward_per_unit,
                "max_reward": args.max_reward,
            },
        )
    )
    return 0


def command_science_jobs(args: argparse.Namespace) -> int:
    path = f"/science/jobs?limit={args.limit}"
    if args.address:
        path = f"{path}&address={args.address}"
    print_json(get_json(args.server, path))
    return 0


def command_science_accept_job(args: argparse.Namespace) -> int:
    print_json(
        post_json(
            args.server,
            f"/science/jobs/{args.job_id}/accept",
            {
                "worker_address": args.worker_address,
                "result_hash": args.result_hash,
                "proof_hash": args.proof_hash,
                "compute_units_used": args.compute_units_used,
            },
        )
    )
    return 0


def command_science_pay_worker(args: argparse.Namespace) -> int:
    print_json(post_json(args.server, f"/science/jobs/{args.job_id}/pay"))
    return 0


def command_science_reserve(args: argparse.Namespace) -> int:
    print_json(get_json(args.server, "/science/reserve"))
    return 0


def command_science_reserve_governance(args: argparse.Namespace) -> int:
    print_json(get_json(args.server, "/science/reserve/governance"))
    return 0


def command_science_propose_activation(args: argparse.Namespace) -> int:
    print_json(
        post_json(
            args.server,
            "/science/reserve/governance/propose-activation",
            {"signer": args.signer},
        )
    )
    return 0


def command_science_approve_activation(args: argparse.Namespace) -> int:
    print_json(
        post_json(
            args.server,
            "/science/reserve/governance/approve-activation",
            {"signer": args.signer},
        )
    )
    return 0


def command_science_execute_activation(args: argparse.Namespace) -> int:
    print_json(post_json(args.server, "/science/reserve/governance/execute-activation"))
    return 0


def command_treasury_status(args: argparse.Namespace) -> int:
    print_json(get_json(args.server, "/treasury/status"))
    return 0


def command_treasury_claim(args: argparse.Namespace) -> int:
    payload: dict[str, Any] = {}
    if args.requested_by:
        payload["requested_by"] = args.requested_by
    if args.claim_to:
        payload["claim_to"] = args.claim_to
    print_json(post_json(args.server, "/treasury/claim", payload))
    return 0


def command_reserve_status(args: argparse.Namespace) -> int:
    print_json(get_json(args.server, "/reserve/status"))
    return 0


def command_reserve_pause(args: argparse.Namespace) -> int:
    print_json(post_json(args.server, "/reserve/pause", {"signer": args.signer}))
    return 0


def command_reserve_unpause(args: argparse.Namespace) -> int:
    print_json(post_json(args.server, "/reserve/unpause", {"signer": args.signer}))
    return 0


def command_testnet_reset(args: argparse.Namespace) -> int:
    forwarded = ["--yes"]
    if args.keep_identities:
        forwarded.append("--keep-identities")
    _run_tool_main(reset_testnet_main, forwarded)
    return 0


def command_testnet_bootstrap(args: argparse.Namespace) -> int:
    forwarded = [
        "--server",
        args.server,
        "--identity-dir",
        str(args.identity_dir),
        "--miner-faucet",
        str(args.miner_faucet),
    ]
    _run_tool_main(bootstrap_testnet_main, forwarded)
    return 0


def command_testnet_cycle(args: argparse.Namespace) -> int:
    forwarded = [
        "--server",
        args.server,
        "--miner",
        str(args.miner_identity),
        "--validator-one",
        str(args.validator_one_identity),
        "--validator-two",
        str(args.validator_two_identity),
        "--validator-three",
        str(args.validator_three_identity),
        "--workers",
        str(args.workers),
    ]
    _run_tool_main(run_testnet_cycle_main, forwarded)
    return 0


def command_testnet_continuous(args: argparse.Namespace) -> int:
    forwarded = [
        "--server",
        args.server,
        "--identity-dir",
        str(args.identity_dir),
        "--miners",
        str(args.miners),
        "--loops",
        str(args.loops),
        "--workers",
        str(args.workers),
        "--sleep",
        str(args.sleep),
        "--faucet",
        str(args.faucet),
    ]
    if not args.retro_audit:
        forwarded.append("--no-retro-audit")
    _run_tool_main(run_testnet_multi_miner_main, forwarded)
    return 0


def command_testnet_fund_wallet(args: argparse.Namespace) -> int:
    if args.wallet:
        wallet = json.loads(args.wallet.read_text(encoding="utf-8"))
        public_key = wallet["public_key"]
        sender = wallet.get("address") or address_from_public_key(public_key)
        if not address_matches_public_key(sender, public_key):
            sender = address_from_public_key(public_key)
        nonce = args.nonce
        if nonce is None:
            nonce = int(get_json(args.server, f"/wallet/{sender}/nonce")["next_nonce"])
        tx = sign_transaction(
            private_key=wallet["private_key"],
            public_key=public_key,
            tx_type="faucet",
            sender=sender,
            amount=args.amount,
            nonce=nonce,
            fee=0.0,
        )
        print_json(post_json(args.server, "/tx/submit", tx))
        return 0
    if not args.address:
        raise SystemExit("--wallet is required for canonical funding; --address is only for legacy local faucet credits")
    print_json(
        post_json(
            args.server,
            "/faucet",
            {
                "account_id": args.address,
                "account_type": "wallet",
                "amount": args.amount,
            },
        )
    )
    return 0


def _run_tool_main(main_func: Any, argv: list[str]) -> None:
    import sys

    old_argv = sys.argv
    try:
        sys.argv = [old_argv[0], *argv]
        main_func()
    finally:
        sys.argv = old_argv


def add_node_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("node", help="Run or inspect a local Picoin coordinator node")
    node_subparsers = parser.add_subparsers(dest="node_command", required=True)

    start_parser = node_subparsers.add_parser("start", help="Start the FastAPI coordinator")
    start_parser.add_argument("--host", default=DEFAULT_HOST)
    start_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    start_parser.add_argument("--reload", action="store_true")
    start_parser.add_argument("--log-level", default="info")
    start_parser.set_defaults(func=command_node_start)

    status_parser = node_subparsers.add_parser("status", help="Show local node health, status and recent events")
    status_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    status_parser.set_defaults(func=command_node_status)

    audit_parser = node_subparsers.add_parser("audit", help="Run full economic audit")
    audit_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    audit_parser.set_defaults(func=command_node_audit)

    repair_rewards_parser = node_subparsers.add_parser(
        "repair-rewards",
        help="Backfill missing reward and block_reward ledger rows for accepted blocks",
    )
    repair_rewards_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    repair_rewards_parser.set_defaults(func=command_node_repair_rewards)

    protocol_parser = node_subparsers.add_parser("protocol", help="Show active protocol")
    protocol_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    protocol_parser.set_defaults(func=command_node_protocol)

    peers_parser = node_subparsers.add_parser("peers", help="Show distributed testnet peers")
    peers_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    peers_parser.add_argument("--include-stale", action="store_true", default=True)
    peers_parser.add_argument("--connected-only", action="store_false", dest="include_stale")
    peers_parser.set_defaults(func=command_node_peers)

    discover_peers_parser = node_subparsers.add_parser("discover-peers", help="Discover peers from bootstrap and connected peers")
    discover_peers_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    discover_peers_parser.add_argument("--limit", type=int, default=32)
    discover_peers_parser.set_defaults(func=command_node_discover_peers)

    sync_parser = node_subparsers.add_parser("sync-status", help="Show distributed sync and mempool status")
    sync_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    sync_parser.set_defaults(func=command_node_sync_status)

    validation_health_parser = node_subparsers.add_parser("validation-health", help="Show pending validation job health")
    validation_health_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    validation_health_parser.add_argument("--stale-after-seconds", type=int, default=120)
    validation_health_parser.add_argument("--limit", type=int, default=20)
    validation_health_parser.set_defaults(func=command_node_validation_health)

    doctor_parser = node_subparsers.add_parser("doctor", help="Run public testnet readiness checks")
    doctor_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    doctor_parser.add_argument("--require-peers", action="store_true")
    doctor_parser.add_argument("--require-checkpoint", action="store_true")
    doctor_parser.add_argument("--verbose", action="store_true")
    doctor_parser.set_defaults(func=command_node_doctor)

    reconcile_parser = node_subparsers.add_parser("reconcile", help="Pull peers, mempool and proposals from connected peers")
    reconcile_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    reconcile_parser.add_argument("--peer", help="Optional peer base URL")
    reconcile_parser.add_argument("--limit", type=int, default=16)
    reconcile_parser.set_defaults(func=command_node_reconcile)

    catch_up_parser = node_subparsers.add_parser("catch-up", help="Reconcile, replay and audit until the node is caught up")
    catch_up_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    catch_up_parser.add_argument("--peer", help="Optional peer base URL")
    catch_up_parser.add_argument("--max-rounds", type=int, default=5)
    catch_up_parser.add_argument("--reconcile-limit", type=int, default=16)
    catch_up_parser.add_argument("--replay-limit", type=int, default=REPLAY_BATCH_SIZE)
    catch_up_parser.add_argument("--replay-batch-size", type=int, default=REPLAY_BATCH_SIZE)
    catch_up_parser.add_argument("--replay-backlog-threshold", type=int, default=REPLAY_BACKLOG_THRESHOLD)
    catch_up_parser.set_defaults(func=command_node_catch_up)

    report_parser = node_subparsers.add_parser("report", help="Run public testnet pass/fail readiness report")
    report_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    report_parser.add_argument("--peer", help="Optional peer base URL")
    report_parser.add_argument("--require-peers", action="store_true")
    report_parser.add_argument("--verbose", action="store_true")
    report_parser.set_defaults(func=command_node_report)

    mainnet_preflight_parser = node_subparsers.add_parser(
        "mainnet-preflight",
        help="Run mainnet launch preflight checks",
    )
    mainnet_preflight_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    mainnet_preflight_parser.add_argument("--peer", help="Optional peer base URL")
    mainnet_preflight_parser.add_argument(
        "--allow-mempool",
        action="store_true",
        help="Downgrade non-empty mempool from error to warning",
    )
    mainnet_preflight_parser.add_argument("--verbose", action="store_true")
    mainnet_preflight_parser.set_defaults(func=command_node_mainnet_preflight)

    compare_parser = node_subparsers.add_parser("compare", help="Compare local chain identity and tip with one peer")
    compare_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    compare_parser.add_argument("--peer", required=True)
    compare_parser.set_defaults(func=command_node_compare)

    compare_payloads_parser = node_subparsers.add_parser(
        "compare-block-payloads",
        help="Compare local and peer canonical block hash payloads at one height",
    )
    compare_payloads_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    compare_payloads_parser.add_argument("--peer", required=True)
    compare_payloads_parser.add_argument("--height", type=int, required=True)
    compare_payloads_parser.add_argument("--max-diffs", type=int, default=50)
    compare_payloads_parser.set_defaults(func=command_node_compare_block_payloads)

    checkpoint_parser = node_subparsers.add_parser("checkpoint", help="Create and verify canonical state checkpoints")
    checkpoint_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    checkpoint_subparsers = checkpoint_parser.add_subparsers(dest="checkpoint_command", required=True)

    checkpoint_list = checkpoint_subparsers.add_parser("list", help="List canonical checkpoints")
    checkpoint_list.add_argument("--limit", type=int, default=50)
    checkpoint_list.set_defaults(func=command_node_checkpoint_list)

    checkpoint_latest = checkpoint_subparsers.add_parser("latest", help="Show latest canonical checkpoint")
    checkpoint_latest.set_defaults(func=command_node_checkpoint_latest)

    checkpoint_create = checkpoint_subparsers.add_parser("create", help="Create a canonical checkpoint")
    checkpoint_create.add_argument("--height", type=int)
    checkpoint_create.add_argument("--source", default="manual")
    checkpoint_create.add_argument("--trusted", action="store_true", default=True)
    checkpoint_create.add_argument("--untrusted", action="store_false", dest="trusted")
    checkpoint_create.set_defaults(func=command_node_checkpoint_create)

    checkpoint_verify = checkpoint_subparsers.add_parser("verify", help="Verify a checkpoint against local replay")
    checkpoint_verify.add_argument("--height", type=int, required=True)
    checkpoint_verify.set_defaults(func=command_node_checkpoint_verify)

    checkpoint_export = checkpoint_subparsers.add_parser("export", help="Export a canonical checkpoint snapshot JSON")
    checkpoint_export.add_argument("--height", type=int)
    checkpoint_export.add_argument("--output", type=Path)
    checkpoint_export.set_defaults(func=command_node_checkpoint_export)

    checkpoint_import = checkpoint_subparsers.add_parser("import", help="Import and verify a canonical snapshot JSON")
    checkpoint_import.add_argument("--file", type=Path, required=True)
    checkpoint_import.add_argument("--source", default="cli")
    checkpoint_import.set_defaults(func=command_node_checkpoint_import)

    checkpoint_imports = checkpoint_subparsers.add_parser("imports", help="List imported canonical snapshots")
    checkpoint_imports.add_argument("--limit", type=int, default=50)
    checkpoint_imports.set_defaults(func=command_node_checkpoint_imports)

    checkpoint_activate = checkpoint_subparsers.add_parser("activate", help="Use an imported snapshot as sync base")
    checkpoint_activate.add_argument("--snapshot-hash", required=True)
    checkpoint_activate.set_defaults(func=command_node_checkpoint_activate)

    checkpoint_apply = checkpoint_subparsers.add_parser("apply", help="Apply an imported snapshot as local fast-sync state")
    checkpoint_apply.add_argument("--snapshot-hash", required=True)
    checkpoint_apply.set_defaults(func=command_node_checkpoint_apply)

    checkpoint_restore_peer = checkpoint_subparsers.add_parser(
        "restore-peer",
        help="Replace local chain state with a verified canonical snapshot fetched from a peer",
    )
    checkpoint_restore_peer.add_argument("--peer", required=True)
    checkpoint_restore_peer.add_argument("--height", type=int)
    checkpoint_restore_peer.add_argument("--source", default="peer-restore")
    checkpoint_restore_peer.set_defaults(func=command_node_checkpoint_restore_peer)

    checkpoint_restore_sqlite = checkpoint_subparsers.add_parser(
        "restore-sqlite",
        help="Replace local chain state with a verified canonical snapshot extracted from a SQLite backup",
    )
    checkpoint_restore_sqlite.add_argument("--file", type=Path, required=True)
    checkpoint_restore_sqlite.add_argument("--height", type=int)
    checkpoint_restore_sqlite.add_argument("--source", default="sqlite-restore")
    checkpoint_restore_sqlite.add_argument(
        "--backup-current",
        type=Path,
        help="Optional file or directory where the current database is copied before restore",
    )
    checkpoint_restore_sqlite.set_defaults(func=command_node_checkpoint_restore_sqlite)

    genesis_hash_parser = node_subparsers.add_parser("genesis-hash", help="Compute deterministic hash for a genesis allocation file")
    genesis_hash_parser.add_argument("--file", type=Path, required=True)
    genesis_hash_parser.add_argument(
        "--mainnet",
        action="store_true",
        help="Validate the allocation file against mainnet launch rules before printing the hash",
    )
    genesis_hash_parser.set_defaults(func=command_node_genesis_hash)

    bootstrap_stake_parser = node_subparsers.add_parser(
        "bootstrap-validator-stake",
        help="Apply canonical pre-launch wallet-backed validator stake at height 0",
    )
    bootstrap_stake_parser.add_argument("--file", type=Path, required=True)
    bootstrap_stake_parser.add_argument("--dry-run", action="store_true")
    bootstrap_stake_parser.set_defaults(func=command_node_bootstrap_validator_stake)


def add_wallet_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("wallet", help="Create wallets and query balances")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    wallet_subparsers = parser.add_subparsers(dest="wallet_command", required=True)

    create_parser = wallet_subparsers.add_parser("create", help="Create an Ed25519 Picoin wallet")
    create_parser.add_argument("--name", default="picoin-wallet")
    create_parser.add_argument("--output", type=Path)
    create_parser.add_argument("--network", help="Optional network_id metadata to store in the wallet file")
    create_parser.add_argument("--chain-id", help="Optional chain_id metadata to store in the wallet file")
    create_parser.set_defaults(func=command_wallet_create)

    import_parser = wallet_subparsers.add_parser("import", help="Import a wallet JSON into the local default wallet path")
    import_parser.add_argument("--file", type=Path, required=True)
    import_parser.add_argument("--output", type=Path)
    import_parser.set_defaults(func=command_wallet_import)

    export_parser = wallet_subparsers.add_parser("export", help="Print a local wallet JSON")
    export_parser.add_argument("--wallet", type=Path)
    export_parser.set_defaults(func=command_wallet_export)

    address_parser = wallet_subparsers.add_parser("address", help="Print a wallet address")
    address_parser.add_argument("--wallet", type=Path)
    address_parser.set_defaults(func=command_wallet_address)

    balance_parser = wallet_subparsers.add_parser("balance", help="Query a wallet/account balance")
    balance_parser.add_argument("--address")
    balance_parser.add_argument("--wallet", type=Path)
    balance_parser.set_defaults(func=command_wallet_balance)

    history_parser = wallet_subparsers.add_parser("history", help="Show wallet/account ledger history")
    history_parser.add_argument("--address")
    history_parser.add_argument("--wallet", type=Path)
    history_parser.add_argument("--limit", type=int, default=100)
    history_parser.set_defaults(func=command_wallet_history)

    send_parser = wallet_subparsers.add_parser("send", help="Submit a signed wallet transfer")
    send_parser.add_argument("--wallet", type=Path)
    send_parser.add_argument("--to", required=True)
    send_parser.add_argument("--amount", type=float, required=True)
    send_parser.add_argument("--fee", type=float, default=0.0)
    send_parser.add_argument("--nonce", type=int)
    send_parser.add_argument("--sender")
    send_parser.add_argument("--payload", help="Optional JSON payload")
    send_parser.add_argument("--type", default="transfer")
    send_parser.set_defaults(func=command_tx_send)

    nonce_parser = wallet_subparsers.add_parser("nonce", help="Show confirmed, pending and next nonce for an address")
    nonce_parser.add_argument("--address", required=True)
    nonce_parser.set_defaults(func=command_wallet_nonce)


def add_tx_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("tx", help="Create and submit signed transactions")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    tx_subparsers = parser.add_subparsers(dest="tx_command", required=True)

    send_parser = tx_subparsers.add_parser("send", help="Submit a signed transaction to the mempool")
    send_parser.add_argument("--wallet", type=Path)
    send_parser.add_argument("--to")
    send_parser.add_argument("--amount", type=float, default=0.0)
    send_parser.add_argument("--fee", type=float, default=0.0)
    send_parser.add_argument("--nonce", type=int)
    send_parser.add_argument(
        "--type",
        default="transfer",
        choices=["transfer", "stake", "unstake", "science_job_create", "governance_action", "treasury_claim", "faucet"],
    )
    send_parser.add_argument("--sender")
    send_parser.add_argument("--payload", help="Optional JSON payload")
    send_parser.add_argument("--stake-type", choices=["science", "validator"], help="Stake domain for stake/unstake transactions")
    send_parser.add_argument("--validator-id", help="Validator id for validator stake/unstake transactions")
    send_parser.set_defaults(func=command_tx_send)

    status_parser = tx_subparsers.add_parser("status", help="Show transaction status by hash")
    status_parser.add_argument("--hash", required=True)
    status_parser.set_defaults(func=command_tx_status)

    mempool_parser = tx_subparsers.add_parser("mempool", help="List mempool transactions")
    mempool_parser.add_argument("--status", choices=["pending", "propagated", "confirmed", "rejected", "expired"])
    mempool_parser.add_argument("--limit", type=int, default=100)
    mempool_parser.set_defaults(func=command_tx_mempool)


def add_consensus_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("consensus", help="Distributed block proposal, voting and replay")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    consensus_subparsers = parser.add_subparsers(dest="consensus_command", required=True)

    status_parser = consensus_subparsers.add_parser("status", help="Show distributed consensus status")
    status_parser.set_defaults(func=command_consensus_status)

    proposals_parser = consensus_subparsers.add_parser("proposals", help="List block proposals")
    proposals_parser.add_argument("--status")
    proposals_parser.add_argument("--limit", type=int, default=50)
    proposals_parser.set_defaults(func=command_consensus_proposals)

    votes_parser = consensus_subparsers.add_parser("votes", help="List weighted votes for a proposal")
    votes_parser.add_argument("--proposal-id", required=True)
    votes_parser.set_defaults(func=command_consensus_votes)

    propose_parser = consensus_subparsers.add_parser("propose-block", help="Propose a block JSON to distributed consensus")
    propose_parser.add_argument("--block", type=Path, required=True)
    propose_parser.add_argument("--proposer", required=True)
    propose_parser.set_defaults(func=command_consensus_propose)

    vote_parser = consensus_subparsers.add_parser("vote", help="Sign and submit a validator vote")
    vote_parser.add_argument("--proposal-id", required=True)
    vote_parser.add_argument("--identity", type=Path, required=True)
    vote_parser.add_argument("--reason", default="distributed validator accepted block proposal")
    vote_parser.add_argument("--reject", action="store_true")
    vote_parser.set_defaults(func=command_consensus_vote)

    finalize_parser = consensus_subparsers.add_parser("finalize", help="Finalize a proposal after quorum")
    finalize_parser.add_argument("--proposal-id", required=True)
    finalize_parser.set_defaults(func=command_consensus_finalize)

    replay_parser = consensus_subparsers.add_parser("replay", help="Replay finalized blocks into the local canonical chain")
    replay_parser.add_argument("--limit", type=int, default=REPLAY_BATCH_SIZE)
    replay_parser.set_defaults(func=command_consensus_replay)


def add_miner_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("miner", help="Register or run a local miner")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    parser.add_argument("--identity", type=Path, default=Path("miner_identity.json"))
    miner_subparsers = parser.add_subparsers(dest="miner_command", required=True)

    register_parser = miner_subparsers.add_parser("register", help="Register a miner identity")
    register_parser.add_argument("--name", default="local-miner")
    register_parser.add_argument("--overwrite", action="store_true")
    register_parser.set_defaults(func=miner_register)

    mine_parser = miner_subparsers.add_parser("mine", help="Mine Proof-of-Pi tasks")
    mine_parser.add_argument("--once", action="store_true")
    mine_parser.add_argument("--loops", type=int, default=1)
    mine_parser.add_argument("--sleep", type=float, default=1.0)
    mine_parser.add_argument("--workers", type=int, default=1)
    mine_parser.set_defaults(func=command_mine)

    stats_parser = miner_subparsers.add_parser("stats", help="Show miner stats")
    stats_parser.set_defaults(func=miner_stats)


def add_validator_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("validator", help="Register or run a local validator")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    parser.add_argument("--identity", type=Path, default=Path("validator_identity.json"))
    validator_subparsers = parser.add_subparsers(dest="validator_command", required=True)

    register_parser = validator_subparsers.add_parser("register", help="Register a validator identity")
    register_parser.add_argument("--name", default="local-validator")
    register_parser.add_argument("--overwrite", action="store_true")
    register_parser.set_defaults(func=validator_register)

    validate_parser = validator_subparsers.add_parser("validate", help="Validate pending jobs")
    validate_parser.add_argument("--once", action="store_true")
    validate_parser.add_argument("--loops", type=int, default=1)
    validate_parser.add_argument("--sleep", type=float, default=1.0)
    validate_parser.add_argument(
        "--node-server",
        default=os.getenv("PICOIN_VALIDATOR_NODE_SERVER", os.getenv("PICOIN_NODE_SERVER", "http://127.0.0.1:8000")),
        help="Local Picoin node API used for signed validator liveness",
    )
    validate_parser.add_argument(
        "--node-timeout",
        type=float,
        default=10.0,
        help="Seconds to wait for the local node heartbeat probe",
    )
    validate_parser.set_defaults(func=command_validate)


def add_validators_admin_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("validators", help="Inspect and administer validator liveness")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    validators_subparsers = parser.add_subparsers(dest="validators_command", required=True)

    list_parser = validators_subparsers.add_parser("list", help="List validator status and quorum eligibility")
    list_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    list_parser.set_defaults(func=command_validators_list)

    prune_parser = validators_subparsers.add_parser("prune-stale", help="Prune old offline validators without history")
    prune_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    prune_parser.add_argument("--older-than", type=int, default=300)
    prune_parser.set_defaults(func=command_validators_prune_stale)

    enable_parser = validators_subparsers.add_parser("enable", help="Enable a validator")
    enable_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    enable_parser.add_argument("validator_id")
    enable_parser.set_defaults(func=command_validators_enable)

    disable_parser = validators_subparsers.add_parser("disable", help="Disable a validator")
    disable_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    disable_parser.add_argument("validator_id")
    disable_parser.set_defaults(func=command_validators_disable)


def add_miners_admin_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("miners", help="Inspect and administer miner liveness")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    miners_subparsers = parser.add_subparsers(dest="miners_command", required=True)

    list_parser = miners_subparsers.add_parser("list", help="List miner status")
    list_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    list_parser.set_defaults(func=command_miners_list)

    prune_parser = miners_subparsers.add_parser("prune-stale", help="Prune old offline miners without blocks")
    prune_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    prune_parser.add_argument("--older-than", type=int, default=300)
    prune_parser.set_defaults(func=command_miners_prune_stale)

    enable_parser = miners_subparsers.add_parser("enable", help="Enable a miner")
    enable_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    enable_parser.add_argument("miner_id")
    enable_parser.set_defaults(func=command_miners_enable)

    disable_parser = miners_subparsers.add_parser("disable", help="Disable a miner")
    disable_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    disable_parser.add_argument("miner_id")
    disable_parser.set_defaults(func=command_miners_disable)


def add_science_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("science", help="Manage Science Compute Access Layer")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    parser.add_argument("--address", default=DEFAULT_SCIENCE_ADDRESS)
    science_subparsers = parser.add_subparsers(dest="science_command", required=True)

    stake_parser = science_subparsers.add_parser("stake", help="Stake PI for science compute access")
    stake_parser.add_argument("--amount", type=float, required=True)
    stake_parser.set_defaults(func=command_science_stake)

    account_parser = science_subparsers.add_parser("account", help="Show science stake account")
    account_parser.set_defaults(func=command_science_account)

    create_job_parser = science_subparsers.add_parser("create-job", help="Create a science compute job record")
    create_job_parser.add_argument("--type", required=True)
    create_job_parser.add_argument("--metadata-hash", required=True)
    create_job_parser.add_argument("--storage-pointer", required=True)
    create_job_parser.add_argument("--max-compute-units", type=float, required=True)
    create_job_parser.add_argument("--reward-per-unit", type=float, required=True)
    create_job_parser.add_argument("--max-reward", type=float, required=True)
    create_job_parser.set_defaults(func=command_science_create_job)

    jobs_parser = science_subparsers.add_parser("jobs", help="List science jobs")
    jobs_parser.add_argument("--limit", type=int, default=50)
    jobs_parser.set_defaults(func=command_science_jobs)

    accept_parser = science_subparsers.add_parser("accept-job", help="Mark a verified science job as accepted")
    accept_parser.add_argument("--job-id", required=True)
    accept_parser.add_argument("--worker-address")
    accept_parser.add_argument("--result-hash")
    accept_parser.add_argument("--proof-hash")
    accept_parser.add_argument("--compute-units-used", type=float, required=True)
    accept_parser.set_defaults(func=command_science_accept_job)

    pay_parser = science_subparsers.add_parser("pay-worker", help="Pay an accepted science worker")
    pay_parser.add_argument("--job-id", required=True)
    pay_parser.set_defaults(func=command_science_pay_worker)

    reserve_parser = science_subparsers.add_parser("reserve", help="Show science compute reward reserve")
    reserve_parser.set_defaults(func=command_science_reserve)

    governance_parser = science_subparsers.add_parser("reserve-governance", help="Show science reserve governance lock")
    governance_parser.set_defaults(func=command_science_reserve_governance)

    propose_parser = science_subparsers.add_parser("propose-l2-activation", help="Propose timelocked L2 marketplace activation")
    propose_parser.add_argument("--signer", required=True)
    propose_parser.set_defaults(func=command_science_propose_activation)

    approve_parser = science_subparsers.add_parser("approve-l2-activation", help="Approve timelocked L2 marketplace activation")
    approve_parser.add_argument("--signer", required=True)
    approve_parser.set_defaults(func=command_science_approve_activation)

    execute_parser = science_subparsers.add_parser("execute-l2-activation", help="Execute L2 activation after timelock and multisig threshold")
    execute_parser.set_defaults(func=command_science_execute_activation)


def add_treasury_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("treasury", help="Inspect or claim the Scientific Development Fund")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    treasury_subparsers = parser.add_subparsers(dest="treasury_command", required=True)

    status_parser = treasury_subparsers.add_parser("status", help="Show Scientific Development Fund status")
    status_parser.set_defaults(func=command_treasury_status)

    claim_parser = treasury_subparsers.add_parser("claim", help="Claim unlocked Scientific Development Fund balance")
    claim_parser.add_argument("--requested-by")
    claim_parser.add_argument("--claim-to")
    claim_parser.set_defaults(func=command_treasury_claim)


def add_reserve_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("reserve", help="Inspect the Science Compute Marketplace reserve")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    reserve_subparsers = parser.add_subparsers(dest="reserve_command", required=True)

    status_parser = reserve_subparsers.add_parser("status", help="Show Science Compute Marketplace reserve status")
    status_parser.set_defaults(func=command_reserve_status)

    pause_parser = reserve_subparsers.add_parser("pause", help="Emergency pause Science Compute Marketplace payouts")
    pause_parser.add_argument("--signer", required=True)
    pause_parser.set_defaults(func=command_reserve_pause)

    unpause_parser = reserve_subparsers.add_parser("unpause", help="Unpause Science Compute Marketplace payouts")
    unpause_parser.add_argument("--signer", required=True)
    unpause_parser.set_defaults(func=command_reserve_unpause)


def add_testnet_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("testnet", help="Manage local demo testnet")
    testnet_subparsers = parser.add_subparsers(dest="testnet_command", required=True)

    reset_parser = testnet_subparsers.add_parser("reset", help="Reset SQLite and demo files")
    reset_parser.add_argument("--keep-identities", action="store_true")
    reset_parser.set_defaults(func=command_testnet_reset)

    bootstrap_parser = testnet_subparsers.add_parser("bootstrap", help="Create demo miner and validators")
    bootstrap_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    bootstrap_parser.add_argument("--identity-dir", type=Path, default=Path("data/testnet/identities"))
    bootstrap_parser.add_argument("--miner-faucet", type=float, default=FAUCET_DEFAULT_AMOUNT)
    bootstrap_parser.set_defaults(func=command_testnet_bootstrap)

    cycle_parser = testnet_subparsers.add_parser("cycle", help="Mine once and validate with three demo validators")
    cycle_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    cycle_parser.add_argument("--miner-identity", type=Path, default=Path("data/testnet/identities/miner-alice.json"))
    cycle_parser.add_argument("--validator-one-identity", type=Path, default=Path("data/testnet/identities/validator-one.json"))
    cycle_parser.add_argument("--validator-two-identity", type=Path, default=Path("data/testnet/identities/validator-two.json"))
    cycle_parser.add_argument("--validator-three-identity", type=Path, default=Path("data/testnet/identities/validator-three.json"))
    cycle_parser.add_argument("--workers", type=int, default=1)
    cycle_parser.set_defaults(func=command_testnet_cycle)

    continuous_parser = testnet_subparsers.add_parser("continuous", help="Run continuous mining with multiple demo miners")
    continuous_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    continuous_parser.add_argument("--identity-dir", type=Path, default=Path("data/testnet/identities"))
    continuous_parser.add_argument("--miners", type=int, default=3)
    continuous_parser.add_argument("--loops", type=int, default=3)
    continuous_parser.add_argument("--workers", type=int, default=1)
    continuous_parser.add_argument("--sleep", type=float, default=0.5)
    continuous_parser.add_argument("--faucet", type=float, default=0.0)
    continuous_parser.add_argument("--retro-audit", action="store_true", default=True)
    continuous_parser.add_argument("--no-retro-audit", action="store_false", dest="retro_audit")
    continuous_parser.set_defaults(func=command_testnet_continuous)

    fund_wallet_parser = testnet_subparsers.add_parser("fund-wallet", help="Fund a wallet from the configured testnet faucet")
    fund_wallet_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    fund_wallet_parser.add_argument("--wallet", type=Path, help="Wallet file to sign a canonical faucet transaction")
    fund_wallet_parser.add_argument("--address", help="Legacy direct local faucet address")
    fund_wallet_parser.add_argument("--amount", type=float, default=0.1)
    fund_wallet_parser.add_argument("--nonce", type=int)
    fund_wallet_parser.set_defaults(func=command_testnet_fund_wallet)


def add_debug_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("debug", help="Deterministic consensus diagnostics")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    debug_subparsers = parser.add_subparsers(dest="debug_command", required=True)

    replay_parser = debug_subparsers.add_parser("replay-check", help="Find the first local replay state-root divergence")
    replay_parser.add_argument("--peer")
    replay_parser.add_argument("--from-height", type=int, required=True)
    replay_parser.add_argument("--to-height", type=int, required=True)
    replay_parser.set_defaults(func=command_debug_replay_check)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="picoin", description=f"{PROJECT_NAME} local node CLI")
    parser.add_argument("--version", action="store_true", help="Show CLI version context and exit")
    subparsers = parser.add_subparsers(dest="command")
    add_node_parser(subparsers)
    add_wallet_parser(subparsers)
    add_tx_parser(subparsers)
    add_consensus_parser(subparsers)
    add_miner_parser(subparsers)
    add_validator_parser(subparsers)
    add_miners_admin_parser(subparsers)
    add_validators_admin_parser(subparsers)
    add_science_parser(subparsers)
    add_treasury_parser(subparsers)
    add_reserve_parser(subparsers)
    add_testnet_parser(subparsers)
    add_debug_parser(subparsers)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.version:
        print_json({"project": PROJECT_NAME, "cli": "picoin", "protocol_version": PROTOCOL_VERSION, "mode": "local-node"})
        raise SystemExit(0)
    if not hasattr(args, "func"):
        parser.print_help()
        raise SystemExit(2)
    try:
        raise SystemExit(args.func(args))
    except requests.RequestException as exc:
        print(f"Network/API error: {exc}")
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
