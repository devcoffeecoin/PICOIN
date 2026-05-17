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
PICOIN_DATA_DIR="${PICOIN_DATA_DIR:-$PICOIN_HOME/data}"
PICOIN_DB_PATH="${PICOIN_DB_PATH:-$PICOIN_DATA_DIR/picoin.sqlite3}"
PICOIN_SMOKE_DIR="${PICOIN_SMOKE_DIR:-$PICOIN_DATA_DIR/testnet/smoke}"
PICOIN_RECONCILE_DIR="${PICOIN_RECONCILE_DIR:-$PICOIN_DATA_DIR/testnet/reconcile}"
PICOIN_BACKUP_DIR="${PICOIN_BACKUP_DIR:-$PICOIN_HOME/backups}"
PICOIN_PUBLIC_API_URL="${PICOIN_PUBLIC_API_URL:-}"

PICOIN_BOOTSTRAP_PEER="${PICOIN_BOOTSTRAP_PEER:-}"
if [ -z "$PICOIN_BOOTSTRAP_PEER" ] && [ -n "${PICOIN_BOOTSTRAP_PEERS:-}" ]; then
  PICOIN_BOOTSTRAP_PEER="${PICOIN_BOOTSTRAP_PEERS%%,*}"
fi

if [ ! -x "$PICOIN_PYTHON" ] && [ "$PICOIN_PYTHON" = "$PICOIN_HOME/.venv/bin/python" ]; then
  PICOIN_PYTHON="python3"
fi

errors=()
warnings=()

check() {
  local name="$1"
  shift
  if "$@"; then
    printf '[ok] %s\n' "$name"
  else
    printf '[fail] %s\n' "$name"
    errors+=("$name")
  fi
}

warn_check() {
  local name="$1"
  shift
  if "$@"; then
    printf '[ok] %s\n' "$name"
  else
    printf '[warn] %s\n' "$name"
    warnings+=("$name")
  fi
}

service_ok() {
  local service="$1"
  local state
  state="$(systemctl is-active "$service" 2>/dev/null || true)"
  [ "$state" = "active" ] || [ "$state" = "activating" ]
}

json_field_ok() {
  local file="$1"
  local expr="$2"
  "$PICOIN_PYTHON" - "$file" "$expr" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expr = sys.argv[2]
value = payload
for part in expr.split("."):
    if part == "":
        continue
    value = value.get(part) if isinstance(value, dict) else None
if value in (True, "ok", 0):
    raise SystemExit(0)
raise SystemExit(1)
PY
}

curl_quiet() {
  curl -fsS "$1" >/dev/null
}

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

echo "Picoin public testnet service check"
echo "home=$PICOIN_HOME"
echo "server=$PICOIN_SERVER"
echo "peer=${PICOIN_BOOTSTRAP_PEER:-none}"
echo "data=$PICOIN_DATA_DIR"
echo "db=$PICOIN_DB_PATH"

check "picoin-node service" service_ok picoin-node.service
check "picoin-auditor service" service_ok picoin-auditor.service
check "picoin-reconciler service" service_ok picoin-reconciler.service
check "picoin-validator service" service_ok picoin-validator.service
check "picoin-miner service" service_ok picoin-miner.service

check "database file exists" test -s "$PICOIN_DB_PATH"
check "data dir writable by current user" test -w "$PICOIN_DATA_DIR"
warn_check "smoke dir exists" test -d "$PICOIN_SMOKE_DIR"
warn_check "reconcile dir exists" test -d "$PICOIN_RECONCILE_DIR"
warn_check "backup dir exists" test -d "$PICOIN_BACKUP_DIR"
warn_check "recent smoke report exists" test -s "$PICOIN_SMOKE_DIR/report.json"
warn_check "recent backup log exists" test -s "$PICOIN_BACKUP_DIR/latest.log"

cd "$PICOIN_HOME"

"$PICOIN_PYTHON" -m picoin node sync-status --server "$PICOIN_SERVER" >"$tmp_dir/sync.json"
"$PICOIN_PYTHON" -m picoin node audit --server "$PICOIN_SERVER" >"$tmp_dir/audit.json"

report_args=(node report --server "$PICOIN_SERVER")
if [ -n "$PICOIN_BOOTSTRAP_PEER" ]; then
  report_args+=(--peer "$PICOIN_BOOTSTRAP_PEER")
fi
"$PICOIN_PYTHON" -m picoin "${report_args[@]}" >"$tmp_dir/report.json"

check "sync pending replay is zero" json_field_ok "$tmp_dir/sync.json" "pending_replay_blocks"
check "audit is valid" json_field_ok "$tmp_dir/audit.json" "valid"
check "report is ok" json_field_ok "$tmp_dir/report.json" "status"

if [ -n "$PICOIN_PUBLIC_API_URL" ]; then
  warn_check "public api /health reachable" curl_quiet "$PICOIN_PUBLIC_API_URL/health"
fi

"$PICOIN_PYTHON" - "$tmp_dir/sync.json" "$tmp_dir/report.json" "${#errors[@]}" "${#warnings[@]}" <<'PY'
import json
import sys
from pathlib import Path

sync = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
report = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
errors = int(sys.argv[3])
warnings = int(sys.argv[4])
print(
    json.dumps(
        {
            "status": "ok" if errors == 0 else "fail",
            "errors": errors,
            "warnings": warnings,
            "height": report.get("height") or sync.get("latest_block_height"),
            "block_hash": report.get("block_hash") or sync.get("latest_block_hash"),
            "pending_replay_blocks": sync.get("pending_replay_blocks"),
            "peer_count": sync.get("peer_counts", {}),
        },
        indent=2,
    )
)
PY

if [ "${#errors[@]}" -ne 0 ]; then
  printf 'PICOIN_SERVICE_CHECK_STATUS=fail\n'
  exit 1
fi

printf 'PICOIN_SERVICE_CHECK_STATUS=ok\n'
