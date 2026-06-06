# Picoin Pool Alpha

`picoin-pool/` is optional community software. It does not change Picoin consensus, validator rules, mining rewards, or mainnet APIs. To mainnet, the pool is just one normal miner identity. Pool workers connect to the pool, not to mainnet, and the pool operator decides how to distribute any reward the pool earns.

## How It Works

1. The pool server owns one normal Picoin miner identity.
2. The pool server asks mainnet for one task from `/tasks/next`.
3. The pool chooses a mining mode and either splits that task range into chunks or runs a full-task race.
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

## Pool Mining Modes

Pool modes are operator policy. They do not change mainnet consensus, mainnet task assignment, validator behavior, or the public reward paid to the pool miner identity.

Choose the mode with the pool server's `--chunk-size` argument.

### Auto

Use:

```text
--chunk-size auto
```

`auto` creates one useful chunk per task unit:

```text
10 task units -> 10 chunks
```

Workers ask for a chunk, compute it, submit it, and immediately ask for more. A worker earns shares only for chunks it completes first:

```text
credited_shares = completed_chunk_units
```

Example:

```text
worker-a completes 4 one-unit chunks -> 4 shares
worker-b completes 6 one-unit chunks -> 6 shares
```

If all chunks are already assigned and another worker asks for work, the pool may hand out a speculative duplicate attempt over an assigned chunk. The first complete submit wins that chunk. Later duplicate submits are marked `stale` and receive 0 shares.

`auto` is simple and fair when there are enough active workers to cover most task units quickly.

### Hybrid Race

Use:

```text
--chunk-size hybrid-race
```

`hybrid-race` switches per task.

If there are more task units than active workers:

```text
task_units > active_workers
```

the pool uses `full_task_race`. It creates one chunk containing the full task range and lets workers race over that same full range:

```text
10 task units, 3 active workers -> 1 full-task chunk
```

The first complete submit wins internally and triggers commit/reveal. The winner earns:

```text
winner_shares = task_units
```

Other complete submits that arrive while the task is still open receive time-weighted shares:

```text
late_worker_shares = floor(task_units * winner_compute_ms / worker_compute_ms)
```

Workers that do not submit before the task closes receive 0 shares.

Example:

```text
task_units = 10
winner_compute_ms = 2000

worker-a submits first in 2000 ms -> 10 shares
worker-b submits in 2500 ms       -> floor(10 * 2000 / 2500) = 8 shares
worker-c submits in 5000 ms       -> floor(10 * 2000 / 5000) = 4 shares
worker-d does not submit          -> 0 shares
```

If there are enough active workers to cover the task units:

```text
task_units <= active_workers
```

the pool uses the same one-unit chunk strategy as `auto`:

```text
10 task units, 20 active workers -> 10 one-unit chunks
```

`hybrid-race` is experimental. It is useful for comparing whether a small number of workers do better racing the full range or splitting the range into one-unit chunks.

### Fixed Chunk Size

Use a positive integer:

```text
--chunk-size 5
```

This forces fixed chunks:

```text
20 task units -> 4 chunks of up to 5 units
```

Fixed chunks are useful for controlled private testing, but public pools should usually prefer `auto` or `hybrid-race`.

### Speculative Assignment

Speculative assignment is enabled by default. It applies when a worker asks for work but all available chunks have already been assigned and not yet completed.

The pool can then return an already-assigned chunk with:

```text
assignment_mode = speculative
```

The first complete submit wins the chunk. Late duplicate submits receive:

```text
status = stale
credited_shares = 0
```

To disable duplicate assignment:

```text
--disable-speculative-chunks
```

### Share Accounting

For `auto`, fixed-size chunks, and the one-unit branch of `hybrid-race`:

```text
shares = completed_chunk_units
```

For the full-task branch of `hybrid-race`:

```text
winner_shares = task_units
late_worker_shares = floor(task_units * winner_compute_ms / worker_compute_ms)
```

The pool does not create extra mainnet rewards. If the pool wins a block, accepted-block payouts are calculated from the pool reward, minus the configured pool fee, divided by credited shares:

```text
worker_reward = reward_after_fee * worker_shares / total_credited_shares
```

When the pool is started with `--trust-workers`, the coordinator does not recalculate every worker submission. It still checks basic shape, records shares, assembles the pool result, and lets mainnet validators verify the final commit/reveal.

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
