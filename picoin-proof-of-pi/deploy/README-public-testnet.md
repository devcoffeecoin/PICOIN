# Picoin Public Testnet Onboarding

This guide prepares a public Picoin testnet node, miner, or validator for Ubuntu servers and external participants.

Public users should use DNS names and HTTPS. Do not publish raw bootstrap IP addresses in ANN posts, docs, or templates. Raw IPs are only an internal fallback/debug tool.

## Public Testnet Status

- Status: live public testnet.
- Public API: `https://api.picoin.science`
- Validator public API name: `https://validador.picoin.science`
- Protocol version: `0.18`
- Network ID: `public-testnet`
- Chain ID: `picoin-public-testnet-v018`
- Mining: enabled.
- External validation: enabled.
- Consensus approvals: public testnet quorum is configurable and currently recommended as `PICOIN_REQUIRED_VALIDATOR_APPROVALS=2`.
- Current bootstrap setup: bootstrap runs API, reconciler, and nginx. The miner was moved out of bootstrap because bootstrap RAM was almost exhausted; free memory improved from roughly 55 MiB to about 1.3 GiB.

The current setup already supports distributed validation, external validators, public HTTPS API access, public mining, consensus approvals, and public testnet participation.

## Architecture

```text
                  +----------------------+
                  | bootstrap/API/nginx  |
                  | api.picoin.science   |
                  +----------+-----------+
                             |
          +------------------+------------------+
          |                  |                  |
 +--------v-------+ +--------v-------+ +--------v-------+
 | validator-one  | | validator-two  | | validator-three|
 | miner + node   | | external node  | | external node  |
 +----------------+ +----------------+ +----------------+
```

Current validator architecture:

- One validator per machine/droplet.
- Validators are not executed sequentially in one shared loop.
- Validators communicate with the bootstrap/API through `https://api.picoin.science`.
- Droplet 1 runs bootstrap/API/reconciler/nginx.
- Droplet 2 runs `validator-one` plus the current public testnet miner.
- Droplets 3 and 4 run `validator-two` and `validator-three`.

Recommended bootstrap sizing:

- Minimum: 2 vCPU / 4 GB RAM.
- Recommended: 2-4 vCPU / 4-8 GB RAM.
- Optional swap for smaller machines:

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Future architecture goal:

- Keep miner outside the bootstrap node.
- Add public peer discovery.
- Allow community validators.
- Increase decentralization.
- Enable public peer nodes.

## Quick Start

Install the code on a fresh Ubuntu host:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip sqlite3 curl ufw
sudo useradd --system --create-home --home-dir /opt/picoin --shell /bin/bash picoin
sudo -u picoin git clone https://github.com/devcoffeecoin/PICOIN.git /opt/picoin/PICOIN
sudo -u picoin bash -lc 'cd /opt/picoin/PICOIN/picoin-proof-of-pi && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt'
```

The preferred installed path is:

```text
/opt/picoin/picoin-proof-of-pi
```

If the clone path is `/opt/picoin/PICOIN/picoin-proof-of-pi`, create the canonical symlink:

```bash
sudo -u picoin ln -s /opt/picoin/PICOIN/picoin-proof-of-pi /opt/picoin/picoin-proof-of-pi
```

Install systemd units:

```bash
sudo PICOIN_REPO_DIR=/opt/picoin/picoin-proof-of-pi \
  /opt/picoin/picoin-proof-of-pi/deploy/scripts/install-systemd-service.sh
```

Copy a role template:

```bash
cd /opt/picoin/picoin-proof-of-pi
sudo cp .env.public-testnet.example /etc/picoin/picoin.env
sudo nano /etc/picoin/picoin.env
```

Role-specific templates are available:

- `.env.node.example`
- `.env.miner.example`
- `.env.validator.example`
- `.env.public-testnet.example`

Runtime state is intentionally separated from code:

- Chain database and identities: `/var/lib/picoin/data`
- SQLite backups: `/var/backups/picoin`
- Pre-refresh state copies: `/opt/picoin/state-backups`

Public testnet env additions used by the current deployment:

```bash
PICOIN_REQUIRED_VALIDATOR_APPROVALS=2
PICOIN_SMOKE_SKIP_CATCH_UP=1
PICOIN_SMOKE_WARN_ONLY=1
PICOIN_SMOKE_TIMEOUT=60
PICOIN_AUDITOR_INTERVAL=300
PICOIN_RECONCILER_SKIP_WITHOUT_PEER=1
PICOIN_REPLAY_BATCH_SIZE=10
PICOIN_REPLAY_BACKLOG_THRESHOLD=25
PICOIN_REPLAY_STALL_FAILURES=3
PICOIN_MIN_QUORUM_PEERS=1
PICOIN_AUTO_RECOVERY_ENABLED=0
PICOIN_WALLET_PATH=/var/lib/picoin/data/wallets/default.json
PICOIN_MINER_REWARD_ADDRESS=PI...
PICOIN_VALIDATOR_REWARD_ADDRESS=PI...
```

On bootstrap, the auditor is optional and should run warning-only if enabled. Bootstrap can use local health, audit, and sync checks without a bootstrap peer; external validators should use `https://api.picoin.science` as peer.

## Run a Node

For a bootstrap/API node:

```bash
PICOIN_HOME=/opt/picoin/picoin-proof-of-pi
PICOIN_DATA_DIR=/var/lib/picoin/data
PICOIN_SERVER=http://127.0.0.1:8000
PICOIN_NODE_ID=bootstrap-nyc1-1
PICOIN_NODE_TYPE=bootstrap
PICOIN_NODE_ADDRESS=https://api.picoin.science
PICOIN_BOOTSTRAP_PEER=
PICOIN_BOOTSTRAP_PEERS=
```

For an external validator node:

```bash
PICOIN_HOME=/opt/picoin/picoin-proof-of-pi
PICOIN_DATA_DIR=/var/lib/picoin/data
PICOIN_SERVER=http://127.0.0.1:8000
PICOIN_NODE_ID=validator-yourname-1
PICOIN_NODE_TYPE=validator
PICOIN_NODE_ADDRESS=https://validador.picoin.science
PICOIN_BOOTSTRAP_PEER=https://api.picoin.science
PICOIN_BOOTSTRAP_PEERS=https://api.picoin.science
```

Manual node commands:

```bash
cd /opt/picoin/picoin-proof-of-pi
.venv/bin/python -m picoin node doctor --server http://127.0.0.1:8000
.venv/bin/python -m picoin node sync-status --server http://127.0.0.1:8000
.venv/bin/python -m picoin node catch-up \
  --server http://127.0.0.1:8000 \
  --peer https://api.picoin.science
```

Systemd:

```bash
sudo systemctl start picoin-node
sudo systemctl status picoin-node --no-pager
```

## Run a Miner

Public miners should submit work to the HTTPS bootstrap API:

```bash
PICOIN_WORKER_ROLE=miner
PICOIN_MINER_SERVER=https://api.picoin.science
PICOIN_MINER_NAME=miner-yourname
PICOIN_MINER_IDENTITY=/var/lib/picoin/data/testnet/identities/miner-yourname.json
PICOIN_MINER_REWARD_ADDRESS=PI...
PICOIN_MINER_LOOPS=1
PICOIN_MINER_SLEEP=5
PICOIN_MINER_WORKERS=1
PICOIN_WORKER_SLEEP=10
```

Manual miner command:

```bash
cd /opt/picoin/picoin-proof-of-pi
.venv/bin/python -m picoin miner \
  --server https://api.picoin.science \
  --identity /var/lib/picoin/data/testnet/identities/miner-yourname.json \
  mine \
  --loops 1 \
  --sleep 5 \
  --workers 1
```

Systemd:

```bash
sudo systemctl restart picoin-miner
sudo journalctl -u picoin-miner -f
```

## Run a Validator

Run one validator per machine. Do not reuse the same validator identity on multiple hosts.

Validator one:

```bash
PICOIN_WORKER_ROLE=validator
PICOIN_VALIDATOR_SERVER=https://api.picoin.science
PICOIN_VALIDATOR_NAME=validator-one
PICOIN_VALIDATOR_IDENTITY=/var/lib/picoin/data/testnet/identities/validator-one.json
PICOIN_VALIDATOR_REWARD_ADDRESS=PI...
PICOIN_VALIDATOR_LOOPS=1
PICOIN_VALIDATOR_SLEEP=5
PICOIN_WORKER_SLEEP=10
```

Validator two:

```bash
PICOIN_WORKER_ROLE=validator
PICOIN_VALIDATOR_SERVER=https://api.picoin.science
PICOIN_VALIDATOR_NAME=validator-two
PICOIN_VALIDATOR_IDENTITY=/var/lib/picoin/data/testnet/identities/validator-two.json
PICOIN_VALIDATOR_REWARD_ADDRESS=PI...
```

Validator three:

```bash
PICOIN_WORKER_ROLE=validator
PICOIN_VALIDATOR_SERVER=https://api.picoin.science
PICOIN_VALIDATOR_NAME=validator-three
PICOIN_VALIDATOR_IDENTITY=/var/lib/picoin/data/testnet/identities/validator-three.json
PICOIN_VALIDATOR_REWARD_ADDRESS=PI...
```

Manual validator command:

```bash
cd /opt/picoin/picoin-proof-of-pi
.venv/bin/python -m picoin validator \
  --server https://api.picoin.science \
  --identity /var/lib/picoin/data/testnet/identities/validator-three.json \
  validate \
  --loops 1 \
  --sleep 5
```

Systemd:

```bash
sudo systemctl restart picoin-validator
sudo journalctl -u picoin-validator -f
```

## Wallets and Reward Addresses

Public testnet rewards can now be paid to real Picoin wallet addresses instead of only internal operational accounts such as `miner_xxx` or `validator_xxx`.

Operational identities still sign miner/validator actions:

- Miner identity: `miner_id`, `public_key`, `private_key`
- Validator identity: `validator_id`, `public_key`, `private_key`

Economic ownership is separate:

- Miner rewards use `PICOIN_MINER_REWARD_ADDRESS`
- Validator rewards use `PICOIN_VALIDATOR_REWARD_ADDRESS`
- If a reward address is not configured, Picoin keeps legacy behavior and pays the internal `miner_xxx` or `validator_xxx` account.
- Existing balances are not migrated; only new rewards use the configured wallet address.

Create a testnet wallet:

```bash
cd /opt/picoin/picoin-proof-of-pi
.venv/bin/python -m picoin wallet create --name yourname
.venv/bin/python -m picoin wallet address
```

The default wallet path is:

```text
~/.picoin/wallets/default.json
```

Query balance and history:

```bash
.venv/bin/python -m picoin wallet balance --server https://api.picoin.science
.venv/bin/python -m picoin wallet history --server https://api.picoin.science
curl https://api.picoin.science/accounts/PI...
curl https://api.picoin.science/accounts/PI.../history
```

Send a signed testnet transaction:

```bash
.venv/bin/python -m picoin wallet send \
  --server https://api.picoin.science \
  --to PI... \
  --amount 1 \
  --fee 0.001
```

Web wallet:

```text
https://www.picoin.science/wallet
```

The web wallet belongs to the Vercel frontend in `picoin-web`, not the bootstrap/API droplet. It signs locally in the browser with WebCrypto Ed25519 when supported by the browser, and connects to the public API at `https://api.picoin.science`. The private key is not sent to the server.

Configure miner rewards:

```bash
PICOIN_MINER_REWARD_ADDRESS=PI...
```

Configure validator rewards:

```bash
PICOIN_VALIDATOR_REWARD_ADDRESS=PI...
```

You can also store `reward_address` in the identity JSON:

```json
{
  "name": "validator-yourname",
  "validator_id": "validator_your_id",
  "public_key": "ed25519:YOUR_PUBLIC_KEY",
  "private_key": "ed25519:YOUR_PRIVATE_KEY",
  "reward_address": "PI...",
  "server_url": "https://api.picoin.science",
  "created_at": "2026-01-01T00:00:00+00:00"
}
```

Testnet warning: do not reuse private keys from any mainnet or production wallet.

## Systemd Services

```bash
sudo systemctl status picoin-node --no-pager
sudo systemctl status picoin-miner --no-pager
sudo systemctl status picoin-validator --no-pager
sudo systemctl status picoin-reconciler --no-pager
sudo systemctl status picoin-auditor --no-pager
```

`picoin-miner` and `picoin-validator` both run through `deploy/scripts/picoin-worker-loop.sh`. A single polling iteration can exit non-zero when no task or validation job is ready; the wrapper keeps polling.

## Health / Sync / Audit Commands

Public API checks:

```bash
curl https://api.picoin.science/health
curl https://api.picoin.science/node/sync-status
curl https://api.picoin.science/consensus/status
curl 'https://api.picoin.science/consensus/proposals?status=pending_missing_ancestors&limit=20'
curl https://api.picoin.science/validators?limit=100
```

Fast operational checks:

```bash
curl https://api.picoin.science/node/sync-status | grep -o '"latest_block_height":[0-9]*'
curl https://api.picoin.science/node/sync-status | grep -o '"pending_missing_ancestors":[0-9]*'
curl https://api.picoin.science/consensus/status
sudo systemctl status picoin-node picoin-miner picoin-validator picoin-reconciler picoin-auditor --no-pager
free -h
```

Local node checks:

```bash
cd /opt/picoin/picoin-proof-of-pi
.venv/bin/python -m picoin node doctor
.venv/bin/python -m picoin node sync-status
.venv/bin/python -m picoin node audit
.venv/bin/python -m picoin node report \
  --server http://127.0.0.1:8000 \
  --peer https://api.picoin.science
```

Catch up a validator node from bootstrap:

```bash
cd /opt/picoin/picoin-proof-of-pi
.venv/bin/python -m picoin node catch-up \
  --server http://127.0.0.1:8000 \
  --peer https://api.picoin.science
```

Repair historical reward rows if an older node shows reward/audit mismatches:

```bash
cd /opt/picoin/picoin-proof-of-pi
.venv/bin/python -m picoin node repair-rewards --server http://127.0.0.1:8000
.venv/bin/python -m picoin node audit --server http://127.0.0.1:8000
curl http://127.0.0.1:8000/health
```

Restore a validator node from the healthy bootstrap snapshot:

```bash
cd /opt/picoin/picoin-proof-of-pi
sudo systemctl stop picoin-validator picoin-reconciler picoin-auditor picoin-miner
.venv/bin/python -m picoin node checkpoint \
  --server http://127.0.0.1:8000 \
  restore-peer \
  --peer https://api.picoin.science \
  --source bootstrap-repaired
.venv/bin/python -m picoin node catch-up \
  --server http://127.0.0.1:8000 \
  --peer https://api.picoin.science
.venv/bin/python -m picoin node audit --server http://127.0.0.1:8000
curl http://127.0.0.1:8000/health
sudo systemctl restart picoin-validator picoin-reconciler picoin-auditor picoin-miner
```

After a checkpoint restore, a validator can legitimately show `local_block_height=0`
because old local block rows were cleared and replaced by an active snapshot. The
reconciler must use `snapshot_height` / `effective_latest_block_height` as the sync
base and request only blocks after that height. For example, if the snapshot is at
height `939` and the bootstrap is at height `960`, catch-up should request blocks
starting after `939` and replay `940..960`.

Check the restore/catch-up diagnostics:

```bash
curl -s http://127.0.0.1:8000/node/sync-status; echo
curl -s http://127.0.0.1:8000/replay/status; echo
.venv/bin/python -m picoin node catch-up \
  --server http://127.0.0.1:8000 \
  --peer https://api.picoin.science
curl -X POST "http://127.0.0.1:8000/consensus/replay?limit=100"; echo
```

Fields to verify:

- `local_block_height`: local block table height. This may remain `0` in snapshot mode.
- `snapshot_height`: active restored checkpoint height.
- `effective_latest_block_height`: best local chain state, including active snapshot and replayed blocks.
- `peer_height`: bootstrap height reported during catch-up.
- `catch_up_start_height`: the base height used for the next sync request.
- `blocks_imported`: number of post-snapshot blocks imported.
- `headers_skipped_pre_snapshot`: stale headers ignored because the snapshot already covers them.
- `replay.queue_size`: pending replay backlog.
- `replay.active`: true while the background replay worker is processing a batch.
- `replay.replay_blocks_per_second`, `replay.replay_avg_ms`, `replay.replay_eta_seconds`: replay drain metrics.
- `sync_status`: `healthy`, `catching_up`, `stalled`, or `divergent`.
- `replay_stalled`: true when replay has pending work but repeated batches make no height/import progress.
- `replay_last_progress_at`: last time a replay batch imported or normalized work.
- `replay_last_imported_height`: latest local height observed by replay health.
- `replay_consecutive_failures`: consecutive no-progress replay batches.
- `divergence_detected` and `divergence_reason`: explicit signal for state-root, canonical hash, missing-field, or ancestor failures.
- `auto_recovery_active`: reserved for guarded restore automation. Keep `PICOIN_AUTO_RECOVERY_ENABLED=0` unless testing recovery on a disposable testnet node.

Healthy behavior after restore is that `effective_latest_block_height` advances
toward the bootstrap height without repeated `cannot import block before ancestors`
messages for pre-snapshot history.

Replay is intentionally throttled. The node drains replay in small batches
(`PICOIN_REPLAY_BATCH_SIZE=10` by default). If the replay backlog is above
`PICOIN_REPLAY_BACKLOG_THRESHOLD=25`, catch-up avoids large synchronous replay
and runs one bounded drain batch. This prevents reconciler storms while still
allowing the queue to make progress, and keeps `/health`, `/node/sync-status`,
and `/replay/status` responsive.

For public testnet, `PICOIN_REPLAY_STALL_FAILURES=3` marks replay as stalled
after three consecutive no-progress batches. `PICOIN_MIN_QUORUM_PEERS=1` is
acceptable for the current small bootstrap-led testnet. Raise it for mainnet
restore policy.

`PICOIN_AUTO_RECOVERY_ENABLED=1` enables an opt-in testnet recovery path during
`node catch-up`: when replay is already marked `divergent`, the node downloads a
fresh canonical snapshot from the configured peer and restores local state
without stopping the API process. Keep this disabled on bootstrap and enable it
only on disposable validator nodes until the recovery policy is hardened for
mainnet.

If replay reports `block_hash does not match canonical payload`, inspect the exact
canonical hash inputs before restarting the network:

```bash
curl -s http://127.0.0.1:8000/consensus/debug/block/940; echo
.venv/bin/python -m picoin node compare-block-payloads \
  --server http://127.0.0.1:8000 \
  --peer https://api.picoin.science \
  --height 940
```

The debug output includes `canonical_payload`, `normalized_payload`, `hash_input`,
`computed_hash`, `expected_hash`, and all known deterministic schema variants. A
healthy block has `matched=true` and a non-empty `matched_variant`.

## Logs

```bash
sudo journalctl -u picoin-node -f
sudo journalctl -u picoin-miner -f
sudo journalctl -u picoin-validator -f
sudo journalctl -u picoin-reconciler -f
sudo journalctl -u picoin-auditor -f
```

Recent logs without following:

```bash
sudo journalctl -u picoin-node -u picoin-miner -u picoin-validator -u picoin-reconciler -u picoin-auditor -n 240 --no-pager
```

## Expected Healthy Logs

Validator:

```text
Intentando validacion con: validator-two
No validation jobs available.
Done. validation_jobs_completed=0
Validated job_xxx: approved=True status=validation_pending approvals=1/2
Validated job_xxx: approved=True status=approved approvals=2/2
```

Miner:

```text
Mining attempt 1/1 as miner_xxx
Task assigned: task_xxx
Done. accepted=1 attempts=1
```

## Safe Code Refresh

Do not delete `/opt/picoin/picoin-proof-of-pi` by hand on a running public node. Use the refresh helper; it stops services, backs up existing state, migrates `data/` to `/var/lib/picoin/data`, refreshes code while excluding state, reinstalls systemd units, and leaves `/opt/picoin/picoin-proof-of-pi/data` as a symlink to persistent state.

Bootstrap or validator source paths may differ:

- Bootstrap source example: `/opt/picoin/src/PICOIN`
- Validator source example: `/root/PICOIN/PICOIN`
- Installed runtime path: `/opt/picoin/picoin-proof-of-pi`

Refresh:

```bash
cd /opt/picoin/src/PICOIN
git pull
SOURCE_DIR="$PWD/picoin-proof-of-pi"

sudo PICOIN_SOURCE_DIR="$SOURCE_DIR" \
  PICOIN_REPO_DIR=/opt/picoin/picoin-proof-of-pi \
  PICOIN_DATA_DIR=/var/lib/picoin/data \
  bash "$SOURCE_DIR/deploy/scripts/refresh-code.sh"

sudo systemctl daemon-reload
sudo systemctl restart picoin-node picoin-reconciler picoin-auditor picoin-validator picoin-miner
```

On a validator host cloned under `/root/PICOIN/PICOIN`, use:

```bash
cd /root/PICOIN/PICOIN
git pull
SOURCE_DIR="$PWD/picoin-proof-of-pi"
```

## Troubleshooting

- If a validator shows `405 Method Not Allowed` for `/validation/results`, check that the endpoint is `https://api.picoin.science` directly, not the HTTP version of that URL.
- If HTTP redirects to HTTPS, use HTTPS directly in `/etc/picoin/picoin.env`.
- If block height stalls, check `curl https://api.picoin.science/node/sync-status`, `consensus.pending`, `consensus.pending_missing_ancestors`, miner logs, validator logs, and reconciler logs.
- If `pending_missing_ancestors` is non-zero, inspect `curl 'https://api.picoin.science/consensus/proposals?status=pending_missing_ancestors&limit=20'`; do not delete proposals silently.
- If `/consensus/replay` is slow, call it with a bounded batch such as `curl -X POST 'http://127.0.0.1:8000/consensus/replay?limit=50'` and check the structured `status`, `missing_ancestors`, and `errors` fields.
- If `/health` reports `sync_status=stalled` or `sync_status=divergent`, stop validator/miner signing on that droplet and restore from a verified peer snapshot before rejoining consensus.
- If replay shows `state_root mismatch after canonical replay`, use `/consensus/debug/block/{height}` and `node compare-block-payloads` before deleting data. This is treated as deterministic consensus divergence, not a networking issue.
- If replay shows `cannot import block before ancestors`, check `snapshot_height`, `effective_latest_block_height`, `headers_skipped_pre_snapshot`, and `pending_missing_ancestors` in `/node/sync-status`.
- On bootstrap, keep `PICOIN_SMOKE_SKIP_CATCH_UP=1` and `PICOIN_SMOKE_WARN_ONLY=1` if the auditor is enabled, because bootstrap has no upstream peer.
- If a validator auto-registers the wrong identity, check `PICOIN_VALIDATOR_IDENTITY`, `PICOIN_VALIDATOR_NAME`, and the JSON identity path.
- If a service uses the wrong script, run `sudo systemctl cat picoin-validator`.
- If a local node shows height `0` on a validator droplet, the bootstrap is the source of public chain state unless the local node is fully synced. Run `node catch-up` against `https://api.picoin.science`.
- If `node catch-up` reports a state root mismatch after replay, stop writer services and restore a verified peer snapshot from `https://api.picoin.science`.
- If audit reports missing reward rows on an older node, run `node repair-rewards` and then `node audit`.
- Keep raw bootstrap IPs out of public docs; use `https://api.picoin.science`.

## ANN Checklist

- Bootstrap/API live: `https://api.picoin.science`
- Explorer live.
- Mining enabled.
- External validation enabled.
- 3 validators online.
- Health endpoint returns `status=ok`.
- Sync endpoint returns current height and hash.
- Current block height visible.
- Public docs link ready.
- GitHub link ready.
- Website link ready.
- Explorer link ready.
