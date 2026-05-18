#!/usr/bin/env bash
set -u

PICOIN_HOME="${PICOIN_HOME:-/opt/picoin/picoin-proof-of-pi}"
PICOIN_PYTHON="${PICOIN_PYTHON:-$PICOIN_HOME/.venv/bin/python}"
PICOIN_SERVER="${PICOIN_SERVER:-http://127.0.0.1:8000}"
PICOIN_MINER_SERVER="${PICOIN_MINER_SERVER:-$PICOIN_SERVER}"
PICOIN_VALIDATOR_SERVER="${PICOIN_VALIDATOR_SERVER:-$PICOIN_SERVER}"
PICOIN_WORKER_ROLE="${PICOIN_WORKER_ROLE:-}"
PICOIN_WORKER_SLEEP="${PICOIN_WORKER_SLEEP:-10}"

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
        --workers "${PICOIN_MINER_WORKERS:-1}"
      rc=$?
      if [ "$rc" -ne 0 ]; then
        echo "picoin $PICOIN_WORKER_ROLE iteration exited with rc=$rc; continuing after sleep" >&2
      fi
      ;;
      
    validator)
      # Iteración secuencial sobre las tres identidades para forzar quórum de consenso (2/3)
      for val in one two three; do
        CURRENT_IDENTITY="$PICOIN_HOME/data/testnet/identities/validator-${val}.json"
        
        echo "[$(date +%T)] Intentando validación secuencial con: validator-${val}" >&2
        
        "$PICOIN_PYTHON" -m picoin validator \
          --server "$PICOIN_VALIDATOR_SERVER" \
          --identity "$CURRENT_IDENTITY" \
          validate \
          --loops "${PICOIN_VALIDATOR_LOOPS:-1}" \
          --sleep "${PICOIN_VALIDATOR_SLEEP:-5}"
        rc=$?
        
        if [ "$rc" -ne 0 ]; then
          echo "picoin validator (${val}) iteration exited with rc=$rc; saltando al siguiente validador" >&2
        fi
      done
      # Forzamos un código de salida limpio general para la ronda completa
      rc=0
      ;;
      
    *)
      echo "invalid PICOIN_WORKER_ROLE=$PICOIN_WORKER_ROLE" >&2
      exit 2
      ;;
  esac

  sleep "$PICOIN_WORKER_SLEEP"
done
