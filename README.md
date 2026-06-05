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
| Genesis hash | `da286143167d14044c053fbb23fcf4673bb11bcd34fb1a11e5510ee8f8edb6e7` |
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

Canonical mainnet treasury and governance wallets:

```text
Scientific Development Treasury: PIE1EE818AA165EECC3F0CCF058F4FF7BC04517F8CD07385
Governance Wallet:              PI251078EE911B17EDC747DB5BDF505649ECAF60F787AA23
```

The public testnet has been used for launch rehearsals and can still be studied from its deployment guide, but production configuration must use the mainnet values above.

## Repository Layout

| Path | Purpose |
| --- | --- |
| `picoin-proof-of-pi/` | Core protocol, API, CLI, node, miner, validator, tests, deploy scripts |
| `picoin-proof-of-pi/deploy/` | Production runbooks, systemd services, env templates |
| `picoin-web/` | Web explorer and web wallet frontend |
| `picoin-desktop-wallet/` | Desktop wallet, API based, no embedded node |
| `picoin-desktop-miner-source/` | Desktop miner UI and packaged mining client |
| `picoin-desktop-validator/` | Desktop validator UI with embedded local node and validator stake flow |
| `api/` | Supporting API/frontend glue used by deployments |

## Environment Files

There are six tracked env examples. Treat them as the source of truth.

| File | Use it for | Notes |
| --- | --- | --- |
| `picoin-proof-of-pi/deploy/mainnet.env.example` | Mainnet nodes, miners, validators | Production template. Every `CHANGE_ME` value must be replaced before services start. |
| `picoin-proof-of-pi/deploy/mainnet-shadow-full-node.env.example` | Disposable mainnet shadow full node | Read-only template for Phase 1B reproduction tests. Do not use on the mainnet bootstrap. |
| `picoin-proof-of-pi/deploy/mainnet-public-bootstrap-candidate.env.example` | Phase 2 public bootstrap candidate | Read-only public candidate template. Keep miner, validator, and auditor disabled; run reconciler so the candidate keeps up with mainnet. |
| `picoin-proof-of-pi/deploy/phase1-full-node.env.example` | Isolated Phase 1 full-node lab | Test-only template with a non-mainnet network id, chain id, and genesis hash. |
| `picoin-proof-of-pi/deploy/public-testnet.env.example` | Historical public-testnet rehearsal only | Keeps the old `public-testnet` and `picoin-public-testnet-v018` values for reference. Do not use for mainnet. |
| `picoin-proof-of-pi/.env.example` | Local development only | Uses `local` and `picoin-local-testnet`; useful for tests and isolated dev nodes. |

Critical mainnet variables:

```env
PICOIN_NETWORK=picoin-mainnet-v1
PICOIN_CHAIN_ID=314159
PICOIN_PROTOCOL_VERSION=1.0
PICOIN_GENESIS_ALLOCATIONS_FILE=/absolute/path/to/mainnet-genesis.allocations.final.json
PICOIN_GENESIS_HASH=da286143167d14044c053fbb23fcf4673bb11bcd34fb1a11e5510ee8f8edb6e7
PICOIN_TREASURY_WALLET=PIE1EE818AA165EECC3F0CCF058F4FF7BC04517F8CD07385
PICOIN_GOVERNANCE_WALLET=PI251078EE911B17EDC747DB5BDF505649ECAF60F787AA23
PICOIN_NODE_ADDRESS=https://api.picoin.science
PICOIN_MINER_SERVER=https://api.picoin.science
PICOIN_VALIDATOR_SERVER=https://api.picoin.science
PICOIN_SCIENCE_RESERVE_AUTHORIZED_SIGNERS=<signer-1>,<signer-2>
```

Miner and validator signatures include `network_id` and `chain_id`. If a worker falls back to `local` or a testnet chain ID, commits will be rejected with signature errors.

Use `https://api.picoin.science` for miners and validators. `http://api.picoin.science` redirects to HTTPS and can break POST requests such as `/tasks/commit`, causing `405 Method Not Allowed`.

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
PICOIN_NETWORK=picoin-mainnet-v1
PICOIN_CHAIN_ID=314159
PICOIN_PROTOCOL_VERSION=1.0
PICOIN_GENESIS_HASH=da286143167d14044c053fbb23fcf4673bb11bcd34fb1a11e5510ee8f8edb6e7
PICOIN_TREASURY_WALLET=PIE1EE818AA165EECC3F0CCF058F4FF7BC04517F8CD07385
PICOIN_GOVERNANCE_WALLET=PI251078EE911B17EDC747DB5BDF505649ECAF60F787AA23
PICOIN_FAUCET_ALLOWED_NETWORKS=
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
PICOIN_PROTOCOL_VERSION=1.0
PICOIN_GENESIS_HASH=da286143167d14044c053fbb23fcf4673bb11bcd34fb1a11e5510ee8f8edb6e7
PICOIN_TREASURY_WALLET=PIE1EE818AA165EECC3F0CCF058F4FF7BC04517F8CD07385
PICOIN_GOVERNANCE_WALLET=PI251078EE911B17EDC747DB5BDF505649ECAF60F787AA23
PICOIN_FAUCET_ALLOWED_NETWORKS=
PICOIN_MINER_SERVER=https://api.picoin.science
PICOIN_MINER_IDENTITY=/var/lib/picoin/data/mainnet/identities/miner-mainnet.json
PICOIN_MINER_REWARD_ADDRESS=<canonical-PI-reward-wallet>
PICOIN_MINER_WORKERS=1
PICOIN_MINER_LOOPS=1
PICOIN_MINER_SLEEP=5
```

### Linux Command-Line Miner

Use this path for a clean Ubuntu/Linux miner. Replace `YOUR_PI_WALLET_ADDRESS` with a wallet address you control. The wallet file itself is not needed on the miner; the miner only needs the public reward address.

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-pip

git clone https://github.com/devcoffeecoin/PICOIN.git
cd PICOIN/picoin-proof-of-pi

python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

export PICOIN_NETWORK=picoin-mainnet-v1
export PICOIN_CHAIN_ID=314159
export PICOIN_PROTOCOL_VERSION=1.0
export PICOIN_GENESIS_HASH=da286143167d14044c053fbb23fcf4673bb11bcd34fb1a11e5510ee8f8edb6e7
export PICOIN_TREASURY_WALLET=PIE1EE818AA165EECC3F0CCF058F4FF7BC04517F8CD07385
export PICOIN_GOVERNANCE_WALLET=PI251078EE911B17EDC747DB5BDF505649ECAF60F787AA23
export PICOIN_FAUCET_ALLOWED_NETWORKS=
export PICOIN_MINER_REWARD_ADDRESS=YOUR_PI_WALLET_ADDRESS

.venv/bin/python -m picoin miner \
  --server https://api.picoin.science \
  --identity ./miner-mainnet.json \
  register \
  --name miner-mainnet-1 \
  --overwrite

.venv/bin/python -m picoin miner \
  --server https://api.picoin.science \
  --identity ./miner-mainnet.json \
  mine \
  --loops 999999 \
  --sleep 1 \
  --workers 1
```

Healthy mining logs should include:

```text
Task assigned
Commit accepted
Reveal accepted
Waiting for an external validator to approve the block
Done. accepted=1 attempts=1
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
| `mainnet PICOIN_TREASURY_WALLET is required` | Export the canonical mainnet treasury and governance variables above |
| `mainnet genesis allocations must fund wallet accounts only` | Do not provide a genesis allocations file for a normal miner; if running a node, every genesis allocation must use `account_type: wallet` |
| `405 Method Not Allowed` on `/tasks/commit` | Use `https://api.picoin.science`, not `http://api.picoin.science` |
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

## Decentralization Roadmap

This section is the tracked mainnet decentralization artifact. Items remain unchecked until they are implemented on an isolated branch, deployed to independent test droplets, verified with reproducible local audits, and explicitly approved for mainnet. Mainnet stability takes priority over roadmap speed.

Status rules:

- `[ ]` Pending or untested
- `[x]` Tested on isolated droplets and accepted for the next stage

### Phase 0: Stable Mainnet Baseline

Mainnet stays on the current stable path while decentralization work happens separately.

- [ ] Keep mainnet mining, validators, explorer, wallet, and bootstrap health stable while Phase 1 is tested separately
- [ ] Monitor competitive mining rounds for accepted/rejected closure, stuck validation jobs, validator lag, and audit validity
- [ ] Keep bootstrap running only the required API/node role unless explicitly testing another service
- [ ] Keep desktop validator recovery under review until community reports confirm stable long-running sync
- [ ] Do not merge decentralization code into mainnet until the Phase 1 acceptance gates below pass

### Phase 1: Independent Full Node Verification

Goal: any operator can run a Linux node on a clean droplet, sync from a canonical checkpoint or replay path, and independently verify the same chain state as the bootstrap without mining or validating.

Test scope:

- [x] Create an isolated decentralization branch from stable main
- [x] Provision at least two independent test droplets that are not mainnet bootstrap machines
- [x] Configure a separate network id, chain id, env file, data directory, and DNS/API endpoints for the test lab
- [x] Start bootstrap-test and full-node-test roles without enabling miner, validator, reconciler, or auditor services by default
- [x] Document clean install commands for Ubuntu 22.04/24.04 full nodes
- [x] Add a reproducible snapshot restore path for a fresh full node
- [x] Add a reproducible block/header catch-up path after snapshot restore
- [x] Verify local `/health`, `/protocol`, `/node/sync-status`, `/audit/full`, and checkpoint endpoints on each full node
- [x] Verify full nodes compute the same latest height, block hash, state root, balances hash, validators hash, and pending rewards hash as the test bootstrap
- [x] Verify a full node can restart from disk and remain consistent without manual database edits
- [x] Verify a full node can fall behind, catch up, and recover without replay divergence
- [x] Verify a full node rejects snapshots or blocks from the wrong network id, chain id, genesis hash, or protocol version
- [x] Verify a full node can serve read-only explorer/wallet API requests locally
- [x] Verify no private wallet, miner, or validator identity files are required for a read-only full node
- [x] Produce a full-node operator runbook for the community

Phase 1 lab evidence:

- [x] `phase1-bootstrap-test` (`159.65.35.231`) started on isolated network `picoin-phase1-fullnode-lab-v1`, chain `31415991`, genesis `060d1089d198a42d043ea19e89d5d5aa08b40446a713a3c5f6df84c50b13fed5`
- [x] `phase1-full-node-a` (`143.110.172.254`) caught up from height `0` to height `5` and matched bootstrap hash `f18b8961345f14011a7786771bfa3d7aeb6d11820ea793590bb86864ac1f1aa1`
- [x] `phase1-full-node-b` (`209.38.90.231`) restored the canonical snapshot at height `5`, restarted cleanly, caught up to height `7`, and matched bootstrap hash `98a6db57d4d56b2e26003712eea77e142815e6b588b4c005c54750ebb06c0699`
- [x] `phase1-full-node-a` and `phase1-full-node-b` both synced to bootstrap height `7` with matching tip hash `98a6db57d4d56b2e26003712eea77e142815e6b588b4c005c54750ebb06c0699`
- [x] `phase1-full-node-b` matched bootstrap checkpoint fields for state root, balances hash, validators hash, pending rewards hash, protocol params hash, retarget events hash, and snapshot hash
- [x] Negative env identity test rejected mismatched network id, chain id, and genesis hash before accepting the node as valid
- [x] Real env control returned `status=ok`, `errors=0`, `warnings=0`, `lag=0`, and matching local/peer tip hash at height `5`
- [x] Full-node operator runbook is tracked at `picoin-proof-of-pi/deploy/README-full-node-phase1.md`

Acceptance gates:

- [x] Two independent droplets sync to the same test height and hashes
- [x] `/audit/full` returns `valid=true` on every full node
- [x] Full nodes stay healthy across at least one restart and one catch-up cycle
- [x] No manual SQLite edits are required during setup or recovery
- [x] The test lab can be rebuilt from the documented commands
- [x] Mainnet remains untouched during testing

### Phase 1B: Mainnet Shadow Full Node

Goal: prove the Phase 1 full-node path can reproduce live mainnet as a read-only shadow node before any public bootstrap or miner/validator failover work begins.

- [x] Provision one disposable mainnet-shadow full node that is not the mainnet bootstrap
- [x] Start only `picoin-node`; keep miner, validator, reconciler, and auditor disabled
- [x] Restore from `https://api.picoin.science` canonical snapshot without manual SQLite edits
- [x] Catch up to live mainnet tip with zero lag and matching effective block hash
- [x] Verify `/audit/full valid=true`, replay healthy, no divergence, and checkpoint/snapshot hashes match
- [x] Restart the shadow node and verify it remains healthy from disk
- [x] Destroy or keep the shadow node read-only after evidence is recorded

Phase 1B evidence:

- [x] `mainnet-shadow-full-node-1` (`137.184.59.98`) restored from `https://api.picoin.science`, verified height `4504`, lag `0`, and matched tip hash `9bbc91b7fab575fb00736bd2ff770f55cf6440c361b4f614a3f6c00371acb004`
- [x] Shadow node matched mainnet checkpoint fields for state root, balances hash, validators hash, pending rewards hash, protocol params hash, retarget events hash, and snapshot hash
- [x] After restart, `mainnet-shadow-full-node-1` caught up from snapshot height `4504` to live height `4508` with lag `0`, audit valid, no replay divergence, and matching tip hash `63e5595549e39c719f20245ad06dd19cd834adbf8274ba4cf1b45801ca14c647`
- [x] Service check confirmed only `picoin-node` active; miner, validator, reconciler, and auditor remained inactive
- [x] Negative snapshot import on `mainnet-shadow-full-node-1` rejected mismatched network id, chain id, and genesis hash, then remained replay healthy at height `4508`
- [x] Local read-only API check on `mainnet-shadow-full-node-1` returned JSON for health, protocol, sync status, stats, blocks, mempool, recent transactions, validators, miners, account balance, and account history endpoints
- [x] Decision recorded: keep `mainnet-shadow-full-node-1` alive as a read-only observer while Phase 2 is planned

### Phase 2: Multiple Public Bootstrap Nodes

Goal: replace the single public bootstrap dependency with multiple API/bootstrap nodes that serve the same chain view.

- [x] Add reproducible public bootstrap candidate env template, runbook, and read-only comparison verifier
- [x] Deploy at least three public bootstrap candidates in different regions
- [x] Add node identity and peer health checks for bootstrap candidates
- [x] Add explorer and wallet read failover across bootstrap endpoints
- [x] Verify initial public bootstrap candidates agree on height, block hash, state root, and audit validity
- [x] Verify one bootstrap candidate can go offline without losing read-only bootstrap quorum
- [x] Verify one production web bootstrap route can go offline without stopping explorer or wallet reads; miner and validator traffic remains pinned to the primary API
- [x] Publish bootstrap endpoint list and operator requirements

Phase 2 preparation evidence:

- [x] Public bootstrap candidate env template is tracked at `picoin-proof-of-pi/deploy/mainnet-public-bootstrap-candidate.env.example`
- [x] Public bootstrap candidate runbook is tracked at `picoin-proof-of-pi/deploy/README-bootstrap-phase2.md`
- [x] Read-only multi-bootstrap comparison verifier is tracked at `picoin-proof-of-pi/deploy/scripts/bootstrap-phase2-verify.py`
- [x] `mainnet-bootstrap-candidate-b` (`138.68.139.141`) restored from mainnet, caught up to height `4571`, and matched tip hash `92519b241db6d20300e2cfe583836fabfa1f37c946c9923674abcdf1a73c766c`
- [x] `mainnet-bootstrap-candidate-a` (`178.62.30.17`) restored from `mainnet-bootstrap-candidate-b`, caught up to height `4571`, and matched tip hash `92519b241db6d20300e2cfe583836fabfa1f37c946c9923674abcdf1a73c766c`
- [x] Phase 2 verifier compared candidates A and B with `status=ok`, `checked=59`, `errors=0`, `warnings=0`, and matching checkpoint hashes
- [x] `mainnet-bootstrap-candidate-c` (`159.89.115.183`) restored from `mainnet-bootstrap-candidate-b`, caught up to height `4571`, and matched tip hash `92519b241db6d20300e2cfe583836fabfa1f37c946c9923674abcdf1a73c766c`
- [x] Phase 2 verifier compared candidates A, B, and C with `status=ok`, `checked=88`, `errors=0`, `warnings=0`, and matching read-only endpoint responses
- [x] Controlled offline drill stopped `mainnet-bootstrap-candidate-c`; candidates A and B remained healthy with `status=ok`, `checked=59`, `errors=0`, `warnings=0`, height `4571`, and matching tip hash `92519b241db6d20300e2cfe583836fabfa1f37c946c9923674abcdf1a73c766c`
- [x] After restarting `mainnet-bootstrap-candidate-c`, candidates A, B, and C returned to `status=ok`, `checked=88`, `errors=0`, `warnings=0`, height `4571`, and matching tip hash `92519b241db6d20300e2cfe583836fabfa1f37c946c9923674abcdf1a73c766c`
- [x] Published candidate endpoint list and operator requirements in `picoin-proof-of-pi/deploy/README-bootstrap-phase2.md`
- [x] Added web read failover for explorer, miner search, transaction lookup, and wallet balance/history through same-origin `/api/bootstrap-*` routes; signed wallet submissions remain pinned to the primary route until write propagation is tested
- [x] Added `picoin-web/tests/phase2-failover.test.mjs` to verify read failover and primary-only wallet POST behavior
- [x] Added `picoin-web/tests/phase2-web-routes-smoke.mjs` to verify deployed `/api/bootstrap-*` routes before the final production failover drill
- [x] Production web failover drill with `mainnet-bootstrap-candidate-c` stopped passed against `/api/bootstrap`, `/api/bootstrap-a`, and `/api/bootstrap-b` with `required=3`, `allowed_lag=5`, `status=ok`, `healthy_routes=3`, and `errors=0`
- [x] `picoin-reconciler` is enabled on bootstrap candidates A, B, and C so read-only candidates keep catching up automatically while miner, validator, and auditor services remain disabled
- [x] After restarting `mainnet-bootstrap-candidate-c`, deployed web route smoke passed with `/api/bootstrap`, `/api/bootstrap-a`, `/api/bootstrap-b`, and `/api/bootstrap-c`: `required=4`, `allowed_lag=5`, `status=ok`, `healthy_routes=4`, `errors=0`, primary height `4815`, candidate height `4814`

### Phase 3: Peer Gossip And Consensus Propagation

Goal: nodes exchange chain data and consensus messages directly instead of relying on one coordinator API.

- [x] Add read-only multi-peer reconcile selection for catch-up from more than one compatible peer
- [x] Add deterministic peer selection filters for local self, stale peers, duplicate addresses, wrong network id, wrong chain id, wrong genesis hash, and wrong protocol version
- [x] Add block payload relay for `/node/blocks/receive` with source-peer exclusion and queued-header duplicate suppression
- [ ] Gossip block headers and finalized block payloads between peers
- [ ] Gossip signed transactions and mempool inventory between peers
- [ ] Gossip validator heartbeats and validator votes between peers
- [ ] Add deterministic duplicate suppression for peer messages
- [ ] Add peer scoring, stale peer detection, and peer ban/cooldown rules
- [ ] Verify nodes can catch up from multiple peers instead of one bootstrap

Phase 3 alpha evidence:

- [x] `select_reconcile_peers()` now returns unique, connected, locally compatible peers for read-only reconcile/catch-up
- [x] `/node/reconcile` multi-peer mode now reports `selected_peers` so operators can see which peers were attempted
- [x] Added tests for filtering local/stale/wrong-identity peers and attempting multiple selected peers in one reconcile pass
- [x] `POST /node/blocks/receive` now relays new pending block payloads to peers with `gossip=false`, excludes the source peer, and does not re-gossip duplicate queued headers

### Phase 4: Decentralized Mempool And Block Candidate Construction

Goal: every full node can independently validate transaction ordering and reconstruct the same candidate block state.

- [ ] Define canonical transaction selection rules for competitive rounds
- [ ] Verify deterministic nonce ordering, fee ordering, and tx merkle root generation across nodes
- [ ] Propagate signed transactions without exposing private keys
- [ ] Add conflict handling for replaced, expired, failed, or already-confirmed transactions
- [ ] Verify candidate block replay produces identical state roots across nodes

### Phase 5: Miner Task Independence

Goal: miners can request the same canonical competitive round work from any healthy node.

- [ ] Derive competitive task ranges from canonical height, previous block hash, and protocol params
- [ ] Allow multiple full nodes to serve the same round without creating conflicting work
- [ ] Verify first valid reveal wins independent of which node assigned the task
- [ ] Verify late reveals become stale consistently across nodes
- [ ] Verify miners can fail over to another node without losing identity or reward wallet configuration

### Phase 6: Validator Finality Certificates

Goal: a block becomes canonical by validator quorum certificate, not by one API database decision.

- [ ] Define signed finality certificate schema for each block
- [ ] Include quorum validator ids, signatures, reward addresses, and protocol params id in canonical payloads
- [ ] Reject conflicting certificates for the same height unless deterministic rules select one valid canonical block
- [ ] Add slashing evidence for validators that sign conflicting blocks at the same height
- [ ] Verify all full nodes can validate finality certificates from disk after restart

### Phase 7: Exchange And Infrastructure Full-Node Package

Goal: exchanges and infrastructure operators can run PICOIN without depending on the public API server.

- [ ] Provide Linux full-node install package or script
- [ ] Provide wallet manager commands for address creation, balance checks, nonce checks, and signed withdrawals
- [ ] Provide local API/RPC endpoints for deposits, withdrawals, confirmations, blocks, transactions, and health
- [ ] Document confirmation policy and block maturity behavior
- [ ] Provide backup, restore, audit, and monitoring runbooks

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
| `picoin-proof-of-pi/deploy/README-full-node-phase1.md` | Decentralization Phase 1 full-node verification lab |
| `picoin-proof-of-pi/deploy/README-mainnet-shadow-full-node.md` | Phase 1B read-only mainnet shadow full-node verification |
| `picoin-proof-of-pi/deploy/README-bootstrap-phase2.md` | Phase 2 public bootstrap candidate and failover verification |
| `picoin-proof-of-pi/deploy/README-public-testnet.md` | Historical public-testnet deployment guide |
| `picoin-proof-of-pi/README.md` | Core developer and protocol reference |
| `picoin-desktop-wallet/README.md` | Desktop wallet build and usage |
| `picoin-desktop-validator/README.md` | Desktop validator build, embedded node and stake flow |
| `picoin-web/README.md` | Web explorer and wallet frontend |
