# Picoin Pool Alpha

`picoin-pool/` is optional community software. It does not change Picoin consensus, validator rules, mining rewards, or mainnet APIs. To mainnet, the pool is just one normal miner identity. Pool workers connect to the pool, not to mainnet, and the pool operator decides how to distribute any reward the pool earns.

## How It Works

1. The pool server owns one normal Picoin miner identity.
2. The pool server asks mainnet for one task from `/tasks/next`.
3. The pool splits that task range into smaller chunks.
4. Pool workers calculate chunks and submit them back to the pool.
5. The pool verifies each chunk, assembles the full segment, signs `/tasks/commit`, and signs `/tasks/reveal`.
6. If mainnet accepts the reveal or creates a validation job, the pool records credited shares for the workers that contributed chunks.
7. For accepted blocks, `/stats` and `/payouts` calculate each worker's pending payout from the accepted reward and credited units, after the pool operator fee.
8. If a payout wallet is configured, the pool automatically sends worker payouts on the configured interval once a worker reaches the minimum payout.

This means the pool does not multiply mining identities. It gives a community a way to share one miner's reward internally if that pool miner wins a block.

## Security Model

- Workers never receive the pool miner private key.
- The pool operator controls the mainnet reward wallet and the payout wallet.
- Payouts are calculated and exposed by the pool. Automatic payout transfers are enabled only when the operator starts the server with `--payout-wallet`.
- The default pool operator fee is 1%.
- Public pools should use `--public-workers`, which lets workers register with only a worker id and a valid PI payout wallet.
- Private pools can still use `--auth-token` for workers.
- The server verifies worker chunks by default. Use `--trust-workers` only on a trusted private network.

## Start A Pool Server

From the repository root:

```bash
python picoin-pool/pool_server.py \
  --server https://api.picoin.science \
  --identity picoin-pool/pool_identity.json \
  --host 0.0.0.0 \
  --port 9321 \
  --pool-name pool1 \
  --chunk-size auto \
  --poll-seconds 2 \
  --public-workers \
  --pool-fee-percent 1 \
  --payout-wallet /var/lib/picoin-pool/payout_wallet.json \
  --payout-interval-seconds 7200 \
  --payout-min-amount 0.1
```

The first run auto-registers `pool_identity.json` as a normal miner, using the same official miner registration flow.
If `--payout-wallet` is omitted, automatic transfers are disabled and the pool only reports pending payout balances.
With `--chunk-size auto`, the pool creates one useful chunk per task unit: 10 units become 10 chunks, no matter how many workers are online. Fast workers finish a chunk and immediately ask for another. If all chunks are already assigned and more workers ask for work, the pool can hand out speculative duplicate attempts over the assigned chunks. The first valid submit completes the chunk and receives the share; later duplicate submits are marked stale and receive no share.

With `--chunk-size hybrid-race`, the pool switches per task:

- If `task_units > active_workers`, it creates one full-task chunk and gives that same full range to workers as a race. The first complete submit wins `task_units` shares and triggers commit/reveal. Other complete submits that arrive while the task is still open receive `floor(task_units * winner_compute_ms / worker_compute_ms)` shares. Workers that do not submit before the task closes receive 0.
- If `task_units <= active_workers`, it creates one chunk per unit, the same as `auto`.

Operators can pass a positive integer to force fixed chunk sizes, or `--disable-speculative-chunks` to turn off duplicate assignment.

For the first hosted pool, the intended public API is:

```text
https://pool1.picoin.science
```

The public stats page is served by the website at:

```text
https://picoin.science/pool1
```

## Start A Worker

```bash
python picoin-pool/pool_worker.py \
  --pool http://POOL_SERVER_IP:9321 \
  --worker-id alice-rig-1 \
  --payout-address PI_YOUR_PAYOUT_ADDRESS \
  --loops 999999 \
  --sleep 1
```

For a private pool, add the same `--auth-token` value to both the server and each worker.

## Mine In A Public Pool From Linux

Linux workers do not need a validator key and do not need the pool miner private key. A worker only needs:

- Linux with Python 3,
- this repository,
- a PI payout wallet address,
- the public pool URL, for example `https://pool1.picoin.science`.

Install the basic packages:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv ca-certificates
```

Download or update the repository:

```bash
sudo mkdir -p /opt/picoin/src
sudo git clone https://github.com/devcoffeecoin/PICOIN.git /opt/picoin/src/PICOIN
cd /opt/picoin/src/PICOIN
```

If the repository already exists:

```bash
cd /opt/picoin/src/PICOIN
git pull origin main
```

For a fresh Linux worker, create the Python environment:

```bash
cd /opt/picoin/src/PICOIN
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r picoin-proof-of-pi/requirements.txt
```

The recommended way to run a Linux pool worker is as a systemd service. The installer script creates or updates `picoin-pool-worker.service`:

```bash
cd /opt/picoin/src/PICOIN

sudo bash picoin-pool/deploy/install-pool-worker-service.sh \
  --pool https://pool1.picoin.science \
  --worker-id pool1-my-linux-rig \
  --name my-linux-rig \
  --payout-address PI_YOUR_PAYOUT_ADDRESS \
  --install-deps
```

For a public pool running with `--public-workers`, no token is needed. For a private pool, add:

```bash
  --auth-token PRIVATE_POOL_TOKEN
```

If the machine is already a Picoin full node or validator and you want to stop only the old direct miner while keeping validation online, add:

```bash
  --disable-direct-miner
```

That flag stops and disables only `picoin-miner`. It does not stop `picoin-node` or `picoin-validator`.

The service generated by the script is equivalent to:

```ini
[Unit]
Description=Picoin Pool Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=picoin
Group=picoin
WorkingDirectory=/opt/picoin/src/PICOIN
Environment="PICOIN_CORE_PATH=/opt/picoin/src/PICOIN/picoin-proof-of-pi"
Environment="PICOIN_POOL_PAYOUT_ADDRESS=PI_YOUR_PAYOUT_ADDRESS"
ExecStart=/opt/picoin/src/PICOIN/.venv/bin/python /opt/picoin/src/PICOIN/picoin-pool/pool_worker.py --pool https://pool1.picoin.science --worker-id pool1-my-linux-rig --name my-linux-rig --payout-address PI_YOUR_PAYOUT_ADDRESS --loops 999999999 --sleep 1 --request-timeout 90
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Check the worker:

```bash
systemctl is-active picoin-pool-worker
sudo journalctl -u picoin-pool-worker -n 80 --no-pager -l
```

Normal worker logs include either:

```text
Pool idle: no pool work available
```

or:

```text
Submitted chunk_... start..end units=... compute_ms=...
```

`Pool idle` means the pool currently has no chunk available or is waiting for mainnet to assign the pool miner a new task. It does not mean the worker service is broken.

To update a Linux worker:

```bash
cd /opt/picoin/src/PICOIN
git pull origin main
sudo systemctl restart picoin-pool-worker
```

To stop mining in the pool:

```bash
sudo systemctl stop picoin-pool-worker
sudo systemctl disable picoin-pool-worker
```

## Desktop Miner Pool Mode

The Desktop Miner supports two mining modes:

- `Direct`: mines directly against the selected Picoin API node.
- `Pool`: registers the desktop as a pool worker and mines chunks from the selected Pool URL.

For Pool mode, users only need:

- their PI payout wallet,
- a worker/miner display name,
- the pool URL, for example `https://pool1.picoin.science`.

No shared token is required when the pool server is running with `--public-workers`.

## Inspect Pool State

```bash
curl http://127.0.0.1:9321/health
curl http://127.0.0.1:9321/stats
curl http://127.0.0.1:9321/payouts
sqlite3 picoin-pool/pool.sqlite3 "select worker_id, sum(units) from pool_shares where credited=1 group by worker_id;"
sqlite3 picoin-pool/pool.sqlite3 "select worker_id, payout_address, amount, tx_hash, status, created_at from pool_payouts order by created_at desc limit 10;"
```

For a public deployment behind nginx:

```bash
curl https://pool1.picoin.science/health
curl https://pool1.picoin.science/stats
curl https://pool1.picoin.science/payouts
curl https://picoin.science/api/pool1/stats
```

Deployment templates are included in:

```text
picoin-pool/deploy/picoin-pool.service.example
picoin-pool/deploy/nginx-pool1.conf.example
```

## Current Limits

- This is an alpha pool coordinator, not a custody product.
- Automatic payouts require a configured payout wallet file on the pool server.
- A pool is one mainnet miner identity, so it can still receive `429 Too Many Requests` or wait for the next competitive block like any other miner.
- If mainnet task sizes are small, pooling mostly helps reward sharing. It does not make one identity mathematically equivalent to many independent miners.
- Production pool operators should add HTTPS, monitoring, payout policy, abuse controls, backups, and clear community rules before accepting public workers.
