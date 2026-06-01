import argparse
import json
import time
from pathlib import Path
from typing import Any

import requests

from app.core.settings import FAUCET_DEFAULT_AMOUNT
from app.tools.run_testnet_cycle import validate_once
from miner.client import load_identity as load_miner_identity
from miner.client import mine_once, register as register_miner_identity
from validator.client import load_identity as load_validator_identity


DEFAULT_IDENTITY_DIR = Path("data/testnet/identities")
DEFAULT_SERVER_URL = "http://127.0.0.1:8000"


def miner_path(identity_dir: Path, index: int) -> Path:
    if index == 1:
        return identity_dir / "miner-alice.json"
    return identity_dir / f"miner-{index}.json"


def load_or_register_miner(server_url: str, identity_dir: Path, index: int, faucet_amount: float) -> dict[str, Any]:
    path = miner_path(identity_dir, index)
    if path.exists():
        return load_miner_identity(path)

    identity = register_miner_identity(server_url, f"demo-miner-{index}", path, overwrite=False)
    if faucet_amount <= 0:
        return identity
    response = requests.post(
        f"{server_url}/faucet",
        json={"account_id": identity["miner_id"], "account_type": "miner", "amount": faucet_amount},
        timeout=20,
    )
    response.raise_for_status()
    return identity


def accepted_blocks(server_url: str) -> int:
    response = requests.get(f"{server_url}/stats", timeout=20)
    response.raise_for_status()
    return int(response.json()["accepted_blocks"])


def validate_until_block_progress(server_url: str, validators: list[dict[str, Any]], previous_blocks: int) -> bool:
    for _ in range(max(1, len(validators) * 2)):
        progressed = False
        for validator in validators:
            result = validate_once(server_url, validator)
            if result is not None:
                progressed = True
            if accepted_blocks(server_url) > previous_blocks:
                return True
        if not progressed:
            return False
    return accepted_blocks(server_url) > previous_blocks


def main() -> None:
    parser = argparse.ArgumentParser(description="Run continuous local testnet mining with multiple miners.")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    parser.add_argument("--identity-dir", type=Path, default=DEFAULT_IDENTITY_DIR)
    parser.add_argument("--miners", type=int, default=3)
    parser.add_argument("--loops", type=int, default=3)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--sleep", type=float, default=0.5)
    parser.add_argument("--faucet", type=float, default=FAUCET_DEFAULT_AMOUNT)
    parser.add_argument("--retro-audit", action="store_true", default=True)
    parser.add_argument("--no-retro-audit", action="store_false", dest="retro_audit")
    args = parser.parse_args()

    server_url = args.server.rstrip("/")
    miners = [
        load_or_register_miner(server_url, args.identity_dir, index, args.faucet)
        for index in range(1, args.miners + 1)
    ]
    validators = [
        load_validator_identity(args.identity_dir / "validator-one.json"),
        load_validator_identity(args.identity_dir / "validator-two.json"),
        load_validator_identity(args.identity_dir / "validator-three.json"),
    ]

    completed = 0
    audits = 0
    for loop_index in range(args.loops):
        for miner in miners:
            print(f"Round {loop_index + 1}/{args.loops}: mining as {miner['miner_id']}")
            mined = mine_once(server_url, miner, args.workers)
            if not mined:
                continue

            before_blocks = accepted_blocks(server_url)
            block_accepted = validate_until_block_progress(server_url, validators, before_blocks)
            if not block_accepted:
                print("Mining attempt is pending validator quorum; skipping audit until a block is accepted.")
                continue
            completed += 1
            if args.retro_audit:
                response = requests.post(
                    f"{server_url}/audit/retroactive/run",
                    params={"sample_multiplier": 2},
                    timeout=60,
                )
                response.raise_for_status()
                audit = response.json()["audit"]
                audits += 1
                print(
                    f"Retro audit block={audit['block_height']} "
                    f"samples={audit['sample_count']} passed={audit['passed']}"
                )

            if args.sleep and (loop_index + 1 < args.loops):
                time.sleep(args.sleep)

    stats = requests.get(f"{server_url}/stats", timeout=20)
    stats.raise_for_status()
    health = requests.get(f"{server_url}/health", timeout=20)
    health.raise_for_status()
    print(
        json.dumps(
            {
                "completed_mining_attempts": completed,
                "retroactive_audits": audits,
                "stats": stats.json(),
                "health": health.json(),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
