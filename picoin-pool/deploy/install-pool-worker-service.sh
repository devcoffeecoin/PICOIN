#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${PICOIN_POOL_WORKER_SERVICE:-picoin-pool-worker}"
SERVICE_USER="${PICOIN_POOL_WORKER_USER:-picoin}"
REPO_DIR="${PICOIN_REPO_DIR:-/opt/picoin/src/PICOIN}"
POOL_URL="${PICOIN_POOL_URL:-https://pool1.picoin.science}"
WORKER_ID="${PICOIN_POOL_WORKER_ID:-}"
WORKER_NAME="${PICOIN_POOL_WORKER_NAME:-}"
PAYOUT_ADDRESS="${PICOIN_POOL_PAYOUT_ADDRESS:-}"
AUTH_TOKEN="${PICOIN_POOL_TOKEN:-}"
LOOPS="${PICOIN_POOL_WORKER_LOOPS:-999999999}"
SLEEP_SECONDS="${PICOIN_POOL_WORKER_SLEEP:-1}"
REQUEST_TIMEOUT="${PICOIN_POOL_WORKER_REQUEST_TIMEOUT:-90}"
CORE_PATH="${PICOIN_CORE_PATH:-}"
PYTHON_BIN="${PICOIN_POOL_PYTHON:-}"
INSTALL_DEPS=0
DISABLE_DIRECT_MINER=0

usage() {
  cat <<'EOF'
Usage:
  sudo bash picoin-pool/deploy/install-pool-worker-service.sh \
    --payout-address PI_YOUR_PAYOUT_WALLET \
    [--pool https://pool1.picoin.science] \
    [--worker-id pool1-my-rig] \
    [--name my-rig] \
    [--repo-dir /opt/picoin/src/PICOIN] \
    [--core-path /opt/picoin/picoin-proof-of-pi] \
    [--python /path/to/python] \
    [--auth-token PRIVATE_POOL_TOKEN] \
    [--install-deps] \
    [--disable-direct-miner]

Environment variables with the same PICOIN_POOL_* names are also supported.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --pool)
      POOL_URL="$2"
      shift 2
      ;;
    --worker-id)
      WORKER_ID="$2"
      shift 2
      ;;
    --name)
      WORKER_NAME="$2"
      shift 2
      ;;
    --payout-address)
      PAYOUT_ADDRESS="$2"
      shift 2
      ;;
    --repo-dir)
      REPO_DIR="$2"
      shift 2
      ;;
    --core-path)
      CORE_PATH="$2"
      shift 2
      ;;
    --python)
      PYTHON_BIN="$2"
      shift 2
      ;;
    --auth-token)
      AUTH_TOKEN="$2"
      shift 2
      ;;
    --loops)
      LOOPS="$2"
      shift 2
      ;;
    --sleep)
      SLEEP_SECONDS="$2"
      shift 2
      ;;
    --request-timeout)
      REQUEST_TIMEOUT="$2"
      shift 2
      ;;
    --install-deps)
      INSTALL_DEPS=1
      shift
      ;;
    --disable-direct-miner)
      DISABLE_DIRECT_MINER=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  echo "run this installer with sudo" >&2
  exit 1
fi

if [ -z "$PAYOUT_ADDRESS" ]; then
  echo "--payout-address is required" >&2
  exit 1
fi

if [[ "$PAYOUT_ADDRESS" != PI* ]]; then
  echo "payout address should be a Picoin PI wallet address" >&2
  exit 1
fi

if [ ! -f "$REPO_DIR/picoin-pool/pool_worker.py" ]; then
  echo "pool worker not found at $REPO_DIR/picoin-pool/pool_worker.py" >&2
  echo "clone or update the PICOIN repository first, or pass --repo-dir" >&2
  exit 1
fi

if [ -z "$CORE_PATH" ]; then
  if [ -d "/opt/picoin/picoin-proof-of-pi/app" ]; then
    CORE_PATH="/opt/picoin/picoin-proof-of-pi"
  elif [ -d "$REPO_DIR/picoin-proof-of-pi/app" ]; then
    CORE_PATH="$REPO_DIR/picoin-proof-of-pi"
  else
    echo "could not find picoin-proof-of-pi core; pass --core-path" >&2
    exit 1
  fi
fi

if [ -z "$WORKER_NAME" ]; then
  WORKER_NAME="$(hostname 2>/dev/null || echo linux-worker)"
fi

if [ -z "$WORKER_ID" ]; then
  HOST_ID="$(hostname -s 2>/dev/null || hostname 2>/dev/null || echo linux-worker)"
  WORKER_ID="pool1-${HOST_ID//[^A-Za-z0-9_-]/-}"
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /opt/picoin --shell /bin/bash "$SERVICE_USER"
fi

if [ "$INSTALL_DEPS" -eq 1 ]; then
  if [ ! -x "$REPO_DIR/.venv/bin/python" ]; then
    python3 -m venv "$REPO_DIR/.venv"
  fi
  "$REPO_DIR/.venv/bin/python" -m pip install --upgrade pip
  "$REPO_DIR/.venv/bin/python" -m pip install -r "$REPO_DIR/picoin-proof-of-pi/requirements.txt"
fi

if [ -z "$PYTHON_BIN" ]; then
  if [ -x "$REPO_DIR/.venv/bin/python" ]; then
    PYTHON_BIN="$REPO_DIR/.venv/bin/python"
  elif [ -x "$CORE_PATH/.venv/bin/python" ]; then
    PYTHON_BIN="$CORE_PATH/.venv/bin/python"
  else
    PYTHON_BIN="$(command -v python3 || true)"
  fi
fi

if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
  echo "python executable not found; pass --python or run with --install-deps" >&2
  exit 1
fi

if ! "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import requests
PY
then
  echo "python environment is missing requests; run again with --install-deps or install requirements.txt" >&2
  exit 1
fi

systemd_quote() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '"%s"' "$value"
}

UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
EXEC_START="$(systemd_quote "$PYTHON_BIN") $(systemd_quote "$REPO_DIR/picoin-pool/pool_worker.py") --pool $(systemd_quote "$POOL_URL") --worker-id $(systemd_quote "$WORKER_ID") --name $(systemd_quote "$WORKER_NAME") --payout-address $(systemd_quote "$PAYOUT_ADDRESS") --loops $(systemd_quote "$LOOPS") --sleep $(systemd_quote "$SLEEP_SECONDS") --request-timeout $(systemd_quote "$REQUEST_TIMEOUT")"
if [ -n "${AUTH_TOKEN:-}" ]; then
  EXEC_START="$EXEC_START --auth-token $(systemd_quote "$AUTH_TOKEN")"
fi

cat >"$UNIT_PATH" <<EOF
[Unit]
Description=Picoin Pool Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$REPO_DIR
Environment="PICOIN_CORE_PATH=$CORE_PATH"
Environment="PICOIN_POOL_PAYOUT_ADDRESS=$PAYOUT_ADDRESS"
ExecStart=$EXEC_START
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload

if [ "$DISABLE_DIRECT_MINER" -eq 1 ]; then
  systemctl disable --now picoin-miner 2>/dev/null || true
fi

systemctl enable --now "$SERVICE_NAME"

echo "installed $SERVICE_NAME"
echo "pool: $POOL_URL"
echo "worker_id: $WORKER_ID"
echo "payout_address: $PAYOUT_ADDRESS"
echo
echo "status:"
systemctl --no-pager -l status "$SERVICE_NAME" || true
