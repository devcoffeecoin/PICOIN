#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_env_file(path: str | None) -> None:
    if not path:
        return
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def fetch_json(base_url: str, path: str, timeout: float) -> Any:
    url = f"{base_url.rstrip('/')}{path}"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else None


def post_json(base_url: str, path: str, payload: dict[str, Any], timeout: float) -> Any:
    url = f"{base_url.rstrip('/')}{path}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else None


def http_error_detail(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        body = exc.read().decode("utf-8", errors="replace")
        return f"HTTP {exc.code}: {body}"
    return str(exc)


def check(checks: list[dict[str, Any]], name: str, ok: bool, detail: str, *, severity: str = "error") -> None:
    checks.append({"name": name, "ok": bool(ok), "detail": detail, "severity": severity})


def try_fetch(
    checks: list[dict[str, Any]],
    base_url: str,
    path: str,
    timeout: float,
    *,
    name: str,
) -> Any | None:
    try:
        payload = fetch_json(base_url, path, timeout)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        check(checks, name, False, f"{base_url}{path}: {http_error_detail(exc)}")
        return None
    check(checks, name, True, f"{base_url}{path}")
    return payload


def try_fetch_optional(base_url: str, path: str, timeout: float) -> tuple[Any | None, str | None]:
    try:
        return fetch_json(base_url, path, timeout), None
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        return None, http_error_detail(exc)


def number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def identity_tuple(protocol: dict[str, Any] | None, sync: dict[str, Any] | None) -> tuple[Any, Any, Any, Any]:
    protocol = protocol or {}
    sync = sync or {}
    return (
        protocol.get("network_id") or sync.get("network_id"),
        str(protocol.get("chain_id") or sync.get("chain_id")),
        protocol.get("genesis_hash") or sync.get("genesis_hash"),
        protocol.get("protocol_version") or sync.get("protocol_version"),
    )


def normalize_tx(tx: dict[str, Any] | None) -> dict[str, Any]:
    tx = tx or {}
    return {
        "tx_hash": tx.get("tx_hash"),
        "status": tx.get("status"),
        "block_height": tx.get("block_height"),
        "sender": tx.get("sender"),
        "recipient": tx.get("recipient"),
        "amount": tx.get("amount"),
        "amount_units": tx.get("amount_units"),
        "fee": tx.get("fee"),
        "fee_units": tx.get("fee_units"),
        "nonce": tx.get("nonce"),
    }


def load_wallet_tools(checks: list[dict[str, Any]]) -> dict[str, Any] | None:
    try:
        from app.core.settings import CHAIN_ID, NETWORK_ID
        from app.services.wallet import address_from_public_key, address_matches_public_key, is_valid_address, sign_transaction
    except Exception as exc:
        check(checks, "wallet_signing_import", False, f"{type(exc).__name__}: {exc}")
        return None
    check(checks, "wallet_signing_import", True, f"network={NETWORK_ID} chain={CHAIN_ID}")
    return {
        "CHAIN_ID": CHAIN_ID,
        "NETWORK_ID": NETWORK_ID,
        "address_from_public_key": address_from_public_key,
        "address_matches_public_key": address_matches_public_key,
        "is_valid_address": is_valid_address,
        "sign_transaction": sign_transaction,
    }


def load_wallet(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def nonce_fingerprint(payload: dict[str, Any] | None) -> tuple[Any, Any, Any, Any]:
    payload = payload or {}
    return (
        integer(payload.get("confirmed_nonce")),
        integer(payload.get("pending_nonce")),
        integer(payload.get("next_nonce")),
        integer(payload.get("pending_count")),
    )


def tx_status_family(tx: dict[str, Any] | None) -> str:
    return str((tx or {}).get("status") or "missing")


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 8 exchange withdrawal write-path smoke test")
    parser.add_argument("--local", default="http://127.0.0.1:8000", help="Local exchange/full-node base URL")
    parser.add_argument("--reference", default="https://api.picoin.science", help="Reference bootstrap/API base URL")
    parser.add_argument("--wallet", type=Path, required=True, help="Hot wallet JSON used to sign the withdrawal")
    parser.add_argument("--to", help="Recipient PI address for the optional signed withdrawal")
    parser.add_argument("--amount", type=float, default=0.001, help="Withdrawal amount used for balance and submit checks")
    parser.add_argument("--fee", type=float, default=0.001, help="Transaction fee")
    parser.add_argument("--allowed-lag", type=int, default=5, help="Maximum allowed local height lag")
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout seconds")
    parser.add_argument("--wait-seconds", type=int, default=120, help="How long to poll after --submit")
    parser.add_argument("--poll-seconds", type=float, default=5.0, help="Polling interval after --submit")
    parser.add_argument("--env-file", default="/etc/picoin/picoin.env", help="Env file to load before signing imports")
    parser.add_argument("--submit", action="store_true", help="Submit the signed transaction to the local node")
    parser.add_argument("--require-reference-seen", action="store_true", help="Fail if the reference API does not see the tx")
    parser.add_argument("--require-confirmed", action="store_true", help="Fail unless local and reference both confirm the tx")
    args = parser.parse_args()

    started = time.time()
    checks: list[dict[str, Any]] = []
    local = args.local.rstrip("/")
    reference = args.reference.rstrip("/")
    wallet_path = args.wallet

    load_env_file(args.env_file)
    tools = load_wallet_tools(checks)

    local_health = try_fetch(checks, local, "/health", args.timeout, name="local_health_reachable")
    local_protocol = try_fetch(checks, local, "/protocol", args.timeout, name="local_protocol_reachable")
    local_sync = try_fetch(checks, local, "/node/sync-status", args.timeout, name="local_sync_reachable")
    reference_protocol = try_fetch(checks, reference, "/protocol", args.timeout, name="reference_protocol_reachable")
    reference_sync = try_fetch(checks, reference, "/node/sync-status", args.timeout, name="reference_sync_reachable")

    replay = (local_sync or {}).get("replay") or {}
    local_height = int((local_sync or {}).get("effective_latest_block_height") or 0)
    reference_height = int((reference_sync or {}).get("effective_latest_block_height") or 0)
    lag = max(0, reference_height - local_height)
    health_issues = list((local_health or {}).get("issues") or [])

    check(checks, "local_sync_healthy", replay.get("sync_status") == "healthy", f"sync_status={replay.get('sync_status')}")
    check(
        checks,
        "local_not_divergent",
        replay.get("divergence_detected") is False,
        f"divergent={replay.get('divergence_detected')} last_error={replay.get('last_error')}",
    )
    check(
        checks,
        "local_health_no_replay_failure",
        (local_health or {}).get("status") in {"ok", "degraded"}
        and not any("diverg" in str(issue).lower() for issue in health_issues),
        f"status={(local_health or {}).get('status')} issues={health_issues}",
    )
    check(
        checks,
        "identity_matches_reference",
        identity_tuple(local_protocol, local_sync) == identity_tuple(reference_protocol, reference_sync),
        f"local={identity_tuple(local_protocol, local_sync)} reference={identity_tuple(reference_protocol, reference_sync)}",
    )
    check(
        checks,
        "height_lag_within_limit",
        lag <= int(args.allowed_lag),
        f"local={local_height} reference={reference_height} lag={lag} allowed={args.allowed_lag}",
    )

    wallet: dict[str, Any] = {}
    wallet_address = None
    wallet_summary: dict[str, Any] = {
        "path": str(wallet_path),
        "loaded": False,
        "address": None,
        "has_public_key": False,
        "has_private_key": False,
        "network_id": None,
        "chain_id": None,
    }
    if not wallet_path.exists():
        check(checks, "wallet_file_exists", False, f"wallet not found: {wallet_path}")
    else:
        try:
            wallet = load_wallet(wallet_path)
            wallet_address = str(wallet.get("address") or "").strip().upper()
            wallet_summary.update(
                {
                    "loaded": True,
                    "address": wallet_address,
                    "has_public_key": bool(wallet.get("public_key")),
                    "has_private_key": bool(wallet.get("private_key")),
                    "network_id": wallet.get("network_id"),
                    "chain_id": wallet.get("chain_id"),
                }
            )
            check(checks, "wallet_file_exists", True, str(wallet_path))
        except (OSError, json.JSONDecodeError) as exc:
            check(checks, "wallet_file_valid_json", False, f"{type(exc).__name__}: {exc}")

    if tools and wallet:
        if not wallet_address and wallet.get("public_key"):
            wallet_address = tools["address_from_public_key"](wallet["public_key"])
            wallet_summary["address"] = wallet_address
        check(checks, "wallet_has_private_key", bool(wallet.get("private_key")), "private_key present" if wallet.get("private_key") else "missing private_key")
        check(checks, "wallet_has_public_key", bool(wallet.get("public_key")), "public_key present" if wallet.get("public_key") else "missing public_key")
        check(checks, "wallet_address_valid", tools["is_valid_address"](wallet_address), f"address={wallet_address}")
        check(
            checks,
            "wallet_address_matches_public_key",
            tools["address_matches_public_key"](wallet_address, wallet.get("public_key")),
            "address/public_key match",
        )
        check(
            checks,
            "wallet_network_matches_node",
            str(wallet.get("network_id") or tools["NETWORK_ID"]) == str(tools["NETWORK_ID"]),
            f"wallet={wallet.get('network_id')} node={tools['NETWORK_ID']}",
        )
        check(
            checks,
            "wallet_chain_matches_node",
            str(wallet.get("chain_id") or tools["CHAIN_ID"]) == str(tools["CHAIN_ID"]),
            f"wallet={wallet.get('chain_id')} node={tools['CHAIN_ID']}",
        )

    recipient_valid = False
    if args.to:
        recipient = str(args.to).strip().upper()
        recipient_valid = bool(tools and tools["is_valid_address"](recipient))
        check(checks, "recipient_address_valid", recipient_valid, f"recipient={recipient}")
    else:
        check(checks, "recipient_address_valid", not args.submit, "recipient omitted; submit disabled unless --to is provided", severity="warning" if not args.submit else "error")

    check(checks, "amount_positive", args.amount > 0, f"amount={args.amount}")
    check(checks, "fee_non_negative", args.fee >= 0, f"fee={args.fee}")

    local_balance = None
    reference_balance = None
    local_nonce = None
    reference_nonce = None
    if wallet_address:
        quoted_address = urllib.parse.quote(wallet_address)
        local_account = try_fetch(checks, local, f"/accounts/{quoted_address}", args.timeout, name="local_wallet_balance_reachable")
        reference_account = try_fetch(checks, reference, f"/accounts/{quoted_address}", args.timeout, name="reference_wallet_balance_reachable")
        local_balance = number((local_account or {}).get("balance"))
        reference_balance = number((reference_account or {}).get("balance"))
        check(
            checks,
            "wallet_balance_matches_reference",
            local_balance == reference_balance,
            f"local={local_balance} reference={reference_balance}",
        )
        required_balance = float(args.amount) + float(args.fee)
        check(
            checks,
            "wallet_has_sufficient_local_balance",
            local_balance is not None and local_balance >= required_balance,
            f"local_balance={local_balance} required={required_balance}",
        )
        local_nonce = try_fetch(checks, local, f"/wallet/{quoted_address}/nonce", args.timeout, name="local_wallet_nonce_reachable")
        reference_nonce = try_fetch(checks, reference, f"/wallet/{quoted_address}/nonce", args.timeout, name="reference_wallet_nonce_reachable")
        check(
            checks,
            "wallet_nonce_matches_reference",
            nonce_fingerprint(local_nonce) == nonce_fingerprint(reference_nonce),
            f"local={nonce_fingerprint(local_nonce)} reference={nonce_fingerprint(reference_nonce)}",
        )

    tx_hash = None
    signed_tx: dict[str, Any] | None = None
    submitted: dict[str, Any] | None = None
    local_tx: dict[str, Any] | None = None
    reference_tx: dict[str, Any] | None = None
    poll_trace: list[dict[str, Any]] = []
    if args.submit:
        ready_to_submit = (
            tools is not None
            and wallet
            and wallet_address
            and wallet.get("private_key")
            and wallet.get("public_key")
            and recipient_valid
            and local_nonce is not None
            and nonce_fingerprint(local_nonce) == nonce_fingerprint(reference_nonce)
            and local_balance is not None
            and local_balance >= float(args.amount) + float(args.fee)
        )
        check(checks, "submit_preconditions", bool(ready_to_submit), "all submit preconditions passed")
        if ready_to_submit:
            nonce = int((local_nonce or {}).get("next_nonce"))
            signed_tx = tools["sign_transaction"](
                private_key=wallet["private_key"],
                public_key=wallet["public_key"],
                tx_type="transfer",
                sender=wallet_address,
                recipient=str(args.to).strip().upper(),
                amount=float(args.amount),
                nonce=nonce,
                fee=float(args.fee),
            )
            tx_hash = signed_tx["tx_hash"]
            try:
                submitted = post_json(local, "/transactions/submit", signed_tx, args.timeout)
                check(checks, "local_transaction_submit", True, f"tx_hash={tx_hash}")
                check(
                    checks,
                    "submitted_tx_hash_matches_signed",
                    (submitted or {}).get("tx_hash") == tx_hash,
                    f"signed={tx_hash} submitted={(submitted or {}).get('tx_hash')}",
                )
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
                check(checks, "local_transaction_submit", False, http_error_detail(exc))

        if tx_hash:
            deadline = time.time() + max(0, int(args.wait_seconds))
            while True:
                local_tx, local_error = try_fetch_optional(local, f"/tx/{urllib.parse.quote(tx_hash)}", args.timeout)
                reference_tx, reference_error = try_fetch_optional(reference, f"/tx/{urllib.parse.quote(tx_hash)}", args.timeout)
                poll_trace.append(
                    {
                        "elapsed_seconds": round(time.time() - started, 3),
                        "local_status": tx_status_family(local_tx),
                        "reference_status": tx_status_family(reference_tx),
                        "local_error": local_error,
                        "reference_error": reference_error,
                    }
                )
                if local_tx and reference_tx:
                    if not args.require_confirmed:
                        break
                    if tx_status_family(local_tx) == "confirmed" and tx_status_family(reference_tx) == "confirmed":
                        break
                if time.time() >= deadline:
                    break
                time.sleep(max(0.1, float(args.poll_seconds)))

            check(checks, "local_transaction_visible", local_tx is not None, f"tx={normalize_tx(local_tx)}")
            reference_seen = reference_tx is not None
            check(
                checks,
                "reference_transaction_visible",
                reference_seen or not args.require_reference_seen,
                f"tx={normalize_tx(reference_tx)}" if reference_seen else "reference did not return tx",
                severity="error" if args.require_reference_seen else "warning",
            )
            if local_tx and reference_tx:
                comparable_local = normalize_tx(local_tx)
                comparable_reference = normalize_tx(reference_tx)
                check(
                    checks,
                    "local_reference_tx_fields_match",
                    comparable_local == comparable_reference,
                    f"local={comparable_local} reference={comparable_reference}",
                )
            if args.require_confirmed:
                local_confirmed = tx_status_family(local_tx) == "confirmed"
                reference_confirmed = tx_status_family(reference_tx) == "confirmed"
                same_height = (local_tx or {}).get("block_height") == (reference_tx or {}).get("block_height")
                check(
                    checks,
                    "local_reference_tx_confirmed",
                    local_confirmed and reference_confirmed and same_height,
                    f"local={normalize_tx(local_tx)} reference={normalize_tx(reference_tx)}",
                )

            if wallet_address:
                quoted_address = urllib.parse.quote(wallet_address)
                local_nonce_after = try_fetch(checks, local, f"/wallet/{quoted_address}/nonce", args.timeout, name="local_wallet_nonce_after_submit")
                reference_nonce_after = try_fetch(
                    checks,
                    reference,
                    f"/wallet/{quoted_address}/nonce",
                    args.timeout,
                    name="reference_wallet_nonce_after_submit",
                )
                expected_next = int((local_nonce or {}).get("next_nonce") or 0) + 1
                check(
                    checks,
                    "local_nonce_advanced_after_submit",
                    integer((local_nonce_after or {}).get("next_nonce")) is not None
                    and int((local_nonce_after or {}).get("next_nonce")) >= expected_next,
                    f"before={nonce_fingerprint(local_nonce)} after={nonce_fingerprint(local_nonce_after)} expected_next_at_least={expected_next}",
                )
                if reference_seen:
                    check(
                        checks,
                        "reference_nonce_matches_after_submit",
                        nonce_fingerprint(local_nonce_after) == nonce_fingerprint(reference_nonce_after),
                        f"local={nonce_fingerprint(local_nonce_after)} reference={nonce_fingerprint(reference_nonce_after)}",
                    )
    else:
        check(checks, "submit_mode", True, "preflight only; pass --submit to send a signed withdrawal", severity="warning")

    error_count = sum(1 for item in checks if not item["ok"] and item.get("severity") == "error")
    report = {
        "status": "ok" if error_count == 0 else "fail",
        "mode": "submit" if args.submit else "preflight",
        "local": local,
        "reference": reference,
        "allowed_lag": int(args.allowed_lag),
        "lag": lag,
        "local_height": local_height,
        "reference_height": reference_height,
        "wallet": wallet_summary,
        "withdrawal": {
            "recipient": str(args.to).strip().upper() if args.to else None,
            "amount": float(args.amount),
            "fee": float(args.fee),
            "tx_hash": tx_hash,
            "submitted": normalize_tx(submitted),
            "local": normalize_tx(local_tx),
            "reference": normalize_tx(reference_tx),
            "poll_trace": poll_trace,
        },
        "duration_seconds": round(time.time() - started, 3),
        "summary": {"checks": len(checks), "errors": error_count},
        "checks": checks,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if error_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
