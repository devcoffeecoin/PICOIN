# PICOIN

Official mainnet and node operator guide.

Picoin is a useful-compute blockchain protocol based on Proof of Pi. Miners compute deterministic hexadecimal ranges of pi, validators independently verify the work, accepted jobs settle into blocks, and protocol accounting records wallet balances, validator rewards, the Science Compute Reserve, and the Scientific Development Treasury.

This README is the entry point for running Picoin infrastructure. It explains how to configure a node, miner, validator, wallets, genesis, and the environment files used by the repository.

## Mainnet Profile

| Parameter | Mainnet value |
| --- | --- |
| Network ID | `picoin-mainnet-v1` |
| Chain ID | `314159` |
| Protocol version | `1.0` |
| Public API | `https://api.picoin.science` |
| Wallet symbol | `PI` |
| Mining algorithm | `bbp_hex_v1` |
| Wallet cryptography | Ed25519 |
| Address format | `PI...` |
| Base block reward | `3.1416 PI` |
| Miner allocation | `80%` |
| Validator allocation | `10%` |
| Science Compute Reserve allocation | `7%` |
| Scientific Development Treasury allocation | `3%` |
| Validator quorum | `3 approvals` |
| Minimum validator wallet stake | `31.416 PI` |
| Retroactive audit interval | Every `314` blocks |
| Retroactive audit reward | `0 PI` security event, no extra emission |
| Mainnet genesis supply | `300 PI` |
| Faucet | Disabled |

The public testnet has been used for launch rehearsals and can still be studied from its deployment guide, but production configuration must use the mainnet values above.

## Repository Layout

| Path | Purpose |
| --- | --- |
| `picoin-proof-of-pi/` | Core protocol, API, CLI, node, miner, validator, tests, deploy scripts |
| `picoin-proof-of-pi/deploy/` | Production runbooks, systemd services, env templates |
| `picoin-web/` | Web explorer and web wallet frontend |
| `picoin-desktop-wallet/` | Desktop wallet, API based, no embedded node |
| `picoin-desktop-miner-source/` | Desktop miner UI and packaged mining client |
| `api/` | Supporting API/frontend glue used by deployments |

## Environment Files

There are three tracked env examples. Treat them as the source of truth.

| File | Use it for | Notes |
| --- | --- | --- |
| `picoin-proof-of-pi/deploy/mainnet.env.example` | Mainnet nodes, miners, validators | Production template. Every `CHANGE_ME` value must be replaced before services start. |
| `picoin-proof-of-pi/deploy/public-testnet.env.example` | Historical public-testnet rehearsal only | Keeps the old `public-testnet` and `picoin-public-testnet-v018` values for reference. Do not use for mainnet. |
| `picoin-proof-of-pi/.env.example` | Local development only | Uses `local` and `picoin-local-testnet`; useful for tests and isolated dev nodes. |

Critical mainnet variables:

```env
PICOIN_NETWORK=picoin-mainnet-v1
PICOIN_CHAIN_ID=314159
PICOIN_PROTOCOL_VERSION=1.0
PICOIN_GENESIS_ALLOCATIONS_FILE=/absolute/path/to/mainnet-genesis.allocations.final.json
PICOIN_GENESIS_HASH=<published-mainnet-genesis-hash>
PICOIN_TREASURY_WALLET=<canonical-PI-treasury-wallet>
PICOIN_GOVERNANCE_WALLET=<canonical-PI-governance-wallet>
PICOIN_NODE_ADDRESS=https://api.picoin.science
PICOIN_MINER_SERVER=https://api.picoin.science
PICOIN_VALIDATOR_SERVER=https://api.picoin.science
PICOIN_SCIENCE_RESERVE_AUTHORIZED_SIGNERS=<signer-1>,<signer-2>
```

Miner and validator signatures include `network_id` and `chain_id`. If a worker falls back to `local` or a testnet chain ID, commits will be rejected with signature errors.

## Quick Start

Use this path for a clean mainnet server. The detailed sections below explain every variable and role.

### 1. Clone The Repository

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-pip nginx ufw

mkdir -p /opt/picoin/src
cd /opt/picoin/src
git clone https://github.com/devcoffeecoin/PICOIN.git
cd /opt/picoin/src/PICOIN
```

### 2. Install Picoin Runtime

```bash
SOURCE_DIR=/opt/picoin/src/PICOIN/picoin-proof-of-pi

PICOIN_SOURCE_DIR="$SOURCE_DIR" \
PICOIN_REPO_DIR=/opt/picoin/picoin-proof-of-pi \
PICOIN_DATA_DIR=/var/lib/picoin/data \
bash "$SOURCE_DIR/deploy/scripts/refresh-code.sh"

cd /opt/picoin/picoin-proof-of-pi
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

### 3. Create Mainnet Env

```bash
sudo cp /opt/picoin/picoin-proof-of-pi/deploy/mainnet.env.example /etc/picoin/picoin.env
sudo chown root:picoin /etc/picoin/picoin.env
sudo chmod 0640 /etc/picoin/picoin.env
sudo nano /etc/picoin/picoin.env
```

At minimum, replace the genesis, treasury, governance, node address, API domain, reward wallet, and signer placeholders. Before starting services:

```bash
sudo grep -n "CHANGE_ME" /etc/picoin/picoin.env
```

That command must return no unresolved production values.

### 4. Start And Verify A Node

```bash
sudo systemctl daemon-reload
sudo systemctl start picoin-node
sudo systemctl status picoin-node --no-pager

cd /opt/picoin/picoin-proof-of-pi
.venv/bin/python -m picoin node mainnet-preflight --server http://127.0.0.1:8000 --verbose
curl -s http://127.0.0.1:8000/audit/full | python3 -m json.tool
```

### 5. Add A Miner

Set these values in `/etc/picoin/picoin.env` on the miner machine:

```env
PICOIN_MINER_SERVER=https://api.picoin.science
PICOIN_MINER_IDENTITY=/var/lib/picoin/data/mainnet/identities/miner-mainnet.json
PICOIN_MINER_REWARD_ADDRESS=<canonical-PI-reward-wallet>
PICOIN_MINER_WORKERS=1
```

Register and test:

```bash
cd /opt/picoin/picoin-proof-of-pi
set -a
source /etc/picoin/picoin.env
set +a

.venv/bin/python -m picoin miner --server "$PICOIN_MINER_SERVER" --identity "$PICOIN_MINER_IDENTITY" register --name miner-mainnet-1 --overwrite
.venv/bin/python -m picoin miner --server "$PICOIN_MINER_SERVER" --identity "$PICOIN_MINER_IDENTITY" mine --once --workers "$PICOIN_MINER_WORKERS"
```

Then run it continuously:

```bash
sudo systemctl start picoin-miner
sudo journalctl -u picoin-miner -f
```

### 6. Add A Validator

Set these values in `/etc/picoin/picoin.env` on the validator machine:

```env
PICOIN_VALIDATOR_SERVER=https://api.picoin.science
PICOIN_VALIDATOR_NODE_SERVER=http://127.0.0.1:8000
PICOIN_VALIDATOR_NODE_ADDRESS=https://validator.example.com
PICOIN_VALIDATOR_IDENTITY=/var/lib/picoin/data/mainnet/identities/validator-mainnet.json
PICOIN_VALIDATOR_REWARD_ADDRESS=<canonical-PI-reward-wallet>
```

Register, stake, and test:

```bash
cd /opt/picoin/picoin-proof-of-pi
set -a
source /etc/picoin/picoin.env
set +a

.venv/bin/python -m picoin validator --server "$PICOIN_VALIDATOR_SERVER" --identity "$PICOIN_VALIDATOR_IDENTITY" register --name validator-mainnet-1 --overwrite
```

For the first mainnet validator set only, register the initial validators first, then apply their wallet-backed stake on the bootstrap node before block `1`. This is a local launch command, not a public API endpoint:

```json
{
  "version": 1,
  "network_id": "picoin-mainnet-v1",
  "chain_id": 314159,
  "stakes": [
    {
      "validator_id": "validator_xxxxxxxxxxxxxxxx",
      "stake_owner_address": "PI...",
      "amount": 31.416
    }
  ]
}
```

```bash
.venv/bin/python -m picoin node bootstrap-validator-stake --file /etc/picoin/mainnet-validator-stakes.json
.venv/bin/python -m picoin node mainnet-preflight --server http://127.0.0.1:8000 --verbose
```

After launch, validators stake through normal signed wallet transactions:

```bash

.venv/bin/python -m picoin tx --server https://api.picoin.science send \
  --wallet /secure/validator-owner.json \
  --type stake \
  --stake-type validator \
  --validator-id validator_xxxxxxxxxxxxxxxx \
  --amount 31.416 \
  --fee 0.001

.venv/bin/python -m picoin validator --server "$PICOIN_VALIDATOR_SERVER" --identity "$PICOIN_VALIDATOR_IDENTITY" validate --once --node-server "$PICOIN_VALIDATOR_NODE_SERVER"
```

Then run it continuously:

```bash
sudo systemctl start picoin-validator
sudo journalctl -u picoin-validator -f
```

## Install

The production examples assume Ubuntu 22.04 or 24.04.

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-pip nginx ufw

mkdir -p /opt/picoin/src
cd /opt/picoin/src
git clone https://github.com/devcoffeecoin/PICOIN.git
cd /opt/picoin/src/PICOIN
```

Install or refresh the runtime tree:

```bash
SOURCE_DIR=/opt/picoin/src/PICOIN/picoin-proof-of-pi

PICOIN_SOURCE_DIR="$SOURCE_DIR" \
PICOIN_REPO_DIR=/opt/picoin/picoin-proof-of-pi \
PICOIN_DATA_DIR=/var/lib/picoin/data \
bash "$SOURCE_DIR/deploy/scripts/refresh-code.sh"
```

Install Python dependencies:

```bash
cd /opt/picoin/picoin-proof-of-pi
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m picoin --help
```

`PyNaCl` is required for Ed25519 signatures and is included in `requirements.txt`.

## Mainnet Env Setup

Copy the mainnet template and edit it:

```bash
sudo cp /opt/picoin/picoin-proof-of-pi/deploy/mainnet.env.example /etc/picoin/picoin.env
sudo chown root:picoin /etc/picoin/picoin.env
sudo chmod 0640 /etc/picoin/picoin.env
sudo nano /etc/picoin/picoin.env
```

Before launch, this command must return no unresolved launch placeholders except comments:

```bash
sudo grep -n "CHANGE_ME" /etc/picoin/picoin.env
```

Use service-specific reward addresses only on machines that actually mine or validate. A plain bootstrap node does not need miner or validator reward wallet variables.

## Wallets Before Mainnet

Generate mainnet wallets before the first block. Keep private wallet JSON files offline when they control treasury, governance, or reserve authority.

```bash
cd /opt/picoin/picoin-proof-of-pi
PY=.venv/bin/python

$PY -m picoin wallet create \
  --name treasury-mainnet \
  --network picoin-mainnet-v1 \
  --chain-id 314159 \
  --output /secure/offline/treasury-mainnet.json

$PY -m picoin wallet create \
  --name governance-mainnet \
  --network picoin-mainnet-v1 \
  --chain-id 314159 \
  --output /secure/offline/governance-mainnet.json

$PY -m picoin wallet address --wallet /secure/offline/treasury-mainnet.json
$PY -m picoin wallet address --wallet /secure/offline/governance-mainnet.json
```

Create separate reward wallets for miners and validators:

```bash
$PY -m picoin wallet create \
  --name miner-reward-mainnet \
  --network picoin-mainnet-v1 \
  --chain-id 314159 \
  --output /secure/offline/miner-reward-mainnet.json

$PY -m picoin wallet create \
  --name validator-reward-mainnet \
  --network picoin-mainnet-v1 \
  --chain-id 314159 \
  --output /secure/offline/validator-reward-mainnet.json

$PY -m picoin wallet address --wallet /secure/offline/miner-reward-mainnet.json
$PY -m picoin wallet address --wallet /secure/offline/validator-reward-mainnet.json
```

Only copy the public `PI...` addresses into `/etc/picoin/picoin.env`. Do not put private wallet JSON files on public servers unless that machine must sign transactions.

## Genesis

Mainnet requires a final allocation file and a canonical genesis hash. The final allocation file must be identical on all launch nodes.

1. Create `deploy/mainnet-genesis.allocations.final.json` from the final launch plan.
2. Use canonical `PI...` wallet addresses only.
3. Keep total allocation equal to the configured mainnet genesis supply: `300 PI`.
4. Compute the hash:

```bash
cd /opt/picoin/picoin-proof-of-pi
.venv/bin/python -m picoin node genesis-hash \
  --file deploy/mainnet-genesis.allocations.final.json \
  --mainnet
```

5. Set these values in `/etc/picoin/picoin.env`:

```env
PICOIN_GENESIS_ALLOCATIONS_FILE=/opt/picoin/picoin-proof-of-pi/deploy/mainnet-genesis.allocations.final.json
PICOIN_GENESIS_HASH=<hash printed by genesis-hash>
```

If `PICOIN_GENESIS_HASH` is blank, the node computes it from the file. For launch, publish and pin the final hash to make mismatches obvious.

## Run A Node

A node exposes the Picoin API, keeps local chain state, handles mempool, consensus, peers, replay, and health endpoints.

Required node config:

```env
PICOIN_NETWORK=picoin-mainnet-v1
PICOIN_CHAIN_ID=314159
PICOIN_PROTOCOL_VERSION=1.0
PICOIN_NODE_ID=<unique-node-id>
PICOIN_NODE_TYPE=bootstrap
PICOIN_NODE_ADDRESS=https://api.picoin.science
PICOIN_HOST=0.0.0.0
PICOIN_PORT=8000
PICOIN_SERVER=http://127.0.0.1:8000
PICOIN_DB_PATH=/var/lib/picoin/data/picoin.sqlite3
PICOIN_BOOTSTRAP_PEERS=
```

For a non-bootstrap node, set `PICOIN_NODE_TYPE=full` or `validator`, set `PICOIN_NODE_ADDRESS` to that node's public HTTPS URL, and put the bootstrap URL in `PICOIN_BOOTSTRAP_PEERS`.

Start manually:

```bash
cd /opt/picoin/picoin-proof-of-pi
set -a
source /etc/picoin/picoin.env
set +a

.venv/bin/python -m picoin node start --host "$PICOIN_HOST" --port "$PICOIN_PORT"
```

Start with systemd:

```bash
sudo systemctl daemon-reload
sudo systemctl start picoin-node
sudo systemctl status picoin-node --no-pager
```

Verify:

```bash
SERVER=http://127.0.0.1:8000

curl -s "$SERVER/protocol" | python3 -m json.tool
curl -s "$SERVER/node/sync-status" | python3 -m json.tool
curl -s "$SERVER/node/peers" | python3 -m json.tool

cd /opt/picoin/picoin-proof-of-pi
.venv/bin/python -m picoin node mainnet-preflight --server "$SERVER" --verbose
.venv/bin/python -m picoin node audit --server "$SERVER"
```

The node must not start mainnet with placeholder treasury, governance, genesis, or chain values.

## Run A Miner

A miner receives tasks from the API, computes pi ranges, signs commits locally, reveals results, and receives rewards to its configured reward wallet.

Required miner config:

```env
PICOIN_NETWORK=picoin-mainnet-v1
PICOIN_CHAIN_ID=314159
PICOIN_MINER_SERVER=https://api.picoin.science
PICOIN_MINER_IDENTITY=/var/lib/picoin/data/mainnet/identities/miner-mainnet.json
PICOIN_MINER_REWARD_ADDRESS=<canonical-PI-reward-wallet>
PICOIN_MINER_WORKERS=1
PICOIN_MINER_LOOPS=1
PICOIN_MINER_SLEEP=5
```

Register or refresh the miner identity:

```bash
cd /opt/picoin/picoin-proof-of-pi
set -a
source /etc/picoin/picoin.env
set +a

.venv/bin/python -m picoin miner \
  --server "$PICOIN_MINER_SERVER" \
  --identity "$PICOIN_MINER_IDENTITY" \
  register \
  --name miner-mainnet-1 \
  --overwrite
```

Run one mining attempt:

```bash
.venv/bin/python -m picoin miner \
  --server "$PICOIN_MINER_SERVER" \
  --identity "$PICOIN_MINER_IDENTITY" \
  mine \
  --once \
  --workers "$PICOIN_MINER_WORKERS"
```

Run as a service:

```bash
sudo systemctl start picoin-miner
sudo journalctl -u picoin-miner -f
```

Miner troubleshooting:

| Symptom | Check |
| --- | --- |
| `invalid miner signature` | `PICOIN_NETWORK`, `PICOIN_CHAIN_ID`, `PyNaCl`, identity file, and exact server URL |
| No tasks | API health, miner registration, node sync status, validator availability |
| Rewards not reaching wallet | `PICOIN_MINER_REWARD_ADDRESS` and block ledger entries |
| Very slow pi calculation | Worker count, CPU limits, and dynamic task expiration from `/protocol` |

## Run A Validator

A validator verifies miner work, signs liveness heartbeats, participates in quorum, and earns validator rewards. Mainnet validator eligibility is backed by wallet stake, not only legacy internal stake.

Required validator config:

```env
PICOIN_NETWORK=picoin-mainnet-v1
PICOIN_CHAIN_ID=314159
PICOIN_VALIDATOR_SERVER=https://api.picoin.science
PICOIN_VALIDATOR_NODE_SERVER=http://127.0.0.1:8000
PICOIN_VALIDATOR_NODE_ADDRESS=https://validator.example.com
PICOIN_VALIDATOR_IDENTITY=/var/lib/picoin/data/mainnet/identities/validator-mainnet.json
PICOIN_VALIDATOR_REWARD_ADDRESS=<canonical-PI-reward-wallet>
PICOIN_VALIDATOR_LOOPS=1
PICOIN_VALIDATOR_SLEEP=5
```

The validator machine must run a synced local node. `PICOIN_VALIDATOR_NODE_ADDRESS` must be reachable by the network if that validator advertises liveness publicly.

Register or refresh validator identity:

```bash
cd /opt/picoin/picoin-proof-of-pi
set -a
source /etc/picoin/picoin.env
set +a

.venv/bin/python -m picoin validator \
  --server "$PICOIN_VALIDATOR_SERVER" \
  --identity "$PICOIN_VALIDATOR_IDENTITY" \
  register \
  --name validator-mainnet-1 \
  --overwrite
```

Stake the validator from a wallet that owns PI:

```bash
SERVER=https://api.picoin.science
VALIDATOR_ID=validator_xxxxxxxxxxxxxxxx
OWNER_WALLET=/secure/offline-or-operator/validator-owner.json

.venv/bin/python -m picoin tx --server "$SERVER" send \
  --wallet "$OWNER_WALLET" \
  --type stake \
  --stake-type validator \
  --validator-id "$VALIDATOR_ID" \
  --amount 31.416 \
  --fee 0.001
```

Run one validation loop:

```bash
.venv/bin/python -m picoin validator \
  --server "$PICOIN_VALIDATOR_SERVER" \
  --identity "$PICOIN_VALIDATOR_IDENTITY" \
  validate \
  --once \
  --node-server "$PICOIN_VALIDATOR_NODE_SERVER"
```

Run as a service:

```bash
sudo systemctl start picoin-validator
sudo journalctl -u picoin-validator -f
```

Verify validator status:

```bash
curl -s https://api.picoin.science/validators/status | python3 -m json.tool
```

A mainnet validator should show:

```text
online_status = online
sync_status = synced
eligible = true
reward_address = PI...
wallet_stake_locked >= 31.416
```

## Wallet And Transaction Checks

Query nonce:

```bash
ADDRESS=PI...
curl -s "https://api.picoin.science/wallet/$ADDRESS/nonce" | python3 -m json.tool
```

Send PI:

```bash
.venv/bin/python -m picoin tx --server https://api.picoin.science send \
  --wallet /secure/wallet.json \
  --to PI_RECIPIENT_ADDRESS \
  --amount 1.0 \
  --fee 0.001
```

Check transaction:

```bash
TX_HASH=<tx-hash>
curl -s "https://api.picoin.science/tx/$TX_HASH" | python3 -m json.tool
```

If a transaction fails with `invalid nonce`, query the wallet nonce and use the returned `next_nonce`.

## Routine Health Checks

```bash
SERVER=https://api.picoin.science

curl -s "$SERVER/protocol" | python3 -m json.tool
curl -s "$SERVER/node/sync-status" | python3 -m json.tool
curl -s "$SERVER/mempool/status" | python3 -m json.tool
curl -s "$SERVER/validators/status" | python3 -m json.tool
curl -s "$SERVER/audit/full" | python3 -m json.tool
curl -s "$SERVER/transactions/recent?limit=20" | python3 -m json.tool
```

Local service checks:

```bash
systemctl status picoin-node picoin-miner picoin-validator picoin-reconciler picoin-auditor --no-pager
journalctl -u picoin-node -n 100 --no-pager
journalctl -u picoin-miner -n 100 --no-pager
journalctl -u picoin-validator -n 100 --no-pager
```

## Launch Checklist

Before mainnet starts:

```text
[ ] Final mainnet commit selected and tagged
[ ] Tests pass on a clean install
[ ] Treasury wallet generated offline
[ ] Governance wallet generated offline
[ ] Miner reward wallet generated
[ ] Validator reward wallets generated
[ ] Final genesis allocation file created
[ ] Final genesis hash computed and published
[ ] /etc/picoin/picoin.env has no CHANGE_ME values
[ ] PICOIN_NETWORK=picoin-mainnet-v1
[ ] PICOIN_CHAIN_ID=314159
[ ] PICOIN_PROTOCOL_VERSION=1.0
[ ] PICOIN_TREASURY_WALLET is canonical and unique
[ ] PICOIN_GOVERNANCE_WALLET is canonical and unique
[ ] PICOIN_SCIENCE_RESERVE_AUTHORIZED_SIGNERS set
[ ] api.picoin.science DNS points to the mainnet bootstrap node
[ ] TLS certificate installed for api.picoin.science
[ ] picoin-node mainnet-preflight passes
[ ] audit/full returns valid=true
[ ] Initial validators are online, synced, staked, and eligible
[ ] Limited mining starts only after validators are healthy
```

## Security Rules

Do not commit:

```text
/etc/picoin/picoin.env
wallet JSON files
miner or validator identity JSON files
private keys
server backups
SQLite production databases
```

Use separate wallets for treasury, governance, miner rewards, validator rewards, and operational testing. Keep treasury and governance keys offline. Public servers should normally receive only public `PI...` reward addresses.

## More Documentation

| Document | Purpose |
| --- | --- |
| `picoin-proof-of-pi/deploy/README-mainnet.md` | Mainnet deployment runbook |
| `picoin-proof-of-pi/deploy/README-public-testnet.md` | Historical public-testnet deployment guide |
| `picoin-proof-of-pi/README.md` | Core developer and protocol reference |
| `picoin-desktop-wallet/README.md` | Desktop wallet build and usage |
| `picoin-web/README.md` | Web explorer and wallet frontend |
