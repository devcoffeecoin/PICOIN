# Picoin Mainnet Shadow Full-Node Verification

This runbook verifies that a clean Linux full node can reproduce the live mainnet chain as a read-only shadow node. It must run on a disposable droplet and must not run miner, validator, reconciler, or auditor services.

Mainnet bootstrap remains untouched. The shadow node only reads from `https://api.picoin.science`.

## Install

Run on a clean Ubuntu 22.04/24.04 droplet:

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-pip sqlite3 curl jq rsync

sudo useradd --system --create-home --home-dir /var/lib/picoin --shell /usr/sbin/nologin picoin 2>/dev/null || true
sudo mkdir -p /opt/picoin/src /var/lib/picoin/data-mainnet-shadow /etc/picoin /var/backups/picoin-mainnet-shadow
sudo chown -R picoin:picoin /var/lib/picoin /var/backups/picoin-mainnet-shadow

cd /opt/picoin/src
sudo git clone https://github.com/devcoffeecoin/PICOIN.git PICOIN 2>/dev/null || true
cd /opt/picoin/src/PICOIN
sudo git fetch origin
sudo git switch codex/decentralization-roadmap-phase1
sudo git pull --ff-only origin codex/decentralization-roadmap-phase1
```

Install the runtime copy:

```bash
SOURCE_DIR=/opt/picoin/src/PICOIN/picoin-proof-of-pi

sudo env \
  PICOIN_SOURCE_DIR="$SOURCE_DIR" \
  PICOIN_REPO_DIR=/opt/picoin/picoin-proof-of-pi \
  PICOIN_DATA_DIR=/var/lib/picoin/data-mainnet-shadow \
  bash "$SOURCE_DIR/deploy/scripts/refresh-code.sh"

cd /opt/picoin/picoin-proof-of-pi
sudo -u picoin python3 -m venv .venv
sudo -u picoin .venv/bin/pip install --upgrade pip
sudo -u picoin .venv/bin/pip install -r requirements.txt
```

## Environment

```bash
sudo cp /opt/picoin/picoin-proof-of-pi/deploy/mainnet-shadow-full-node.env.example /etc/picoin/picoin.env
sudo sed -i 's/CHANGE_ME_MAINNET_SHADOW_NODE_ID/mainnet-shadow-full-node-1/' /etc/picoin/picoin.env
sudo sed -i 's/CHANGE_ME_PUBLIC_IP/YOUR_DROPLET_PUBLIC_IP/' /etc/picoin/picoin.env
sudo chown root:picoin /etc/picoin/picoin.env
sudo chmod 0640 /etc/picoin/picoin.env
sudo grep -n "CHANGE_ME\|YOUR_DROPLET_PUBLIC_IP" /etc/picoin/picoin.env
```

The final grep must print nothing.

## Start Read-Only Node

```bash
sudo systemctl daemon-reload
sudo systemctl disable --now picoin-miner picoin-validator picoin-reconciler picoin-auditor 2>/dev/null || true
sudo systemctl enable --now picoin-node
sleep 20

systemctl is-active picoin-node picoin-miner picoin-validator picoin-reconciler picoin-auditor 2>/dev/null || true
curl -sS http://127.0.0.1:8000/health | python3 -m json.tool
curl -sS http://127.0.0.1:8000/node/sync-status | python3 -m json.tool
```

## Restore Mainnet Snapshot

```bash
cd /opt/picoin/picoin-proof-of-pi
set -a
. /etc/picoin/picoin.env
set +a

HEAD_HEIGHT=$(curl -sS --max-time 30 "$PICOIN_BOOTSTRAP_PEER/node/sync-status" | \
  python3 -c 'import json,sys; d=json.load(sys.stdin); print(int(d.get("effective_latest_block_height") or d.get("latest_block_height") or 1))')
echo "HEAD_HEIGHT=$HEAD_HEIGHT"

sudo -u picoin .venv/bin/python -m picoin node checkpoint \
  --server http://127.0.0.1:8000 \
  restore-peer \
  --peer "$PICOIN_BOOTSTRAP_PEER" \
  --height "$HEAD_HEIGHT" \
  --source mainnet-shadow-restore
```

## Verify

```bash
sudo -u picoin PICOIN_ENV_FILE=/etc/picoin/picoin.env \
  bash deploy/scripts/full-node-phase1-verify.sh
```

Expected result:

```json
{
  "status": "ok",
  "summary": {
    "errors": 0
  }
}
```

If the node is a few blocks behind while catching up, run:

```bash
sudo -u picoin .venv/bin/python -m picoin node catch-up \
  --server http://127.0.0.1:8000 \
  --peer https://api.picoin.science \
  --max-rounds 10 \
  --reconcile-limit 20 \
  --replay-limit 20
```

Final acceptance requires zero lag, matching effective tip hash, no replay divergence, and `/audit/full` valid.
