#!/usr/bin/env python3
"""Verify Phase 6 finality certificates across full-node candidates."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from app.core.crypto import canonical_json, sha256_text
    from app.core.signatures import verify_payload_signature
except Exception as exc:  # pragma: no cover - only used when run outside the repo venv.
    raise SystemExit(f"Could not import Picoin verification helpers from {REPO_ROOT}: {exc}") from exc


def split_endpoints(raw: str) -> list[str]:
    return [part.strip().rstrip("/") for part in raw.replace("\n", ",").split(",") if part.strip()]


def fetch_json(base: str, path: str, timeout: float) -> tuple[bool, Any, str | None, float]:
    started = time.time()
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=timeout) as response:
            raw = response.read().decode("utf-8")
        elapsed = round(time.time() - started, 3)
        return True, json.loads(raw) if raw.strip() else None, None, elapsed
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
    check: dict[str, Any] = {
        "name": name,
        "ok": bool(ok),
        "detail": detail,
        "severity": severity,
    }
    if endpoint:
        check["endpoint"] = endpoint
    checks.append(check)


def latest_block_height(blocks: Any) -> int:
    if not isinstance(blocks, list) or not blocks:
        return 0
    heights = []
    for block in blocks:
        if isinstance(block, dict):
            try:
                heights.append(int(block.get("height") or 0))
            except (TypeError, ValueError):
                pass
    return max(heights or [0])


def certificate_hash(certificate: dict[str, Any]) -> str:
    return sha256_text(
        canonical_json(
            {
                "payload": certificate.get("payload") or {},
                "votes": certificate.get("votes") or [],
            }
        )
    )


def collect_endpoint(endpoint: str, timeout: float, target_height: int | None) -> dict[str, Any]:
    ok_sync, sync, sync_error, sync_time = fetch_json(endpoint, "/node/sync-status", timeout)
    ok_blocks, blocks, blocks_error, blocks_time = fetch_json(endpoint, "/blocks?limit=1", timeout)
    height = int(target_height or latest_block_height(blocks))
    certificate = None
    certificate_error = None
    certificate_time = 0.0
    if height > 0:
        ok_cert, certificate, certificate_error, certificate_time = fetch_json(
            endpoint,
            f"/blocks/{height}/finality",
            timeout,
        )
        if not ok_cert:
            certificate = None
    return {
        "endpoint": endpoint,
        "sync": sync if ok_sync and isinstance(sync, dict) else None,
        "sync_error": sync_error,
        "blocks": blocks if ok_blocks and isinstance(blocks, list) else [],
        "blocks_error": blocks_error,
        "height": height,
        "certificate": certificate if isinstance(certificate, dict) else None,
        "certificate_error": certificate_error,
        "timings": {
            "sync": sync_time,
            "blocks": blocks_time,
            "certificate": certificate_time,
        },
    }


def validate_certificate(view: dict[str, Any], checks: list[dict[str, Any]]) -> dict[str, Any] | None:
    endpoint = view["endpoint"]
    height = int(view.get("height") or 0)
    certificate = view.get("certificate")
    if height <= 0:
        add_check(checks, "endpoint_has_block", False, "latest local block height is 0", endpoint=endpoint)
        return None
    if not certificate:
        add_check(
            checks,
            "certificate_exists",
            False,
            f"height={height} error={view.get('certificate_error')}",
            endpoint=endpoint,
        )
        return None

    payload = certificate.get("payload") or {}
    block_payload = payload.get("block") or {}
    validation_payload = payload.get("validation") or {}
    votes = certificate.get("votes") or []
    block_hash = str(certificate.get("block_hash") or "")
    computed_hash = certificate_hash(certificate)

    add_check(
        checks,
        "certificate_height_matches",
        int(certificate.get("block_height") or 0) == height and int(block_payload.get("height") or 0) == height,
        f"height={height} certificate={certificate.get('block_height')} payload={block_payload.get('height')}",
        endpoint=endpoint,
    )
    add_check(
        checks,
        "certificate_hash_matches_payload",
        computed_hash == certificate.get("certificate_hash"),
        f"computed={computed_hash} certificate={certificate.get('certificate_hash')}",
        endpoint=endpoint,
    )
    add_check(
        checks,
        "certificate_block_hash_matches_payload",
        block_hash == block_payload.get("block_hash"),
        f"block_hash={block_hash} payload={block_payload.get('block_hash')}",
        endpoint=endpoint,
    )

    required = int(certificate.get("required_approvals") or 0)
    approval_count = int(certificate.get("approval_count") or 0)
    add_check(
        checks,
        "certificate_has_required_votes",
        approval_count >= required > 0 and len(votes) >= required,
        f"approval_count={approval_count} required={required} votes={len(votes)}",
        endpoint=endpoint,
    )
    add_check(
        checks,
        "certificate_validation_payload_matches",
        validation_payload.get("job_id") == certificate.get("job_id")
        and int(validation_payload.get("required_approvals") or 0) == required
        and int(validation_payload.get("approval_count") or 0) == approval_count,
        (
            f"job={validation_payload.get('job_id')} required={validation_payload.get('required_approvals')} "
            f"approval_count={validation_payload.get('approval_count')}"
        ),
        endpoint=endpoint,
    )

    for index, vote in enumerate(votes):
        vote_endpoint = f"{endpoint}#vote-{index}"
        signature_payload = vote.get("signature_payload") or {}
        public_key = vote.get("validator_public_key")
        signature = vote.get("signature")
        add_check(
            checks,
            "vote_payload_matches_certificate",
            bool(vote.get("approved"))
            and signature_payload.get("job_id") == certificate.get("job_id")
            and signature_payload.get("task_id") == certificate.get("task_id")
            and signature_payload.get("validator_id") == vote.get("validator_id"),
            f"validator={vote.get('validator_id')}",
            endpoint=vote_endpoint,
        )
        try:
            signature_ok = bool(public_key and signature and verify_payload_signature(public_key, signature_payload, signature))
        except Exception as exc:
            signature_ok = False
            add_check(
                checks,
                "vote_signature_check_error",
                False,
                f"validator={vote.get('validator_id')} error={exc}",
                endpoint=vote_endpoint,
            )
        add_check(
            checks,
            "vote_signature_valid",
            signature_ok,
            f"validator={vote.get('validator_id')}",
            endpoint=vote_endpoint,
        )

    return {
        "height": height,
        "block_hash": block_hash,
        "task_id": certificate.get("task_id"),
        "job_id": certificate.get("job_id"),
        "certificate_hash": certificate.get("certificate_hash"),
        "required_approvals": required,
        "approval_count": approval_count,
        "vote_validators": [vote.get("validator_id") for vote in votes],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("endpoints", nargs="*", help="Full-node candidate base URLs")
    parser.add_argument(
        "--required",
        type=int,
        default=int(os.environ.get("PICOIN_PHASE6_REQUIRED_NODES", "3")),
        help="Minimum number of endpoints required",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=None,
        help="Block height to verify. Defaults to the lowest latest block height reported by candidates.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("PICOIN_PHASE6_TIMEOUT_SECONDS", "20")),
        help="HTTP timeout per request in seconds",
    )
    args = parser.parse_args()

    env_endpoints = split_endpoints(os.environ.get("PICOIN_PHASE6_NODES", ""))
    endpoints = [endpoint.rstrip("/") for endpoint in args.endpoints] or env_endpoints
    endpoints = list(dict.fromkeys(endpoint for endpoint in endpoints if endpoint))
    checks: list[dict[str, Any]] = []

    if len(endpoints) < args.required:
        add_check(checks, "required_endpoint_count", False, f"found={len(endpoints)} required={args.required}")
        print(json.dumps({"status": "fail", "checks": checks}, indent=2, sort_keys=True))
        return 1

    first_pass = [collect_endpoint(endpoint, args.timeout, args.height) for endpoint in endpoints]
    target_height = args.height
    if target_height is None:
        candidate_heights = [int(view.get("height") or 0) for view in first_pass if int(view.get("height") or 0) > 0]
        target_height = min(candidate_heights or [0])
    views = first_pass if args.height is not None else [
        collect_endpoint(endpoint, args.timeout, target_height) for endpoint in endpoints
    ]

    add_check(
        checks,
        "target_height_selected",
        int(target_height or 0) > 0,
        f"height={target_height}",
    )

    summaries = []
    for view in views:
        summary = validate_certificate(view, checks)
        if summary is not None:
            summaries.append(summary)

    if summaries:
        reference = summaries[0]
        for summary in summaries[1:]:
            add_check(
                checks,
                "certificate_matches_reference",
                summary["height"] == reference["height"]
                and summary["block_hash"] == reference["block_hash"]
                and summary["certificate_hash"] == reference["certificate_hash"],
                (
                    f"height={summary['height']} block={summary['block_hash']} "
                    f"cert={summary['certificate_hash']} reference={reference['certificate_hash']}"
                ),
            )

    errors = [check for check in checks if not check["ok"] and check["severity"] == "error"]
    report = {
        "status": "ok" if not errors else "fail",
        "target_height": target_height,
        "nodes_checked": len(views),
        "certificates": summaries,
        "checks": checks,
        "summary": {
            "errors": len(errors),
            "checks": len(checks),
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
