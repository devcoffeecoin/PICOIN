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
- Consensus approvals: working with multi-validator approval logs such as `approvals=2/3` and `approvals=3/3`.
- Current bootstrap setup: the bootstrap/API node still runs the miner together with the API/bootstrap services.

The current setup already supports distributed validation, external validators, public HTTPS API access, public mining, consensus approvals, and public testnet participation.

## Architecture

```text
                  +----------------------+
                  | bootstrap/API/miner  |
                  | api.picoin.science   |
                  +----------+-----------+
                             |
          +------------------+------------------+
          |                  |                  |
 +--------v-------+ +--------v-------+ +--------v-------+
 | validator-one  | | validator-two  | | validator-three|
 | external node  | | external node  | | external node  |
 +----------------+ +----------------+ +----------------+
```

Current validator architecture:

- One validator per machine/droplet.
- Validators are not executed sequentially in one shared loop.
- Validators communicate with the bootstrap/API through `https://api.picoin.science`.
- Droplet 1 runs bootstrap/API plus miner.
- Droplets 2, 3, and 4 run `validator-one`, `validator-two`, and `validator-three`.

Future architecture goal:

- Move miner outside the bootstrap node.
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
```

Validator three:

```bash
PICOIN_WORKER_ROLE=validator
PICOIN_VALIDATOR_SERVER=https://api.picoin.science
PICOIN_VALIDATOR_NAME=validator-three
PICOIN_VALIDATOR_IDENTITY=/var/lib/picoin/data/testnet/identities/validator-three.json
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
curl https://api.picoin.science/validators?limit=100
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
Intentando validación con: validator-two
No validation jobs available.
Done. validation_jobs_completed=0
Validated job_xxx: approved=True status=validation_pending approvals=2/3
Validated job_xxx: approved=True status=approved approvals=3/3
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
