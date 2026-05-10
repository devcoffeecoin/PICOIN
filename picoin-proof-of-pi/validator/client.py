import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from app.core.merkle import verify_merkle_proof
from app.core.pi import calculate_pi_segment
from app.core.signatures import build_validation_result_signature_payload, generate_keypair, sign_payload


DEFAULT_IDENTITY_PATH = Path("validator_identity.json")


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
        f"{server_url}/validators/register",
        json={"name": name, "public_key": keypair["public_key"]},
        timeout=20,
    )
    response.raise_for_status()
    validator = response.json()
    identity = {
        "validator_id": validator["validator_id"],
        "name": validator["name"],
        "public_key": keypair["public_key"],
        "private_key": keypair["private_key"],
        "server_url": server_url,
        "created_at": utc_now(),
    }
    save_identity(identity_path, identity)
    return identity


def get_job(server_url: str, validator_id: str) -> dict[str, Any] | None:
    response = requests.get(f"{server_url}/validation/jobs", params={"validator_id": validator_id}, timeout=20)
    response.raise_for_status()
    if not response.content or response.text == "null":
        return None
    return response.json()


def validate_job(job: dict[str, Any]) -> tuple[bool, str]:
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
    identity = load_identity(args.identity)
    server_url = args.server.rstrip("/")
    completed = 0

    for index in range(args.loops):
        job = get_job(server_url, identity["validator_id"])
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
