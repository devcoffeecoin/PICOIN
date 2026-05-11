import argparse
import json
from pathlib import Path
from typing import Any

import requests

from miner.client import load_identity as load_miner_identity
from miner.client import mine_once
from validator.client import get_job, load_identity as load_validator_identity
from validator.client import submit_result, validate_job


DEFAULT_MINER = Path("data/testnet/identities/miner-alice.json")
DEFAULT_VALIDATOR_ONE = Path("data/testnet/identities/validator-one.json")
DEFAULT_VALIDATOR_TWO = Path("data/testnet/identities/validator-two.json")
DEFAULT_SERVER_URL = "http://127.0.0.1:8000"


def validate_once(server_url: str, identity: dict[str, Any]) -> dict[str, Any] | None:
    job = get_job(server_url, identity["validator_id"])
    if job is None:
        print(f"No validation job available for {identity['validator_id']}.")
        return None

    approved, reason = validate_job(job)
    result = submit_result(server_url, identity, job, approved, reason)
    print(
        f"Validator {identity['validator_id']} voted approved={approved} "
        f"status={result['status']} approvals={result.get('approvals', 0)}/"
        f"{result.get('required_approvals', 1)}"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one repeatable local mining cycle over the REST API.")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    parser.add_argument("--miner", type=Path, default=DEFAULT_MINER)
    parser.add_argument("--validator-one", type=Path, default=DEFAULT_VALIDATOR_ONE)
    parser.add_argument("--validator-two", type=Path, default=DEFAULT_VALIDATOR_TWO)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    server_url = args.server.rstrip("/")
    miner_identity = load_miner_identity(args.miner)
    validator_one = load_validator_identity(args.validator_one)
    validator_two = load_validator_identity(args.validator_two)

    print(f"Mining once as {miner_identity['miner_id']}")
    mined = mine_once(server_url, miner_identity, args.workers)
    if not mined:
        raise SystemExit(1)

    first_result = validate_once(server_url, validator_one)
    second_result = validate_once(server_url, validator_two)

    stats = requests.get(f"{server_url}/stats", timeout=20)
    stats.raise_for_status()
    verify = requests.get(f"{server_url}/blocks/verify", timeout=20)
    verify.raise_for_status()

    summary = {
        "first_validator_status": None if first_result is None else first_result["status"],
        "second_validator_status": None if second_result is None else second_result["status"],
        "stats": stats.json(),
        "chain": verify.json(),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
