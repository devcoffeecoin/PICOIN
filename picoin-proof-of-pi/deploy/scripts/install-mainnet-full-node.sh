#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${PICOIN_REPO_URL:-https://github.com/devcoffeecoin/PICOIN.git}"
BRANCH="${PICOIN_BRANCH:-main}"
SOURCE_ROOT="${PICOIN_SOURCE_ROOT:-/opt/picoin/src/PICOIN}"
TARGET_DIR="${PICOIN_REPO_DIR:-/opt/picoin/picoin-proof-of-pi}"
DATA_DIR="${PICOIN_DATA_DIR:-/var/lib/picoin/data-mainnet-exchange-full-node}"
BACKUP_DIR="${PICOIN_BACKUP_DIR:-/var/backups/picoin-mainnet-exchange-full-node}"
BOOTSTRAP_PEER="${PICOIN_BOOTSTRAP_PEER:-https://api.picoin.science}"
ENV_FILE="${PICOIN_ENV_FILE:-/etc/picoin/picoin.env}"
NODE_ID=""
NODE_ADDRESS=""
START_SERVICES=0
FORCE_ENV=0

usage() {
  cat <<'EOF'
Usage:
  sudo deploy/scripts/install-mainnet-full-node.sh \
    --node-id exchange-node-1 \
    --node-address https://node.example.com \
    [--branch main] [--bootstrap-peer https://api.picoin.science] [--start] [--force-env]

Installs or refreshes a Picoin mainnet full node for exchanges/infrastructure.
It enables picoin-node and picoin-reconciler, and disables miner/validator/auditor
services by default.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --repo-url)
      REPO_URL="$2"; shift 2 ;;
    --branch)
      BRANCH="$2"; shift 2 ;;
    --source-root)
      SOURCE_ROOT="$2"; shift 2 ;;
    --target-dir)
      TARGET_DIR="$2"; shift 2 ;;
    --data-dir)
      DATA_DIR="$2"; shift 2 ;;
    --backup-dir)
      BACKUP_DIR="$2"; shift 2 ;;
    --bootstrap-peer)
      BOOTSTRAP_PEER="$2"; shift 2 ;;
    --node-id)
      NODE_ID="$2"; shift 2 ;;
    --node-address)
      NODE_ADDRESS="$2"; shift 2 ;;
    --env-file)
      ENV_FILE="$2"; shift 2 ;;
    --start)
      START_SERVICES=1; shift ;;
    --force-env)
      FORCE_ENV=1; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "unknown argument: $1" >&2
      usage
      exit 2 ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root with sudo" >&2
  exit 1
fi

if [ -z "$NODE_ID" ] || [ -z "$NODE_ADDRESS" ]; then
  echo "--node-id and --node-address are required" >&2
  usage
  exit 2
fi

if command -v apt-get >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y git python3 python3-venv python3-pip sqlite3 curl rsync ca-certificates
fi

if ! id picoin >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /opt/picoin --shell /bin/bash picoin
fi

install -d -o picoin -g picoin -m 0755 /opt/picoin/src
if [ -d "$SOURCE_ROOT/.git" ]; then
  git -C "$SOURCE_ROOT" fetch origin "$BRANCH"
  if git -C "$SOURCE_ROOT" show-ref --verify --quiet "refs/remotes/origin/$BRANCH"; then
    git -C "$SOURCE_ROOT" checkout -B "$BRANCH" "origin/$BRANCH"
  else
    git -C "$SOURCE_ROOT" checkout "$BRANCH"
  fi
else
  rm -rf "$SOURCE_ROOT"
  git clone --branch "$BRANCH" "$REPO_URL" "$SOURCE_ROOT"
fi

SOURCE_DIR="$SOURCE_ROOT/picoin-proof-of-pi"
if [ ! -f "$SOURCE_DIR/picoin/__main__.py" ]; then
  echo "source directory does not look like picoin-proof-of-pi: $SOURCE_DIR" >&2
  exit 1
fi

install -d -o picoin -g picoin -m 0755 "$DATA_DIR" "$DATA_DIR/wallets" "$DATA_DIR/identities" "$BACKUP_DIR"
install -d -m 0755 "$(dirname "$ENV_FILE")"

PICOIN_SOURCE_DIR="$SOURCE_DIR" \
PICOIN_REPO_DIR="$TARGET_DIR" \
PICOIN_DATA_DIR="$DATA_DIR" \
PICOIN_STATE_BACKUP_ROOT=/opt/picoin/state-backups \
PICOIN_ENV_SOURCE="$SOURCE_DIR/deploy/mainnet-exchange-full-node.env.example" \
  bash "$SOURCE_DIR/deploy/scripts/refresh-code.sh"

if [ "$FORCE_ENV" -eq 1 ] || [ ! -f "$ENV_FILE" ]; then
  install -m 0640 "$TARGET_DIR/deploy/mainnet-exchange-full-node.env.example" "$ENV_FILE"
fi

set_kv() {
  key="$1"
  value="$2"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >>"$ENV_FILE"
  fi
}

set_kv PICOIN_NODE_ID "$NODE_ID"
set_kv PICOIN_NODE_TYPE "full"
set_kv PICOIN_NODE_ADDRESS "$NODE_ADDRESS"
set_kv PICOIN_BOOTSTRAP_PEER "$BOOTSTRAP_PEER"
set_kv PICOIN_BOOTSTRAP_PEERS "$BOOTSTRAP_PEER"
set_kv PICOIN_HOME "$TARGET_DIR"
set_kv PICOIN_DATA_DIR "$DATA_DIR"
set_kv PICOIN_DB_PATH "$DATA_DIR/picoin.sqlite3"
set_kv PICOIN_WALLET_PATH "$DATA_DIR/wallets/default.json"
set_kv PICOIN_BACKUP_DIR "$BACKUP_DIR"
set_kv PICOIN_MINER_SERVER ""
set_kv PICOIN_VALIDATOR_SERVER ""
set_kv PICOIN_VALIDATOR_NODE_SERVER "http://127.0.0.1:8000"
set_kv PICOIN_MINER_IDENTITY "$DATA_DIR/identities/miner-disabled.json"
set_kv PICOIN_VALIDATOR_IDENTITY "$DATA_DIR/identities/validator-disabled.json"
set_kv PICOIN_MINER_REWARD_ADDRESS ""
set_kv PICOIN_VALIDATOR_REWARD_ADDRESS ""

chown root:picoin "$ENV_FILE"
chmod 0640 "$ENV_FILE"
chown -R picoin:picoin "$TARGET_DIR" "$DATA_DIR" "$BACKUP_DIR" /opt/picoin/state-backups

if [ ! -d "$TARGET_DIR/.venv" ]; then
  runuser -u picoin -- python3 -m venv "$TARGET_DIR/.venv"
fi
runuser -u picoin -- "$TARGET_DIR/.venv/bin/pip" install --upgrade pip
runuser -u picoin -- "$TARGET_DIR/.venv/bin/pip" install -r "$TARGET_DIR/requirements.txt"

systemctl daemon-reload
systemctl enable picoin-node.service picoin-reconciler.service
systemctl disable picoin-miner.service picoin-validator.service picoin-auditor.service 2>/dev/null || true

if [ "$START_SERVICES" -eq 1 ]; then
  systemctl restart picoin-node.service
  systemctl restart picoin-reconciler.service
fi

cat <<EOF
Picoin full node installed.

Branch:        $BRANCH
Runtime:       $TARGET_DIR
Data:          $DATA_DIR
Env:           $ENV_FILE
Node id:       $NODE_ID
Node address:  $NODE_ADDRESS
Bootstrap:     $BOOTSTRAP_PEER

Next:
  sudo systemctl status picoin-node --no-pager -l
  sudo systemctl status picoin-reconciler --no-pager -l
  curl -sS http://127.0.0.1:8000/node/sync-status | python3 -m json.tool
EOF
