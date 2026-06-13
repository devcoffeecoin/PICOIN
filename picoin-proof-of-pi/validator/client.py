import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from app.core.http import worker_http_timeout_seconds
from app.core.merkle import verify_merkle_proof
from app.core.money import canonical_amount, to_units
from app.core.pi import calculate_pi_segment
from app.core.signatures import build_validation_result_signature_payload, generate_keypair, sign_payload, verify_payload_signature
from app.core.settings import CHAIN_ID, NETWORK_ID
from app.services.transactions import selected_tx_hashes_hash, transaction_commitment
from app.services.wallet import matching_transaction_signature_payload, unsigned_transaction_payload


DEFAULT_IDENTITY_PATH = Path("validator_identity.json")
AUTO_REGISTER_IDENTITY = os.getenv("PICOIN_AUTO_REGISTER_IDENTITY", "1").strip().lower() not in {"0", "false", "no"}
VALIDATOR_REWARD_ADDRESS = os.getenv("PICOIN_VALIDATOR_REWARD_ADDRESS", "").strip()
DEFAULT_NODE_SERVER = os.getenv("PICOIN_VALIDATOR_NODE_SERVER", os.getenv("PICOIN_NODE_SERVER", "http://127.0.0.1:8000"))
VALIDATOR_NODE_ADDRESS = os.getenv("PICOIN_VALIDATOR_NODE_ADDRESS", "").strip().rstrip("/")
DEFAULT_VALIDATOR_WORKERS = max(1, int(os.getenv("PICOIN_VALIDATOR_WORKERS", "4")))
FALSE_VALUES = {"0", "false", "no", "off"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_node_address(address: str) -> str:
    normalized = str(address or "").strip().rstrip("/")
    normalized = normalized.rstrip("=").strip().rstrip("/")
    for duplicated in ("http://http://", "https://https://", "https://http://", "http://https://"):
        if normalized.startswith(duplicated):
            scheme, rest = duplicated.split("://", 1)
            normalized = f"{scheme}://{normalized[len(duplicated):]}"
            break
    return normalized


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 100) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(minimum, min(parsed, maximum))


def _validator_reconcile_enabled() -> bool:
    return os.getenv("PICOIN_VALIDATOR_RECONCILE_ENABLED", "1").strip().lower() not in FALSE_VALUES


def configured_reconcile_peers(server_url: str) -> list[str]:
    raw = (
        os.getenv("PICOIN_VALIDATOR_RECONCILE_PEERS")
        or os.getenv("PICOIN_RECONCILE_PEERS")
        or os.getenv("PICOIN_BOOTSTRAP_PEERS")
        or os.getenv("PICOIN_BOOTSTRAP_PEER")
        or ""
    )
    local_addresses = {
        normalize_node_address(server_url),
        normalize_node_address(os.getenv("PICOIN_NODE_ADDRESS", "")),
        normalize_node_address(os.getenv("PICOIN_NODE_SERVER", "")),
        normalize_node_address(os.getenv("PICOIN_VALIDATOR_NODE_SERVER", "")),
    }
    local_addresses.discard("")
    peers: list[str] = []
    seen: set[str] = set()
    for item in raw.split(","):
        peer = normalize_node_address(item)
        if not peer or peer in local_addresses or peer in seen:
            continue
        if not (peer.startswith("http://") or peer.startswith("https://")):
            continue
        peers.append(peer)
        seen.add(peer)
    return peers


def reconcile_configured_peers(
    server_url: str,
    peers: list[str],
    *,
    limit: int,
    timeout: float,
) -> dict[str, int]:
    summary = {
        "attempted": 0,
        "jobs": 0,
        "votes": 0,
        "heartbeats": 0,
        "errors": 0,
    }
    if not peers or not _validator_reconcile_enabled():
        return summary
    coordinator = normalize_node_address(server_url)
    for peer in peers:
        response = requests.post(
            f"{coordinator}/node/reconcile",
            params={"limit": limit, "peer_address": peer},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        summary["attempted"] += 1
        summary["jobs"] += int(payload.get("validation_jobs_imported") or 0)
        summary["votes"] += int(payload.get("validation_votes_imported") or 0)
        summary["heartbeats"] += int(payload.get("validator_heartbeats_imported") or 0)
        summary["errors"] += int(payload.get("errors") or 0)
    return summary


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


def _request_error_summary(exc: BaseException) -> str:
    detail = _request_error_detail(exc)
    if detail:
        return f"{exc} detail={detail}"
    return str(exc)


def _request_error_detail(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return ""
    detail = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            detail = str(payload.get("detail") or payload)
        else:
            detail = str(payload)
    except ValueError:
        detail = str(getattr(response, "text", "") or "")
    return detail.strip()


def _poll_error_needs_heartbeat(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    if int(getattr(response, "status_code", 0) or 0) != 403:
        return False
    detail = _request_error_detail(exc).lower()
    return (
        "validator offline" in detail
        or "validator stale" in detail
        or "heartbeat required" in detail
    )


def register(server_url: str, name: str, identity_path: Path, overwrite: bool) -> dict[str, Any]:
    if identity_path.exists() and not overwrite:
        raise FileExistsError(f"identity already exists: {identity_path}")

    keypair = generate_keypair()
    response = requests.post(
        f"{server_url}/validators/register",
        json={"name": name, "public_key": keypair["public_key"], "reward_address": VALIDATOR_REWARD_ADDRESS or None},
        timeout=worker_http_timeout_seconds(),
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
    response = requests.get(
        f"{server_url}/validation/jobs",
        params=params,
        timeout=worker_http_timeout_seconds(),
    )
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
    local_status = _heartbeat_node_status(node_server, timeout=timeout)

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
        "sync_lag": max(0, int(local_status.get("sync_lag") or 0)),
        "version": local_status.get("protocol_version") or "0.18",
        "heartbeat_at": utc_now(),
    }
    payload["signature"] = sign_payload(identity["private_key"], payload)
    response = requests.post(f"{coordinator}/validators/heartbeat", json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _heartbeat_node_status(server_url: str, *, timeout: float) -> dict[str, Any]:
    last_error: requests.RequestException | None = None
    for path in ("/node/liveness", "/node/sync-status"):
        try:
            response = requests.get(f"{server_url.rstrip('/')}{path}", timeout=timeout)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise requests.RequestException("node status unavailable")


def validate_sample(args: tuple[dict[str, Any], str, str]) -> tuple[bool, str]:
    sample, algorithm, merkle_root = args
    position = sample["position"]
    digit = str(sample["digit"]).upper()
    expected_digit = calculate_pi_segment(position, position, algorithm)
    if digit != expected_digit:
        return False, f"digit mismatch at position {position}"
    if not verify_merkle_proof(position, digit, sample["proof"], merkle_root):
        return False, f"invalid merkle proof at position {position}"
    return True, ""


def validate_samples(job: dict[str, Any], workers: int) -> tuple[bool, str]:
    samples = list(job["samples"])
    algorithm = job["algorithm"]
    merkle_root = job["merkle_root"]
    if workers <= 1 or len(samples) <= 1:
        for sample in samples:
            ok, reason = validate_sample((sample, algorithm, merkle_root))
            if not ok:
                return ok, reason
        return True, ""

    worker_count = max(1, min(int(workers), len(samples)))
    with ProcessPoolExecutor(max_workers=worker_count) as executor:
        results = executor.map(validate_sample, ((sample, algorithm, merkle_root) for sample in samples))
    for ok, reason in results:
        if not ok:
            return ok, reason
    return True, ""


def validate_job(job: dict[str, Any], workers: int = 1) -> tuple[bool, str]:
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
            signature_payload = matching_transaction_signature_payload(
                unsigned,
                tx.get("public_key", ""),
                tx.get("tx_hash", ""),
            )
            if signature_payload is None:
                return False, "invalid_tx_payload"
            if not verify_payload_signature(tx.get("public_key", ""), signature_payload, tx.get("signature", "")):
                return False, "invalid_tx_signature"
        commitment = transaction_commitment(transactions)
        if commitment["tx_merkle_root"] != (job.get("tx_merkle_root") or ""):
            return False, "invalid_tx_merkle_root"
        if int(commitment["tx_fee_total_units"]) != int(job.get("tx_fee_total_units") or 0):
            return False, "invalid_fee_total"
    samples_valid, sample_reason = validate_samples(job, workers)
    if not samples_valid:
        return False, sample_reason
    return True, "external validator accepted samples"


def submit_result(
    server_url: str,
    identity: dict[str, Any],
    job: dict[str, Any],
    approved: bool,
    reason: str,
    *,
    timeout: float = 90.0,
) -> dict[str, Any]:
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
        timeout=timeout,
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
    configured_poll_seconds = getattr(args, "poll_seconds", None)
    if configured_poll_seconds is None:
        configured_poll_seconds = getattr(args, "sleep", 1.0)
    poll_seconds = max(0.0, float(configured_poll_seconds))
    heartbeat_interval = max(1.0, float(getattr(args, "heartbeat_interval", 30.0)))
    heartbeat_timeout = max(
        float(args.node_timeout),
        _env_float("PICOIN_VALIDATOR_HEARTBEAT_TIMEOUT_SECONDS", 30.0),
    )
    workers = max(1, int(getattr(args, "workers", DEFAULT_VALIDATOR_WORKERS)))
    reconcile_peers = configured_reconcile_peers(server_url)
    reconcile_interval = max(0.0, _env_float("PICOIN_VALIDATOR_RECONCILE_INTERVAL_SECONDS", 10.0))
    reconcile_limit = _env_int("PICOIN_VALIDATOR_RECONCILE_LIMIT", 100, minimum=1, maximum=100)
    reconcile_timeout = max(1.0, _env_float("PICOIN_VALIDATOR_RECONCILE_TIMEOUT_SECONDS", 30.0))
    last_reconcile_at = 0.0
    heartbeat: dict[str, Any] | None = None
    heartbeat_at = 0.0
    heartbeat_attempt_at = 0.0

    def heartbeat_is_due() -> bool:
        last_heartbeat_check_at = max(heartbeat_at, heartbeat_attempt_at)
        return (
            last_heartbeat_check_at <= 0.0
            or (time.monotonic() - last_heartbeat_check_at) >= heartbeat_interval
        )

    def refresh_heartbeat_if_due(*, force: bool = False) -> bool:
        nonlocal heartbeat, heartbeat_at, heartbeat_attempt_at
        if not force and not heartbeat_is_due():
            return False
        heartbeat_attempt_at = time.monotonic()
        try:
            heartbeat = send_validator_heartbeat(
                server_url,
                identity,
                node_server_url=args.node_server.rstrip("/"),
                timeout=heartbeat_timeout,
            )
            heartbeat_at = time.monotonic()
            heartbeat_attempt_at = heartbeat_at
            return True
        except requests.RequestException as exc:
            print(
                "Validator coordinator temporarily unavailable during heartbeat: "
                f"{_request_error_summary(exc)}; "
                "continuing to poll validation jobs with previous liveness",
                file=sys.stderr,
            )
            return False

    for index in range(args.loops):
        refresh_heartbeat_if_due()
        job_poll_failed = False
        try:
            job = get_job(server_url, identity)
        except requests.RequestException as exc:
            job = None
            if _poll_error_needs_heartbeat(exc):
                print(
                    "Validator liveness rejected while polling validation job: "
                    f"{_request_error_summary(exc)}; refreshing heartbeat and retrying",
                    file=sys.stderr,
                )
                if refresh_heartbeat_if_due(force=True) and (
                    heartbeat is None or heartbeat.get("eligible") is not False
                ):
                    try:
                        job = get_job(server_url, identity)
                    except requests.RequestException as retry_exc:
                        print(
                            "Validator coordinator temporarily unavailable while polling validation job "
                            f"after heartbeat retry: {_request_error_summary(retry_exc)}",
                            file=sys.stderr,
                        )
                        job_poll_failed = True
                else:
                    job_poll_failed = True
            else:
                print(
                    "Validator coordinator temporarily unavailable while polling validation job: "
                    f"{_request_error_summary(exc)}",
                    file=sys.stderr,
                )
                job_poll_failed = True
        if job is not None:
            refresh_heartbeat_if_due()
            approved, reason = validate_job(job, workers=workers)
            try:
                result = submit_result(server_url, identity, job, approved, reason, timeout=args.submit_timeout)
            except requests.RequestException as exc:
                print(
                    "Validator coordinator temporarily unavailable while submitting validation result: "
                    f"{_request_error_summary(exc)}",
                    file=sys.stderr,
                )
                if args.once:
                    return 0
                time.sleep(poll_seconds)
                continue
            print(
                f"Validated {job['job_id']}: approved={approved} "
                f"status={result['status']} approvals={result.get('approvals', 0)}/"
                f"{result.get('required_approvals', 1)}"
            )
            completed += 1

            if not args.once:
                refresh_heartbeat_if_due()
            if args.once:
                break
            if index + 1 < args.loops:
                time.sleep(poll_seconds)
            continue

        refresh_heartbeat_if_due()
        if heartbeat is not None and heartbeat.get("eligible") is False:
            print(
                f"Validator node heartbeat accepted but not eligible: "
                f"{heartbeat.get('reason_if_not_eligible') or heartbeat.get('sync_status') or heartbeat.get('online_status')}"
            )
            if args.once:
                return 0
            time.sleep(poll_seconds)
            continue

        if not job_poll_failed:
            print("No validation jobs available.")

        should_reconcile = (
            bool(reconcile_peers)
            and _validator_reconcile_enabled()
            and (last_reconcile_at <= 0.0 or (time.monotonic() - last_reconcile_at) >= reconcile_interval)
        )
        if should_reconcile:
            try:
                reconcile = reconcile_configured_peers(
                    server_url,
                    reconcile_peers,
                    limit=reconcile_limit,
                    timeout=reconcile_timeout,
                )
                last_reconcile_at = time.monotonic()
                if reconcile["jobs"] or reconcile["votes"] or reconcile["heartbeats"]:
                    print(
                        "Validator reconcile imported "
                        f"jobs={reconcile['jobs']} votes={reconcile['votes']} "
                        f"heartbeats={reconcile['heartbeats']} peers={reconcile['attempted']}"
                    )
            except requests.RequestException as exc:
                last_reconcile_at = time.monotonic()
                print(
                    f"Validator peer reconcile temporarily unavailable: {_request_error_summary(exc)}",
                    file=sys.stderr,
                )

        if args.once:
            return 0
        if index + 1 < args.loops:
            time.sleep(poll_seconds)

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
    validate_parser.add_argument(
        "--poll-seconds",
        type=float,
        default=(
            float(os.environ["PICOIN_VALIDATOR_POLL_SECONDS"])
            if "PICOIN_VALIDATOR_POLL_SECONDS" in os.environ
            else None
        ),
        help="Seconds between validation job polls while the heartbeat remains fresh",
    )
    validate_parser.add_argument(
        "--heartbeat-interval",
        type=float,
        default=float(os.getenv("PICOIN_VALIDATOR_HEARTBEAT_INTERVAL_SECONDS", "30")),
        help="Seconds between signed validator heartbeat refreshes",
    )
    validate_parser.add_argument("--node-server", default=DEFAULT_NODE_SERVER, help="Local Picoin node API used for signed validator liveness")
    validate_parser.add_argument(
        "--node-timeout",
        type=float,
        default=worker_http_timeout_seconds(default=10.0),
        help="Seconds to wait for the local node heartbeat probe",
    )
    validate_parser.add_argument("--submit-timeout", type=float, default=90.0, help="Seconds to wait while submitting a validation vote")
    validate_parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_VALIDATOR_WORKERS,
        help="Parallel workers for validating revealed sample digits",
    )
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
