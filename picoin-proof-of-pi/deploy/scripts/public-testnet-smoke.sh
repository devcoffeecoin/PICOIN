#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PICOIN_ENV_FILE="${PICOIN_ENV_FILE:-/etc/picoin/picoin.env}"
if [ -f "$PICOIN_ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$PICOIN_ENV_FILE"
  set +a
fi

PICOIN_HOME="${PICOIN_HOME:-$REPO_ROOT}"
PICOIN_SERVER="${PICOIN_SERVER:-http://127.0.0.1:8000}"
PICOIN_PYTHON="${PICOIN_PYTHON:-$PICOIN_HOME/.venv/bin/python}"
PICOIN_SMOKE_DIR="${PICOIN_SMOKE_DIR:-$PICOIN_HOME/data/testnet/smoke}"
PICOIN_SMOKE_TIMEOUT="${PICOIN_SMOKE_TIMEOUT:-60}"
PICOIN_SMOKE_SKIP_CATCH_UP="${PICOIN_SMOKE_SKIP_CATCH_UP:-0}"
PICOIN_SMOKE_WARN_ONLY="${PICOIN_SMOKE_WARN_ONLY:-0}"
export PICOIN_HTTP_TIMEOUT_SECONDS="$PICOIN_SMOKE_TIMEOUT"

if [ ! -x "$PICOIN_PYTHON" ] && [ "$PICOIN_PYTHON" = "$PICOIN_HOME/.venv/bin/python" ]; then
  PICOIN_PYTHON="python3"
fi

PICOIN_BOOTSTRAP_PEER="${PICOIN_BOOTSTRAP_PEER:-}"
if [ -z "$PICOIN_BOOTSTRAP_PEER" ] && [ -n "${PICOIN_BOOTSTRAP_PEERS:-}" ]; then
  PICOIN_BOOTSTRAP_PEER="${PICOIN_BOOTSTRAP_PEERS%%,*}"
fi

mkdir -p "$PICOIN_SMOKE_DIR"

CATCH_UP_JSON="$PICOIN_SMOKE_DIR/catch-up.json"
REPORT_JSON="$PICOIN_SMOKE_DIR/report.json"
AUDIT_JSON="$PICOIN_SMOKE_DIR/audit.json"

PEER_ARGS=()
if [ -n "$PICOIN_BOOTSTRAP_PEER" ]; then
  PEER_ARGS=(--peer "$PICOIN_BOOTSTRAP_PEER")
fi

cd "$PICOIN_HOME"

echo "Picoin public testnet smoke"
echo "server=$PICOIN_SERVER"
if [ -n "$PICOIN_BOOTSTRAP_PEER" ]; then
  echo "peer=$PICOIN_BOOTSTRAP_PEER"
else
  echo "peer=(none)"
fi

run_json() {
  local name="$1"
  local outfile="$2"
  shift 2

  echo "running $name..."
  if ! "$PICOIN_PYTHON" -m picoin "$@" >"$outfile"; then
    echo "PICOIN_SMOKE_STATUS=fail"
    echo "$name command failed; output follows:" >&2
    cat "$outfile" >&2 || true
    if [ "$PICOIN_SMOKE_WARN_ONLY" = "1" ]; then
      return 1
    fi
    exit 1
  fi
}

PICOIN_SMOKE_LOCAL_ONLY=0
if [ "$PICOIN_SMOKE_SKIP_CATCH_UP" = "1" ] || [ -z "$PICOIN_BOOTSTRAP_PEER" ]; then
  PICOIN_SMOKE_LOCAL_ONLY=1
  echo "skipping catch-up smoke check"
  printf '{"status":"skipped","audit_valid":true,"pending_replay_blocks":0,"peer_matches":true}\n' >"$CATCH_UP_JSON"
else
  run_json "catch-up" "$CATCH_UP_JSON" node catch-up --server "$PICOIN_SERVER" "${PEER_ARGS[@]}" || true
fi
if [ "$PICOIN_SMOKE_LOCAL_ONLY" = "1" ]; then
  run_json "sync-status" "$REPORT_JSON" node sync-status --server "$PICOIN_SERVER" || true
else
  run_json "report" "$REPORT_JSON" node report --server "$PICOIN_SERVER" "${PEER_ARGS[@]}" || true
fi
run_json "audit" "$AUDIT_JSON" node audit --server "$PICOIN_SERVER" || true

"$PICOIN_PYTHON" - "$CATCH_UP_JSON" "$REPORT_JSON" "$AUDIT_JSON" "$PICOIN_SMOKE_WARN_ONLY" <<'PY'
import json
import sys
from pathlib import Path

catch_up_path, report_path, audit_path = map(Path, sys.argv[1:4])
warn_only = sys.argv[4] == "1"

checks = []
errors = []


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"{path.name}: cannot parse JSON: {exc}")
        return {}


catch_up = load(catch_up_path)
report = load(report_path)
audit = load(audit_path)

checks.append(("catch_up_status", catch_up.get("status") in {"ok", "skipped"}, catch_up.get("status")))
checks.append(("catch_up_audit", catch_up.get("audit_valid") is True, catch_up.get("audit_valid")))
checks.append(
    (
        "catch_up_pending_replay",
        int(catch_up.get("pending_replay_blocks") or 0) == 0,
        catch_up.get("pending_replay_blocks"),
    )
)
if "peer_matches" in catch_up:
    checks.append(("catch_up_peer_match", catch_up.get("peer_matches") is True, catch_up.get("peer_matches")))

if "status" in report:
    checks.append(("report_status", report.get("status") in {"ok", "warn"}, report.get("status")))
else:
    checks.append(("sync_status_height", int(report.get("effective_latest_block_height", report.get("latest_block_height", 0)) or 0) >= 0, report.get("latest_block_height")))
checks.append(("audit_valid", audit.get("valid") is True, audit.get("valid")))
checks.append(("audit_issues", len(audit.get("issues") or []) == 0, len(audit.get("issues") or [])))

for name, ok, detail in checks:
    print(f"[{'ok' if ok else 'fail'}] {name}: {detail}")
    if not ok:
        errors.append(f"{name}={detail}")

if errors:
    status = "warn" if warn_only else "fail"
    print(json.dumps({"status": status, "errors": errors}, indent=2))
    if warn_only:
        raise SystemExit(0)
    raise SystemExit(1)

print(
    json.dumps(
        {
            "status": "ok",
            "height": report.get("height") or report.get("effective_latest_block_height") or report.get("latest_block_height") or catch_up.get("final_height"),
            "block_hash": report.get("block_hash") or report.get("effective_latest_block_hash") or report.get("latest_block_hash") or catch_up.get("final_block_hash"),
            "output_dir": str(catch_up_path.parent),
        },
        indent=2,
    )
)
PY

echo "PICOIN_SMOKE_STATUS=ok"
echo "outputs=$PICOIN_SMOKE_DIR"
