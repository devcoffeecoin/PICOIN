#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${PICOIN_REPO_DIR:-/opt/picoin/picoin-proof-of-pi}"
ENV_SOURCE="$REPO_DIR/deploy/public-testnet.env.example"
SERVICES=(
  picoin-node.service
  picoin-miner.service
  picoin-validator.service
  picoin-auditor.service
)

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root: sudo PICOIN_REPO_DIR=$REPO_DIR $0" >&2
  exit 1
fi

install -d -m 0755 /etc/picoin

if ! id picoin >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /opt/picoin --shell /bin/bash picoin
fi

install -d -o picoin -g picoin -m 0755 "$REPO_DIR/data"
install -d -o picoin -g picoin -m 0755 "$REPO_DIR/data/testnet"
install -d -o picoin -g picoin -m 0755 "$REPO_DIR/data/testnet/identities"
install -d -o picoin -g picoin -m 0755 "$REPO_DIR/data/testnet/smoke"
install -d -o picoin -g picoin -m 0755 "$REPO_DIR/test-output"
install -d -o picoin -g picoin -m 0755 "$REPO_DIR/backups"
install -d -o picoin -g picoin -m 0755 /var/lib/picoin/data
install -d -o picoin -g picoin -m 0755 /var/lib/picoin/data/testnet
install -d -o picoin -g picoin -m 0755 /var/lib/picoin/data/testnet/identities
install -d -o picoin -g picoin -m 0755 /var/lib/picoin/data/testnet/smoke
install -d -o picoin -g picoin -m 0755 /var/backups/picoin
install -d -o picoin -g picoin -m 0755 /opt/picoin/state-backups
chmod 0755 "$REPO_DIR/deploy/scripts/picoin-worker-loop.sh"
chmod 0755 "$REPO_DIR/deploy/scripts/refresh-code.sh"

if [ ! -f /etc/picoin/picoin.env ]; then
  install -m 0640 "$ENV_SOURCE" /etc/picoin/picoin.env
  echo "created /etc/picoin/picoin.env; edit CHANGE_ME values before starting"
fi
chown root:picoin /etc/picoin/picoin.env
chmod 0640 /etc/picoin/picoin.env

for service in "${SERVICES[@]}"; do
  install -m 0644 "$REPO_DIR/deploy/systemd/$service" "/etc/systemd/system/$service"
done
systemctl daemon-reload
systemctl enable picoin-node.service

echo "installed Picoin systemd services:"
printf '  %s\n' "${SERVICES[@]}"
echo "next:"
echo "  sudo nano /etc/picoin/picoin.env"
echo "  sudo systemctl start picoin-node"
echo "  sudo systemctl start picoin-validator picoin-miner picoin-auditor"
echo "  sudo systemctl status picoin-node --no-pager"
