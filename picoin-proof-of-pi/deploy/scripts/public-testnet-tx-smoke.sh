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
PICOIN_SMOKE_DIR="${PICOIN_SMOKE_DIR:-$PICOIN_DATA_DIR/testnet/smoke}"
PICOIN_TX_SMOKE_WALLET="${PICOIN_TX_SMOKE_WALLET:-$PICOIN_DATA_DIR/wallets/alice.json}"
PICOIN_TX_SMOKE_RECIPIENT="${PICOIN_TX_SMOKE_RECIPIENT:-}"
PICOIN_TX_SMOKE_AMOUNT="${PICOIN_TX_SMOKE_AMOUNT:-0.001}"
PICOIN_TX_SMOKE_FEE="${PICOIN_TX_SMOKE_FEE:-0.0001}"
PICOIN_TX_SMOKE_TIMEOUT_SECONDS="${PICOIN_TX_SMOKE_TIMEOUT_SECONDS:-300}"
PICOIN_TX_SMOKE_POLL_SECONDS="${PICOIN_TX_SMOKE_POLL_SECONDS:-10}"

if [ ! -x "$PICOIN_PYTHON" ] && [ "$PICOIN_PYTHON" = "$PICOIN_HOME/.venv/bin/python" ]; then
  PICOIN_PYTHON="python3"
fi

if [ -z "$PICOIN_TX_SMOKE_RECIPIENT" ]; then
  echo "PICOIN_TX_SMOKE_RECIPIENT is required" >&2
  echo "example: PICOIN_TX_SMOKE_RECIPIENT=PI... $0" >&2
  exit 2
fi

if [ ! -f "$PICOIN_TX_SMOKE_WALLET" ]; then
  echo "wallet file not found: $PICOIN_TX_SMOKE_WALLET" >&2
  exit 2
fi

mkdir -p "$PICOIN_SMOKE_DIR"
cd "$PICOIN_HOME"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SUBMIT_JSON="$PICOIN_SMOKE_DIR/tx-smoke-submit-$STAMP.json"
STATUS_JSON="$PICOIN_SMOKE_DIR/tx-smoke-status-$STAMP.json"
SUMMARY_JSON="$PICOIN_SMOKE_DIR/tx-smoke-latest.json"

SENDER="$("$PICOIN_PYTHON" - "$PICOIN_TX_SMOKE_WALLET" <<'PY'
import json
import sys
from pathlib import Path

print(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["address"])
PY
)"

echo "Picoin tx smoke"
echo "server=$PICOIN_SERVER"
echo "sender=$SENDER"
echo "recipient=$PICOIN_TX_SMOKE_RECIPIENT"
echo "amount=$PICOIN_TX_SMOKE_AMOUNT"
echo "fee=$PICOIN_TX_SMOKE_FEE"

"$PICOIN_PYTHON" -m picoin wallet --server "$PICOIN_SERVER" balance --address "$SENDER" >"$PICOIN_SMOKE_DIR/tx-smoke-sender-before-$STAMP.json"
"$PICOIN_PYTHON" -m picoin wallet --server "$PICOIN_SERVER" nonce --address "$SENDER" >"$PICOIN_SMOKE_DIR/tx-smoke-nonce-before-$STAMP.json"

"$PICOIN_PYTHON" -m picoin tx --server "$PICOIN_SERVER" send \
  --wallet "$PICOIN_TX_SMOKE_WALLET" \
  --to "$PICOIN_TX_SMOKE_RECIPIENT" \
  --amount "$PICOIN_TX_SMOKE_AMOUNT" \
  --fee "$PICOIN_TX_SMOKE_FEE" >"$SUBMIT_JSON"

TX_HASH="$("$PICOIN_PYTHON" - "$SUBMIT_JSON" <<'PY'
import json
import sys
from pathlib import Path

print(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["tx_hash"])
PY
)"

echo "tx_hash=$TX_HASH"

deadline=$((SECONDS + PICOIN_TX_SMOKE_TIMEOUT_SECONDS))
status="pending"
while [ "$SECONDS" -le "$deadline" ]; do
  "$PICOIN_PYTHON" -m picoin tx --server "$PICOIN_SERVER" status --hash "$TX_HASH" >"$STATUS_JSON"
  status="$("$PICOIN_PYTHON" - "$STATUS_JSON" <<'PY'
import json
import sys
from pathlib import Path

print(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")).get("status", "unknown"))
PY
)"
  echo "status=$status"
  case "$status" in
    confirmed)
      "$PICOIN_PYTHON" -m picoin wallet --server "$PICOIN_SERVER" balance --address "$SENDER" >"$PICOIN_SMOKE_DIR/tx-smoke-sender-after-$STAMP.json"
      "$PICOIN_PYTHON" -m picoin wallet --server "$PICOIN_SERVER" balance --address "$PICOIN_TX_SMOKE_RECIPIENT" >"$PICOIN_SMOKE_DIR/tx-smoke-recipient-after-$STAMP.json"
      "$PICOIN_PYTHON" - "$TX_HASH" "$STATUS_JSON" "$SUMMARY_JSON" <<'PY'
import json
import sys
from pathlib import Path

tx_hash, status_path, summary_path = sys.argv[1:]
status = json.loads(Path(status_path).read_text(encoding="utf-8"))
Path(summary_path).write_text(
    json.dumps(
        {
            "status": "ok",
            "tx_hash": tx_hash,
            "tx_status": status.get("status"),
            "block_height": status.get("block_height"),
        },
        indent=2,
        sort_keys=True,
    ),
    encoding="utf-8",
)
PY
      cat "$SUMMARY_JSON"
      echo "PICOIN_TX_SMOKE_STATUS=ok"
      exit 0
      ;;
    rejected|expired)
      cat "$STATUS_JSON" >&2
      echo "PICOIN_TX_SMOKE_STATUS=fail"
      exit 1
      ;;
  esac
  sleep "$PICOIN_TX_SMOKE_POLL_SECONDS"
done

"$PICOIN_PYTHON" - "$TX_HASH" "$STATUS_JSON" "$SUMMARY_JSON" "$PICOIN_TX_SMOKE_TIMEOUT_SECONDS" <<'PY'
import json
import sys
from pathlib import Path

tx_hash, status_path, summary_path, timeout = sys.argv[1:]
status = json.loads(Path(status_path).read_text(encoding="utf-8"))
Path(summary_path).write_text(
    json.dumps(
        {
            "status": "timeout",
            "tx_hash": tx_hash,
            "tx_status": status.get("status"),
            "block_height": status.get("block_height"),
            "timeout_seconds": int(timeout),
        },
        indent=2,
        sort_keys=True,
    ),
    encoding="utf-8",
)
PY
cat "$SUMMARY_JSON" >&2
echo "PICOIN_TX_SMOKE_STATUS=fail"
exit 1
