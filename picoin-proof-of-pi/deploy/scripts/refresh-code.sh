#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="${PICOIN_SOURCE_DIR:-$(pwd)}"
TARGET_DIR="${PICOIN_REPO_DIR:-/opt/picoin/picoin-proof-of-pi}"
STATE_DIR="${PICOIN_DATA_DIR:-/var/lib/picoin/data}"
BACKUP_ROOT="${PICOIN_STATE_BACKUP_ROOT:-/opt/picoin/state-backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SERVICES=(picoin-miner picoin-validator picoin-auditor picoin-node)

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root: sudo PICOIN_SOURCE_DIR=$SOURCE_DIR PICOIN_REPO_DIR=$TARGET_DIR $0" >&2
  exit 1
fi

if [ ! -d "$SOURCE_DIR" ]; then
  echo "source directory not found: $SOURCE_DIR" >&2
  exit 1
fi

if [ ! -f "$SOURCE_DIR/picoin/__main__.py" ]; then
  echo "source directory does not look like picoin-proof-of-pi: $SOURCE_DIR" >&2
  exit 1
fi

if ! id picoin >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /opt/picoin --shell /bin/bash picoin
fi

SOURCE_REAL="$(readlink -f "$SOURCE_DIR")"
TARGET_REAL="$(readlink -f "$TARGET_DIR" 2>/dev/null || printf '%s' "$TARGET_DIR")"

echo "Stopping Picoin services..."
systemctl stop "${SERVICES[@]}" 2>/dev/null || true

install -d -o picoin -g picoin -m 0755 "$BACKUP_ROOT"
install -d -o picoin -g picoin -m 0755 "$STATE_DIR"

if [ -d "$TARGET_DIR/data" ] && [ ! -L "$TARGET_DIR/data" ]; then
  echo "Backing up current repo data..."
  cp -a "$TARGET_DIR/data" "$BACKUP_ROOT/data-before-code-refresh-$STAMP"
  if [ -f "$TARGET_DIR/data/picoin.sqlite3" ] && [ ! -f "$STATE_DIR/picoin.sqlite3" ]; then
    echo "Migrating repo data to $STATE_DIR..."
    cp -a "$TARGET_DIR/data/." "$STATE_DIR/"
  fi
fi

if [ -f "$STATE_DIR/picoin.sqlite3" ]; then
  echo "Backing up persistent state..."
  cp -a "$STATE_DIR" "$BACKUP_ROOT/data-persistent-before-code-refresh-$STAMP"
fi

echo "Refreshing code in $TARGET_DIR..."
install -d -o picoin -g picoin -m 0755 "$TARGET_DIR"
if [ "$SOURCE_REAL" = "$TARGET_REAL" ]; then
  echo "Source and target are the same directory; preserving code and migrating state only."
elif command -v rsync >/dev/null 2>&1; then
  rsync -a --delete \
    --exclude '/.git/' \
    --exclude '/.venv/' \
    --exclude '/data/' \
    --exclude '/backups/' \
    --exclude '/test-output/' \
    "$SOURCE_DIR/" "$TARGET_DIR/"
else
  echo "rsync is required for safe refresh; install it with: sudo apt install -y rsync" >&2
  exit 1
fi

rm -rf "$TARGET_DIR/data"
ln -s "$STATE_DIR" "$TARGET_DIR/data"
install -d -o picoin -g picoin -m 0755 "$TARGET_DIR/test-output"
install -d -o picoin -g picoin -m 0755 /var/backups/picoin
chown -R picoin:picoin "$TARGET_DIR" "$STATE_DIR" "$BACKUP_ROOT" /var/backups/picoin

"$TARGET_DIR/deploy/scripts/install-systemd-service.sh"

echo "Refresh complete."
echo "Persistent data: $STATE_DIR"
echo "State backups: $BACKUP_ROOT"
echo "Next:"
echo "  sudo systemctl restart picoin-node picoin-auditor picoin-validator picoin-miner"
echo "  cd $TARGET_DIR && .venv/bin/python -m picoin node report --peer http://BOOTSTRAP_PUBLIC_IP:8000"
