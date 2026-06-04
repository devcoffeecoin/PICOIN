#!/usr/bin/env bash
set -euo pipefail

PICOIN_ENV_FILE="${PICOIN_ENV_FILE:-/etc/picoin/picoin.env}"
if [ -f "$PICOIN_ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$PICOIN_ENV_FILE"
  set +a
fi

PICOIN_HOME="${PICOIN_HOME:-/opt/picoin/picoin-proof-of-pi}"
PICOIN_SERVER="${PICOIN_SERVER:-http://127.0.0.1:8000}"
PICOIN_PYTHON="${PICOIN_PYTHON:-$PICOIN_HOME/.venv/bin/python}"
PICOIN_PHASE1_ALLOWED_LAG_BLOCKS="${PICOIN_PHASE1_ALLOWED_LAG_BLOCKS:-0}"
PICOIN_BOOTSTRAP_PEER="${PICOIN_BOOTSTRAP_PEER:-}"
if [ -z "$PICOIN_BOOTSTRAP_PEER" ] && [ -n "${PICOIN_BOOTSTRAP_PEERS:-}" ]; then
  PICOIN_BOOTSTRAP_PEER="${PICOIN_BOOTSTRAP_PEERS%%,*}"
fi

if [ -z "${PICOIN_BOOTSTRAP_PEER:-}" ]; then
  echo "PICOIN_BOOTSTRAP_PEER or PICOIN_BOOTSTRAP_PEERS is required" >&2
  exit 2
fi

if [ ! -x "$PICOIN_PYTHON" ] && [ "$PICOIN_PYTHON" = "$PICOIN_HOME/.venv/bin/python" ]; then
  PICOIN_PYTHON="python3"
fi

"$PICOIN_PYTHON" - "$PICOIN_SERVER" "$PICOIN_BOOTSTRAP_PEER" "$PICOIN_PHASE1_ALLOWED_LAG_BLOCKS" <<'PY'
import json
import os
import sys
import urllib.error
import urllib.request

server = sys.argv[1].rstrip("/")
peer = sys.argv[2].rstrip("/")
allowed_lag = int(sys.argv[3])


def fetch_json(base: str, path: str, timeout: int = 30):
    url = f"{base}{path}"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    if raw.strip() == "":
        return None
    return json.loads(raw)


def effective_height(sync: dict) -> int:
    return int(sync.get("effective_latest_block_height") or sync.get("latest_block_height") or 0)


def effective_hash(sync: dict) -> str | None:
    return sync.get("effective_latest_block_hash") or sync.get("latest_block_hash")


def checkpoint_payload(checkpoint: dict | None) -> dict:
    return dict((checkpoint or {}).get("payload") or {})


def checkpoint_view(checkpoint: dict | None, sync: dict) -> dict | None:
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
    checkpoint_payload_value = snapshot_payload.get("checkpoint") or {}
    if not isinstance(checkpoint_payload_value, dict):
        checkpoint_payload_value = {}
    checkpoint_payload_value = dict(checkpoint_payload_value)
    if not checkpoint_payload_value:
        for key in (
            "block_hash",
            "state_root",
            "balances_hash",
            "validators_hash",
            "pending_rewards_hash",
            "protocol_params_hash",
            "retarget_events_hash",
            "snapshot_hash",
        ):
            value = active_snapshot.get(key)
            if value is not None:
                checkpoint_payload_value[key] = value
    height = checkpoint_payload_value.get("height") or active_snapshot.get("height")
    if not height:
        return None
    return {
        "height": height,
        "payload": checkpoint_payload_value,
        "_source": "active_snapshot_base",
    }


checks: list[dict] = []
payloads: dict[str, object] = {}


def add_check(name: str, ok: bool, detail: str, severity: str = "error") -> None:
    checks.append({"name": name, "ok": bool(ok), "detail": detail, "severity": severity})


try:
    local_health = fetch_json(server, "/health")
    local_sync = fetch_json(server, "/node/sync-status")
    local_audit = fetch_json(server, "/audit/full", timeout=60)
    local_checkpoint = fetch_json(server, "/node/checkpoints/latest")
    peer_sync = fetch_json(peer, "/node/sync-status")
    peer_checkpoint = fetch_json(peer, "/node/checkpoints/latest")
except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
    print(json.dumps({"status": "error", "server": server, "peer": peer, "error": str(exc)}, indent=2))
    raise SystemExit(1)

payloads.update(
    {
        "local_health": local_health,
        "local_sync": local_sync,
        "local_audit": local_audit,
        "local_checkpoint": local_checkpoint,
        "peer_sync": peer_sync,
        "peer_checkpoint": peer_checkpoint,
    }
)

local_height = effective_height(local_sync)
peer_height = effective_height(peer_sync)
local_hash = effective_hash(local_sync)
peer_hash = effective_hash(peer_sync)
lag = max(0, peer_height - local_height)

local_health_issues = list(local_health.get("issues") or [])
expected_read_only_issues = {
    "not enough eligible validators for quorum",
}
health_ok = local_health.get("status") == "ok" or (
    local_health.get("status") == "degraded"
    and local_sync.get("node_id")
    and set(local_health_issues).issubset(expected_read_only_issues)
)
add_check(
    "local_health_ok",
    health_ok,
    f"status={local_health.get('status')} issues={local_health_issues}",
)
add_check("local_sync_healthy", (local_sync.get("replay") or {}).get("sync_status") == "healthy", f"replay={(local_sync.get('replay') or {}).get('sync_status')}")
add_check("local_not_divergent", (local_sync.get("replay") or {}).get("divergence_detected") is False, f"divergence={(local_sync.get('replay') or {}).get('divergence_reason')}")
add_check("local_audit_valid", local_audit.get("valid") is True, f"issues={local_audit.get('issues')}")

expected_env = {
    "network_id": os.environ.get("PICOIN_NETWORK", "").strip(),
    "chain_id": os.environ.get("PICOIN_CHAIN_ID", "").strip(),
    "genesis_hash": os.environ.get("PICOIN_GENESIS_HASH", "").strip(),
    "protocol_version": os.environ.get("PICOIN_PROTOCOL_VERSION", "").strip(),
}
for key, expected in expected_env.items():
    if expected:
        add_check(
            f"local_{key}_matches_env",
            str(local_sync.get(key)) == expected,
            f"local={local_sync.get(key)} env={expected}",
        )

for key in ("network_id", "chain_id", "genesis_hash", "protocol_version"):
    add_check(f"{key}_match", local_sync.get(key) == peer_sync.get(key), f"local={local_sync.get(key)} peer={peer_sync.get(key)}")

add_check("height_lag_within_limit", lag <= allowed_lag, f"local={local_height} peer={peer_height} lag={lag} allowed={allowed_lag}")
if lag == 0:
    add_check("tip_hash_match", local_hash == peer_hash, f"local={local_hash} peer={peer_hash}")
else:
    add_check("tip_hash_match", True, f"skipped while lag={lag}", "warning")

local_checkpoint_view = checkpoint_view(local_checkpoint, local_sync)
peer_checkpoint_view = checkpoint_view(peer_checkpoint, peer_sync)
local_cp_payload = checkpoint_payload(local_checkpoint_view)
peer_cp_payload = checkpoint_payload(peer_checkpoint_view)
if (
    local_checkpoint_view
    and peer_checkpoint_view
    and int(local_checkpoint_view.get("height") or 0) == int(peer_checkpoint_view.get("height") or -1)
):
    add_check(
        "checkpoint_height_match",
        True,
        (
            f"height={local_checkpoint_view.get('height')} "
            f"local_source={local_checkpoint_view.get('_source')} "
            f"peer_source={peer_checkpoint_view.get('_source')}"
        ),
    )
    for key in (
        "block_hash",
        "state_root",
        "balances_hash",
        "validators_hash",
        "pending_rewards_hash",
        "protocol_params_hash",
        "retarget_events_hash",
        "snapshot_hash",
    ):
        add_check(
            f"checkpoint_{key}_match",
            local_cp_payload.get(key) == peer_cp_payload.get(key),
            f"local={local_cp_payload.get(key)} peer={peer_cp_payload.get(key)}",
        )
elif not local_checkpoint_view and not peer_checkpoint_view and local_height == peer_height:
    add_check("checkpoint_height_match", True, f"both nodes have no checkpoint at height={local_height}")
else:
    add_check(
        "checkpoint_height_match",
        False,
        (
            f"local={None if not local_checkpoint_view else local_checkpoint_view.get('height')} "
            f"peer={None if not peer_checkpoint_view else peer_checkpoint_view.get('height')}"
        ),
        "warning",
    )

errors = [check for check in checks if not check["ok"] and check["severity"] == "error"]
warnings = [check for check in checks if not check["ok"] and check["severity"] == "warning"]
report = {
    "status": "fail" if errors else "warn" if warnings else "ok",
    "server": server,
    "peer": peer,
    "local_height": local_height,
    "peer_height": peer_height,
    "lag": lag,
    "local_hash": local_hash,
    "peer_hash": peer_hash,
    "checks": checks,
    "summary": {"errors": len(errors), "warnings": len(warnings), "checked": len(checks)},
}
print(json.dumps(report, indent=2, sort_keys=True))
raise SystemExit(1 if errors else 0)
PY
