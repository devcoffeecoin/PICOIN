#!/usr/bin/env python3
"""Compare Phase 2 public bootstrap candidates using read-only API calls."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any


CHECKPOINT_KEYS = (
    "block_hash",
    "state_root",
    "balances_hash",
    "validators_hash",
    "pending_rewards_hash",
    "protocol_params_hash",
    "retarget_events_hash",
    "snapshot_hash",
)

EXPECTED_READ_ONLY_ISSUES = {
    "not enough eligible validators for quorum",
}

CORE_PATHS = {
    "health": "/health",
    "protocol": "/protocol",
    "sync": "/node/sync-status",
    "audit": "/audit/full",
    "checkpoint": "/node/checkpoints/latest",
}

READ_ONLY_PATHS = {
    "stats": "/stats",
    "blocks": "/blocks?limit=1",
    "mempool_status": "/mempool/status",
    "validators_status": "/validators/status",
    "miners_status": "/miners/status",
}


def split_endpoints(raw: str) -> list[str]:
    return [part.strip().rstrip("/") for part in raw.replace("\n", ",").split(",") if part.strip()]


def effective_height(sync: dict[str, Any]) -> int:
    return int(sync.get("effective_latest_block_height") or sync.get("latest_block_height") or 0)


def effective_hash(sync: dict[str, Any]) -> str | None:
    return sync.get("effective_latest_block_hash") or sync.get("latest_block_hash")


def checkpoint_payload(checkpoint: dict[str, Any] | None) -> dict[str, Any]:
    return dict((checkpoint or {}).get("payload") or {})


def checkpoint_view(checkpoint: dict[str, Any] | None, sync: dict[str, Any]) -> dict[str, Any] | None:
    if checkpoint:
        view = dict(checkpoint)
        view["_source"] = "canonical_checkpoint"
        return view

    active_snapshot = sync.get("active_snapshot_base") or {}
    if not isinstance(active_snapshot, dict):
        return None
    snapshot_payload = active_snapshot.get("payload") or {}
    if not isinstance(snapshot_payload, dict):
        snapshot_payload = {}
    checkpoint_value = snapshot_payload.get("checkpoint") or {}
    if not isinstance(checkpoint_value, dict):
        checkpoint_value = {}
    checkpoint_value = dict(checkpoint_value)
    if not checkpoint_value:
        for key in CHECKPOINT_KEYS:
            value = active_snapshot.get(key)
            if value is not None:
                checkpoint_value[key] = value
    height = checkpoint_value.get("height") or active_snapshot.get("height")
    if not height:
        return None
    return {
        "height": height,
        "payload": checkpoint_value,
        "_source": "active_snapshot_base",
    }


def fetch_json(base: str, path: str, timeout: float) -> tuple[bool, Any, str | None, float]:
    started = time.time()
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=timeout) as response:
            raw = response.read().decode("utf-8")
        elapsed = round(time.time() - started, 3)
        if not raw.strip():
            return True, None, None, elapsed
        return True, json.loads(raw), None, elapsed
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        elapsed = round(time.time() - started, 3)
        return False, None, str(exc), elapsed


def add_check(
    checks: list[dict[str, Any]],
    name: str,
    ok: bool,
    detail: str,
    *,
    endpoint: str | None = None,
    severity: str = "error",
) -> None:
    item: dict[str, Any] = {
        "name": name,
        "ok": bool(ok),
        "detail": detail,
        "severity": severity,
    }
    if endpoint:
        item["endpoint"] = endpoint
    checks.append(item)


def collect_endpoint(endpoint: str, timeout: float, check_read_only: bool) -> dict[str, Any]:
    paths = dict(CORE_PATHS)
    if check_read_only:
        paths.update(READ_ONLY_PATHS)

    responses: dict[str, Any] = {}
    timings: dict[str, float] = {}
    errors: dict[str, str] = {}
    for name, path in paths.items():
        ok, payload, error, elapsed = fetch_json(endpoint, path, timeout)
        timings[name] = elapsed
        if ok:
            responses[name] = payload
        else:
            errors[name] = error or "unknown error"

    sync = responses.get("sync") or {}
    checkpoint = responses.get("checkpoint")
    checkpoint_data = checkpoint if isinstance(checkpoint, dict) else None
    sync_data = sync if isinstance(sync, dict) else {}
    return {
        "endpoint": endpoint,
        "responses": responses,
        "timings": timings,
        "errors": errors,
        "height": effective_height(sync_data),
        "hash": effective_hash(sync_data),
        "checkpoint_view": checkpoint_view(checkpoint_data, sync_data),
    }


def health_is_ok(health: dict[str, Any]) -> bool:
    issues = set(health.get("issues") or [])
    return health.get("status") == "ok" or (
        health.get("status") == "degraded" and issues.issubset(EXPECTED_READ_ONLY_ISSUES)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("endpoints", nargs="*", help="Bootstrap candidate base URLs")
    parser.add_argument(
        "--allowed-lag",
        type=int,
        default=int(os.environ.get("PICOIN_PHASE2_ALLOWED_LAG_BLOCKS", "0")),
        help="Allowed lag in blocks from the highest candidate height",
    )
    parser.add_argument(
        "--required",
        type=int,
        default=int(os.environ.get("PICOIN_PHASE2_REQUIRED_BOOTSTRAPS", "2")),
        help="Minimum number of endpoints required",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("PICOIN_PHASE2_TIMEOUT_SECONDS", "30")),
        help="HTTP timeout per request in seconds",
    )
    parser.add_argument(
        "--strict-checkpoint",
        action="store_true",
        default=os.environ.get("PICOIN_PHASE2_STRICT_CHECKPOINT", "0") == "1",
        help="Treat checkpoint mismatches as errors instead of warnings",
    )
    parser.add_argument(
        "--skip-read-only",
        action="store_true",
        help="Skip stats/blocks/mempool/validators/miners read-only endpoint checks",
    )
    args = parser.parse_args()

    env_endpoints = split_endpoints(os.environ.get("PICOIN_PHASE2_BOOTSTRAPS", ""))
    endpoints = [endpoint.rstrip("/") for endpoint in args.endpoints] or env_endpoints
    endpoints = list(dict.fromkeys(endpoint for endpoint in endpoints if endpoint))

    checks: list[dict[str, Any]] = []
    if len(endpoints) < args.required:
        add_check(
            checks,
            "required_bootstrap_count",
            False,
            f"found={len(endpoints)} required={args.required}",
        )
        report = {
            "status": "fail",
            "endpoints": endpoints,
            "checks": checks,
            "summary": {"errors": 1, "warnings": 0, "checked": len(checks)},
        }
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1

    add_check(checks, "required_bootstrap_count", True, f"found={len(endpoints)} required={args.required}")

    endpoint_reports = [
        collect_endpoint(endpoint, args.timeout, not args.skip_read_only) for endpoint in endpoints
    ]

    for item in endpoint_reports:
        endpoint = item["endpoint"]
        errors = item["errors"]
        for name in CORE_PATHS:
            add_check(
                checks,
                f"{name}_responds",
                name not in errors,
                errors.get(name, "ok"),
                endpoint=endpoint,
            )
        if not args.skip_read_only:
            for name in READ_ONLY_PATHS:
                add_check(
                    checks,
                    f"{name}_responds",
                    name not in errors,
                    errors.get(name, "ok"),
                    endpoint=endpoint,
                )

        health = item["responses"].get("health") or {}
        sync = item["responses"].get("sync") or {}
        audit = item["responses"].get("audit") or {}
        if isinstance(health, dict):
            add_check(
                checks,
                "health_ok_or_read_only_degraded",
                health_is_ok(health),
                f"status={health.get('status')} issues={health.get('issues')}",
                endpoint=endpoint,
            )
        if isinstance(sync, dict):
            replay = sync.get("replay") or {}
            add_check(
                checks,
                "sync_healthy",
                replay.get("sync_status") == "healthy",
                f"replay={replay.get('sync_status')}",
                endpoint=endpoint,
            )
            add_check(
                checks,
                "not_divergent",
                replay.get("divergence_detected") is False,
                f"reason={replay.get('divergence_reason')}",
                endpoint=endpoint,
            )
        if isinstance(audit, dict):
            add_check(
                checks,
                "audit_valid",
                audit.get("valid") is True,
                f"issues={audit.get('issues')}",
                endpoint=endpoint,
            )

    healthy_reports = [item for item in endpoint_reports if not item["errors"].get("sync")]
    if healthy_reports:
        reference = healthy_reports[0]
        reference_sync = reference["responses"].get("sync") or {}
        max_height = max(item["height"] for item in healthy_reports)

        for item in healthy_reports:
            sync = item["responses"].get("sync") or {}
            endpoint = item["endpoint"]
            for key in ("network_id", "chain_id", "genesis_hash", "protocol_version"):
                add_check(
                    checks,
                    f"{key}_matches_reference",
                    sync.get(key) == reference_sync.get(key),
                    f"value={sync.get(key)} reference={reference_sync.get(key)}",
                    endpoint=endpoint,
                )

            lag = max_height - item["height"]
            add_check(
                checks,
                "height_lag_within_limit",
                lag <= args.allowed_lag,
                f"height={item['height']} max_height={max_height} lag={lag} allowed={args.allowed_lag}",
                endpoint=endpoint,
            )

        if all(max_height - item["height"] == 0 for item in healthy_reports):
            reference_hash = reference["hash"]
            for item in healthy_reports:
                add_check(
                    checks,
                    "tip_hash_matches_reference",
                    item["hash"] == reference_hash,
                    f"hash={item['hash']} reference={reference_hash}",
                    endpoint=item["endpoint"],
                )
        else:
            for item in healthy_reports:
                add_check(
                    checks,
                    "tip_hash_matches_reference",
                    True,
                    "skipped while candidates have allowed height lag",
                    endpoint=item["endpoint"],
                    severity="warning",
                )

        checkpoint_reports = [item for item in healthy_reports if item["checkpoint_view"]]
        if len(checkpoint_reports) >= 2:
            reference_checkpoint = checkpoint_reports[0]["checkpoint_view"]
            reference_payload = checkpoint_payload(reference_checkpoint)
            for item in checkpoint_reports:
                view = item["checkpoint_view"]
                payload = checkpoint_payload(view)
                same_height = int(view.get("height") or 0) == int(reference_checkpoint.get("height") or -1)
                add_check(
                    checks,
                    "checkpoint_height_matches_reference",
                    same_height,
                    f"height={view.get('height')} reference={reference_checkpoint.get('height')}",
                    endpoint=item["endpoint"],
                    severity="error" if args.strict_checkpoint else "warning",
                )
                if same_height:
                    for key in CHECKPOINT_KEYS:
                        add_check(
                            checks,
                            f"checkpoint_{key}_matches_reference",
                            payload.get(key) == reference_payload.get(key),
                            f"value={payload.get(key)} reference={reference_payload.get(key)}",
                            endpoint=item["endpoint"],
                            severity="error" if args.strict_checkpoint else "warning",
                        )
        else:
            add_check(
                checks,
                "checkpoint_comparison_available",
                False,
                f"checkpoint_views={len(checkpoint_reports)}",
                severity="warning",
            )

    errors = [check for check in checks if not check["ok"] and check["severity"] == "error"]
    warnings = [check for check in checks if not check["ok"] and check["severity"] == "warning"]
    report = {
        "status": "fail" if errors else "warn" if warnings else "ok",
        "allowed_lag": args.allowed_lag,
        "strict_checkpoint": args.strict_checkpoint,
        "endpoints": [
            {
                "endpoint": item["endpoint"],
                "height": item["height"],
                "hash": item["hash"],
                "errors": item["errors"],
                "timings": item["timings"],
            }
            for item in endpoint_reports
        ],
        "checks": checks,
        "summary": {"errors": len(errors), "warnings": len(warnings), "checked": len(checks)},
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
