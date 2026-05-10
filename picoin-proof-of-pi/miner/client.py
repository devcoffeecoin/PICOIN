import argparse
import sys
from typing import Any

import requests

from app.core.crypto import hash_result, sha256_text
from app.core.pi import calculate_pi_segment


def simple_signature(miner_id: str, task_id: str, result_hash: str) -> str:
    return sha256_text(f"{miner_id}:{task_id}:{result_hash}")


def register(server_url: str, name: str) -> dict[str, Any]:
    response = requests.post(
        f"{server_url}/miners/register",
        json={"name": name, "public_key": f"simple:{name}"},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def get_task(server_url: str, miner_id: str) -> dict[str, Any]:
    response = requests.get(f"{server_url}/tasks/next", params={"miner_id": miner_id}, timeout=20)
    response.raise_for_status()
    return response.json()


def submit_result(
    server_url: str,
    task: dict[str, Any],
    miner_id: str,
    segment: str,
    result_hash: str,
) -> dict[str, Any]:
    response = requests.post(
        f"{server_url}/tasks/submit",
        json={
            "task_id": task["task_id"],
            "miner_id": miner_id,
            "result_hash": result_hash,
            "segment": segment,
            "signature": simple_signature(miner_id, task["task_id"], result_hash),
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def run_once(server_url: str, name: str, miner_id: str | None) -> int:
    if miner_id is None:
        miner = register(server_url, name)
        miner_id = miner["miner_id"]
        print(f"Miner registered: {miner_id} ({miner['name']})")
    else:
        print(f"Using miner: {miner_id}")

    task = get_task(server_url, miner_id)
    print(
        "Task assigned: "
        f"{task['task_id']} positions {task['range_start']}..{task['range_end']} "
        f"using {task['algorithm']}"
    )

    segment = calculate_pi_segment(task["range_start"], task["range_end"], task["algorithm"])
    result_hash = hash_result(segment, task["range_start"], task["range_end"], task["algorithm"])
    print(f"Calculated segment: {segment}")
    print(f"Result hash: {result_hash}")

    submission = submit_result(server_url, task, miner_id, segment, result_hash)
    if submission["accepted"]:
        block = submission["block"]
        print(f"Accepted block #{block['height']}: {block['block_hash']}")
        print(f"Reward: {block['reward']}")
        return 0

    print(f"Rejected: {submission['message']}")
    print(f"Validation: {submission['validation']}")
    return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a Picoin Proof-of-Pi miner once.")
    parser.add_argument("--server", default="http://127.0.0.1:8000", help="Coordinator API base URL")
    parser.add_argument("--name", default="local-miner", help="Miner display name used when registering")
    parser.add_argument("--miner-id", default=None, help="Existing miner id. If omitted, a new miner is registered.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        raise SystemExit(run_once(args.server.rstrip("/"), args.name, args.miner_id))
    except requests.RequestException as exc:
        print(f"Network/API error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
