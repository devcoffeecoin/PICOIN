# Picoin Pool Alpha

`picoin-pool/` is optional community software. It does not change Picoin consensus, validator rules, mining rewards, or mainnet APIs. To mainnet, the pool is just one normal miner identity. Pool workers connect to the pool, not to mainnet, and the pool operator decides how to distribute any reward the pool earns.

## How It Works

1. The pool server owns one normal Picoin miner identity.
2. The pool server asks mainnet for one task from `/tasks/next`.
3. The pool splits that task range into smaller chunks.
4. Pool workers calculate chunks and submit them back to the pool.
5. The pool verifies each chunk, assembles the full segment, signs `/tasks/commit`, and signs `/tasks/reveal`.
6. If mainnet accepts the reveal or creates a validation job, the pool records credited shares for the workers that contributed chunks.
7. For accepted blocks, `/stats` and `/payouts` calculate each worker's pending payout from the accepted reward and credited units.

This means the pool does not multiply mining identities. It gives a community a way to share one miner's reward internally if that pool miner wins a block.

## Security Model

- Workers never receive the pool miner private key.
- The pool operator controls the mainnet reward wallet.
- Payouts are calculated and exposed by the pool, but transfers are not automatic in this alpha. The pool operator still sends payout transactions.
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
  --chunk-size 1 \
  --public-workers \
  --pool-fee-percent 0
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
  --loops 999999 \
  --sleep 1
```

For a private pool, add the same `--auth-token` value to both the server and each worker.

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
- Payout accounting is transparent, but payout transactions are still operator-controlled.
- A pool is one mainnet miner identity, so it can still receive `429 Too Many Requests` or wait for the next competitive block like any other miner.
- If mainnet task sizes are small, pooling mostly helps reward sharing. It does not make one identity mathematically equivalent to many independent miners.
- Production pool operators should add HTTPS, monitoring, payout policy, abuse controls, backups, and clear community rules before accepting public workers.
