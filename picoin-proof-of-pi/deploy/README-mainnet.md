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
