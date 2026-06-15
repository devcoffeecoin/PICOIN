#!/usr/bin/env bash
set -u

PICOIN_ENV_FILE="${PICOIN_ENV_FILE:-/etc/picoin/picoin.env}"
if [ -f "$PICOIN_ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$PICOIN_ENV_FILE"
  set +a
fi

PICOIN_HOME="${PICOIN_HOME:-/opt/picoin/picoin-proof-of-pi}"
PICOIN_PYTHON="${PICOIN_PYTHON:-$PICOIN_HOME/.venv/bin/python}"
PICOIN_SERVER="${PICOIN_SERVER:-http://127.0.0.1:8000}"
PICOIN_DATA_DIR="${PICOIN_DATA_DIR:-$PICOIN_HOME/data}"
PICOIN_RECONCILE_DIR="${PICOIN_RECONCILE_DIR:-$PICOIN_DATA_DIR/testnet/reconcile}"
PICOIN_RECONCILER_SLEEP_SECONDS="${PICOIN_RECONCILER_SLEEP_SECONDS:-30}"
PICOIN_RECONCILER_ROUNDS="${PICOIN_RECONCILER_ROUNDS:-2}"
PICOIN_RECONCILER_LIMIT="${PICOIN_RECONCILER_LIMIT:-32}"
PICOIN_RECONCILER_SKIP_WITHOUT_PEER="${PICOIN_RECONCILER_SKIP_WITHOUT_PEER:-1}"
PICOIN_RECONCILER_MODE="${PICOIN_RECONCILER_MODE:-catch-up}"
PICOIN_RECONCILE_PEERS="${PICOIN_RECONCILE_PEERS:-}"
export PICOIN_HTTP_TIMEOUT_SECONDS="${PICOIN_HTTP_TIMEOUT_SECONDS:-180}"

PICOIN_BOOTSTRAP_PEER="${PICOIN_BOOTSTRAP_PEER:-}"
if [ -z "$PICOIN_BOOTSTRAP_PEER" ] && [ -n "${PICOIN_BOOTSTRAP_PEERS:-}" ]; then
  PICOIN_BOOTSTRAP_PEER="${PICOIN_BOOTSTRAP_PEERS%%,*}"
fi
if [ -z "$PICOIN_RECONCILE_PEERS" ] && [ -n "${PICOIN_BOOTSTRAP_PEERS:-}" ]; then
  PICOIN_RECONCILE_PEERS="$PICOIN_BOOTSTRAP_PEERS"
elif [ -z "$PICOIN_RECONCILE_PEERS" ] && [ -n "$PICOIN_BOOTSTRAP_PEER" ]; then
  PICOIN_RECONCILE_PEERS="$PICOIN_BOOTSTRAP_PEER"
fi

if [ ! -x "$PICOIN_PYTHON" ] && [ "$PICOIN_PYTHON" = "$PICOIN_HOME/.venv/bin/python" ]; then
  PICOIN_PYTHON="python3"
fi

mkdir -p "$PICOIN_RECONCILE_DIR"
cd "$PICOIN_HOME" || exit 2

json_string() {
  "$PICOIN_PYTHON" -c 'import json, sys; print(json.dumps(sys.argv[1]))' "$1"
}

json_file() {
  "$PICOIN_PYTHON" -c '
import json
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text()
try:
    value = json.loads(text)
except Exception:
    value = {"raw": text}
print(json.dumps(value, separators=(",", ":")), end="")
' "$1"
}

run_catch_up() {
  local peer="$1"
  if [ -n "$peer" ]; then
    "$PICOIN_PYTHON" -m picoin node catch-up \
      --server "$PICOIN_SERVER" \
      --peer "$peer" \
      --max-rounds "$PICOIN_RECONCILER_ROUNDS" \
      --reconcile-limit "$PICOIN_RECONCILER_LIMIT"
  else
    "$PICOIN_PYTHON" -m picoin node catch-up \
      --server "$PICOIN_SERVER" \
      --max-rounds "$PICOIN_RECONCILER_ROUNDS" \
      --reconcile-limit "$PICOIN_RECONCILER_LIMIT"
  fi
}

run_reconcile() {
  local peer="$1"
  if [ -n "$peer" ]; then
    "$PICOIN_PYTHON" -m picoin node reconcile \
      --server "$PICOIN_SERVER" \
      --peer "$peer" \
      --limit "$PICOIN_RECONCILER_LIMIT"
  else
    "$PICOIN_PYTHON" -m picoin node reconcile \
      --server "$PICOIN_SERVER" \
      --limit "$PICOIN_RECONCILER_LIMIT"
  fi
}

run_heartbeat_sync() {
  local peer="$1"
  if [ -z "$peer" ]; then
    echo "heartbeat reconcile requires a peer" >&2
    return 2
  fi
  "$PICOIN_PYTHON" -m picoin node reconcile-validator-heartbeats \
    --server "$PICOIN_SERVER" \
    --peer "$peer" \
    --limit "$PICOIN_RECONCILER_LIMIT"
}

run_peer_sync() {
  local peer="$1"
  case "$PICOIN_RECONCILER_MODE" in
    heartbeats)
      run_heartbeat_sync "$peer"
      ;;
    reconcile)
      run_reconcile "$peer"
      ;;
    catch-up)
      run_catch_up "$peer"
      ;;
    *)
      echo "invalid PICOIN_RECONCILER_MODE=$PICOIN_RECONCILER_MODE" >&2
      return 2
      ;;
  esac
}

run_once() {
  local stamp
  local out
  local latest
  local rc
  local peer
  local peer_json
  local result_file
  local index
  local any_failed
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  out="$PICOIN_RECONCILE_DIR/catch-up-$stamp.json"
  latest="$PICOIN_RECONCILE_DIR/latest.json"

  echo "Picoin reconciler tick $stamp"
  echo "server=$PICOIN_SERVER"
  echo "mode=$PICOIN_RECONCILER_MODE"
  if [ -n "$PICOIN_RECONCILE_PEERS" ]; then
    printf '{"stamp":%s,"server":%s,"peers":[' "$(json_string "$stamp")" "$(json_string "$PICOIN_SERVER")" >"$out"
    index=0
    any_failed=0
    IFS=',' read -r -a peers <<<"$PICOIN_RECONCILE_PEERS"
    for peer in "${peers[@]}"; do
      peer="$(echo "$peer" | xargs)"
      if [ -z "$peer" ]; then
        continue
      fi
      echo "peer=$peer"
      result_file="$PICOIN_RECONCILE_DIR/sync-$stamp-peer-$index.json"
      run_peer_sync "$peer" >"$result_file"
      rc=$?
      if [ "$rc" -ne 0 ]; then
        any_failed=1
        echo "picoin reconciler sync peer=$peer exited with rc=$rc; continuing" >&2
        cat "$result_file" >&2 || true
      fi
      if [ "$index" -gt 0 ]; then
        printf ',' >>"$out"
      fi
      peer_json="$(json_string "$peer")"
      printf '{"peer":%s,"rc":%s,"result":' "$peer_json" "$rc" >>"$out"
      json_file "$result_file" >>"$out"
      printf '}' >>"$out"
      index=$((index + 1))
    done
    printf ']}' >>"$out"
    rc="$any_failed"
  else
    echo "peer=(none)"
    if [ "$PICOIN_RECONCILER_SKIP_WITHOUT_PEER" = "1" ]; then
      "$PICOIN_PYTHON" -m picoin node sync-status --server "$PICOIN_SERVER" >"$out"
    else
      run_peer_sync "" >"$out"
    fi
    rc=$?
  fi

  cp "$out" "$latest"
  if [ "$rc" -ne 0 ]; then
    echo "picoin reconciler catch-up exited with rc=$rc; continuing after sleep" >&2
    cat "$out" >&2 || true
  fi
}

while true; do
  run_once
  sleep "$PICOIN_RECONCILER_SLEEP_SECONDS"
done
