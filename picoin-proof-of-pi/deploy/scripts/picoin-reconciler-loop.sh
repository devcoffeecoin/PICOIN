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
PICOIN_RECONCILER_SLEEP_SECONDS="${PICOIN_RECONCILER_SLEEP_SECONDS:-120}"
PICOIN_RECONCILER_ROUNDS="${PICOIN_RECONCILER_ROUNDS:-2}"
PICOIN_RECONCILER_LIMIT="${PICOIN_RECONCILER_LIMIT:-32}"

PICOIN_BOOTSTRAP_PEER="${PICOIN_BOOTSTRAP_PEER:-}"
if [ -z "$PICOIN_BOOTSTRAP_PEER" ] && [ -n "${PICOIN_BOOTSTRAP_PEERS:-}" ]; then
  PICOIN_BOOTSTRAP_PEER="${PICOIN_BOOTSTRAP_PEERS%%,*}"
fi

if [ ! -x "$PICOIN_PYTHON" ] && [ "$PICOIN_PYTHON" = "$PICOIN_HOME/.venv/bin/python" ]; then
  PICOIN_PYTHON="python3"
fi

mkdir -p "$PICOIN_RECONCILE_DIR"
cd "$PICOIN_HOME" || exit 2

run_once() {
  local stamp
  local out
  local latest
  local rc
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  out="$PICOIN_RECONCILE_DIR/catch-up-$stamp.json"
  latest="$PICOIN_RECONCILE_DIR/latest.json"

  echo "Picoin reconciler tick $stamp"
  echo "server=$PICOIN_SERVER"
  if [ -n "$PICOIN_BOOTSTRAP_PEER" ]; then
    echo "peer=$PICOIN_BOOTSTRAP_PEER"
    "$PICOIN_PYTHON" -m picoin node catch-up \
      --server "$PICOIN_SERVER" \
      --peer "$PICOIN_BOOTSTRAP_PEER" \
      --rounds "$PICOIN_RECONCILER_ROUNDS" \
      --reconcile-limit "$PICOIN_RECONCILER_LIMIT" >"$out"
    rc=$?
  else
    echo "peer=(none)"
    "$PICOIN_PYTHON" -m picoin node catch-up \
      --server "$PICOIN_SERVER" \
      --rounds "$PICOIN_RECONCILER_ROUNDS" \
      --reconcile-limit "$PICOIN_RECONCILER_LIMIT" >"$out"
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
