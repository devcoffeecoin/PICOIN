import argparse
import json
import os
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

from app.core.settings import FAUCET_DEFAULT_AMOUNT, PROJECT_NAME, PROTOCOL_VERSION
from app.core.signatures import sign_payload
from app.services.consensus import consensus_vote_payload
from app.services.genesis import genesis_allocations_hash, load_genesis_allocations
from app.services.wallet import create_wallet, sign_transaction


DEFAULT_SERVER_URL = os.getenv("PICOIN_SERVER", "http://127.0.0.1:8000")
DEFAULT_HOST = os.getenv("PICOIN_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.getenv("PICOIN_PORT", "8000"))
DEFAULT_SCIENCE_ADDRESS = os.getenv("PICOIN_SCIENCE_ADDRESS", "local-science-user")


def normalize_server_url(server: str) -> str:
    return server.rstrip("/")


def print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def get_json(server_url: str, path: str) -> Any:
    response = requests.get(f"{normalize_server_url(server_url)}{path}", timeout=20)
    response.raise_for_status()
    return response.json()


def post_json(server_url: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    response = requests.post(f"{normalize_server_url(server_url)}{path}", json=payload, timeout=20)
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


def command_node_protocol(args: argparse.Namespace) -> int:
    print_json(get_json(args.server, "/protocol"))
    return 0


def command_node_peers(args: argparse.Namespace) -> int:
    print_json(get_json(args.server, f"/node/peers?include_stale={str(args.include_stale).lower()}"))
    return 0


def command_node_sync_status(args: argparse.Namespace) -> int:
    print_json(get_json(args.server, "/node/sync-status"))
    return 0


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
        path = f"/node/reconcile?limit={args.reconcile_limit}"
        if peer_url:
            path = f"{path}&peer_address={peer_url}"
        reconcile = post_json(server_url, path)
        replay = post_json(server_url, f"/consensus/replay?limit={args.replay_limit}")
        final_sync = get_json(server_url, "/node/sync-status")
        final_audit = get_json(server_url, "/audit/full")
        if peer_url:
            peer_sync = get_json(peer_url, "/node/sync-status")
        reconcile_results = reconcile.get("results") or []
        blocks_seen = sum(int(item.get("blocks_seen", 0)) for item in reconcile_results)
        peer_height = int(peer_sync.get("latest_block_height", 0)) if peer_sync else None
        peer_hash = peer_sync.get("latest_block_hash") if peer_sync else None
        local_height = int(final_sync.get("latest_block_height", 0))
        local_hash = final_sync.get("latest_block_hash")
        round_summary = {
            "round": round_number,
            "reconcile": {
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
                "normalized": replay.get("normalized", 0),
                "errors": replay.get("errors", []),
            },
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
        peer_matches = (
            final_sync.get("network_id") == peer_sync.get("network_id")
            and final_sync.get("chain_id") == peer_sync.get("chain_id")
            and final_sync.get("genesis_hash") == peer_sync.get("genesis_hash")
            and int(final_sync.get("latest_block_height", 0)) == int(peer_sync.get("latest_block_height", 0))
            and final_sync.get("latest_block_hash") == peer_sync.get("latest_block_hash")
        )
    healthy = final_sync.get("pending_replay_blocks", 0) == 0 and bool(final_audit.get("valid")) and peer_matches
    output = {
        "server": server_url,
        "peer": peer_url,
        "status": "ok" if healthy else "needs_attention",
        "initial_height": initial_sync.get("latest_block_height", 0),
        "final_height": final_sync.get("latest_block_height", 0),
        "final_block_hash": final_sync.get("latest_block_hash"),
        "peer_height": peer_sync.get("latest_block_height") if peer_sync else None,
        "peer_block_hash": peer_sync.get("latest_block_hash") if peer_sync else None,
        "peer_matches": peer_matches,
        "network_matches_peer": None if peer_sync is None else final_sync.get("network_id") == peer_sync.get("network_id"),
        "chain_matches_peer": None if peer_sync is None else final_sync.get("chain_id") == peer_sync.get("chain_id"),
        "genesis_matches_peer": None if peer_sync is None else final_sync.get("genesis_hash") == peer_sync.get("genesis_hash"),
        "pending_replay_blocks": final_sync.get("pending_replay_blocks", 0),
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
        add_check("network_match", sync.get("network_id") == peer_sync.get("network_id"), f"peer={peer_sync.get('network_id')}")
        add_check("chain_match", sync.get("chain_id") == peer_sync.get("chain_id"), f"peer={peer_sync.get('chain_id')}")
        add_check(
            "genesis_match",
            sync.get("genesis_hash") == peer_sync.get("genesis_hash"),
            f"peer={peer_sync.get('genesis_hash')}",
        )
        add_check(
            "height_match",
            int(sync.get("latest_block_height", 0)) == int(peer_sync.get("latest_block_height", 0)),
            f"local={sync.get('latest_block_height')}, peer={peer_sync.get('latest_block_height')}",
        )
        add_check(
            "block_hash_match",
            sync.get("latest_block_hash") == peer_sync.get("latest_block_hash"),
            f"local={sync.get('latest_block_hash')}, peer={peer_sync.get('latest_block_hash')}",
        )

    failures = [check for check in checks if not check["ok"] and check["severity"] == "error"]
    warnings = [check for check in checks if not check["ok"] and check["severity"] == "warning"]
    output = {
        "server": server_url,
        "peer": peer_url,
        "status": "fail" if failures else "warn" if warnings else "ok",
        "height": sync.get("latest_block_height", 0),
        "block_hash": sync.get("latest_block_hash"),
        "network_id": sync.get("network_id"),
        "chain_id": sync.get("chain_id"),
        "checks": checks,
        "summary": {"errors": len(failures), "warnings": len(warnings), "checked": len(checks)},
    }
    if args.verbose:
        output["payloads"] = payloads
    print_json(output)
    return 1 if failures else 0


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


def command_node_genesis_hash(args: argparse.Namespace) -> int:
    document = load_genesis_allocations(args.file)
    print_json({"genesis_hash": genesis_allocations_hash(document), "allocations": len(document["allocations"])})
    return 0


def command_wallet_create(args: argparse.Namespace) -> int:
    wallet = create_wallet(args.name)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(wallet, indent=2, sort_keys=True), encoding="utf-8")
    print_json(wallet)
    return 0


def command_wallet_balance(args: argparse.Namespace) -> int:
    print_json(get_json(args.server, f"/balances/{args.address}"))
    return 0


def command_wallet_nonce(args: argparse.Namespace) -> int:
    print_json(get_json(args.server, f"/wallet/{args.address}/nonce"))
    return 0


def command_tx_send(args: argparse.Namespace) -> int:
    wallet = json.loads(args.wallet.read_text(encoding="utf-8"))
    payload = json.loads(args.payload) if args.payload else {}
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
    print_json(post_json(args.server, "/tx/submit", tx))
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

    protocol_parser = node_subparsers.add_parser("protocol", help="Show active protocol")
    protocol_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    protocol_parser.set_defaults(func=command_node_protocol)

    peers_parser = node_subparsers.add_parser("peers", help="Show distributed testnet peers")
    peers_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    peers_parser.add_argument("--include-stale", action="store_true", default=True)
    peers_parser.add_argument("--connected-only", action="store_false", dest="include_stale")
    peers_parser.set_defaults(func=command_node_peers)

    sync_parser = node_subparsers.add_parser("sync-status", help="Show distributed sync and mempool status")
    sync_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    sync_parser.set_defaults(func=command_node_sync_status)

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
    catch_up_parser.add_argument("--replay-limit", type=int, default=100)
    catch_up_parser.set_defaults(func=command_node_catch_up)

    report_parser = node_subparsers.add_parser("report", help="Run public testnet pass/fail readiness report")
    report_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    report_parser.add_argument("--peer", help="Optional peer base URL")
    report_parser.add_argument("--require-peers", action="store_true")
    report_parser.add_argument("--verbose", action="store_true")
    report_parser.set_defaults(func=command_node_report)

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

    genesis_hash_parser = node_subparsers.add_parser("genesis-hash", help="Compute deterministic hash for a genesis allocation file")
    genesis_hash_parser.add_argument("--file", type=Path, required=True)
    genesis_hash_parser.set_defaults(func=command_node_genesis_hash)


def add_wallet_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("wallet", help="Create wallets and query balances")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    wallet_subparsers = parser.add_subparsers(dest="wallet_command", required=True)

    create_parser = wallet_subparsers.add_parser("create", help="Create an Ed25519 Picoin wallet")
    create_parser.add_argument("--name", default="picoin-wallet")
    create_parser.add_argument("--output", type=Path)
    create_parser.set_defaults(func=command_wallet_create)

    balance_parser = wallet_subparsers.add_parser("balance", help="Query a wallet/account balance")
    balance_parser.add_argument("--address", required=True)
    balance_parser.set_defaults(func=command_wallet_balance)

    nonce_parser = wallet_subparsers.add_parser("nonce", help="Show confirmed, pending and next nonce for an address")
    nonce_parser.add_argument("--address", required=True)
    nonce_parser.set_defaults(func=command_wallet_nonce)


def add_tx_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("tx", help="Create and submit signed transactions")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    tx_subparsers = parser.add_subparsers(dest="tx_command", required=True)

    send_parser = tx_subparsers.add_parser("send", help="Submit a signed transaction to the mempool")
    send_parser.add_argument("--wallet", type=Path, required=True)
    send_parser.add_argument("--to")
    send_parser.add_argument("--amount", type=float, default=0.0)
    send_parser.add_argument("--fee", type=float, default=0.0)
    send_parser.add_argument("--nonce", type=int)
    send_parser.add_argument(
        "--type",
        default="transfer",
        choices=["transfer", "stake", "unstake", "science_job_create", "governance_action", "treasury_claim"],
    )
    send_parser.add_argument("--sender")
    send_parser.add_argument("--payload", help="Optional JSON payload")
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
    replay_parser.add_argument("--limit", type=int, default=100)
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
    validate_parser.set_defaults(func=command_validate)


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
    add_science_parser(subparsers)
    add_treasury_parser(subparsers)
    add_reserve_parser(subparsers)
    add_testnet_parser(subparsers)
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
