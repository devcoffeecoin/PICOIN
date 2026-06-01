import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from app.core.merkle import verify_merkle_proof
from app.core.money import canonical_amount, to_units
from app.core.pi import calculate_pi_segment
from app.core.signatures import build_validation_result_signature_payload, generate_keypair, sign_payload, verify_payload_signature
from app.core.settings import CHAIN_ID, NETWORK_ID
from app.services.transactions import selected_tx_hashes_hash, transaction_commitment
from app.services.wallet import transaction_hash, unsigned_transaction_payload


DEFAULT_IDENTITY_PATH = Path("validator_identity.json")
AUTO_REGISTER_IDENTITY = os.getenv("PICOIN_AUTO_REGISTER_IDENTITY", "1").strip().lower() not in {"0", "false", "no"}
VALIDATOR_REWARD_ADDRESS = os.getenv("PICOIN_VALIDATOR_REWARD_ADDRESS", "").strip()
DEFAULT_NODE_SERVER = os.getenv("PICOIN_VALIDATOR_NODE_SERVER", os.getenv("PICOIN_NODE_SERVER", "http://127.0.0.1:8000"))
VALIDATOR_NODE_ADDRESS = os.getenv("PICOIN_VALIDATOR_NODE_ADDRESS", "").strip().rstrip("/")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_node_address(address: str) -> str:
    normalized = str(address or "").strip().rstrip("/")
    for duplicated in ("http://http://", "https://https://", "https://http://", "http://https://"):
        if normalized.startswith(duplicated):
            scheme, rest = duplicated.split("://", 1)
            normalized = f"{scheme}://{normalized[len(duplicated):]}"
            break
    return normalized


def load_identity(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"identity file not found: {path}")
    identity = json.loads(path.read_text(encoding="utf-8"))
    if VALIDATOR_REWARD_ADDRESS:
        identity["reward_address"] = VALIDATOR_REWARD_ADDRESS
    return identity


def load_or_register_identity(server_url: str, identity_path: Path, default_name: str | None = None) -> dict[str, Any]:
    try:
        return load_identity(identity_path)
    except FileNotFoundError:
        if not AUTO_REGISTER_IDENTITY:
            raise
    name = default_name or identity_path.stem or "local-validator"
    identity = register(server_url, name, identity_path, overwrite=False)
    print(f"Auto-registered validator identity: {identity['validator_id']} ({identity['name']})")
    print(f"Identity saved: {identity_path}")
    return identity


def save_identity(path: Path, identity: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(identity, indent=2, sort_keys=True), encoding="utf-8")


def register(server_url: str, name: str, identity_path: Path, overwrite: bool) -> dict[str, Any]:
    if identity_path.exists() and not overwrite:
        raise FileExistsError(f"identity already exists: {identity_path}")

    keypair = generate_keypair()
    response = requests.post(
        f"{server_url}/validators/register",
        json={"name": name, "public_key": keypair["public_key"], "reward_address": VALIDATOR_REWARD_ADDRESS or None},
        timeout=20,
    )
    response.raise_for_status()
    validator = response.json()
    identity = {
        "validator_id": validator["validator_id"],
        "name": validator["name"],
        "public_key": keypair["public_key"],
        "private_key": keypair["private_key"],
        "reward_address": VALIDATOR_REWARD_ADDRESS or validator.get("reward_address"),
        "server_url": server_url,
        "created_at": utc_now(),
    }
    save_identity(identity_path, identity)
    return identity


def get_job(server_url: str, identity: dict[str, Any] | str) -> dict[str, Any] | None:
    if isinstance(identity, str):
        params = {"validator_id": identity}
    else:
        params = {
            "validator_id": identity["validator_id"],
            "public_key": identity.get("public_key"),
            "name": identity.get("name") or identity["validator_id"],
            "reward_address": identity.get("reward_address"),
        }
        params = {key: value for key, value in params.items() if value}
    response = requests.get(f"{server_url}/validation/jobs", params=params, timeout=20)
    response.raise_for_status()
    if not response.content or response.text == "null":
        return None
    return response.json()


def send_validator_heartbeat(
    server_url: str,
    identity: dict[str, Any],
    *,
    node_server_url: str,
    timeout: float = 10.0,
) -> dict[str, Any]:
    node_server = node_server_url.rstrip("/")
    coordinator = server_url.rstrip("/")
    local_response = requests.get(f"{node_server}/node/sync-status", timeout=timeout)
    local_response.raise_for_status()
    local_status = local_response.json()

    try:
        remote_response = requests.get(f"{coordinator}/node/sync-status", timeout=timeout)
        remote_response.raise_for_status()
        remote_status = remote_response.json()
        remote_height = int(
            remote_status.get("effective_latest_block_height")
            or remote_status.get("latest_block_height")
            or 0
        )
    except requests.RequestException:
        remote_height = int(
            local_status.get("effective_latest_block_height")
            or local_status.get("latest_block_height")
            or 0
        )

    effective_height = int(
        local_status.get("effective_latest_block_height")
        or local_status.get("latest_block_height")
        or 0
    )
    local_height = int(local_status.get("local_block_height") or local_status.get("latest_block_height") or 0)
    advertised_address = normalize_node_address(
        VALIDATOR_NODE_ADDRESS
        or str(local_status.get("peer_address") or "")
        or node_server
    )
    payload = {
        "validator_id": identity["validator_id"],
        "node_id": local_status.get("node_id") or identity.get("node_id") or identity["validator_id"],
        "public_key": identity["public_key"],
        "address": advertised_address,
        "local_height": local_height,
        "effective_height": effective_height,
        "latest_block_hash": local_status.get("effective_latest_block_hash") or local_status.get("latest_block_hash"),
        "pending_replay_blocks": int(local_status.get("pending_replay_blocks") or 0),
        "sync_lag": max(0, remote_height - effective_height),
        "version": local_status.get("protocol_version") or "0.18",
    }
    payload["signature"] = sign_payload(identity["private_key"], payload)
    response = requests.post(f"{coordinator}/validators/heartbeat", json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()


def validate_job(job: dict[str, Any]) -> tuple[bool, str]:
    tx_hashes = list(job.get("selected_tx_hashes") or [])
    transactions = list(job.get("transactions") or [])
    if len(tx_hashes) != int(job.get("tx_count") or 0):
        return False, "tx_snapshot_mismatch"
    if selected_tx_hashes_hash(tx_hashes) != (job.get("selected_tx_hashes_hash") or selected_tx_hashes_hash([])):
        return False, "tx_snapshot_mismatch"
    if transactions:
        actual_hashes = [tx.get("tx_hash") for tx in transactions]
        if actual_hashes != tx_hashes:
            return False, "invalid_tx_order"
        for tx in transactions:
            amount_units = int(tx.get("amount_units") if tx.get("amount_units") is not None else to_units(tx.get("amount", 0)))
            fee_units = int(tx.get("fee_units") if tx.get("fee_units") is not None else to_units(tx.get("fee", 0)))
            unsigned = unsigned_transaction_payload(
                tx_type=tx.get("tx_type"),
                sender=tx.get("sender"),
                recipient=tx.get("recipient"),
                amount=canonical_amount(amount_units),
                nonce=int(tx.get("nonce") or 0),
                fee=canonical_amount(fee_units),
                payload=tx.get("payload") or {},
                timestamp=tx.get("timestamp"),
                network_id=tx.get("network_id"),
                chain_id=tx.get("chain_id"),
            )
            if str(unsigned["chain_id"]) != str(CHAIN_ID) or unsigned["network_id"] != NETWORK_ID:
                return False, "invalid_tx_payload"
            if transaction_hash(unsigned, tx.get("public_key", "")) != tx.get("tx_hash"):
                return False, "invalid_tx_payload"
            if not verify_payload_signature(tx.get("public_key", ""), unsigned, tx.get("signature", "")):
                return False, "invalid_tx_signature"
        commitment = transaction_commitment(transactions)
        if commitment["tx_merkle_root"] != (job.get("tx_merkle_root") or ""):
            return False, "invalid_tx_merkle_root"
        if int(commitment["tx_fee_total_units"]) != int(job.get("tx_fee_total_units") or 0):
            return False, "invalid_fee_total"
    for sample in job["samples"]:
        position = sample["position"]
        digit = str(sample["digit"]).upper()
        expected_digit = calculate_pi_segment(position, position, job["algorithm"])
        if digit != expected_digit:
            return False, f"digit mismatch at position {position}"
        if not verify_merkle_proof(position, digit, sample["proof"], job["merkle_root"]):
            return False, f"invalid merkle proof at position {position}"
    return True, "external validator accepted samples"


def submit_result(server_url: str, identity: dict[str, Any], job: dict[str, Any], approved: bool, reason: str) -> dict[str, Any]:
    signed_at = utc_now()
    payload = build_validation_result_signature_payload(
        job_id=job["job_id"],
        validator_id=identity["validator_id"],
        task_id=job["task_id"],
        approved=approved,
        reason=reason,
        signed_at=signed_at,
    )
    signature = sign_payload(identity["private_key"], payload)
    response = requests.post(
        f"{server_url}/validation/results",
        json={
            "job_id": job["job_id"],
            "validator_id": identity["validator_id"],
            "approved": approved,
            "reason": reason,
            "signature": signature,
            "signed_at": signed_at,
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def command_register(args: argparse.Namespace) -> int:
    identity = register(args.server.rstrip("/"), args.name, args.identity, args.overwrite)
    print(f"Validator registered: {identity['validator_id']} ({identity['name']})")
    print(f"Identity saved: {args.identity}")
    return 0


def command_validate(args: argparse.Namespace) -> int:
    server_url = args.server.rstrip("/")
    identity = load_or_register_identity(server_url, args.identity)
    completed = 0

    for index in range(args.loops):
        heartbeat = send_validator_heartbeat(
            server_url,
            identity,
            node_server_url=args.node_server.rstrip("/"),
            timeout=args.node_timeout,
        )
        if heartbeat.get("eligible") is False:
            print(
                f"Validator node heartbeat accepted but not eligible: "
                f"{heartbeat.get('reason_if_not_eligible') or heartbeat.get('sync_status') or heartbeat.get('online_status')}"
            )
            if args.once:
                return 0
            time.sleep(args.sleep)
            continue

        job = get_job(server_url, identity)
        if job is None:
            print("No validation jobs available.")
            if args.once:
                return 0
            time.sleep(args.sleep)
            continue

        approved, reason = validate_job(job)
        result = submit_result(server_url, identity, job, approved, reason)
        print(
            f"Validated {job['job_id']}: approved={approved} "
            f"status={result['status']} approvals={result.get('approvals', 0)}/"
            f"{result.get('required_approvals', 1)}"
        )
        completed += 1

        if args.once:
            break
        if index + 1 < args.loops:
            time.sleep(args.sleep)

    print(f"Done. validation_jobs_completed={completed}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Picoin external validator.")
    parser.add_argument("--server", default="http://127.0.0.1:8000", help="Coordinator API base URL")
    parser.add_argument("--identity", type=Path, default=DEFAULT_IDENTITY_PATH, help="Local validator identity JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)

    register_parser = subparsers.add_parser("register", help="Register a validator and create Ed25519 identity")
    register_parser.add_argument("--name", default="local-validator", help="Validator display name")
    register_parser.add_argument("--overwrite", action="store_true", help="Overwrite existing identity file")
    register_parser.set_defaults(func=command_register)

    validate_parser = subparsers.add_parser("validate", help="Validate pending reveal jobs")
    validate_parser.add_argument("--once", action="store_true", help="Validate at most one job")
    validate_parser.add_argument("--loops", type=int, default=1, help="Number of polling attempts")
    validate_parser.add_argument("--sleep", type=float, default=1.0, help="Seconds between polls")
    validate_parser.add_argument("--node-server", default=DEFAULT_NODE_SERVER, help="Local Picoin node API used for signed validator liveness")
    validate_parser.add_argument("--node-timeout", type=float, default=10.0, help="Seconds to wait for the local node heartbeat probe")
    validate_parser.set_defaults(func=command_validate)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        raise SystemExit(args.func(args))
    except FileNotFoundError as exc:
        print(f"{exc}. Run: python -m validator.client register --name val1", file=sys.stderr)
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
