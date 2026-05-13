#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${PICOIN_REPO_DIR:-/opt/picoin/picoin-proof-of-pi}"
SERVICE_SOURCE="$REPO_DIR/deploy/systemd/picoin-node.service"
ENV_SOURCE="$REPO_DIR/deploy/public-testnet.env.example"

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root: sudo PICOIN_REPO_DIR=$REPO_DIR $0" >&2
  exit 1
fi

install -d -m 0755 /etc/picoin

if [ ! -f /etc/picoin/picoin.env ]; then
  install -m 0640 "$ENV_SOURCE" /etc/picoin/picoin.env
  echo "created /etc/picoin/picoin.env; edit CHANGE_ME values before starting"
fi

install -m 0644 "$SERVICE_SOURCE" /etc/systemd/system/picoin-node.service
systemctl daemon-reload
systemctl enable picoin-node.service

echo "installed picoin-node.service"
echo "next:"
echo "  sudo nano /etc/picoin/picoin.env"
echo "  sudo systemctl start picoin-node"
echo "  sudo systemctl status picoin-node --no-pager"
