# Picoin Exchange And Infrastructure Full Node

This runbook is Phase 7 of the decentralization roadmap. It gives exchanges, custodians, explorers, payment processors, and infrastructure operators a local Picoin full node so they do not depend on the public API server for balances, deposits, withdrawals, confirmations, block data, health, or audit checks.

For current mainnet operation, treat `https://api.picoin.science` as one bootstrap
peer, not as the only trusted source. A full node may restore from any healthy
mainnet peer that reports the same `network_id`, `chain_id`, `genesis_hash`,
healthy replay, and no divergence.

This profile is a full node by default. It runs:

- `picoin-node`
- `picoin-reconciler`

It keeps these services disabled unless the operator explicitly opts in:

- `picoin-miner`
- `picoin-validator`
- `picoin-auditor`

## Phase 7 Validation

Phase 7 was validated on a clean mainnet full-node droplet on June 7, 2026:

- Node id: `exchange-full-node-test-1`
- Public address: `http://165.22.238.210:8000`
- Runtime services: `picoin-node` and `picoin-reconciler`
- Disabled services: `picoin-miner`, `picoin-validator`, and `picoin-auditor`
- Snapshot restore height: `10508`
- Local catch-up height: `10510`
- Reference bootstrap height during smoke: `10511`
- Lag: `1` block, within the `5` block smoke-test limit
- Replay: `healthy`
- Divergence: `false`
- Phase 7 smoke: `status=ok`, `errors=0`

When a node is restored from a snapshot, balances are proven at the snapshot height and later blocks are replayed from there. Pre-snapshot transaction and account-history rows are not treated as archival proof; the smoke test skips pre-snapshot transaction samples and compares exchange reads only for post-snapshot activity.

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

For code refreshes on an existing full node, prefer rerunning the installer or `deploy/scripts/refresh-code.sh`. Do not run a raw `rsync --delete` into `/opt/picoin/picoin-proof-of-pi` unless `data/`, `backups/`, `test-output/`, and `.venv/` are excluded or recreated before restarting systemd. The service sandbox declares those paths in `ReadWritePaths`, and systemd fails with `status=226/NAMESPACE` if any declared writable path is missing.

## Update An Existing Exchange Node

If an exchange or infrastructure node was installed before the current exchange-history cache, update the source tree and verify that systemd is running the updated code path. A common failure mode is that `/opt/picoin/src/PICOIN` is updated, but `picoin-node` still runs an older copy from `/opt/picoin/picoin-proof-of-pi`.

Run this on the exchange node:

```bash
cd /opt/picoin/src/PICOIN || exit 1

git fetch origin
git reset --hard origin/main

echo "===== VERSION ====="
git rev-parse --short HEAD
git log --oneline -3
```

For peer-backed exchange transaction history, the node must be at commit `7fb3cb2` or newer:

```text
7fb3cb2 Allow peer-backed exchange history cache
```

Point `picoin-node` at the updated source tree while keeping the existing runtime virtualenv and existing `/etc/picoin/picoin.env` data paths:

```bash
sudo mkdir -p /etc/systemd/system/picoin-node.service.d

sudo tee /etc/systemd/system/picoin-node.service.d/zzzz-use-src-main.conf >/dev/null <<'EOF'
[Service]
WorkingDirectory=/opt/picoin/src/PICOIN/picoin-proof-of-pi
Environment=PYTHONPATH=/opt/picoin/src/PICOIN/picoin-proof-of-pi
ExecStart=
ExecStart=/opt/picoin/picoin-proof-of-pi/.venv/bin/python -m picoin node start --host 0.0.0.0 --port 8000
EOF

sudo systemctl daemon-reload
sudo systemctl restart picoin-node
sleep 60
```

Verify that the running service uses the updated working directory:

```bash
systemctl show picoin-node \
  -p WorkingDirectory \
  -p ExecStart \
  -p EnvironmentFiles \
  --no-pager
```

Expected:

```text
WorkingDirectory=/opt/picoin/src/PICOIN/picoin-proof-of-pi
```

If `/transactions/history` returns `404`, the node is still running old code. Recheck `WorkingDirectory`, `ExecStart`, restart `picoin-node`, and confirm the source commit again.

If the node returns `500` after changing the working directory, confirm that the data directory and database are still loaded from `/etc/picoin/picoin.env` and not from the repository directory:

```bash
ENV=$(systemctl show picoin-node -p EnvironmentFiles --value | awk '{print $1}')
[ -f "$ENV" ] || ENV=/etc/picoin/picoin.env

sudo grep -E '^(PICOIN_DATA_DIR|PICOIN_DB_PATH)=' "$ENV" || true
sudo find /var/lib/picoin /opt/picoin -name "picoin.sqlite3" -printf "%s %p\n" 2>/dev/null | sort -nr | head -10
```

The node should use the production database path from the env file, for example:

```text
PICOIN_DATA_DIR=/var/lib/picoin/data
PICOIN_DB_PATH=/var/lib/picoin/data/picoin.sqlite3
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

The preferred current path is direct peer restore:

```bash
cd /opt/picoin/picoin-proof-of-pi

set -a
. /etc/picoin/picoin.env
set +a
export PICOIN_HTTP_TIMEOUT_SECONDS=300

PEER=https://api.picoin.science

sudo systemctl stop picoin-reconciler || true

sudo -u picoin -E env \
  PICOIN_HTTP_TIMEOUT_SECONDS=300 \
  .venv/bin/python -m picoin node checkpoint restore-peer \
  --peer "$PEER" \
  --source exchange-full-node-peer-restore

sudo systemctl restart picoin-node
sleep 25
sudo systemctl start picoin-reconciler || true
```

If the host uses the repository venv instead of the runtime tree, use:

```bash
PY=/opt/picoin/src/PICOIN/.venv/bin/python
sudo -u picoin -E env PICOIN_HTTP_TIMEOUT_SECONDS=300 "$PY" -m picoin node checkpoint restore-peer \
  --peer "$PEER" \
  --source exchange-full-node-peer-restore
```

The file-based snapshot flow below is still useful when direct export times out
or when operators want to archive the exact snapshot document before applying it.

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

The smoke test checks local health, protocol identity, replay divergence, height lag, tip block readability, optional local-vs-reference account parity, transaction parity, and account-history parity. When the local node was restored from a snapshot, automatic transaction and history samples are limited to activity after the snapshot base because the snapshot proves balances at the restore height, not full pre-snapshot archival history.

## Local API For Operators

Use local `127.0.0.1` for internal systems:

```text
GET /health
GET /protocol
GET /node/sync-status
GET /blocks?limit=10
GET /blocks/{height}/finality
GET /tx/{tx_hash}
GET /transactions/history?address={address}&limit=100
GET /wallet/{address}/transactions?limit=100
GET /wallet/balance/{address}
GET /accounts/{address}
GET /accounts/{address}/history?limit=100
GET /wallet/{address}/nonce
GET /mempool?status=pending&limit=100
GET /mempool/inventory?status=pending&limit=100
```

For public exposure, put nginx or another reverse proxy in front of the node. Do not expose wallet private keys or identity JSON files through web roots.

### Address Transaction History

For exchange deposit and withdrawal history, use the exchange-style history endpoint:

```bash
ADDRESS="PI..."
curl -sS "http://127.0.0.1:8000/transactions/history?address=$ADDRESS&limit=50&confirmed_only=true" \
  | python3 -m json.tool
```

Equivalent wallet-scoped alias:

```bash
curl -sS "http://127.0.0.1:8000/wallet/$ADDRESS/transactions?limit=50&confirmed_only=true" \
  | python3 -m json.tool
```

Local exchange nodes can answer this endpoint even when they were restored from a canonical snapshot:

- Post-snapshot transactions are accepted into the local history cache only when the local canonical block contains the transaction hash.
- Historical confirmed transactions that are not present in the local detailed ledger can be imported from configured peers as read-only history and are returned with `archival_peer_backfill=true`.
- This cache is not part of consensus. It never changes blocks, balances, nonces, replay, state roots, mining, or validator decisions.
- Peer backfill is off by default so normal exchange polling stays fast and local. To fetch archival peer rows for one request, add `backfill=true`.
- Add `confirmed_only=true` for deposit processing so pending, failed, expired, or unconfirmed transactions without `block_height` are not returned.

For an address whose older transaction rows are missing locally, run a one-time peer backfill:

```bash
curl -sS "http://127.0.0.1:8000/transactions/history?address=$ADDRESS&limit=50&confirmed_only=true&backfill=true" \
  | python3 -m json.tool
```

After a successful backfill, use the local-only call for normal polling:

```bash
curl -sS "http://127.0.0.1:8000/transactions/history?address=$ADDRESS&limit=50&confirmed_only=true" \
  | python3 -m json.tool
```

Rows imported from a peer are marked like this:

```json
{
  "source": "history_cache",
  "status": "confirmed",
  "block_height": 16464,
  "confirmations": 1001,
  "verified_local_inclusion": false,
  "archival_peer_backfill": true,
  "history_cache_source_peer": "https://api.picoin.science",
  "note": "archival transaction details were imported from a peer as read-only history; local consensus state and balances are not changed by this cache row"
}
```

For customer deposit crediting, require:

- `status=confirmed`
- `block_height` present
- `confirmations` at or above the exchange policy
- `tx_hash`, `sender`, `recipient`, and `amount` present

The PHP exchange helper exposes the same read:

```php
$history = get_picoin_transactions('PI...', 'http://127.0.0.1:8000', 50);
if (!$history['success']) {
    throw new RuntimeException($history['error']);
}
foreach ($history['transactions'] as $tx) {
    // Use status=confirmed and confirmations >= your policy before crediting.
}
```

Each returned item includes the fields exchanges normally need:

```text
tx_hash
tx_type
sender
recipient
direction
amount
fee
status
block_height
confirmations
timestamp
```

Use `status=confirmed` plus the exchange's required confirmation count before crediting a customer deposit.

### Balance Reads

Use the local node for balance reads after `/node/sync-status` reports `replay.sync_status=healthy`, `divergence_detected=false`, and `readiness.tx_submit_ready=true`:

```bash
ADDRESS="PI..."
curl -sS "http://127.0.0.1:8000/wallet/balance/$ADDRESS" \
  | python3 -m json.tool
```

For a valid PI wallet address with no local balance row yet, the endpoint returns `balance=0.0` and `balance_units=0` instead of a 404. This is intentional for exchange deposit wallets: a newly generated address is valid even before it receives its first transaction.

The PHP exchange helper includes the same call:

```php
$balance = get_picoin_balance('PI...', 'http://127.0.0.1:8000');
if (!$balance['success']) {
    throw new RuntimeException($balance['error']);
}
echo $balance['balance'];
```

Do not use these older endpoints as the primary exchange transaction history:

- `/transactions/recent` is a mempool/recent-activity endpoint. It may be empty for an address even when the address has ledger state.
- `/transactions/{address}` returns raw ledger entries for backward compatibility. It is useful for audit detail, but it is not the exchange-style transaction-history format.
- `/accounts/{address}/history` also returns raw ledger entries.

If a node was restored from a canonical snapshot, historical per-transaction rows before the snapshot may not exist locally. In that case the history endpoint returns a `snapshot_state_import` item with `tx_hash=null` and `related_id` set to the snapshot/state reference. This proves the imported balance at the snapshot height, but it is not an original deposit transaction. Post-snapshot transfers confirmed by the local node include the normal transaction hash and confirmation count.

When peer backfill is enabled, the node may replace that snapshot placeholder or empty local history with confirmed transfer rows fetched from a trusted peer. Those rows are marked as read-only archival history, while local consensus validation remains unchanged.

Optional history backfill environment variables:

```text
PICOIN_HISTORY_BACKFILL_ENABLED=1
PICOIN_HISTORY_BACKFILL_TIMEOUT_SECONDS=2
PICOIN_HISTORY_BACKFILL_MAX_PEERS=2
PICOIN_HISTORY_BACKFILL_MIN_INTERVAL_SECONDS=300
```

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
sudo -u picoin .venv/bin/python -m picoin tx \
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

## Phase 8 Withdrawal Write-Path Smoke

Use the Phase 8 smoke before enabling an exchange or payment processor withdrawal path. By default it is a safe preflight and does not spend funds:

```bash
cd /opt/picoin/picoin-proof-of-pi

sudo -u picoin -E python3 deploy/scripts/phase8-exchange-withdrawal-smoke.py \
  --local http://127.0.0.1:8000 \
  --reference https://api.picoin.science \
  --wallet /var/lib/picoin/exchange-wallets/hot-wallet.json \
  --to PI_DESTINATION_ADDRESS \
  --amount 0.001 \
  --fee 0.001
```

The preflight checks local/reference health, protocol identity, replay divergence, height lag, wallet address/public-key match, wallet network/chain metadata, local/reference wallet balance parity, sufficient local balance, and local/reference nonce parity.

To send a real signed withdrawal through the local full node and require the reference bootstrap to see the same transaction:

```bash
sudo -u picoin -E python3 deploy/scripts/phase8-exchange-withdrawal-smoke.py \
  --local http://127.0.0.1:8000 \
  --reference https://api.picoin.science \
  --wallet /var/lib/picoin/exchange-wallets/hot-wallet.json \
  --to PI_DESTINATION_ADDRESS \
  --amount 0.001 \
  --fee 0.001 \
  --submit \
  --require-reference-seen \
  --wait-seconds 180
```

For a final block-inclusion drill, keep mining active and require confirmation parity:

```bash
sudo -u picoin -E python3 deploy/scripts/phase8-exchange-withdrawal-smoke.py \
  --local http://127.0.0.1:8000 \
  --reference https://api.picoin.science \
  --wallet /var/lib/picoin/exchange-wallets/hot-wallet.json \
  --to PI_DESTINATION_ADDRESS \
  --amount 0.001 \
  --fee 0.001 \
  --submit \
  --require-reference-seen \
  --require-confirmed \
  --wait-seconds 900
```

The report intentionally prints only public wallet metadata, balances, nonces, transaction hash, and transaction status. It never prints the wallet private key.

## Phase 8 Validation

Phase 8 was validated on mainnet on June 8, 2026 UTC:

- Hot wallet: `PIFDE77B556D494F5DE4B1523D8CDDDCA961A76151E8C761`
- Recipient: `PI3BEE15E913DD2BF4D1194E47B5127339D1AAF79C976CE2`
- Transaction: `3f12faa6f9bd9a6455d7749f0437270cb1d5bf361696b8715b4fe4fdc13f45e3`
- Amount: `0.001` PI
- Transaction fee: `0.001` PI
- Confirmed block: `10563`
- Confirmed at: `2026-06-08T00:01:30.291963Z`
- Local/reference nonce after confirmation: `confirmed_nonce=1`, `pending_nonce=0`, `next_nonce=2`

The public exchange/full-node profile still runs read-only services by default. Signed withdrawals are an explicit operator workflow: the operator must provide a private hot-wallet JSON, run the Phase 8 smoke, and choose `--submit` deliberately.

## Service Preflight

Before restarting services after a manual code refresh, verify systemd writable paths:

```bash
cd /opt/picoin/picoin-proof-of-pi
sudo deploy/scripts/picoin-service-preflight.sh --fix --repo-dir /opt/picoin/picoin-proof-of-pi
sudo systemctl daemon-reload
sudo systemctl restart picoin-node picoin-reconciler
```

`refresh-code.sh` runs this preflight automatically. This prevents `status=226/NAMESPACE` failures when systemd `ReadWritePaths` directories such as `data/`, `backups/`, or `test-output/` are missing.

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
