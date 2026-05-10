import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from app.core.crypto import hash_result
from app.core.merkle import merkle_proof, merkle_root
from app.core.pi import calculate_pi_segment
from app.core.signatures import (
    build_commit_signature_payload,
    build_reveal_signature_payload,
    build_submission_signature_payload,
    generate_keypair,
    sign_payload,
)


DEFAULT_IDENTITY_PATH = Path("miner_identity.json")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_identity(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"identity file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_identity(path: Path, identity: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(identity, indent=2, sort_keys=True), encoding="utf-8")


def register(server_url: str, name: str, identity_path: Path, overwrite: bool) -> dict[str, Any]:
    if identity_path.exists() and not overwrite:
        raise FileExistsError(f"identity already exists: {identity_path}")

    keypair = generate_keypair()
    response = requests.post(
        f"{server_url}/miners/register",
        json={"name": name, "public_key": keypair["public_key"]},
        timeout=20,
    )
    response.raise_for_status()
    miner = response.json()
    identity = {
        "miner_id": miner["miner_id"],
        "name": miner["name"],
        "public_key": keypair["public_key"],
        "private_key": keypair["private_key"],
        "server_url": server_url,
        "created_at": utc_now(),
    }
    save_identity(identity_path, identity)
    return identity


def get_task(server_url: str, miner_id: str) -> dict[str, Any]:
    response = requests.get(f"{server_url}/tasks/next", params={"miner_id": miner_id}, timeout=20)
    response.raise_for_status()
    return response.json()


def get_miner(server_url: str, miner_id: str) -> dict[str, Any]:
    response = requests.get(f"{server_url}/miners/{miner_id}", timeout=20)
    response.raise_for_status()
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

    response = requests.post(
        f"{server_url}/tasks/submit",
        json={
            "task_id": task["task_id"],
            "miner_id": identity["miner_id"],
            "result_hash": result_hash,
            "segment": segment,
            "signature": signature,
            "signed_at": signed_at,
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def commit_result(
    server_url: str,
    task: dict[str, Any],
    identity: dict[str, Any],
    result_hash: str,
    root: str,
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
    )
    signature = sign_payload(identity["private_key"], signature_payload)

    response = requests.post(
        f"{server_url}/tasks/commit",
        json={
            "task_id": task["task_id"],
            "miner_id": identity["miner_id"],
            "result_hash": result_hash,
            "merkle_root": root,
            "signature": signature,
            "signed_at": signed_at,
        },
        timeout=20,
    )
    response.raise_for_status()
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
    )
    signature = sign_payload(identity["private_key"], signature_payload)

    response = requests.post(
        f"{server_url}/tasks/reveal",
        json={
            "task_id": task["task_id"],
            "miner_id": identity["miner_id"],
            "samples": samples,
            "signature": signature,
            "signed_at": signed_at,
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def mine_once(server_url: str, identity: dict[str, Any]) -> bool:
    task = get_task(server_url, identity["miner_id"])
    print(
        "Task assigned: "
        f"{task['task_id']} positions {task['range_start']}..{task['range_end']} "
        f"using {task['algorithm']}"
    )

    segment = calculate_pi_segment(task["range_start"], task["range_end"], task["algorithm"])
    result_hash = hash_result(segment, task["range_start"], task["range_end"], task["algorithm"])
    root = merkle_root(segment, task["range_start"])
    print(f"Calculated segment length: {len(segment)}")
    print(f"Result hash: {result_hash}")
    print(f"Merkle root: {root}")

    challenge = commit_result(server_url, task, identity, result_hash, root)
    if not challenge["accepted"]:
        print(f"Commit rejected: {challenge['message']}")
        return False
    print(f"Commit accepted. Revealing {len(challenge['samples'])} samples.")

    submission = reveal_samples(server_url, task, identity, segment, root, challenge)
    if submission["accepted"]:
        block = submission["block"]
        print(f"Accepted block #{block['height']}: {block['block_hash']}")
        print(f"Reward: {block['reward']}")
        return True

    print(f"Rejected: {submission['message']}")
    print(f"Validation: {submission['validation']}")
    return False


def command_register(args: argparse.Namespace) -> int:
    identity = register(args.server.rstrip("/"), args.name, args.identity, args.overwrite)
    print(f"Miner registered: {identity['miner_id']} ({identity['name']})")
    print(f"Identity saved: {args.identity}")
    return 0


def command_mine(args: argparse.Namespace) -> int:
    identity = load_identity(args.identity)
    server_url = args.server.rstrip("/") if args.server else identity.get("server_url", "http://127.0.0.1:8000")

    accepted = 0
    attempts = 0
    loops = 1 if args.once else args.loops

    for index in range(loops):
        attempts += 1
        print(f"Mining attempt {index + 1}/{loops} as {identity['miner_id']}")
        if mine_once(server_url, identity):
            accepted += 1
        if index + 1 < loops:
            time.sleep(args.sleep)

    print(f"Done. accepted={accepted} attempts={attempts}")
    return 0 if accepted == attempts else 1


def command_stats(args: argparse.Namespace) -> int:
    identity = load_identity(args.identity)
    server_url = args.server.rstrip("/") if args.server else identity.get("server_url", "http://127.0.0.1:8000")
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
    mine_parser.set_defaults(func=command_mine)

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
