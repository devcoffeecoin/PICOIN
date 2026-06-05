import argparse
import json
import os
import socket
import sys
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from app.core.crypto import hash_result
from app.core.merkle import merkle_proof, merkle_root
from app.core.performance import elapsed_ms, now_perf
from app.core.pi import calculate_pi_segment
from app.core.signatures import (
    build_commit_signature_payload,
    build_reveal_signature_payload,
    build_submission_signature_payload,
    generate_keypair,
    sign_payload,
)
from app.core.settings import CHAIN_ID, NETWORK_ID


DEFAULT_IDENTITY_PATH = Path("miner_identity.json")
AUTO_REGISTER_IDENTITY = os.getenv("PICOIN_AUTO_REGISTER_IDENTITY", "1").strip().lower() not in {"0", "false", "no"}
MINER_REWARD_ADDRESS = os.getenv("PICOIN_MINER_REWARD_ADDRESS", "").strip()
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
DEFAULT_POOL_URL = "https://pool1.picoin.science"
POOL_IDLE_LOG_INTERVAL_SECONDS = 30.0
_last_pool_idle_log_at = 0.0
_pool_idle_polls = 0


def http_timeout_seconds() -> float:
    value = os.getenv("PICOIN_HTTP_TIMEOUT_SECONDS", "60")
    try:
        return max(5.0, float(value))
    except ValueError:
        return 60.0


def http_max_retries() -> int:
    value = os.getenv("PICOIN_HTTP_MAX_RETRIES", "3")
    try:
        return max(1, int(value))
    except ValueError:
        return 3


def request_with_retries(method: str, url: str, **kwargs: Any) -> requests.Response:
    timeout = kwargs.pop("timeout", http_timeout_seconds())
    max_retries = http_max_retries()
    last_error: requests.RequestException | None = None
    for attempt in range(max_retries):
        try:
            response = requests.request(method, url, timeout=timeout, **kwargs)
            if response.status_code in RETRY_STATUS_CODES and attempt + 1 < max_retries:
                time.sleep(min(1.5 * (attempt + 1), 5.0))
                continue
            response.raise_for_status()
            return response
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            if attempt + 1 >= max_retries:
                raise
            print(f"Network/API transient error: {exc}; retrying {attempt + 2}/{max_retries}", file=sys.stderr)
            time.sleep(min(1.5 * (attempt + 1), 5.0))
    if last_error is not None:
        raise last_error
    raise RuntimeError("Network/API request failed without a response")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_identity(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"identity file not found: {path}")
    identity = json.loads(path.read_text(encoding="utf-8"))
    if MINER_REWARD_ADDRESS:
        identity["reward_address"] = MINER_REWARD_ADDRESS
    return identity


def load_or_register_identity(server_url: str, identity_path: Path, default_name: str | None = None) -> dict[str, Any]:
    try:
        return load_identity(identity_path)
    except FileNotFoundError:
        if not AUTO_REGISTER_IDENTITY:
            raise
    name = default_name or identity_path.stem or "local-miner"
    identity = register(server_url, name, identity_path, overwrite=False)
    print(f"Auto-registered miner identity: {identity['miner_id']} ({identity['name']})")
    print(f"Identity saved: {identity_path}")
    return identity


def save_identity(path: Path, identity: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(identity, indent=2, sort_keys=True), encoding="utf-8")


def calculate_single_position(args: tuple[int, str]) -> tuple[int, str]:
    position, algorithm = args
    return position, calculate_pi_segment(position, position, algorithm)


def calculate_segment_with_workers(range_start: int, range_end: int, algorithm: str, workers: int) -> str:
    if workers <= 1:
        return calculate_pi_segment(range_start, range_end, algorithm)

    positions = list(range(range_start, range_end + 1))
    with ProcessPoolExecutor(max_workers=workers) as executor:
        results = executor.map(calculate_single_position, ((position, algorithm) for position in positions))
    digits = {position: digit for position, digit in results}
    return "".join(digits[position] for position in positions)


def register(server_url: str, name: str, identity_path: Path, overwrite: bool) -> dict[str, Any]:
    if identity_path.exists() and not overwrite:
        raise FileExistsError(f"identity already exists: {identity_path}")

    keypair = generate_keypair()
    response = request_with_retries(
        "POST",
        f"{server_url}/miners/register",
        json={"name": name, "public_key": keypair["public_key"], "reward_address": MINER_REWARD_ADDRESS or None},
    )
    response.raise_for_status()
    miner = response.json()
    identity = {
        "miner_id": miner["miner_id"],
        "name": miner["name"],
        "public_key": keypair["public_key"],
        "private_key": keypair["private_key"],
        "reward_address": MINER_REWARD_ADDRESS or miner.get("reward_address"),
        "server_url": server_url,
        "created_at": utc_now(),
    }
    save_identity(identity_path, identity)
    return identity


def get_task(server_url: str, miner_id: str) -> dict[str, Any]:
    response = request_with_retries("GET", f"{server_url}/tasks/next", params={"miner_id": miner_id})
    return response.json()


def get_task_for_identity(server_url: str, identity: dict[str, Any]) -> dict[str, Any]:
    params = {
        "miner_id": identity["miner_id"],
        "public_key": identity.get("public_key"),
        "name": identity.get("name") or identity["miner_id"],
        "reward_address": identity.get("reward_address"),
    }
    params = {key: value for key, value in params.items() if value}
    response = request_with_retries("GET", f"{server_url}/tasks/next", params=params)
    return response.json()


def get_miner(server_url: str, miner_id: str) -> dict[str, Any]:
    response = request_with_retries("GET", f"{server_url}/miners/{miner_id}")
    return response.json()


def submit_result(
    server_url: str,
    task: dict[str, Any],
    identity: dict[str, Any],
    segment: str,
    result_hash: str,
) -> dict[str, Any]:
    signed_at = utc_now()
    signature_payload = build_submission_signature_payload(
        task_id=task["task_id"],
        miner_id=identity["miner_id"],
        range_start=task["range_start"],
        range_end=task["range_end"],
        algorithm=task["algorithm"],
        result_hash=result_hash,
        signed_at=signed_at,
    )
    signature = sign_payload(identity["private_key"], signature_payload)

    response = request_with_retries(
        "POST",
        f"{server_url}/tasks/submit",
        json={
            "task_id": task["task_id"],
            "miner_id": identity["miner_id"],
            "result_hash": result_hash,
            "segment": segment,
            "signature": signature,
            "signed_at": signed_at,
        },
    )
    return response.json()


def commit_result(
    server_url: str,
    task: dict[str, Any],
    identity: dict[str, Any],
    result_hash: str,
    root: str,
    compute_ms: int,
) -> dict[str, Any]:
    signed_at = utc_now()
    signature_payload = build_commit_signature_payload(
        task_id=task["task_id"],
        miner_id=identity["miner_id"],
        range_start=task["range_start"],
        range_end=task["range_end"],
        algorithm=task["algorithm"],
        result_hash=result_hash,
        merkle_root=root,
        signed_at=signed_at,
        tx_merkle_root=task.get("tx_merkle_root", ""),
        mempool_snapshot_id=task.get("mempool_snapshot_id"),
        selected_tx_hashes_hash=task.get("selected_tx_hashes_hash"),
        tx_count=int(task.get("tx_count") or 0),
        tx_fee_total_units=int(task.get("tx_fee_total_units") or 0),
        chain_id=task.get("chain_id") or CHAIN_ID,
        network_id=task.get("network_id") or NETWORK_ID,
    )
    signature = sign_payload(identity["private_key"], signature_payload)

    response = request_with_retries(
        "POST",
        f"{server_url}/tasks/commit",
        json={
            "task_id": task["task_id"],
            "miner_id": identity["miner_id"],
            "result_hash": result_hash,
            "merkle_root": root,
            "tx_merkle_root": task.get("tx_merkle_root", ""),
            "mempool_snapshot_id": task.get("mempool_snapshot_id"),
            "selected_tx_hashes_hash": task.get("selected_tx_hashes_hash"),
            "tx_count": int(task.get("tx_count") or 0),
            "tx_fee_total_units": int(task.get("tx_fee_total_units") or 0),
            "compute_ms": compute_ms,
            "signature": signature,
            "signed_at": signed_at,
        },
    )
    return response.json()


def reveal_samples(
    server_url: str,
    task: dict[str, Any],
    identity: dict[str, Any],
    segment: str,
    root: str,
    challenge: dict[str, Any],
) -> dict[str, Any]:
    samples = []
    for sample in challenge["samples"]:
        position = sample["position"]
        samples.append(
            {
                "position": position,
                "digit": segment[position - task["range_start"]],
                "proof": merkle_proof(segment, task["range_start"], position),
            }
        )

    signed_at = utc_now()
    signature_payload = build_reveal_signature_payload(
        task_id=task["task_id"],
        miner_id=identity["miner_id"],
        merkle_root=root,
        challenge_seed=challenge["challenge_seed"],
        signed_at=signed_at,
        tx_merkle_root=task.get("tx_merkle_root", ""),
        mempool_snapshot_id=task.get("mempool_snapshot_id"),
        selected_tx_hashes_hash=task.get("selected_tx_hashes_hash"),
    )
    signature = sign_payload(identity["private_key"], signature_payload)

    response = request_with_retries(
        "POST",
        f"{server_url}/tasks/reveal",
        json={
            "task_id": task["task_id"],
            "miner_id": identity["miner_id"],
            "samples": samples,
            "tx_merkle_root": task.get("tx_merkle_root", ""),
            "mempool_snapshot_id": task.get("mempool_snapshot_id"),
            "selected_tx_hashes_hash": task.get("selected_tx_hashes_hash"),
            "tx_count": int(task.get("tx_count") or 0),
            "tx_fee_total_units": int(task.get("tx_fee_total_units") or 0),
            "signature": signature,
            "signed_at": signed_at,
        },
    )
    return response.json()


def mine_once(server_url: str, identity: dict[str, Any], workers: int) -> bool:
    task = get_task_for_identity(server_url, identity)
    print(
        "Task assigned: "
        f"{task['task_id']} positions {task['range_start']}..{task['range_end']} "
        f"using {task['algorithm']}"
    )
    if task.get("status") != "assigned":
        print(f"Task is waiting for protocol progress: status={task.get('status')}")
        return False

    compute_started = now_perf()
    segment = calculate_segment_with_workers(task["range_start"], task["range_end"], task["algorithm"], workers)
    result_hash = hash_result(segment, task["range_start"], task["range_end"], task["algorithm"])
    root = merkle_root(segment, task["range_start"])
    compute_ms = elapsed_ms(compute_started)
    print(f"Calculated segment length: {len(segment)}")
    print(f"Compute time: {compute_ms} ms")
    print(f"Workers: {workers}")
    print(f"Result hash: {result_hash}")
    print(f"Merkle root: {root}")

    challenge = commit_result(server_url, task, identity, result_hash, root, compute_ms)
    if not challenge["accepted"]:
        print(f"Commit rejected: {challenge['message']}")
        return False
    print(f"Commit accepted. Revealing {len(challenge['samples'])} samples.")

    submission = reveal_samples(server_url, task, identity, segment, root, challenge)
    if submission["status"] == "validation_pending":
        print(f"Reveal accepted. Validation job: {submission['validation']['job_id']}")
        print("Waiting for an external validator to approve the block.")
        return True

    if submission["accepted"]:
        block = submission["block"]
        print(f"Accepted block #{block['height']}: {block['block_hash']}")
        print(f"Reward: {block['reward']}")
        return True

    print(f"Rejected: {submission['message']}")
    print(f"Validation: {submission['validation']}")
    return False


def pool_headers(token: str | None) -> dict[str, str]:
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def pool_worker_identity_path(identity_path: Path) -> Path:
    return identity_path.with_name("pool_worker_identity.json")


def load_or_create_pool_worker_id(identity_path: Path, requested_worker_id: str | None, name: str) -> str:
    requested = (requested_worker_id or "").strip()
    if requested:
        return requested

    path = pool_worker_identity_path(identity_path)
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            worker_id = str(saved.get("worker_id") or "").strip()
            if worker_id:
                return worker_id
        except (OSError, ValueError):
            pass

    worker_id = f"poolworker_{uuid.uuid4().hex[:16]}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "worker_id": worker_id,
                "name": name,
                "host": socket.gethostname(),
                "created_at": utc_now(),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return worker_id


def pool_register_worker(
    pool_url: str,
    *,
    worker_id: str,
    name: str,
    payout_address: str,
    auth_token: str | None,
) -> dict[str, Any]:
    response = request_with_retries(
        "POST",
        f"{pool_url}/workers/register",
        json={"worker_id": worker_id, "name": name, "payout_address": payout_address},
        headers=pool_headers(auth_token),
    )
    return response.json()


def pool_mine_once(pool_url: str, worker_id: str, auth_token: str | None, workers: int) -> bool:
    global _last_pool_idle_log_at, _pool_idle_polls
    response = request_with_retries(
        "GET",
        f"{pool_url}/work/next",
        params={"worker_id": worker_id},
        headers=pool_headers(auth_token),
    )
    work = response.json()
    if work.get("status") != "work":
        _pool_idle_polls += 1
        current = time.monotonic()
        if current - _last_pool_idle_log_at >= POOL_IDLE_LOG_INTERVAL_SECONDS:
            print(
                "Pool connected. Waiting for available pool chunk "
                f"(idle_polls={_pool_idle_polls}, message={work.get('message', 'no pool work available')})"
            )
            _last_pool_idle_log_at = current
            _pool_idle_polls = 0
        return False

    _pool_idle_polls = 0
    units = int(work["range_end"]) - int(work["range_start"]) + 1
    print(
        "Pool work received: "
        f"task={work.get('task_id')} chunk={work['chunk_id']} "
        f"range={work['range_start']}..{work['range_end']} units={units} "
        f"algorithm={work['algorithm']}"
    )
    compute_started = now_perf()
    segment = calculate_segment_with_workers(int(work["range_start"]), int(work["range_end"]), work["algorithm"], workers)
    compute_ms = elapsed_ms(compute_started)
    print(f"Calculated segment length: {len(segment)}")
    print(f"Compute time: {compute_ms} ms")
    print(f"Workers: {workers}")
    print(f"Pool chunk computed: {work['chunk_id']} units={units} compute_ms={compute_ms}")

    submit = request_with_retries(
        "POST",
        f"{pool_url}/work/submit",
        json={
            "worker_id": worker_id,
            "chunk_id": work["chunk_id"],
            "segment": segment,
            "compute_ms": compute_ms,
        },
        headers=pool_headers(auth_token),
    )
    result = submit.json()
    print(
        f"Pool chunk accepted: {work['chunk_id']} "
        f"range={work['range_start']}..{work['range_end']} "
        f"units={result.get('units')} compute_ms={compute_ms}"
    )
    return True


def command_register(args: argparse.Namespace) -> int:
    identity = register(args.server.rstrip("/"), args.name, args.identity, args.overwrite)
    print(f"Miner registered: {identity['miner_id']} ({identity['name']})")
    print(f"Identity saved: {args.identity}")
    return 0


def command_mine(args: argparse.Namespace) -> int:
    server_url = args.server.rstrip("/") if args.server else "http://127.0.0.1:8000"
    identity = load_or_register_identity(server_url, args.identity)
    server_url = args.server.rstrip("/") if args.server else identity.get("server_url", server_url)

    accepted = 0
    attempts = 0
    loops = 1 if args.once else args.loops

    for index in range(loops):
        attempts += 1
        print(f"Mining attempt {index + 1}/{loops} as {identity['miner_id']}")
        if mine_once(server_url, identity, args.workers):
            accepted += 1
        if index + 1 < loops:
            time.sleep(args.sleep)

    print(f"Done. accepted={accepted} attempts={attempts}")
    return 0 if accepted == attempts else 1


def command_pool_register(args: argparse.Namespace) -> int:
    pool_url = args.pool_url.rstrip("/")
    payout_address = (args.payout_address or MINER_REWARD_ADDRESS).strip()
    if not payout_address:
        raise RuntimeError("pool payout address is required")
    worker_id = load_or_create_pool_worker_id(args.identity, args.worker_id, args.name)
    result = pool_register_worker(
        pool_url,
        worker_id=worker_id,
        name=args.name,
        payout_address=payout_address,
        auth_token=args.auth_token.strip() or None,
    )
    print(f"Pool worker registered: {result.get('worker_id', worker_id)}")
    print(f"Pool URL: {pool_url}")
    print(f"Payout wallet: {payout_address}")
    print(f"Worker identity: {pool_worker_identity_path(args.identity)}")
    return 0


def command_pool_mine(args: argparse.Namespace) -> int:
    pool_url = args.pool_url.rstrip("/")
    payout_address = (args.payout_address or MINER_REWARD_ADDRESS).strip()
    if not payout_address:
        raise RuntimeError("pool payout address is required")
    worker_id = load_or_create_pool_worker_id(args.identity, args.worker_id, args.name)
    auth_token = args.auth_token.strip() or None
    pool_register_worker(
        pool_url,
        worker_id=worker_id,
        name=args.name,
        payout_address=payout_address,
        auth_token=auth_token,
    )

    completed = 0
    attempts = 0
    loops = 1 if args.once else args.loops
    last_status_log_at = 0.0
    for index in range(loops):
        attempts += 1
        current = time.monotonic()
        if index == 0 or current - last_status_log_at >= 30:
            print(
                f"Pool worker active: {worker_id} "
                f"attempts={attempts}/{loops} completed_chunks={completed}"
            )
            last_status_log_at = current
        if pool_mine_once(pool_url, worker_id, auth_token, args.workers):
            completed += 1
        if index + 1 < loops:
            time.sleep(args.sleep)

    print(f"Done. completed_chunks={completed} attempts={attempts}")
    return 0


def command_stats(args: argparse.Namespace) -> int:
    server_url = args.server.rstrip("/") if args.server else "http://127.0.0.1:8000"
    identity = load_or_register_identity(server_url, args.identity)
    server_url = args.server.rstrip("/") if args.server else identity.get("server_url", server_url)
    miner = get_miner(server_url, identity["miner_id"])
    print(json.dumps(miner, indent=2, sort_keys=True))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Picoin Proof-of-Pi miner.")
    parser.add_argument("--server", default="http://127.0.0.1:8000", help="Coordinator API base URL")
    parser.add_argument("--identity", type=Path, default=DEFAULT_IDENTITY_PATH, help="Local miner identity JSON")

    subparsers = parser.add_subparsers(dest="command", required=True)

    register_parser = subparsers.add_parser("register", help="Register a miner and create Ed25519 identity")
    register_parser.add_argument("--name", default="local-miner", help="Miner display name")
    register_parser.add_argument("--overwrite", action="store_true", help="Overwrite existing identity file")
    register_parser.set_defaults(func=command_register)

    mine_parser = subparsers.add_parser("mine", help="Mine assigned Proof-of-Pi tasks")
    mine_parser.add_argument("--once", action="store_true", help="Mine exactly one task")
    mine_parser.add_argument("--loops", type=int, default=1, help="Number of mining attempts")
    mine_parser.add_argument("--sleep", type=float, default=1.0, help="Seconds between attempts")
    mine_parser.add_argument("--workers", type=int, default=1, help="Parallel workers for BBP segment calculation")
    mine_parser.set_defaults(func=command_mine)

    pool_register_parser = subparsers.add_parser("pool-register", help="Register this machine as a pool worker")
    pool_register_parser.add_argument("--pool-url", default=DEFAULT_POOL_URL, help="Pool API base URL")
    pool_register_parser.add_argument("--worker-id", default="", help="Optional stable worker id")
    pool_register_parser.add_argument("--name", default=socket.gethostname(), help="Worker display name")
    pool_register_parser.add_argument("--payout-address", default="", help="PI wallet that should receive pool payouts")
    pool_register_parser.add_argument("--auth-token", default=os.getenv("PICOIN_POOL_TOKEN", ""), help="Optional private pool token")
    pool_register_parser.set_defaults(func=command_pool_register)

    pool_mine_parser = subparsers.add_parser("pool-mine", help="Mine chunks from a Picoin pool")
    pool_mine_parser.add_argument("--pool-url", default=DEFAULT_POOL_URL, help="Pool API base URL")
    pool_mine_parser.add_argument("--worker-id", default="", help="Optional stable worker id")
    pool_mine_parser.add_argument("--name", default=socket.gethostname(), help="Worker display name")
    pool_mine_parser.add_argument("--payout-address", default="", help="PI wallet that should receive pool payouts")
    pool_mine_parser.add_argument("--auth-token", default=os.getenv("PICOIN_POOL_TOKEN", ""), help="Optional private pool token")
    pool_mine_parser.add_argument("--once", action="store_true", help="Mine exactly one pool chunk")
    pool_mine_parser.add_argument("--loops", type=int, default=1, help="Number of pool mining attempts")
    pool_mine_parser.add_argument("--sleep", type=float, default=1.0, help="Seconds between attempts")
    pool_mine_parser.add_argument("--workers", type=int, default=1, help="Parallel workers for BBP chunk calculation")
    pool_mine_parser.set_defaults(func=command_pool_mine)

    stats_parser = subparsers.add_parser("stats", help="Show registered miner stats")
    stats_parser.set_defaults(func=command_stats)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        raise SystemExit(args.func(args))
    except FileNotFoundError as exc:
        print(f"{exc}. Run: python -m miner.client register --name alice", file=sys.stderr)
        raise SystemExit(2) from exc
    except FileExistsError as exc:
        print(f"{exc}. Use --overwrite to replace it.", file=sys.stderr)
        raise SystemExit(2) from exc
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    except requests.RequestException as exc:
        print(f"Network/API error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
