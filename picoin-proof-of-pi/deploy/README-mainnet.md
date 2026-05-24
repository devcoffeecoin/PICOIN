# Picoin Mainnet Runbook

This runbook is for private rehearsal and launch preparation. Do not start a
public mainnet from a dirty worktree, an untagged commit, or an environment file
with `CHANGE_ME` values.

## 1. Generate Canonical Wallets Offline

Generate the treasury and governance wallets before genesis. The private key
files should stay offline; only the addresses go into `/etc/picoin/picoin.env`.

```bash
cd /opt/picoin/picoin-proof-of-pi
source .venv/bin/activate

PICOIN_NETWORK=mainnet \
PICOIN_CHAIN_ID=picoin-mainnet-v1 \
python -m picoin wallet create \
  --name treasury-mainnet \
  --output treasury-mainnet.json

PICOIN_NETWORK=mainnet \
PICOIN_CHAIN_ID=picoin-mainnet-v1 \
python -m picoin wallet create \
  --name governance-mainnet \
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

Before launch, replace draft validator IDs and reserve accounts with final
canonical launch allocations, then compute the deterministic hash:

```bash
python -m picoin node genesis-hash \
  --file deploy/mainnet-genesis.allocations.draft.json
```

Every mainnet node must use the same allocation file and resulting genesis hash.

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
PICOIN_NETWORK=mainnet
PICOIN_CHAIN_ID=picoin-mainnet-v1
PICOIN_PROTOCOL_VERSION=1.0
PICOIN_GENESIS_ALLOCATIONS_FILE=deploy/mainnet-genesis.allocations.draft.json
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
