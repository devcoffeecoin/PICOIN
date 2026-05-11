import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.settings import FAUCET_DEFAULT_AMOUNT
from app.core.signatures import generate_keypair
from app.db.database import init_db
from app.services.mining import register_miner, register_validator, request_faucet


DEFAULT_IDENTITY_DIR = Path("data/testnet/identities")
DEFAULT_SERVER_URL = "http://127.0.0.1:8000"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def create_miner_identity(name: str, server_url: str) -> dict[str, Any]:
    keypair = generate_keypair()
    miner = register_miner(name, keypair["public_key"])
    return {
        "miner_id": miner["miner_id"],
        "name": miner["name"],
        "public_key": keypair["public_key"],
        "private_key": keypair["private_key"],
        "server_url": server_url,
        "created_at": utc_now(),
    }


def create_validator_identity(name: str, server_url: str) -> dict[str, Any]:
    keypair = generate_keypair()
    validator = register_validator(name, keypair["public_key"])
    return {
        "validator_id": validator["validator_id"],
        "name": validator["name"],
        "public_key": keypair["public_key"],
        "private_key": keypair["private_key"],
        "server_url": server_url,
        "created_at": utc_now(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Create deterministic local testnet demo identities.")
    parser.add_argument("--identity-dir", type=Path, default=DEFAULT_IDENTITY_DIR)
    parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    parser.add_argument("--miner-name", default="demo-miner-alice")
    parser.add_argument("--validator-one-name", default="demo-validator-one")
    parser.add_argument("--validator-two-name", default="demo-validator-two")
    parser.add_argument("--validator-three-name", default="demo-validator-three")
    parser.add_argument("--miner-faucet", type=float, default=FAUCET_DEFAULT_AMOUNT)
    args = parser.parse_args()

    init_db()
    server_url = args.server.rstrip("/")
    miner = create_miner_identity(args.miner_name, server_url)
    validator_one = create_validator_identity(args.validator_one_name, server_url)
    validator_two = create_validator_identity(args.validator_two_name, server_url)
    validator_three = create_validator_identity(args.validator_three_name, server_url)

    faucet = request_faucet(miner["miner_id"], "miner", args.miner_faucet)

    paths = {
        "miner": args.identity_dir / "miner-alice.json",
        "validator_one": args.identity_dir / "validator-one.json",
        "validator_two": args.identity_dir / "validator-two.json",
        "validator_three": args.identity_dir / "validator-three.json",
    }
    write_json(paths["miner"], miner)
    write_json(paths["validator_one"], validator_one)
    write_json(paths["validator_two"], validator_two)
    write_json(paths["validator_three"], validator_three)

    manifest = {
        "server_url": server_url,
        "created_at": utc_now(),
        "miner": {"identity": str(paths["miner"]), "miner_id": miner["miner_id"]},
        "validators": [
            {"identity": str(paths["validator_one"]), "validator_id": validator_one["validator_id"]},
            {"identity": str(paths["validator_two"]), "validator_id": validator_two["validator_id"]},
            {"identity": str(paths["validator_three"]), "validator_id": validator_three["validator_id"]},
        ],
        "faucet": faucet,
    }
    write_json(args.identity_dir.parent / "manifest.json", manifest)

    print("Local testnet identities created:")
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
