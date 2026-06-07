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

## Catch Up From Bootstrap

The reconciler runs continuously, but a manual catch-up is useful after first install:

```bash
curl -sS --max-time 120 -X POST \
  "http://127.0.0.1:8000/node/reconcile?limit=64&peer_address=https://api.picoin.science" \
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
