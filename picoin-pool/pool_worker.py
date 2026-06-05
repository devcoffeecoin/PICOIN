from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]
CORE_PATH = Path(os.getenv("PICOIN_CORE_PATH", REPO_ROOT / "picoin-proof-of-pi"))
if str(CORE_PATH) not in sys.path:
    sys.path.insert(0, str(CORE_PATH))

from app.core.performance import elapsed_ms, now_perf  # noqa: E402
from app.core.pi import calculate_pi_segment  # noqa: E402


def headers(token: str | None) -> dict[str, str]:
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def register(pool_url: str, worker_id: str, name: str, payout_address: str | None, token: str | None) -> None:
    response = requests.post(
        f"{pool_url}/workers/register",
        json={"worker_id": worker_id, "name": name, "payout_address": payout_address},
        headers=headers(token),
        timeout=20,
    )
    response.raise_for_status()


def mine_once(pool_url: str, worker_id: str, token: str | None) -> bool:
    response = requests.get(
        f"{pool_url}/work/next",
        params={"worker_id": worker_id},
        headers=headers(token),
        timeout=20,
    )
    response.raise_for_status()
    work = response.json()
    if work.get("status") != "work":
        print(f"Pool idle: {work.get('message', 'no work')}")
        return False

    started = now_perf()
    segment = calculate_pi_segment(int(work["range_start"]), int(work["range_end"]), work["algorithm"])
    compute_ms = elapsed_ms(started)
    submit = requests.post(
        f"{pool_url}/work/submit",
        json={
            "worker_id": worker_id,
            "chunk_id": work["chunk_id"],
            "segment": segment,
            "compute_ms": compute_ms,
        },
        headers=headers(token),
        timeout=20,
    )
    submit.raise_for_status()
    result: dict[str, Any] = submit.json()
    print(
        f"Submitted {work['chunk_id']} {work['range_start']}..{work['range_end']} "
        f"units={result.get('units')} compute_ms={compute_ms}"
    )
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Picoin pool worker alpha.")
    parser.add_argument("--pool", default="http://127.0.0.1:9321", help="Pool server URL")
    parser.add_argument("--worker-id", default=f"worker-{socket.gethostname()}")
    parser.add_argument("--name", default=socket.gethostname())
    parser.add_argument("--payout-address", default=os.getenv("PICOIN_POOL_PAYOUT_ADDRESS", ""))
    parser.add_argument("--auth-token", default=os.getenv("PICOIN_POOL_TOKEN", ""))
    parser.add_argument("--loops", type=int, default=1)
    parser.add_argument("--sleep", type=float, default=2.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pool_url = args.pool.rstrip("/")
    token = args.auth_token.strip() or None
    payout_address = args.payout_address.strip() or None
    register(pool_url, args.worker_id, args.name, payout_address, token)
    completed = 0
    for index in range(max(1, args.loops)):
        if mine_once(pool_url, args.worker_id, token):
            completed += 1
        if index + 1 < args.loops:
            time.sleep(max(0.1, args.sleep))
    print(f"Done. completed_chunks={completed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

