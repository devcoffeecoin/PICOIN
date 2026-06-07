#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def fetch_json(base_url: str, path: str, timeout: float) -> Any:
    url = f"{base_url.rstrip('/')}{path}"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else None


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
        check(checks, name, False, f"{base_url}{path}: {exc}")
        return None
    check(checks, name, True, f"{base_url}{path}")
    return payload


def identity_tuple(protocol: dict[str, Any] | None, sync: dict[str, Any] | None) -> tuple[Any, Any, Any, Any]:
    protocol = protocol or {}
    sync = sync or {}
    return (
        protocol.get("network_id") or sync.get("network_id"),
        protocol.get("chain_id") or sync.get("chain_id"),
        protocol.get("genesis_hash") or sync.get("genesis_hash"),
        protocol.get("protocol_version") or sync.get("protocol_version"),
    )


def number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_tx(tx: dict[str, Any] | None) -> dict[str, Any]:
    tx = tx or {}
    return {
        "tx_hash": tx.get("tx_hash"),
        "status": tx.get("status"),
        "block_height": tx.get("block_height"),
        "sender": tx.get("sender"),
        "recipient": tx.get("recipient"),
        "amount": tx.get("amount"),
        "fee": tx.get("fee"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 7 exchange/full-node smoke test")
    parser.add_argument("--local", default="http://127.0.0.1:8000", help="Local full-node base URL")
    parser.add_argument("--reference", default="https://api.picoin.science", help="Reference bootstrap/API base URL")
    parser.add_argument("--allowed-lag", type=int, default=5, help="Maximum allowed local height lag")
    parser.add_argument("--account", action="append", default=[], help="Optional PI account to compare")
    parser.add_argument("--tx-hash", action="append", default=[], help="Optional transaction hash to compare")
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout seconds")
    args = parser.parse_args()

    started = time.time()
    checks: list[dict[str, Any]] = []
    local = args.local.rstrip("/")
    reference = args.reference.rstrip("/")

    local_health = try_fetch(checks, local, "/health", args.timeout, name="local_health_reachable")
    local_protocol = try_fetch(checks, local, "/protocol", args.timeout, name="local_protocol_reachable")
    local_sync = try_fetch(checks, local, "/node/sync-status", args.timeout, name="local_sync_reachable")
    reference_protocol = try_fetch(checks, reference, "/protocol", args.timeout, name="reference_protocol_reachable")
    reference_sync = try_fetch(checks, reference, "/node/sync-status", args.timeout, name="reference_sync_reachable")

    replay = (local_sync or {}).get("replay") or {}
    local_height = int((local_sync or {}).get("effective_latest_block_height") or 0)
    reference_height = int((reference_sync or {}).get("effective_latest_block_height") or 0)
    lag = max(0, reference_height - local_height)

    check(
        checks,
        "local_sync_healthy",
        replay.get("sync_status") == "healthy",
        f"sync_status={replay.get('sync_status')}",
    )
    check(
        checks,
        "local_not_divergent",
        replay.get("divergence_detected") is False,
        f"divergent={replay.get('divergence_detected')} last_error={replay.get('last_error')}",
    )
    health_issues = list((local_health or {}).get("issues") or [])
    health_ok = (local_health or {}).get("status") in {"ok", "degraded"} and not any(
        "diverg" in str(issue).lower() for issue in health_issues
    )
    check(
        checks,
        "local_health_no_replay_failure",
        health_ok,
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
    if local_height == reference_height:
        check(
            checks,
            "tip_hash_matches_reference",
            (local_sync or {}).get("effective_latest_block_hash") == (reference_sync or {}).get("effective_latest_block_hash"),
            f"local={(local_sync or {}).get('effective_latest_block_hash')} reference={(reference_sync or {}).get('effective_latest_block_hash')}",
        )
    else:
        check(
            checks,
            "tip_hash_matches_reference",
            True,
            f"skipped because heights differ: local={local_height} reference={reference_height}",
            severity="warning",
        )

    if local_height > 0:
        block = try_fetch(checks, local, f"/blocks/{local_height}", args.timeout, name="local_tip_block_readable")
        check(
            checks,
            "local_tip_block_height_matches_sync",
            int((block or {}).get("height") or 0) == local_height,
            f"block_height={(block or {}).get('height')} sync_height={local_height}",
        )

    account_results: dict[str, Any] = {}
    for account in args.account:
        path = f"/accounts/{urllib.parse.quote(account)}"
        local_account = try_fetch(checks, local, path, args.timeout, name=f"local_account_{account}")
        reference_account = try_fetch(checks, reference, path, args.timeout, name=f"reference_account_{account}")
        local_balance = number((local_account or {}).get("balance"))
        reference_balance = number((reference_account or {}).get("balance"))
        account_results[account] = {"local": local_account, "reference": reference_account}
        check(
            checks,
            f"account_balance_matches_{account}",
            local_balance == reference_balance and (local_account or {}).get("account_type") == (reference_account or {}).get("account_type"),
            f"local_balance={local_balance} reference_balance={reference_balance}",
        )

    tx_results: dict[str, Any] = {}
    for tx_hash in args.tx_hash:
        path = f"/tx/{urllib.parse.quote(tx_hash)}"
        local_tx = normalize_tx(try_fetch(checks, local, path, args.timeout, name=f"local_tx_{tx_hash}"))
        reference_tx = normalize_tx(try_fetch(checks, reference, path, args.timeout, name=f"reference_tx_{tx_hash}"))
        tx_results[tx_hash] = {"local": local_tx, "reference": reference_tx}
        check(
            checks,
            f"tx_matches_{tx_hash}",
            local_tx == reference_tx,
            f"local={local_tx} reference={reference_tx}",
        )

    error_count = sum(1 for item in checks if not item["ok"] and item.get("severity") == "error")
    report = {
        "status": "ok" if error_count == 0 else "fail",
        "local": local,
        "reference": reference,
        "allowed_lag": int(args.allowed_lag),
        "lag": lag,
        "local_height": local_height,
        "reference_height": reference_height,
        "duration_seconds": round(time.time() - started, 3),
        "summary": {"checks": len(checks), "errors": error_count},
        "checks": checks,
        "accounts": account_results,
        "transactions": tx_results,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if error_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
