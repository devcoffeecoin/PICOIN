#!/usr/bin/env python3
"""Verify Phase 12 task-state gossip across full-node candidates.

The drill is read-mostly. It mutates node state only when --reconcile is passed,
by asking each node to reconcile from the other supplied peers.
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


def replay_payload(sync: dict[str, Any] | None) -> dict[str, Any]:
    sync = sync or {}
    replay = sync.get("replay")
    return replay if isinstance(replay, dict) else {}


def task_from_inventory(inventory: dict[str, Any] | None, task_id: str) -> dict[str, Any] | None:
    if not isinstance(inventory, dict):
        return None
    rows = inventory.get("tasks") if isinstance(inventory.get("tasks"), list) else []
    for item in rows:
        if not isinstance(item, dict):
            continue
        task = item.get("task") if isinstance(item.get("task"), dict) else item
        if str(task.get("task_id") or "") == task_id:
            return item
    return None


def collect_node(node: dict[str, str], timeout: float, inventory_limit: int, task_id: str | None) -> dict[str, Any]:
    base = node["url"]
    result: dict[str, Any] = {"name": node["name"], "url": base, "timings": {}, "errors": {}}
    endpoints = {
        "health": "/health",
        "sync": "/node/sync-status",
        "validators_status": "/validators/status",
        "task_inventory": f"/tasks/inventory?limit={int(inventory_limit)}",
        "validation_job_inventory": f"/validation/jobs/inventory?status=pending&limit={int(inventory_limit)}",
        "validation_vote_inventory": f"/validation/votes/inventory?limit={int(inventory_limit)}",
    }
    if task_id:
        endpoints["task_status"] = f"/tasks/{urllib.parse.quote(task_id, safe='')}/status"
    for key, path in endpoints.items():
        ok, payload, error, elapsed = fetch_json(base, path, timeout)
        result["timings"][key] = elapsed
        if ok and isinstance(payload, (dict, list)):
            result[key] = payload
        else:
            result[key] = None
            result["errors"][key] = error or "unknown error"
    return result


def summarize_node(view: dict[str, Any], task_id: str | None) -> dict[str, Any]:
    health = view.get("health") if isinstance(view.get("health"), dict) else {}
    sync = view.get("sync") if isinstance(view.get("sync"), dict) else {}
    validators = view.get("validators_status") if isinstance(view.get("validators_status"), dict) else {}
    task_inventory = view.get("task_inventory") if isinstance(view.get("task_inventory"), dict) else {}
    job_inventory = view.get("validation_job_inventory") if isinstance(view.get("validation_job_inventory"), dict) else {}
    vote_inventory = view.get("validation_vote_inventory") if isinstance(view.get("validation_vote_inventory"), dict) else {}
    replay = replay_payload(sync)
    task_status = view.get("task_status") if isinstance(view.get("task_status"), dict) else None
    status_validation = task_status.get("validation") if isinstance(task_status, dict) and isinstance(task_status.get("validation"), dict) else {}
    status_block = task_status.get("block") if isinstance(task_status, dict) and isinstance(task_status.get("block"), dict) else {}
    task_envelope = task_from_inventory(task_inventory, task_id) if task_id else None
    task = task_envelope.get("task") if isinstance(task_envelope, dict) and isinstance(task_envelope.get("task"), dict) else None
    commitment = task_envelope.get("commitment") if isinstance(task_envelope, dict) and isinstance(task_envelope.get("commitment"), dict) else None
    snapshot = task_envelope.get("snapshot") if isinstance(task_envelope, dict) and isinstance(task_envelope.get("snapshot"), dict) else None
    return {
        "name": view["name"],
        "url": view["url"],
        "health_status": health.get("status"),
        "height": int(sync.get("effective_latest_block_height") or health.get("latest_block_height") or 0),
        "hash": sync.get("effective_latest_block_hash") or health.get("latest_block_hash"),
        "replay_status": replay.get("sync_status"),
        "divergent": replay.get("divergence_detected"),
        "last_error": replay.get("last_error"),
        "mining_ready": health.get("mining_ready"),
        "required_validator_approvals": validators.get("required_validator_approvals"),
        "eligible_validators": validators.get("eligible_validators"),
        "task_inventory_count": int(task_inventory.get("count") or 0),
        "validation_job_inventory_count": int(job_inventory.get("count") or 0),
        "validation_vote_inventory_count": int(vote_inventory.get("count") or 0),
        "task_status": task_status,
        "task_inventory_match": {
            "found": task is not None,
            "status": task.get("status") if task else None,
            "range_start": task.get("range_start") if task else None,
            "range_end": task.get("range_end") if task else None,
            "assignment_seed": task.get("assignment_seed") if task else None,
            "assignment_mode": task.get("assignment_mode") if task else None,
            "has_commitment": commitment is not None,
            "has_snapshot": snapshot is not None,
            "status_endpoint_status": task_status.get("status") if isinstance(task_status, dict) else None,
            "block_height": status_block.get("height"),
            "block_hash": status_block.get("block_hash"),
            "validation_job_id": status_validation.get("job_id"),
            "validation_status": status_validation.get("status"),
            "validation_approvals": status_validation.get("approvals"),
            "validation_required_approvals": status_validation.get("required_approvals"),
        },
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
            item: dict[str, Any] = {
                "target": target["name"],
                "target_url": target["url"],
                "peer": peer["name"],
                "peer_url": peer["url"],
                "ok": ok,
                "seconds": elapsed,
                "error": error,
            }
            if isinstance(payload, dict):
                nested = payload.get("results") if isinstance(payload.get("results"), list) else []
                item.update(
                    {
                        "tasks_imported": int(payload.get("tasks_imported") or sum(int((row or {}).get("tasks_imported") or 0) for row in nested)),
                        "validation_jobs_imported": int(payload.get("validation_jobs_imported") or sum(int((row or {}).get("validation_jobs_imported") or 0) for row in nested)),
                        "validation_votes_imported": int(payload.get("validation_votes_imported") or sum(int((row or {}).get("validation_votes_imported") or 0) for row in nested)),
                        "blocks_imported": int(payload.get("blocks_imported") or 0),
                        "errors": int(payload.get("errors") or sum(len((row or {}).get("errors") or []) for row in nested)),
                    }
                )
            results.append(item)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("nodes", nargs="+", help="Node URL or label=url, for example A=http://1.2.3.4:8000")
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout seconds")
    parser.add_argument("--inventory-limit", type=int, default=100, help="Task/job/vote inventory limit")
    parser.add_argument("--allowed-lag", type=int, default=5, help="Maximum height lag between tested nodes")
    parser.add_argument("--required", type=int, default=3, help="Required visible eligible validator quorum")
    parser.add_argument("--task-id", help="Task id that must converge across all nodes")
    parser.add_argument("--expect-task-status", action="append", default=[], help="Allowed status for --task-id")
    parser.add_argument("--reconcile", action="store_true", help="Run pairwise /node/reconcile before final checks")
    parser.add_argument("--reconcile-limit", type=int, default=64, help="Reconcile limit used with --reconcile")
    parser.add_argument("--post-reconcile-sleep", type=float, default=2.0, help="Seconds to wait after reconcile")
    args = parser.parse_args()

    started = time.time()
    nodes = [parse_node(raw, index + 1) for index, raw in enumerate(args.nodes)]
    checks: list[dict[str, Any]] = []
    reconcile_results: list[dict[str, Any]] = []

    add_check(checks, "node_count", len(nodes) >= 2, f"nodes={len(nodes)}")
    if args.reconcile:
        reconcile_results = reconcile_nodes(nodes, args.timeout, args.reconcile_limit)
        for item in reconcile_results:
            add_check(
                checks,
                "reconcile_pair",
                bool(item["ok"]),
                f"{item['target']}<-{item['peer']} seconds={item['seconds']} tasks={item.get('tasks_imported')} jobs={item.get('validation_jobs_imported')} votes={item.get('validation_votes_imported')} errors={item.get('errors')}",
                node=item["target"],
            )
        if args.post_reconcile_sleep > 0:
            time.sleep(args.post_reconcile_sleep)

    views = [collect_node(node, args.timeout, args.inventory_limit, args.task_id) for node in nodes]
    summaries = [summarize_node(view, args.task_id) for view in views]

    heights = [item["height"] for item in summaries]
    hashes_by_height: dict[int, set[str]] = {}
    for item in summaries:
        hashes_by_height.setdefault(int(item["height"]), set()).add(str(item.get("hash") or ""))
    max_height = max(heights or [0])
    min_height = min(heights or [0])
    add_check(checks, "height_lag_within_limit", max_height - min_height <= int(args.allowed_lag), f"min={min_height} max={max_height} lag={max_height - min_height}")
    if max_height == min_height and max_height > 0:
        hashes = hashes_by_height.get(max_height, set())
        add_check(checks, "tip_hash_converged", len(hashes) == 1, f"height={max_height} hashes={sorted(hashes)}")

    for item in summaries:
        node_name = str(item["name"])
        add_check(checks, "health_reachable", "health" not in item["errors"], str(item["errors"].get("health")), node=node_name)
        add_check(checks, "task_inventory_reachable", "task_inventory" not in item["errors"], str(item["errors"].get("task_inventory")), node=node_name)
        add_check(checks, "replay_healthy", item["replay_status"] == "healthy", f"replay={item['replay_status']} divergent={item['divergent']} last_error={item['last_error']}", node=node_name)
        add_check(checks, "validator_quorum_visible", int(item.get("eligible_validators") or 0) >= int(args.required), f"eligible={item.get('eligible_validators')} required={args.required}", node=node_name)

    if args.task_id:
        expected_statuses = {str(value) for value in args.expect_task_status if str(value)}
        signatures: dict[str, set[str]] = {}
        for item in summaries:
            match = item["task_inventory_match"]
            status = str(match.get("status") or "")
            signature = "|".join(
                str(match.get(key) or "")
                for key in (
                    "status",
                    "range_start",
                    "range_end",
                    "assignment_seed",
                    "assignment_mode",
                    "status_endpoint_status",
                    "block_height",
                    "block_hash",
                    "validation_job_id",
                    "validation_status",
                    "validation_approvals",
                    "validation_required_approvals",
                )
            )
            signatures.setdefault(signature, set()).add(str(item["name"]))
            add_check(checks, "task_visible_in_inventory", bool(match.get("found")), f"task_id={args.task_id}", node=str(item["name"]))
            add_check(checks, "task_has_commitment", bool(match.get("has_commitment")), f"task_id={args.task_id}", node=str(item["name"]), severity="warning")
            add_check(checks, "task_has_snapshot", bool(match.get("has_snapshot")), f"task_id={args.task_id}", node=str(item["name"]), severity="warning")
            if expected_statuses:
                add_check(checks, "task_status_expected", status in expected_statuses, f"status={status} expected={sorted(expected_statuses)}", node=str(item["name"]))
            if status == "accepted" or str(match.get("status_endpoint_status") or "") == "accepted":
                add_check(
                    checks,
                    "accepted_task_validation_approved",
                    str(match.get("validation_status") or "") == "approved",
                    f"validation_status={match.get('validation_status')} approvals={match.get('validation_approvals')}/{match.get('validation_required_approvals')}",
                    node=str(item["name"]),
                )
        non_empty_signatures = {key: value for key, value in signatures.items() if key.strip("|")}
        add_check(checks, "task_state_converged", len(non_empty_signatures) == 1, f"signatures={non_empty_signatures}")

    errors = sum(1 for check in checks if not check["ok"] and check["severity"] == "error")
    status = "ok" if errors == 0 else "fail"
    output = {
        "status": status,
        "duration_seconds": round(time.time() - started, 3),
        "nodes_checked": len(nodes),
        "task_id": args.task_id,
        "reconcile": reconcile_results,
        "summaries": summaries,
        "checks": checks,
        "summary": {
            "checks": len(checks),
            "errors": errors,
        },
    }
    json.dump(output, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
