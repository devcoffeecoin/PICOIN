#!/usr/bin/env python3
"""Verify Phase 9/10 full-node readiness and validator heartbeat gossip.

This script is intentionally read-mostly. It only mutates node state when
--reconcile is passed, by asking each node to reconcile from its peers.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def parse_node(raw: str, index: int) -> dict[str, str]:
    value = raw.strip().rstrip("/")
    if not value:
        raise ValueError("empty node endpoint")
    if "=" in value and not value.lower().startswith(("http://", "https://")):
        label, url = value.split("=", 1)
        return {"name": label.strip() or f"node-{index}", "url": url.strip().rstrip("/")}
    return {"name": chr(ord("A") + index - 1), "url": value}


def request_json(method: str, url: str, timeout: float) -> tuple[bool, Any, str | None, float]:
    started = time.time()
    try:
        data = b"" if method == "POST" else None
        request = urllib.request.Request(url, data=data, method=method)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
        elapsed = round(time.time() - started, 3)
        return True, json.loads(raw) if raw.strip() else {}, None, elapsed
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        elapsed = round(time.time() - started, 3)
        return False, None, f"HTTP {exc.code}: {body}", elapsed
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        elapsed = round(time.time() - started, 3)
        return False, None, str(exc), elapsed


def fetch_json(base: str, path: str, timeout: float) -> tuple[bool, Any, str | None, float]:
    return request_json("GET", f"{base.rstrip('/')}{path}", timeout)


def post_json(base: str, path: str, timeout: float) -> tuple[bool, Any, str | None, float]:
    return request_json("POST", f"{base.rstrip('/')}{path}", timeout)


def add_check(
    checks: list[dict[str, Any]],
    name: str,
    ok: bool,
    detail: str,
    *,
    node: str | None = None,
    severity: str = "error",
) -> None:
    check: dict[str, Any] = {
        "name": name,
        "ok": bool(ok),
        "detail": detail,
        "severity": severity,
    }
    if node:
        check["node"] = node
    checks.append(check)


def identity_tuple(protocol: dict[str, Any] | None, sync: dict[str, Any] | None) -> tuple[Any, Any, Any, Any]:
    protocol = protocol or {}
    sync = sync or {}
    return (
        protocol.get("network_id") or sync.get("network_id"),
        protocol.get("chain_id") or sync.get("chain_id"),
        protocol.get("genesis_hash") or sync.get("genesis_hash"),
        protocol.get("protocol_version") or sync.get("protocol_version"),
    )


def replay_payload(sync: dict[str, Any] | None) -> dict[str, Any]:
    sync = sync or {}
    replay = sync.get("replay")
    return replay if isinstance(replay, dict) else {}


def readiness_payload(health: dict[str, Any] | None, sync: dict[str, Any] | None) -> dict[str, Any]:
    health = health or {}
    sync = sync or {}
    readiness = health.get("readiness")
    if isinstance(readiness, dict):
        return readiness
    readiness = sync.get("readiness")
    return readiness if isinstance(readiness, dict) else {}


def validator_id_from_heartbeat(item: dict[str, Any]) -> str:
    value = item.get("validator_id")
    if value:
        return str(value)
    heartbeat = item.get("heartbeat")
    if isinstance(heartbeat, dict):
        return str(heartbeat.get("validator_id") or "")
    return ""


def collect_node(node: dict[str, str], timeout: float, inventory_limit: int) -> dict[str, Any]:
    base = node["url"]
    results: dict[str, Any] = {"name": node["name"], "url": base, "timings": {}, "errors": {}}
    endpoints = {
        "health": "/health",
        "protocol": "/protocol",
        "sync": "/node/sync-status",
        "validators_status": "/validators/status",
        "heartbeat_inventory": f"/validators/heartbeat/inventory?limit={int(inventory_limit)}",
    }
    for key, path in endpoints.items():
        ok, payload, error, elapsed = fetch_json(base, path, timeout)
        results["timings"][key] = elapsed
        if ok and isinstance(payload, (dict, list)):
            results[key] = payload
        else:
            results[key] = None
            results["errors"][key] = error or "unknown error"
    return results


def summarize_node(view: dict[str, Any]) -> dict[str, Any]:
    health = view.get("health") if isinstance(view.get("health"), dict) else {}
    protocol = view.get("protocol") if isinstance(view.get("protocol"), dict) else {}
    sync = view.get("sync") if isinstance(view.get("sync"), dict) else {}
    validators_status = view.get("validators_status") if isinstance(view.get("validators_status"), dict) else {}
    inventory = view.get("heartbeat_inventory") if isinstance(view.get("heartbeat_inventory"), dict) else {}
    readiness = readiness_payload(health, sync)
    replay = replay_payload(sync)
    validators = validators_status.get("validators") if isinstance(validators_status.get("validators"), list) else []
    eligible = [
        str(validator.get("validator_id"))
        for validator in validators
        if validator.get("eligible") is True
    ]
    online = [
        str(validator.get("validator_id"))
        for validator in validators
        if validator.get("online_status") == "online"
    ]
    heartbeats = inventory.get("heartbeats") if isinstance(inventory.get("heartbeats"), list) else []
    heartbeat_ids = sorted({validator_id_from_heartbeat(item) for item in heartbeats if validator_id_from_heartbeat(item)})
    return {
        "name": view["name"],
        "url": view["url"],
        "identity": identity_tuple(protocol, sync),
        "health_status": health.get("status"),
        "height": int(sync.get("effective_latest_block_height") or health.get("latest_block_height") or 0),
        "hash": sync.get("effective_latest_block_hash") or health.get("latest_block_hash"),
        "replay_status": replay.get("sync_status"),
        "divergent": replay.get("divergence_detected"),
        "last_error": replay.get("last_error"),
        "node_role": readiness.get("node_role") or health.get("node_role") or protocol.get("node_role"),
        "readiness": {
            "read_ready": readiness.get("read_ready"),
            "tx_submit_ready": readiness.get("tx_submit_ready"),
            "task_assign_ready": readiness.get("task_assign_ready"),
            "validation_job_ready": readiness.get("validation_job_ready"),
            "block_finalize_ready": readiness.get("block_finalize_ready"),
            "mining_ready": readiness.get("mining_ready") or health.get("mining_ready"),
            "reasons": readiness.get("reasons") or {},
        },
        "required_validator_approvals": validators_status.get("required_validator_approvals"),
        "eligible_validators": validators_status.get("eligible_validators"),
        "validator_counts": validators_status.get("counts") or {},
        "eligible_validator_ids": sorted(eligible),
        "online_validator_ids": sorted(online),
        "heartbeat_inventory_count": int(inventory.get("count") or len(heartbeats)),
        "heartbeat_validator_ids": heartbeat_ids,
        "errors": view.get("errors") or {},
        "timings": view.get("timings") or {},
    }


def reconcile_nodes(nodes: list[dict[str, str]], timeout: float, limit: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for target in nodes:
        for peer in nodes:
            if target["url"] == peer["url"]:
                continue
            quoted = urllib.parse.quote(peer["url"], safe="")
            path = f"/node/reconcile?limit={int(limit)}&peer_address={quoted}"
            ok, payload, error, elapsed = post_json(target["url"], path, timeout)
            results.append(
                {
                    "target": target["name"],
                    "target_url": target["url"],
                    "peer": peer["name"],
                    "peer_url": peer["url"],
                    "ok": ok,
                    "seconds": elapsed,
                    "error": error,
                    "transactions_imported": (payload or {}).get("transactions_imported") if isinstance(payload, dict) else None,
                    "blocks_imported": (payload or {}).get("blocks_imported") if isinstance(payload, dict) else None,
                    "validator_heartbeats_imported": sum(
                        int((item or {}).get("validator_heartbeats_imported") or 0)
                        for item in ((payload or {}).get("results") or [])
                    )
                    if isinstance(payload, dict)
                    else None,
                }
            )
    return results


def split_expected_validators(values: list[str]) -> set[str]:
    expected: set[str] = set()
    for value in values:
        for part in value.replace("\n", ",").split(","):
            cleaned = part.strip()
            if cleaned:
                expected.add(cleaned)
    return expected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("nodes", nargs="+", help="Node URL or label=url, for example A=http://1.2.3.4:8000")
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout seconds")
    parser.add_argument("--inventory-limit", type=int, default=100, help="Heartbeat inventory limit per node")
    parser.add_argument("--allowed-lag", type=int, default=5, help="Maximum height lag between tested nodes")
    parser.add_argument("--required", type=int, default=3, help="Required visible eligible validator quorum")
    parser.add_argument("--expected-validator", action="append", default=[], help="Expected validator id; may be comma-separated")
    parser.add_argument("--reconcile", action="store_true", help="Run pairwise /node/reconcile before final checks")
    parser.add_argument("--reconcile-limit", type=int, default=16, help="Reconcile limit used with --reconcile")
    parser.add_argument("--post-reconcile-sleep", type=float, default=2.0, help="Seconds to wait after reconcile")
    parser.add_argument("--allow-read-only", action="store_true", help="Do not require write readiness fields to be true")
    parser.add_argument("--no-require-quorum", action="store_true", help="Do not fail when visible quorum is below --required")
    args = parser.parse_args()

    started = time.time()
    nodes = [parse_node(raw, index + 1) for index, raw in enumerate(args.nodes)]
    expected_validators = split_expected_validators(args.expected_validator)
    checks: list[dict[str, Any]] = []

    add_check(checks, "node_count", len(nodes) >= 2, f"nodes={len(nodes)}")
    reconcile_results: list[dict[str, Any]] = []
    if args.reconcile:
        reconcile_results = reconcile_nodes(nodes, args.timeout, args.reconcile_limit)
        for item in reconcile_results:
            add_check(
                checks,
                "reconcile_pair_ok",
                bool(item["ok"]),
                f"{item['target']}<-{item['peer']} imported_heartbeats={item.get('validator_heartbeats_imported')} error={item.get('error')}",
                node=item["target"],
            )
        if args.post_reconcile_sleep > 0:
            time.sleep(args.post_reconcile_sleep)

    views = [collect_node(node, args.timeout, args.inventory_limit) for node in nodes]
    summaries = [summarize_node(view) for view in views]

    for summary in summaries:
        node_name = summary["name"]
        errors = summary["errors"]
        for key in ("health", "protocol", "sync", "validators_status", "heartbeat_inventory"):
            add_check(
                checks,
                f"{key}_reachable",
                key not in errors,
                summary["url"] if key not in errors else str(errors.get(key)),
                node=node_name,
            )
        add_check(
            checks,
            "sync_healthy",
            summary["replay_status"] == "healthy",
            f"sync_status={summary['replay_status']} last_error={summary['last_error']}",
            node=node_name,
        )
        add_check(
            checks,
            "not_divergent",
            summary["divergent"] is False,
            f"divergent={summary['divergent']} last_error={summary['last_error']}",
            node=node_name,
        )
        readiness = summary["readiness"]
        for key in ("read_ready", "tx_submit_ready", "task_assign_ready", "validation_job_ready", "block_finalize_ready"):
            present = readiness.get(key) is not None
            add_check(checks, f"{key}_present", present, f"{key}={readiness.get(key)}", node=node_name)
        add_check(
            checks,
            "read_ready",
            readiness.get("read_ready") is True,
            f"read_ready={readiness.get('read_ready')} reasons={readiness.get('reasons')}",
            node=node_name,
        )
        if not args.allow_read_only:
            for key in ("task_assign_ready", "validation_job_ready", "block_finalize_ready"):
                add_check(
                    checks,
                    key,
                    readiness.get(key) is True,
                    f"{key}={readiness.get(key)} reasons={readiness.get('reasons', {}).get(key)}",
                    node=node_name,
                )
        if not args.no_require_quorum:
            visible_quorum = int(summary.get("eligible_validators") or 0)
            add_check(
                checks,
                "eligible_quorum_visible",
                visible_quorum >= int(args.required),
                f"eligible={visible_quorum} required={args.required}",
                node=node_name,
            )
            add_check(
                checks,
                "heartbeat_quorum_visible",
                len(summary["heartbeat_validator_ids"]) >= int(args.required),
                f"heartbeat_validators={len(summary['heartbeat_validator_ids'])} required={args.required}",
                node=node_name,
            )
        if expected_validators:
            eligible_ids = set(summary["eligible_validator_ids"])
            heartbeat_ids = set(summary["heartbeat_validator_ids"])
            add_check(
                checks,
                "expected_validators_eligible",
                expected_validators.issubset(eligible_ids),
                f"missing={sorted(expected_validators - eligible_ids)}",
                node=node_name,
            )
            add_check(
                checks,
                "expected_validators_in_heartbeat_inventory",
                expected_validators.issubset(heartbeat_ids),
                f"missing={sorted(expected_validators - heartbeat_ids)}",
                node=node_name,
            )

    identities = [summary["identity"] for summary in summaries if summary["identity"] != (None, None, None, None)]
    first_identity = identities[0] if identities else None
    add_check(
        checks,
        "identity_converged",
        bool(first_identity) and all(identity == first_identity for identity in identities) and len(identities) == len(summaries),
        f"identities={identities}",
    )

    heights = [int(summary["height"] or 0) for summary in summaries]
    height_lag = max(heights or [0]) - min(heights or [0])
    add_check(
        checks,
        "height_lag_within_limit",
        height_lag <= int(args.allowed_lag),
        f"heights={heights} lag={height_lag} allowed={args.allowed_lag}",
    )

    eligible_sets = [set(summary["eligible_validator_ids"]) for summary in summaries]
    heartbeat_sets = [set(summary["heartbeat_validator_ids"]) for summary in summaries]
    common_eligible = set.intersection(*eligible_sets) if eligible_sets else set()
    common_heartbeats = set.intersection(*heartbeat_sets) if heartbeat_sets else set()
    union_eligible = set.union(*eligible_sets) if eligible_sets else set()
    union_heartbeats = set.union(*heartbeat_sets) if heartbeat_sets else set()
    if expected_validators:
        expected = expected_validators
        eligible_converged = all(expected.issubset(item) for item in eligible_sets)
        heartbeat_converged = all(expected.issubset(item) for item in heartbeat_sets)
        eligible_detail = f"expected={sorted(expected)} common={sorted(common_eligible)}"
        heartbeat_detail = f"expected={sorted(expected)} common={sorted(common_heartbeats)}"
    else:
        eligible_converged = bool(eligible_sets) and all(item == eligible_sets[0] for item in eligible_sets)
        heartbeat_converged = bool(heartbeat_sets) and all(item == heartbeat_sets[0] for item in heartbeat_sets)
        eligible_detail = f"common={sorted(common_eligible)} union={sorted(union_eligible)}"
        heartbeat_detail = f"common={sorted(common_heartbeats)} union={sorted(union_heartbeats)}"
    add_check(checks, "eligible_validator_set_converged", eligible_converged, eligible_detail)
    add_check(checks, "heartbeat_inventory_converged", heartbeat_converged, heartbeat_detail)

    errors = [check for check in checks if check["severity"] == "error" and not check["ok"]]
    report = {
        "status": "ok" if not errors else "fail",
        "duration_seconds": round(time.time() - started, 3),
        "required": int(args.required),
        "allowed_lag": int(args.allowed_lag),
        "expected_validators": sorted(expected_validators),
        "reconcile_ran": bool(args.reconcile),
        "reconcile_results": reconcile_results,
        "nodes": summaries,
        "convergence": {
            "common_eligible_validator_ids": sorted(common_eligible),
            "union_eligible_validator_ids": sorted(union_eligible),
            "common_heartbeat_validator_ids": sorted(common_heartbeats),
            "union_heartbeat_validator_ids": sorted(union_heartbeats),
            "height_lag": height_lag,
        },
        "checks": checks,
        "summary": {
            "checks": len(checks),
            "errors": len(errors),
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
