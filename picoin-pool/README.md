# Picoin Pool Alpha

`picoin-pool/` is optional community software. It does not change Picoin consensus, validator rules, mining rewards, or mainnet APIs. To mainnet, the pool is just one normal miner identity. Pool workers connect to the pool, not to mainnet, and the pool operator decides how to distribute any reward the pool earns.

## How It Works

1. The pool server owns one normal Picoin miner identity.
2. The pool server asks mainnet for one task from `/tasks/next`.
3. The pool splits that task range into smaller chunks.
4. Pool workers calculate chunks and submit them back to the pool.
5. The pool verifies each chunk, assembles the full segment, signs `/tasks/commit`, and signs `/tasks/reveal`.
6. If mainnet accepts the reveal or creates a validation job, the pool records credited shares for the workers that contributed chunks.

This means the pool does not multiply mining identities. It gives a community a way to share one miner's reward internally if that pool miner wins a block.

## Security Model

- Workers never receive the pool miner private key.
- The pool operator controls the mainnet reward wallet.
- Payouts are not automatic in this alpha. Use `/stats` and the SQLite share ledger to calculate manual or off-chain payouts.
- Run the pool privately, behind a firewall or reverse proxy. Use `--auth-token` for workers.
- The server verifies worker chunks by default. Use `--trust-workers` only on a trusted private network.

## Start A Pool Server

From the repository root:

```bash
python picoin-pool/pool_server.py \
  --server https://api.picoin.science \
  --identity picoin-pool/pool_identity.json \
  --host 0.0.0.0 \
  --port 9321 \
  --chunk-size 2 \
  --auth-token CHANGE_ME_PRIVATE_POOL_TOKEN
```

The first run auto-registers `pool_identity.json` as a normal miner, using the same official miner registration flow.

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
  --auth-token CHANGE_ME_PRIVATE_POOL_TOKEN \
  --loops 999999 \
  --sleep 1
```

## Inspect Pool State

```bash
curl http://127.0.0.1:9321/health
curl http://127.0.0.1:9321/stats
sqlite3 picoin-pool/pool.sqlite3 "select worker_id, sum(units) from pool_shares where credited=1 group by worker_id;"
```

For a public deployment behind nginx:

```bash
curl https://pool1.picoin.science/health
curl https://pool1.picoin.science/stats
curl https://picoin.science/api/pool1/stats
```

Deployment templates are included in:

```text
picoin-pool/deploy/picoin-pool.service.example
picoin-pool/deploy/nginx-pool1.conf.example
```

## Current Limits

- This is an alpha pool coordinator, not a custody or payout product.
- A pool is one mainnet miner identity, so it can still receive `429 Too Many Requests` or wait for the next competitive block like any other miner.
- If mainnet task sizes are small, pooling mostly helps reward sharing. It does not make one identity mathematically equivalent to many independent miners.
- Production pool operators should add HTTPS, monitoring, payout policy, abuse controls, backups, and clear community rules before accepting public workers.
