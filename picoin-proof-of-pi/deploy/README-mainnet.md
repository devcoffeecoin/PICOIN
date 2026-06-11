# Picoin Mainnet Runbook

This runbook is for private rehearsal and launch preparation. Do not start a
public mainnet from a dirty worktree, an untagged commit, or an environment file
with `CHANGE_ME` values.

## 1. Generate Canonical Wallets Offline

Generate the treasury and governance wallets before genesis. The private key
files should stay offline; only the addresses go into `/etc/picoin/picoin.env`.
Use explicit wallet metadata flags here; do not source the unfinished mainnet
environment while creating the first canonical wallets.

```bash
cd /opt/picoin/picoin-proof-of-pi
source .venv/bin/activate

python -m picoin wallet create \
  --name treasury-mainnet \
  --network picoin-mainnet-v1 \
  --chain-id 314159 \
  --output treasury-mainnet.json

python -m picoin wallet create \
  --name governance-mainnet \
  --network picoin-mainnet-v1 \
  --chain-id 314159 \
  --output governance-mainnet.json

python -m picoin wallet address --wallet treasury-mainnet.json
python -m picoin wallet address --wallet governance-mainnet.json
```

Set:

```bash
PICOIN_TREASURY_WALLET=PI...
PICOIN_GOVERNANCE_WALLET=PI...
```

Mainnet startup rejects missing, placeholder, non-canonical, or duplicate
treasury/governance wallets.

## 2. Finalize Genesis

Start from:

```text
deploy/mainnet-genesis.allocations.draft.json
```

Before launch, create a final allocation file with only canonical wallet
addresses. Mainnet rejects draft validator IDs, placeholders, reserve account
IDs, non-wallet account types, and non-canonical wallet addresses.

```bash
cp deploy/mainnet-genesis.allocations.draft.json deploy/mainnet-genesis.allocations.final.json
nano deploy/mainnet-genesis.allocations.final.json

python -m picoin node genesis-hash \
  --file deploy/mainnet-genesis.allocations.final.json \
  --mainnet
```

The command rejects draft validator IDs, duplicate wallets, non-wallet accounts,
non-canonical addresses, placeholders, and any total other than exactly
`300.000000` PI. Its output contains the deterministic `genesis_hash`,
`mainnet_valid: true`, the allocation count, and the total units.

Every mainnet node must use the same allocation file and resulting genesis hash.
The genesis hash is the SHA-256 of the canonical JSON form of the final
allocation document. It becomes the chain identity anchor: peers with a different
hash are not on the same mainnet, even if they use the same network name.

## 3. Install Services With Mainnet Env

```bash
sudo PICOIN_REPO_DIR=/opt/picoin/picoin-proof-of-pi \
  PICOIN_ENV_SOURCE=/opt/picoin/picoin-proof-of-pi/deploy/mainnet.env.example \
  /opt/picoin/picoin-proof-of-pi/deploy/scripts/install-systemd-service.sh

sudo nano /etc/picoin/picoin.env
```

The installer creates `/etc/picoin/picoin.env` only when it does not already
exist. On a rehearsal host with an existing testnet file, back it up and install
the mainnet template deliberately before starting services.

Required launch values:

```bash
PICOIN_NETWORK=picoin-mainnet-v1
PICOIN_CHAIN_ID=314159
PICOIN_PROTOCOL_VERSION=1.0
PICOIN_GENESIS_ALLOCATIONS_FILE=deploy/mainnet-genesis.allocations.final.json
PICOIN_TREASURY_WALLET=PI...
PICOIN_GOVERNANCE_WALLET=PI...
PICOIN_FAUCET_ALLOWED_NETWORKS=
PICOIN_REQUIRED_VALIDATOR_APPROVALS=3
PICOIN_RETARGET_MAX_PI_POSITION=1000000000000000
```

Check that no placeholders remain:

```bash
sudo grep -n "CHANGE_ME\|PI_TREASURY\|PI_GOVERNANCE" /etc/picoin/picoin.env
```

The command above must return no unresolved launch values.

## 4. Preflight Before Public Launch

Load the environment and verify settings import before starting public services:

```bash
cd /opt/picoin/picoin-proof-of-pi
set -a
source /etc/picoin/picoin.env
set +a

.venv/bin/python - <<'PY'
from app.core import settings
print(settings.NETWORK_ID)
print(settings.CHAIN_ID)
print(settings.PROTOCOL_VERSION)
print(settings.SCIENTIFIC_DEVELOPMENT_TREASURY_WALLET)
print(settings.SCIENTIFIC_DEVELOPMENT_GOVERNANCE_WALLET)
PY
```

Start the node:

```bash
sudo systemctl daemon-reload
sudo systemctl start picoin-node
sudo systemctl status picoin-node --no-pager
```

Then run:

```bash
.venv/bin/python -m picoin node validation-health \
  --server http://127.0.0.1:8000

.venv/bin/python -m picoin node mainnet-preflight \
  --server http://127.0.0.1:8000 \
  --verbose
```

For multi-node rehearsal, compare with a peer:

```bash
.venv/bin/python -m picoin node mainnet-preflight \
  --server https://MAINNET_BOOTSTRAP_DOMAIN \
  --peer https://SECOND_MAINNET_NODE_DOMAIN
```

## 5. Launch Order

1. Bootstrap node online, synced, preflight clean.
2. At least four eligible validators online for a `3` approval quorum, unless
   launch explicitly accepts `3/3` liveness risk.
3. Full audit clean.
4. Explorer/API read-only public.
5. Limited mining.
6. Community validator onboarding after stability.

Treasury claims and Science Reserve activation must use signed transactions on
mainnet. Direct mutation endpoints are disabled by code.

## 6. Safe Mainnet Update Drill

No software update can guarantee zero risk before it runs on production state.
Mainnet safety comes from update gates: prove the code on a canary node, back up
state, update one service layer at a time, and stop immediately if replay,
validator liveness, miner task flow, or wallet accounting changes unexpectedly.

### 6.1 Canary First

Run the new code first on a non-authoritative full node or exchange node, not on
the bootstrap. Keep miner, validator, pool, and auditor disabled there.

```bash
cd /opt/picoin/src/PICOIN
git fetch origin
git checkout main
git pull origin main

SOURCE_DIR=/opt/picoin/src/PICOIN/picoin-proof-of-pi
PICOIN_SOURCE_DIR="$SOURCE_DIR" \
PICOIN_REPO_DIR=/opt/picoin/picoin-proof-of-pi \
bash "$SOURCE_DIR/deploy/scripts/refresh-code.sh"

cd /opt/picoin/picoin-proof-of-pi
sudo deploy/scripts/picoin-service-preflight.sh --fix --repo-dir /opt/picoin/picoin-proof-of-pi
sudo systemctl daemon-reload
sudo systemctl restart picoin-node
sleep 20
sudo systemctl restart picoin-reconciler
```

Canary gates:

```bash
curl -sS http://127.0.0.1:8000/health | python3 -m json.tool
curl -sS http://127.0.0.1:8000/protocol | python3 -m json.tool
curl -sS http://127.0.0.1:8000/node/sync-status | python3 -m json.tool
curl -sS http://127.0.0.1:8000/audit/full | python3 -m json.tool

python3 deploy/scripts/phase7-exchange-full-node-smoke.py \
  --local http://127.0.0.1:8000 \
  --reference https://api.picoin.science \
  --allowed-lag 5
```

The canary must show healthy replay, no divergence, height lag within limit, and
matching tip hash when fully caught up. A read-only canary may be `degraded`
only for `not enough eligible validators for quorum`.

### 6.2 Backup Before Bootstrap Update

Before touching the main bootstrap, record the current commit and back up env and
database state.

```bash
cd /opt/picoin/src/PICOIN
git rev-parse HEAD | sudo tee /var/lib/picoin/pre-update-commit.txt

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
sudo install -d -m 0750 /var/backups/picoin-mainnet-update/$STAMP
sudo cp -a /etc/picoin/picoin.env /var/backups/picoin-mainnet-update/$STAMP/
sudo cp -a /var/lib/picoin/data /var/backups/picoin-mainnet-update/$STAMP/
```

If the deployment uses a different `PICOIN_DB_PATH`, back up that directory
instead of `/var/lib/picoin/data`.

### 6.3 Bootstrap Update Order

Update the bootstrap node and reconciler first. Do not update or restart miners,
validators, or pool in the same step.

```bash
cd /opt/picoin/src/PICOIN
git fetch origin
git checkout main
git pull origin main

SOURCE_DIR=/opt/picoin/src/PICOIN/picoin-proof-of-pi
PICOIN_SOURCE_DIR="$SOURCE_DIR" \
PICOIN_REPO_DIR=/opt/picoin/picoin-proof-of-pi \
bash "$SOURCE_DIR/deploy/scripts/refresh-code.sh"

cd /opt/picoin/picoin-proof-of-pi
sudo deploy/scripts/picoin-service-preflight.sh --fix --repo-dir /opt/picoin/picoin-proof-of-pi
sudo systemctl daemon-reload
sudo systemctl restart picoin-node
sleep 20
sudo systemctl restart picoin-reconciler
```

Bootstrap gates:

```bash
curl -sS http://127.0.0.1:8000/health | python3 -m json.tool
curl -sS http://127.0.0.1:8000/node/sync-status | python3 -m json.tool
curl -sS http://127.0.0.1:8000/audit/full | python3 -m json.tool
.venv/bin/python -m picoin node mainnet-preflight --server http://127.0.0.1:8000 --verbose
```

Stop the rollout if any gate shows `divergence_detected=true`, replay not
`healthy`, a chain audit issue, protocol identity drift, or a node process that
does not stay active.

### 6.4 Validator And Miner Gates

After the bootstrap is stable, update validators one by one. Wait between
validators and check quorum before moving on.

```bash
curl -sS http://127.0.0.1:8000/validators/status | python3 -m json.tool
systemctl is-active picoin-node picoin-reconciler picoin-validator picoin-miner picoin-pool 2>/dev/null || true
```

Validator gate: eligible validators remain online/synced, quorum is available,
and validation jobs do not get stuck in pending after a normal mining round.

Miner gate: miners keep requesting work, idle responses do not crash the miner,
commits/reveals are accepted, and confirmed blocks keep advancing.

Pool gate, if the pool is updated separately: `/stats` must show `status=ok`,
healthy active workers, no stuck active task after reveal, and payout accounting
must retain pending balances.

### 6.5 Total Decentralization Service Pattern

After the Total Decentralization release, production services should use their
own local full node as the normal API target. The public bootstrap/API is a peer,
snapshot source, and temporary fallback, not the default write path.

Use this on every full node, miner, validator, and pool host:

```bash
cd /opt/picoin/src/PICOIN || exit 1
git fetch origin main
git switch main
git pull --ff-only origin main
git rev-parse --short HEAD
```

The expected production commit for this release is the commit currently on
`origin/main`.

Verify node health before enabling local mining or validation:

```bash
curl -fsS --max-time 60 http://127.0.0.1:8000/node/sync-status -o /tmp/sync.json \
  && python3 -c '
import json
d=json.load(open("/tmp/sync.json")); r=d.get("replay") or {}
print("height=", d.get("effective_latest_block_height"))
print("hash=", d.get("effective_latest_block_hash"))
print("pending=", d.get("pending_replay_blocks"))
print("replay=", r.get("sync_status"))
print("divergent=", r.get("divergence_detected"))
print("last_error=", r.get("last_error"))
'
```

Do not mine or validate if the node is divergent, has replay errors, or is far
behind the healthy peer set.

If a node is healthy internally but far behind, or if replay reports an old
divergence, restore from a healthy peer snapshot:

```bash
cd /opt/picoin/src/PICOIN/picoin-proof-of-pi || exit 1

PY=/opt/picoin/picoin-proof-of-pi/.venv/bin/python
export PYTHONPATH=/opt/picoin/src/PICOIN/picoin-proof-of-pi
export PICOIN_HTTP_TIMEOUT_SECONDS=180
BOOTSTRAP=https://api.picoin.science

sudo systemctl stop picoin-validator || true

sudo -u picoin -E env \
  PYTHONPATH="$PYTHONPATH" \
  PICOIN_HTTP_TIMEOUT_SECONDS=180 \
  "$PY" -m picoin node checkpoint restore-peer \
  --peer "$BOOTSTRAP" \
  --source mainnet-peer-restore

sudo systemctl restart picoin-node
sleep 15
```

On hosts installed directly from the repository tree, the Python runtime may be:

```bash
PY=/opt/picoin/src/PICOIN/.venv/bin/python
```

Check `systemctl show picoin-node -p ExecStart --no-pager` before assuming the
runtime path.

Validator services should point both coordinator and node server to the local
node:

```env
PICOIN_VALIDATOR_SERVER=http://127.0.0.1:8000
PICOIN_VALIDATOR_NODE_SERVER=http://127.0.0.1:8000
PICOIN_VALIDATOR_RECONCILE_ENABLED=1
PICOIN_VALIDATOR_RECONCILE_INTERVAL_SECONDS=10
PICOIN_VALIDATOR_RECONCILE_LIMIT=100
PICOIN_VALIDATOR_RECONCILE_TIMEOUT_SECONDS=30
PICOIN_RECONCILE_PEERS=https://api.picoin.science
```

Before troubleshooting validator liveness, verify which env file the service
actually loads:

```bash
systemctl show picoin-validator -p EnvironmentFiles -p ExecStart --no-pager
```

If it reports a role-specific file instead of `/etc/picoin/picoin.env`, put the
same local-node and reconcile variables in that file. A validator can be
`active` while still missing peer jobs if its service env does not include the
reconcile settings.

Pool services should also point to the local node:

```text
--server http://127.0.0.1:8000
```

After a pool migration, verify both the node and pool:

```bash
curl -fsS --max-time 30 http://127.0.0.1:8000/node/sync-status -o /tmp/sync.json
curl -fsS --max-time 20 http://127.0.0.1:9321/health
```

### 6.6 Rollback Rule

If the update changed only code and not database schema, roll back to the saved
commit and restart services:

```bash
cd /opt/picoin/src/PICOIN
PREV=$(sudo cat /var/lib/picoin/pre-update-commit.txt)
git checkout "$PREV"

SOURCE_DIR=/opt/picoin/src/PICOIN/picoin-proof-of-pi
PICOIN_SOURCE_DIR="$SOURCE_DIR" \
PICOIN_REPO_DIR=/opt/picoin/picoin-proof-of-pi \
bash "$SOURCE_DIR/deploy/scripts/refresh-code.sh"

sudo systemctl daemon-reload
sudo systemctl restart picoin-node picoin-reconciler
```

If a schema or replay-state migration ran, restore the database backup instead
of only checking out old code. Never keep mining or validating on a node that is
divergent.
