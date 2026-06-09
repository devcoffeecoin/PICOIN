#!/usr/bin/env bash
set -u

PICOIN_HOME="${PICOIN_HOME:-/opt/picoin/picoin-proof-of-pi}"
PICOIN_PYTHON="${PICOIN_PYTHON:-$PICOIN_HOME/.venv/bin/python}"
PICOIN_SERVER="${PICOIN_SERVER:-http://127.0.0.1:8000}"
PICOIN_BOOTSTRAP_COORDINATOR="${PICOIN_BOOTSTRAP_PEER:-}"
if [ -z "$PICOIN_BOOTSTRAP_COORDINATOR" ] && [ -n "${PICOIN_BOOTSTRAP_PEERS:-}" ]; then
  PICOIN_BOOTSTRAP_COORDINATOR="${PICOIN_BOOTSTRAP_PEERS%%,*}"
fi
PICOIN_BOOTSTRAP_COORDINATOR="$(printf '%s' "$PICOIN_BOOTSTRAP_COORDINATOR" | xargs)"
PICOIN_MINER_SERVER="${PICOIN_MINER_SERVER:-${PICOIN_BOOTSTRAP_COORDINATOR:-$PICOIN_SERVER}}"
PICOIN_VALIDATOR_SERVER="${PICOIN_VALIDATOR_SERVER:-$PICOIN_SERVER}"
PICOIN_WORKER_ROLE="${PICOIN_WORKER_ROLE:-}"
PICOIN_WORKER_SLEEP="${PICOIN_WORKER_SLEEP:-10}"
PICOIN_VALIDATOR_ITERATION_TIMEOUT="${PICOIN_VALIDATOR_ITERATION_TIMEOUT:-75s}"

if [ -z "$PICOIN_WORKER_ROLE" ]; then
  echo "PICOIN_WORKER_ROLE is required: miner or validator" >&2
  exit 2
fi

cd "$PICOIN_HOME" || exit 2

while true; do
  case "$PICOIN_WORKER_ROLE" in
    miner)
      "$PICOIN_PYTHON" -m picoin miner \
        --server "$PICOIN_MINER_SERVER" \
        --identity "${PICOIN_MINER_IDENTITY:-$PICOIN_HOME/data/testnet/identities/miner-alice.json}" \
        mine \
        --loops "${PICOIN_MINER_LOOPS:-1}" \
        --sleep "${PICOIN_MINER_SLEEP:-5}" \
        --workers "${PICOIN_MINER_WORKERS:-2}"
      rc=$?
      if [ "$rc" -ne 0 ]; then
        echo "picoin $PICOIN_WORKER_ROLE iteration exited with rc=$rc; continuing after sleep" >&2
      fi
      ;;

    validator)
      # Ejecuta un solo validador por droplet.
      # Configurar en /etc/picoin/picoin.env:
      # PICOIN_VALIDATOR_NAME=validator-one / validator-two / validator-three
      PICOIN_VALIDATOR_NAME="${PICOIN_VALIDATOR_NAME:-validator-one}"
      CURRENT_IDENTITY="${PICOIN_VALIDATOR_IDENTITY:-$PICOIN_HOME/data/testnet/identities/${PICOIN_VALIDATOR_NAME}.json}"

      echo "[$(date +%T)] Intentando validación con: ${PICOIN_VALIDATOR_NAME} coordinator=${PICOIN_VALIDATOR_SERVER} node=${PICOIN_VALIDATOR_NODE_SERVER:-http://127.0.0.1:8000}" >&2

      timeout "$PICOIN_VALIDATOR_ITERATION_TIMEOUT" "$PICOIN_PYTHON" -m picoin validator \
        --server "$PICOIN_VALIDATOR_SERVER" \
        --identity "$CURRENT_IDENTITY" \
        validate \
        --node-server "${PICOIN_VALIDATOR_NODE_SERVER:-http://127.0.0.1:8000}" \
        --node-timeout "${PICOIN_VALIDATOR_NODE_TIMEOUT:-10}" \
        --submit-timeout "${PICOIN_VALIDATOR_SUBMIT_TIMEOUT:-30}" \
        --loops "${PICOIN_VALIDATOR_LOOPS:-12}" \
        --sleep "${PICOIN_VALIDATOR_SLEEP:-1}" \
        --poll-seconds "${PICOIN_VALIDATOR_POLL_SECONDS:-1}" \
        --heartbeat-interval "${PICOIN_VALIDATOR_HEARTBEAT_INTERVAL_SECONDS:-15}" \
        --workers "${PICOIN_VALIDATOR_WORKERS:-4}"
      rc=$?

      if [ "$rc" -ne 0 ]; then
        echo "picoin validator (${PICOIN_VALIDATOR_NAME}) iteration exited with rc=$rc; continuing after sleep" >&2
      fi
      ;;

    *)
      echo "invalid PICOIN_WORKER_ROLE=$PICOIN_WORKER_ROLE" >&2
      exit 2
      ;;
  esac

  sleep "$PICOIN_WORKER_SLEEP"
done
