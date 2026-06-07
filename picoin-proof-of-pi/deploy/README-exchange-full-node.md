# Picoin Exchange And Infrastructure Full Node

This runbook is Phase 7 of the decentralization roadmap. It gives exchanges, custodians, explorers, payment processors, and infrastructure operators a local Picoin full node so they do not depend on the public API server for balances, deposits, withdrawals, confirmations, block data, health, or audit checks.

This profile is a full node by default. It runs:

- `picoin-node`
- `picoin-reconciler`

It keeps these services disabled unless the operator explicitly opts in:

- `picoin-miner`
- `picoin-validator`
- `picoin-auditor`

## Install

Run on a clean Ubuntu 22.04/24.04 server:

```bash
sudo apt-get update
sudo apt-get install -y git curl ca-certificates

git clone https://github.com/devcoffeecoin/PICOIN.git /opt/picoin/src/PICOIN
cd /opt/picoin/src/PICOIN/picoin-proof-of-pi

sudo deploy/scripts/install-mainnet-full-node.sh \
  --branch main \
  --node-id exchange-full-node-1 \
  --node-address https://node.example.com \
  --bootstrap-peer https://api.picoin.science \
  --start
```

For a test branch:

```bash
sudo deploy/scripts/install-mainnet-full-node.sh \
  --branch codex/decentralization-roadmap \
  --node-id exchange-full-node-1 \
  --node-address http://YOUR_PUBLIC_IP:8000 \
  --bootstrap-peer https://api.picoin.science \
  --start
```

The installer creates or refreshes:

```text
/opt/picoin/src/PICOIN
/opt/picoin/picoin-proof-of-pi
/var/lib/picoin/data-mainnet-exchange-full-node
/var/backups/picoin-mainnet-exchange-full-node
/etc/picoin/picoin.env
```

## Verify Sync

```bash
curl -sS http://127.0.0.1:8000/health | python3 -m json.tool
curl -sS http://127.0.0.1:8000/protocol | python3 -m json.tool
curl -sS http://127.0.0.1:8000/node/sync-status | python3 -m json.tool
curl -sS http://127.0.0.1:8000/audit/full | python3 -m json.tool
```

Healthy minimum:

- `/health` returns `status=ok` or a known degraded reason that does not include replay divergence.
- `/node/sync-status` reports `replay.sync_status=healthy`.
- `divergence_detected=false`.
- `effective_latest_block_height` is catching up toward peers.

For this read-only profile, `/health` may report `status=degraded` with only `not enough eligible validators for quorum`. That is acceptable because the exchange full node does not mine or validate. It must still report healthy replay and no divergence.

## Fast Sync From Bootstrap Snapshot

For a clean exchange/full-node install, prefer canonical snapshot restore before block replay. This avoids replaying the whole historical chain and gives the node a verified local state base.

Download and validate the bootstrap snapshot:

```bash
cd /opt/picoin/picoin-proof-of-pi

curl -sS --max-time 300 \
  -o /tmp/mainnet-snapshot.json \
  https://api.picoin.science/node/snapshots/export

python3 - <<'PY'
import json
d=json.load(open("/tmp/mainnet-snapshot.json"))
c=d.get("checkpoint") or {}
print("valid=", d.get("valid"))
print("issues=", d.get("issues"))
print("height=", c.get("height"))
print("snapshot_hash=", c.get("snapshot_hash"))
PY
```

Import and restore the exact file that was validated:

```bash
cd /opt/picoin/picoin-proof-of-pi

set -a
. /etc/picoin/picoin.env
set +a
export PICOIN_HTTP_TIMEOUT_SECONDS=300

sudo systemctl stop picoin-reconciler

sudo -u picoin -E .venv/bin/python -m picoin node checkpoint \
  --server http://127.0.0.1:8000 \
  import \
  --file /tmp/mainnet-snapshot.json \
  --source phase7-exchange-full-node-bootstrap-file \
  | tee /tmp/phase7-import.json

SNAPSHOT_HASH=$(python3 - <<'PY'
import json
d=json.load(open("/tmp/phase7-import.json"))
print((d.get("snapshot") or {}).get("snapshot_hash"))
PY
)

curl -sS -X POST \
  "http://127.0.0.1:8000/node/snapshots/$SNAPSHOT_HASH/restore" \
  | tee /tmp/phase7-restore.json \
  | python3 -m json.tool | head -160

sudo systemctl restart picoin-node
sleep 25
sudo systemctl start picoin-reconciler
```

Verify the restored state:

```bash
curl -sS http://127.0.0.1:8000/node/sync-status | python3 -c '
import json,sys
d=json.load(sys.stdin); r=d.get("replay") or {}
print("height=", d.get("effective_latest_block_height"))
print("hash=", d.get("effective_latest_block_hash"))
print("snapshot=", d.get("snapshot_height"))
print("pending=", d.get("pending_replay_blocks"))
print("queue=", r.get("queue_size"))
print("headers=", r.get("header_queue_size"))
print("replay=", r.get("sync_status"))
print("divergent=", r.get("divergence_detected"))
print("last_error=", r.get("last_error"))
'
```

## Catch Up From Bootstrap

The reconciler runs continuously, but a manual catch-up is useful after first install:

```bash
curl -sS --max-time 120 -X POST \
  "http://127.0.0.1:8000/node/reconcile?limit=16&peer_address=https://api.picoin.science" \
  | python3 -m json.tool | head -160
```

Repeat until local height is close to the bootstrap:

```bash
curl -sS http://127.0.0.1:8000/node/sync-status | python3 -c '
import json,sys
d=json.load(sys.stdin); r=d.get("replay") or {}
print("height=", d.get("effective_latest_block_height"))
print("hash=", d.get("effective_latest_block_hash"))
print("replay=", r.get("sync_status"))
print("divergent=", r.get("divergence_detected"))
'
```

Mempool duplicate or hash mismatch errors in the reconcile report are not automatically chain failures. The acceptance criteria are healthy replay, no divergence, empty replay queues, and local height within the allowed lag.

## Operational Smoke Test

Run the Phase 7 smoke test after snapshot restore and catch-up:

```bash
cd /opt/picoin/picoin-proof-of-pi

python3 deploy/scripts/phase7-exchange-full-node-smoke.py \
  --local http://127.0.0.1:8000 \
  --reference https://api.picoin.science \
  --allowed-lag 5
```

Optional account and transaction parity checks:

```bash
python3 deploy/scripts/phase7-exchange-full-node-smoke.py \
  --local http://127.0.0.1:8000 \
  --reference https://api.picoin.science \
  --allowed-lag 5 \
  --account PI_ADDRESS_TO_CHECK \
  --tx-hash TX_HASH_TO_CHECK
```

Automatic exchange-readiness sample:

```bash
python3 deploy/scripts/phase7-exchange-full-node-smoke.py \
  --local http://127.0.0.1:8000 \
  --reference https://api.picoin.science \
  --allowed-lag 5 \
  --sample-recent-txs 5 \
  --compare-history
```

The smoke test checks local health, protocol identity, replay divergence, height lag, tip block readability, optional local-vs-reference account parity, transaction parity, and account-history parity for sampled transactions.

## Local API For Operators

Use local `127.0.0.1` for internal systems:

```text
GET /health
GET /protocol
GET /node/sync-status
GET /blocks?limit=10
GET /blocks/{height}/finality
GET /tx/{tx_hash}
GET /accounts/{address}
GET /accounts/{address}/history?limit=100
GET /wallet/{address}/nonce
GET /mempool?status=pending&limit=100
GET /mempool/inventory?status=pending&limit=100
```

For public exposure, put nginx or another reverse proxy in front of the node. Do not expose wallet private keys or identity JSON files through web roots.

## Wallet Manager Commands

Create a hot wallet only on the machine that is authorized to sign withdrawals:

```bash
cd /opt/picoin/picoin-proof-of-pi
sudo install -d -o picoin -g picoin -m 0700 /var/lib/picoin/exchange-wallets

sudo -u picoin .venv/bin/python -m picoin wallet create \
  --network picoin-mainnet-v1 \
  --chain-id 314159 \
  --output /var/lib/picoin/exchange-wallets/hot-wallet.json
```

Print its address:

```bash
sudo -u picoin .venv/bin/python -m picoin wallet address \
  --wallet /var/lib/picoin/exchange-wallets/hot-wallet.json
```

Check balance:

```bash
sudo -u picoin .venv/bin/python -m picoin wallet \
  --server http://127.0.0.1:8000 \
  balance --wallet /var/lib/picoin/exchange-wallets/hot-wallet.json
```

Check nonce:

```bash
ADDRESS="PI..."
sudo -u picoin .venv/bin/python -m picoin wallet \
  --server http://127.0.0.1:8000 \
  nonce --address "$ADDRESS"
```

Submit a signed withdrawal:

```bash
TO="PI_DESTINATION_ADDRESS"
sudo -u picoin .venv/bin/python -m picoin wallet \
  --server http://127.0.0.1:8000 \
  send \
  --wallet /var/lib/picoin/exchange-wallets/hot-wallet.json \
  --to "$TO" \
  --amount 1.25 \
  --fee 0.001
```

Check transaction status:

```bash
TX_HASH="..."
sudo -u picoin .venv/bin/python -m picoin tx \
  --server http://127.0.0.1:8000 \
  status --hash "$TX_HASH"
```

## Deposit Confirmation Policy

Recommended exchange policy:

- Wait until the transaction status is `confirmed`.
- Require the block containing the transaction to remain on the canonical local chain.
- Require at least `6` local confirmations for customer credit.
- If finality certificates are enabled for the network height, verify `GET /blocks/{height}/finality` returns a certificate with `approval_count >= required_approvals`.

Confirmation count:

```text
confirmations = local_effective_latest_block_height - transaction_block_height + 1
```

Protocol reward maturity is configured by `PICOIN_BLOCK_MATURITY_DEPTH`; exchange deposit credit can be stricter than protocol maturity.

## Backup

Stop services and copy the database plus env:

```bash
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
sudo systemctl stop picoin-reconciler picoin-node
sudo install -d -m 0750 /var/backups/picoin-mainnet-exchange-full-node/$STAMP
sudo cp -a /etc/picoin/picoin.env /var/backups/picoin-mainnet-exchange-full-node/$STAMP/
sudo cp -a /var/lib/picoin/data-mainnet-exchange-full-node /var/backups/picoin-mainnet-exchange-full-node/$STAMP/
sudo systemctl start picoin-node picoin-reconciler
```

Wallet JSON files are private key material. Back them up separately with stricter controls and never commit them.

## Restore

Restore onto a stopped node:

```bash
sudo systemctl stop picoin-reconciler picoin-node
sudo cp -a /var/backups/picoin-mainnet-exchange-full-node/STAMP/data-mainnet-exchange-full-node/. \
  /var/lib/picoin/data-mainnet-exchange-full-node/
sudo chown -R picoin:picoin /var/lib/picoin/data-mainnet-exchange-full-node
sudo systemctl start picoin-node picoin-reconciler
```

Then verify:

```bash
curl -sS http://127.0.0.1:8000/node/sync-status | python3 -m json.tool
curl -sS http://127.0.0.1:8000/audit/full | python3 -m json.tool
```

## Monitoring

Monitor:

- `systemctl is-active picoin-node picoin-reconciler`
- `/health`
- `/node/sync-status`
- `/audit/full`
- local height lag versus trusted peers
- disk usage under `/var/lib/picoin`
- backup freshness

Example:

```bash
systemctl is-active picoin-node picoin-reconciler
curl -sS http://127.0.0.1:8000/node/sync-status | python3 -m json.tool
journalctl -u picoin-node -n 100 --no-pager -l
journalctl -u picoin-reconciler -n 100 --no-pager -l
```

## Troubleshooting Bootstrap Timeouts

If first catch-up reports `Read timed out` against the bootstrap peer, increase peer timeout and use smaller reconcile batches:

```bash
sudo sed -i 's/^PICOIN_GOSSIP_TIMEOUT_SECONDS=.*/PICOIN_GOSSIP_TIMEOUT_SECONDS=10.0/' /etc/picoin/picoin.env
sudo sed -i 's/^PICOIN_RECONCILER_LIMIT=.*/PICOIN_RECONCILER_LIMIT=16/' /etc/picoin/picoin.env
sudo sed -i 's/^PICOIN_RECONCILER_SLEEP_SECONDS=.*/PICOIN_RECONCILER_SLEEP_SECONDS=30/' /etc/picoin/picoin.env
sudo systemctl restart picoin-node picoin-reconciler
```

Then retry:

```bash
curl -sS --max-time 180 -X POST \
  "http://127.0.0.1:8000/node/reconcile?limit=16&peer_address=https://api.picoin.science" \
  | python3 -m json.tool | head -160
```
