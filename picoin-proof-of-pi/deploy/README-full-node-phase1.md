# Picoin Phase 1 Full-Node Verification Lab

This runbook is for the decentralization Phase 1 lab only. It must run on isolated droplets and must not be applied to mainnet machines.

Phase 1 goal: prove that an independent Linux full node can sync from a canonical test bootstrap, verify the same chain state, restart cleanly, catch up after lag, and serve read-only API requests without miner, validator, wallet, or manual SQLite access.

## Roles

Use at least two droplets:

| Role | Services enabled | Purpose |
| --- | --- | --- |
| `phase1-bootstrap-test` | `picoin-node` only, plus optional controlled test mining/validation during lab setup | Canonical lab peer that exports snapshots/checkpoints |
| `phase1-full-node-a` | `picoin-node` only | Independent full node under test |
| `phase1-full-node-b` | `picoin-node` only | Second independent full node under test |

Do not enable `picoin-miner`, `picoin-validator`, `picoin-reconciler`, or `picoin-auditor` on read-only full nodes unless the specific test case requires it.

## Branch

Use the isolated decentralization branch:

```bash
git fetch origin
git switch codex/decentralization-roadmap-phase1
git pull --ff-only origin codex/decentralization-roadmap-phase1
```

Mainnet remains on `main` until Phase 1 is accepted.

## Clean Droplet Install

Run on each Ubuntu 22.04/24.04 droplet:

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-pip sqlite3 curl jq rsync

sudo useradd --system --create-home --home-dir /var/lib/picoin --shell /usr/sbin/nologin picoin 2>/dev/null || true
sudo mkdir -p /opt/picoin/src /var/lib/picoin/data-phase1 /etc/picoin /var/backups/picoin-phase1
sudo chown -R picoin:picoin /var/lib/picoin /var/backups/picoin-phase1

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
  PICOIN_DATA_DIR=/var/lib/picoin/data-phase1 \
  bash "$SOURCE_DIR/deploy/scripts/refresh-code.sh"

cd /opt/picoin/picoin-proof-of-pi
sudo -u picoin python3 -m venv .venv
sudo -u picoin .venv/bin/pip install --upgrade pip
sudo -u picoin .venv/bin/pip install -r requirements.txt
```

## Environment

Copy the Phase 1 env template:

```bash
sudo cp /opt/picoin/picoin-proof-of-pi/deploy/phase1-full-node.env.example /etc/picoin/picoin.env
sudo chown root:picoin /etc/picoin/picoin.env
sudo chmod 0640 /etc/picoin/picoin.env
sudo nano /etc/picoin/picoin.env
```

Required edits:

```text
PICOIN_NODE_ID
PICOIN_NODE_TYPE
PICOIN_NODE_ADDRESS
PICOIN_BOOTSTRAP_PEER
PICOIN_BOOTSTRAP_PEERS
PICOIN_GENESIS_ALLOCATIONS_FILE
PICOIN_GENESIS_HASH
PICOIN_TREASURY_WALLET
PICOIN_GOVERNANCE_WALLET
PICOIN_SCIENCE_RESERVE_AUTHORIZED_SIGNERS
```

For read-only full nodes:

```env
PICOIN_NODE_TYPE=full
PICOIN_MINER_SERVER=
PICOIN_VALIDATOR_SERVER=
PICOIN_VALIDATOR_NODE_ADDRESS=
```

The command below must return no unresolved placeholders before services start:

```bash
sudo grep -n "CHANGE_ME" /etc/picoin/picoin.env
```

## Start A Read-Only Full Node

```bash
sudo systemctl daemon-reload
sudo systemctl disable --now picoin-miner picoin-validator picoin-reconciler picoin-auditor 2>/dev/null || true
sudo systemctl enable --now picoin-node
sleep 10
sudo systemctl status picoin-node --no-pager
```

Local health:

```bash
curl -sS http://127.0.0.1:8000/health | python3 -m json.tool
curl -sS http://127.0.0.1:8000/node/sync-status | python3 -m json.tool
```

## Restore From Bootstrap Snapshot

Run this on a fresh full node after the local node is up:

```bash
cd /opt/picoin/picoin-proof-of-pi
set -a
. /etc/picoin/picoin.env
set +a

HEAD_HEIGHT=$(curl -sS --max-time 20 "$PICOIN_BOOTSTRAP_PEER/node/sync-status" | \
  python3 -c 'import json,sys; d=json.load(sys.stdin); print(int(d.get("effective_latest_block_height") or d.get("latest_block_height") or 1))')

sudo -u picoin .venv/bin/python -m picoin node checkpoint \
  --server http://127.0.0.1:8000 \
  restore-peer \
  --peer "$PICOIN_BOOTSTRAP_PEER" \
  --height "$HEAD_HEIGHT" \
  --source phase1-full-node-restore
```

The restore must complete without HTTP `422`, network mismatch, chain mismatch, genesis mismatch, or snapshot hash mismatch.

## Verify Full Node Against Bootstrap

Run the Phase 1 verifier:

```bash
cd /opt/picoin/picoin-proof-of-pi
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

The verifier checks:

- local `/health` is `ok`
- local replay is `healthy`
- no local replay divergence is detected
- local `/audit/full` is valid
- local and peer `network_id`, `chain_id`, `genesis_hash`, and `protocol_version` match
- local and peer effective height/hash match, unless `PICOIN_PHASE1_ALLOWED_LAG_BLOCKS` allows temporary lag
- latest checkpoint hashes match when both nodes expose the same checkpoint height

## Restart Test

```bash
sudo systemctl restart picoin-node
sleep 20
sudo -u picoin PICOIN_ENV_FILE=/etc/picoin/picoin.env \
  bash /opt/picoin/picoin-proof-of-pi/deploy/scripts/full-node-phase1-verify.sh
```

This must pass without manual SQLite edits.

## Catch-Up Test

Stop the full node while the bootstrap advances:

```bash
sudo systemctl stop picoin-node
sleep 120
sudo systemctl start picoin-node
sleep 30
```

Then verify:

```bash
sudo -u picoin PICOIN_ENV_FILE=/etc/picoin/picoin.env \
  bash /opt/picoin/picoin-proof-of-pi/deploy/scripts/full-node-phase1-verify.sh
```

If the node legitimately lags during catch-up, temporarily allow a small lag:

```bash
sudo -u picoin PICOIN_ENV_FILE=/etc/picoin/picoin.env PICOIN_PHASE1_ALLOWED_LAG_BLOCKS=5 \
  bash /opt/picoin/picoin-proof-of-pi/deploy/scripts/full-node-phase1-verify.sh
```

Final acceptance still requires zero lag and matching tip hash.

## Wrong-Network Rejection Test

Use a deliberately wrong peer or env copy on a disposable full-node droplet only. The restore/import must fail when any of these differ:

```text
network_id
chain_id
genesis_hash
protocol_version
```

Record the failed command and error body in the Phase 1 test notes.

## Acceptance Record

Capture these outputs for each full-node droplet:

```bash
hostname -I
git -C /opt/picoin/src/PICOIN rev-parse --short HEAD
systemctl is-active picoin-node picoin-miner picoin-validator picoin-reconciler picoin-auditor
curl -sS http://127.0.0.1:8000/health | python3 -m json.tool
curl -sS http://127.0.0.1:8000/node/sync-status | python3 -m json.tool
sudo -u picoin PICOIN_ENV_FILE=/etc/picoin/picoin.env bash /opt/picoin/picoin-proof-of-pi/deploy/scripts/full-node-phase1-verify.sh
```

Only after two independent droplets pass the restore, restart, catch-up, wrong-network rejection, and verifier checks should the Phase 1 roadmap boxes be marked complete.
