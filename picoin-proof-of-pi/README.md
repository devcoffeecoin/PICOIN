# picoin-proof-of-pi

Functional MVP of **Proof of Pi** with an L1 extension called **Science Compute Access Layer**. A coordinator assigns small ranges of hexadecimal digits of pi, a miner calculates the segment with BBP, the validator recalculates it independently and the server records accepted blocks with a simulated reward. The Science layer prepares the network for a future L2 marketplace for scientific computing and AI.

This project does not execute heavy AI/scientific computing. Since v0.18 it includes a Public Testnet Deployment Kit to run nodes on droplets/real servers with a public env, systemd, health checks, and backups. On L1 it coordinates stake, access, jobs, hashes, reserve, and verified payments to prepare a future evolution.

## Protocol v0.18

Current parameters:

```text
protocol_version = 0.18
network_id = local
algorithm = bbp_hex_v1
validation_mode = external_commit_reveal
required_validator_approvals = 3
range_assignment_mode = pseudo_random
max_pi_position = 10000
range_assignment_max_attempts = 512
segment_size = 64
sample_count = 32
task_expiration_seconds = 600
max_active_tasks_per_miner = 1
genesis_supply = 3.1416
base_reward = 3.1416
difficulty = 4.0
reward_per_block = 3.1416
validator_reward_percent = 10%
validator_reward_pool_per_block = 0.31416
proof_of_pi_reward_percent = 67%
proof_of_pi_reward_per_block = 2.104872
science_compute_reward_percent = 20%
science_compute_reserve_per_block = 0.62832
science_reserve_account_id = science_compute_reserve
science_reserve_status = RESERVE_LOCKED
science_reserve_governance_timelock = 86400 seconds
science_reserve_multisig_threshold = 2
scientific_development_reward_percent = 3%
scientific_development_treasury_per_block = 0.094248
scientific_development_unlock_interval_days = 90
science_base_monthly_quota_units = 100
validator_auditor_reward_percent = 10%
retroactive_audit_interval_blocks = 314
retroactive_audit_sample_multiplier = 2
retroactive_audit_reward_percent = 20%
retroactive_audit_reward_per_audit = 0.62832
fraud_miner_penalty_points = 20
fraud_validator_invalid_results = 3
fraud_cooldown_seconds = 3600
min_validator_stake = 31.416
validator_slash_invalid_signature = 3.1416
penalty_invalid_result = 1
penalty_duplicate = 3
penalty_invalid_signature = 5
cooldown_after_rejections = 3
cooldown_seconds = 300
task_rate_limit = 12 assignments / 60 seconds
faucet_enabled_networks = local
faucet_rate_limit = 3 credits / account / hour
validator_selection_mode = weighted_reputation_stake_rotation
max_transactions_per_block = 100
```

The endpoint `GET /protocol` returns these values so miners and validators know which rules are active. Since v0.16 these parameters live in SQLite, en `protocol_params`, and can change automatically by epochs. `network_id` viene of `PICOIN_NETWORK`; by default it is `local`.

Difficulty is calculated with a simple and auditable formula:

```text
difficulty =
  (segment_size / 64)
  * (sample_count / 8)
  * (log10(max_pi_position) / log10(10000))

miner_reward_per_block = base_reward * 0.67
validator_reward_pool_per_block = base_reward * 0.10
science_compute_reserve_per_block = base_reward * 0.20
scientific_development_treasury_per_block = base_reward * 0.03
retroactive_audit_reward_per_audit = base_reward * 0.20
```

Difficulty regulates work; it does not multiply issuance. The `base_reward` is the total base block emission and is distributed as `67/20/10/3`: `2.104872` for the miner Proof of Pi, `0.62832` for `science_compute_reserve`, `0.31416` for validators/auditors and `0.094248` for the Scientific Development Fund with timelock.

Picoin finances scientific infrastructure and protocol development through a time-locked treasury sustained by ongoing network activity rather than large upfront premine allocations. The account `genesis` not longer represents a large premine: is limited to a normal emission of `3.1416` for local testnet/faucet compatibility. The current validator stake is simulated metadata/collateral until real transaction-based staking is implemented.

Each accepted block credits `2.104872` coins to the winning miner, `0.62832` to the scientific reserve, `0.094248` to the locked Scientific Development Fund and `0.31416` coins distributed among approving validators when the external validation flow reaches quorum. Each automatic retroactive audit credits `0.62832` additional coins to `audit_treasury`.

The 20% scientific allocation is not automatically paid to workers. By default it accumulates as a locked reserve with `status = RESERVE_LOCKED`. While it remains locked, it cannot be transferred, claimed, budget-reserved, or used to pay workers. Only when a future L2 is activated by governance/multisig with timelock, the reserve may be used for jobs `accepted`, with worker, `result_hash`, `proof_hash` and reserved budget. Jobs `rejected`, `disputed` or `expired` not pay.

Each block stores the difficulty and reward used at the time it was accepted.
Tasks and blocks also store `protocol_params_id`, so a retarget does not change the rules of a task that had already been assigned.

### Transactions in blocks

The path toward mainnet already includes basic accounting for signed transactions:

- Wallets use Ed25519 and addresses `PI...`.
- The mempool validates `tx_hash`, signature, `chain_id`, `network_id`, addresses `PI...`, positive nonce and maximum fee.
- The CLI can query the next nonce with `wallet nonce` and `tx send` obtains it automatically if `--nonce`.
- When mining a block, the node selects executable transactions by fee and timestamp without reordering nonces from the same sender, rejects those without valid signature/balance/nonce and calculates `tx_merkle_root`.
- The block stores `tx_count`, `tx_hashes`, `tx_merkle_root`, `fee_reward` and `state_root`.
- When accepting/importing the block, L1 applies a debit to the sender, credit to the recipient, and fee to the miner and marks the transaction as `confirmed`.
- `state_root` is a SHA-256 fingerprint of the accounting state after block replay. If a node changes the local ledger, `verify_chain()` detects that the state not longer matches.
- `verify_chain()` recalculates the canonical hash including the transaction commitment and compare `state_root` when it exists, making the block auditable.
- Canonical checkpoints store `height`, `block_hash`, `state_root`, `balances_hash`, `snapshot_hash` and ledger counters to accelerate future sync and verify snapshots without trusting heavy files.
- An exported canonical snapshot includes checkpoint metadata and aggregated balances by account. The import validates `chain_id`, `network_id`, `genesis_hash`, `balances_hash`, `state_root` and `snapshot_hash` before saving it as an external reference.
- An imported snapshot can be activated as `active_snapshot_base`; from there the node requests from peers only blocks with `height` after the snapshot and accepts the next block if its `previous_hash` points to the `block_hash` of the checkpoint.
- For real fast-sync, a snapshot imported can be applied as the local initial state if the node does not yet have local blocks. This restores aggregated balances from the snapshot and then allows canonical replay of later blocks.
- Initial wallet funding must come from a canonical file `PICOIN_GENESIS_ALLOCATIONS_FILE`, not from manual SQLite edits. All nodes calculate the same genesis hash with `python -m picoin node genesis-hash --file <allocations.json>` and apply the same entries `genesis_allocation` at initialization.
- `stake` locks PI from the wallet to `science_stake:<address>` and updates the scientific tier deterministically.
- `unstake` releases the full scientific stake if the address has not active jobs.
- `science_job_create` creates L1 jobs from a signed payload, with a deterministic `job_id` if none is provided.
- `governance_action` executes canonical actions of `science_reserve`: `propose_activation`, `approve_activation`, `execute_activation`, `pause` and `unpause`.

For now these are executed inside the block `transfer`, `stake`, `unstake`, `science_job_create`, `governance_action` and `treasury_claim`.

`treasury_claim` moves funds from the Scientific Development Treasury only if the signing wallet is the configured governance/owner wallet, the destination is the configured treasury wallet, the timelock has already unlocked funds and the `claim_id` was not used before.

Automatic retarget:

```text
epoch_blocks = 5
target_block_ms = 60000
tolerance = 20%
max_adjustment_factor = 1.25
```

When enough blocks are accepted to close an epoch, the coordinator measures `blocks.total_task_ms`. The goal is for each accepted block to take about 1 minute. If the average was too fast, difficulty increases for the next jobs. If it was too slow, it decreases it. The adjustment is conservative and creates a new active row en `protocol_params`; previous blocks keep the difficulty with which they were accepted.

## Architecture

```text
picoin-proof-of-pi/
  app/
    api/          FastAPI REST endpoints
    core/         Configuration, SHA-256 hashing and BBP pi calculation
    db/           SQLite and simple migrations
    models/       Pydantic schemas
    services/     Tasks, blocks, rewards, penalties, transactions
    web/          Static local dashboard served by FastAPI
  validator/      Independent Proof of Pi verification
  miner/          Miner client executable by users
  tests/          Basic calculation and validator tests
```

## Requirements

- Python 3.11+
- SQLite included with Python

## Installation

```powershell
cd C:\Users\LOQ\Documents\personal\PROYECTOS\PICOIN\picoin-proof-of-pi
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run the Server

```powershell
.\.venv\Scripts\python.exe -m picoin node start --reload
```

The API is available at:

- `http://127.0.0.1:8000`
- Interactive docs: `http://127.0.0.1:8000/docs`
- Dashboard local: `http://127.0.0.1:8000/dashboard`

The SQLite database is created automatically in `data/picoin.sqlite3`.

## Local Dashboard

Since v0.16, the node serves an operational web panel at:

```text
http://127.0.0.1:8000/dashboard
```

The dashboard consumes the REST API from the same node and displays:

- Accepted block explorer with height, miner, range, reward, difficulty, and hash.
- Validator state, including reputation, stake, selection score, recent votes, and rewards.
- Visual faucet to credit demo balances to miners or validators on local network.
- Difficulty metrics, epoch progress, and retarget preview.
- Performance metrics by assignment, compute, commit, validation, and total.
- Economic audit summary and local chain integrity status.
- Manual retroactive audits with double sampling.
- Node operational state, mining readiness, and recent events.

## Local Node CLI

Since v0.13, Picoin includes a unified local CLI:

```powershell
.\.venv\Scripts\python.exe -m picoin --version
.\.venv\Scripts\python.exe -m picoin node start --reload
.\.venv\Scripts\python.exe -m picoin node status
.\.venv\Scripts\python.exe -m picoin node audit
.\.venv\Scripts\python.exe -m picoin node protocol
.\.venv\Scripts\python.exe -m picoin node doctor
```

The CLI also wraps miner, validator, and testnet:

```powershell
.\.venv\Scripts\python.exe -m picoin miner register --name alice
.\.venv\Scripts\python.exe -m picoin miner mine --once
.\.venv\Scripts\python.exe -m picoin validator register --name val1
.\.venv\Scripts\python.exe -m picoin validator validate --once
.\.venv\Scripts\python.exe -m picoin testnet reset
.\.venv\Scripts\python.exe -m picoin testnet bootstrap
.\.venv\Scripts\python.exe -m picoin testnet cycle
.\.venv\Scripts\python.exe -m picoin testnet continuous --miners 3 --loops 3
```

CLI of Science Compute Access Layer:

```powershell
.\.venv\Scripts\python.exe -m picoin science stake --amount 31416
.\.venv\Scripts\python.exe -m picoin science account
.\.venv\Scripts\python.exe -m picoin science create-job --type "ai_inference" --metadata-hash "hash..." --storage-pointer "ipfs://payload" --max-compute-units 10 --reward-per-unit 0.25 --max-reward 2.5
.\.venv\Scripts\python.exe -m picoin science jobs
.\.venv\Scripts\python.exe -m picoin science accept-job --job-id science_job_xxxxxxxxxxxxxxxx --worker-address worker-1 --result-hash hash... --proof-hash proof... --compute-units-used 8
.\.venv\Scripts\python.exe -m picoin science pay-worker --job-id science_job_xxxxxxxxxxxxxxxx
.\.venv\Scripts\python.exe -m picoin science reserve
.\.venv\Scripts\python.exe -m picoin science reserve-governance
.\.venv\Scripts\python.exe -m picoin science propose-l2-activation --signer signer-1
.\.venv\Scripts\python.exe -m picoin science approve-l2-activation --signer signer-2
.\.venv\Scripts\python.exe -m picoin science execute-l2-activation
.\.venv\Scripts\python.exe -m picoin reserve status
.\.venv\Scripts\python.exe -m picoin reserve pause --signer signer-1
.\.venv\Scripts\python.exe -m picoin reserve unpause --signer signer-2
.\.venv\Scripts\python.exe -m picoin treasury status
.\.venv\Scripts\python.exe -m picoin treasury claim
```

`pay-worker` exists to leave the L2 path ready, but fails with `science compute reserve is locked until L2 marketplace activation` while the reserve is locked.
`treasury claim` only moves funds if unlocked balance already exists by the 90-day timelock and if the requester/destination match the governance wallet and treasury wallet configuradas.

Optional local config:

```powershell
Copy-Item .env.example .env
```

Supported variables:

```text
PICOIN_NETWORK=local
PICOIN_CHAIN_ID=picoin-local-testnet
PICOIN_NODE_ID=local-node
PICOIN_NODE_TYPE=full
PICOIN_NODE_ADDRESS=http://127.0.0.1:8000
PICOIN_BOOTSTRAP_PEERS=
PICOIN_HOST=127.0.0.1
PICOIN_PORT=8000
PICOIN_SERVER=http://127.0.0.1:8000
```

## Public Testnet Deployment Kit v0.18

Picoin includes a folder `deploy/` to launch a public node on Ubuntu/DigitalOcean without mixing the institutional website with the node:

- `deploy/public-testnet.env.example`: variables for bootstrap, full node, miner, validator, or auditor.
- `deploy/systemd/picoin-node.service`: `systemd` service with automatic restart.
- `deploy/scripts/install-systemd-service.sh`: installer for the service and `/etc/picoin/picoin.env`.
- `deploy/scripts/health-check.sh`: external check of `/health`, sync, audit and checkpoint.
- `deploy/scripts/backup-sqlite.sh`: compressed backup of `data/picoin.sqlite3`.
- `deploy/scripts/refresh-code.sh`: safe code refresh helper that backs up and preserves node state outside the repo.
- `deploy/README-public-testnet.md`: step-by-step deployment guide.

Base commands on the droplet:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip sqlite3 curl ufw
sudo useradd --system --create-home --home-dir /opt/picoin --shell /bin/bash picoin
sudo -u picoin git clone https://github.com/devcoffeecoin/PICOIN.git /opt/picoin/PICOIN
sudo -u picoin bash -lc 'cd /opt/picoin/PICOIN/picoin-proof-of-pi && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt'
sudo -u picoin bash -lc 'ln -s /opt/picoin/PICOIN/picoin-proof-of-pi /opt/picoin/picoin-proof-of-pi'
sudo PICOIN_REPO_DIR=/opt/picoin/picoin-proof-of-pi /opt/picoin/picoin-proof-of-pi/deploy/scripts/install-systemd-service.sh
sudo nano /etc/picoin/picoin.env
sudo systemctl start picoin-node
```

Readiness checks:

```bash
cd /opt/picoin/picoin-proof-of-pi
.venv/bin/python -m picoin node doctor --require-checkpoint
.venv/bin/python -m picoin node audit
.venv/bin/python -m picoin node sync-status
```

To connect a second droplet, configura `PICOIN_BOOTSTRAP_PEERS=http://BOOTSTRAP_PUBLIC_IP:8000`, uses a `PICOIN_NODE_ID` unico, restart the service and run `python -m picoin node reconcile` on both nodes.

To synchronize a lagging node in a single operation:

```bash
python -m picoin node catch-up --peer http://BOOTSTRAP_PUBLIC_IP:8000
```

`node catch-up` runs rounds of reconcile, consensus replay, sync-status and audit. If se pasa `--peer`, it also compares `network_id`, `chain_id`, `genesis_hash`, height and latest block hash against the peer. Termina with `status=ok` when there are not pending blocks, the economic audit is valid and the node matches the peer.

For a pass/fail report without modifying state:

```bash
python -m picoin node report --peer http://BOOTSTRAP_PUBLIC_IP:8000
```

`node report` checks health, audit, replay backlog, consensus, reserve/treasury, and comparison against peer.

To automate the public check of a node:

```bash
PICOIN_BOOTSTRAP_PEER=http://BOOTSTRAP_PUBLIC_IP:8000 deploy/scripts/public-testnet-smoke.sh
```

The smoke test runs `node catch-up`, `node report` and `node audit`, leaves the JSON files in `data/testnet/smoke` and termina with `PICOIN_SMOKE_STATUS=ok` only when the node is synchronized, the audit es valid and matches the configured peer.

## Run a Miner

In another terminal:

```powershell
cd C:\Users\LOQ\Documents\personal\PROYECTOS\PICOIN\picoin-proof-of-pi
.\.venv\Scripts\Activate.ps1
python -m miner.client register --name alice
python -m miner.client mine --once
```

The miner:

1. Generates a local Ed25519 identity in `miner_identity.json`.
2. Registers the miner with its `public_key`.
3. Requests a task from `GET /tasks/next`.
4. Receives a pseudo-random range of hexadecimal pi positions.
5. Calculates the assigned hexadecimal segment.
6. Genera `result_hash` with SHA-256.
7. Builds a Merkle root of the segment.
8. Sends a signed commit to `POST /tasks/commit`.
9. Receives sample positions generated by the server.
10. Reveals only those samples with Merkle proofs in `POST /tasks/reveal`.
11. Waits for votes from external validators.
12. When it reaches approval quorum, the server records the block.

Miner commands:

```powershell
python -m miner.client register --name alice
python -m miner.client mine --once
python -m miner.client mine --loops 10
python -m miner.client mine --loops 10 --workers 2
python -m miner.client stats
```

To use another identity file:

```powershell
python -m miner.client --identity alice_identity.json register --name alice
python -m miner.client --identity alice_identity.json mine --loops 10
```

To replace an existing local identity:

```powershell
python -m miner.client register --name alice --overwrite
```

The private key remains only in the local identity file. The server only receives the `public_key`.

## Run a Validator

In another terminal:

```powershell
python -m validator.client register --name val1
python -m validator.client validate --once
```

The validator:

1. Generates a local Ed25519 identity in `validator_identity.json`.
2. Registers the validator with its `public_key`.
3. Pide a job a `GET /validation/jobs`.
4. Recalculates each revealed position with BBP.
5. Verifies each Merkle proof against the `merkle_root`.
6. Signs the result.
7. Sends approval or rejection to `POST /validation/results`.

## Local Mining Testnet

The local testnet provides a repeatable flow with:

- controlled reset of SQLite and demo files
- demo miner identity
- 3 demo validator identities
- faucet local for the miner
- local FastAPI server
- full cycle: mine, reveal 32 samples, vote with 3 validators, and accept a block by quorum
- continuous mining with multiple miners and double-sample retroactive audits

### Full automatic flow

This command resets, creates identities, starts the server in the background, mines one block, runs the 3 validators, and shuts down the server when finished:

```powershell
.\scripts\testnet-all.ps1
```

If PowerShell blocks local scripts due to execution policy:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\testnet-all.ps1
```

With a different port:

```powershell
.\scripts\testnet-all.ps1 -Port 8001
```

### Recommended manual flow

1. Controlled reset:

```powershell
.\scripts\testnet-reset.ps1
```

2. Create demo identities and faucet:

```powershell
.\scripts\testnet-bootstrap.ps1
```

Esto crea:

```text
data/testnet/identities/miner-alice.json
data/testnet/identities/validator-one.json
data/testnet/identities/validator-two.json
data/testnet/identities/validator-three.json
data/testnet/manifest.json
```

3. Start server:

```powershell
.\scripts\testnet-server.ps1
```

4. In another terminal, execute a full cycle:

```powershell
.\scripts\testnet-cycle.ps1
```

You can also run each role separately:

```powershell
.\scripts\testnet-mine-once.ps1
.\scripts\testnet-validator1.ps1
.\scripts\testnet-validator2.ps1
.\scripts\testnet-validator3.ps1
```

The first two validators leave the job in `validation_pending`; the third completes quorum and the coordinator accepts the block.

### Continuous multi-miner mining

With the server running, you can test several miners competing in a repeatable way:

```powershell
.\.venv\Scripts\python.exe -m picoin testnet continuous --miners 3 --loops 3 --workers 1
```

Each accepted block triggers by default a retroactive audit with `sample_multiplier = 2`, es decir, 64 samples for protocol blocks v0.16. To disable it in a run:

```powershell
.\.venv\Scripts\python.exe -m picoin testnet continuous --miners 3 --loops 3 --no-retro-audit
```

### Local faucet

The faucet exists for local tests, not for mainnet. From CLI:

```powershell
python -m app.tools.faucet miner_xxxxxxxxxxxxxxxx --type miner --amount 10
```

From API:

```powershell
curl -X POST http://127.0.0.1:8000/faucet `
  -H "Content-Type: application/json" `
  -d '{"account_id":"miner_xxxxxxxxxxxxxxxx","account_type":"miner","amount":10}'
```

## Endpoints

### `GET /health`

Returns node operational health: SQLite connection, active version, uptime, current height, latest hash, chain verification, basic audit and whether there is enough quorum for mining.

```powershell
curl http://127.0.0.1:8000/health
```

### `GET /node/status`

Returns a broader snapshot of the local node: counters for miners, validators, tasks, validation jobs, active difficulty, performance, and summarized economics.

```powershell
curl http://127.0.0.1:8000/node/status
```

### `GET /events`

Lists normalized recent events for dashboard and debugging: accepted blocks, validator votes, faucet, penalties, and retargets.

```powershell
curl "http://127.0.0.1:8000/events?limit=20"
```

### `GET /protocol`

Returns active protocol parameters, incluyendo `base_reward`, `difficulty` and `reward_per_block`.

It also exposes the conceptual distribution of useful work:

```text
proof_of_pi_reward_percent = 0.67
science_compute_reward_percent = 0.20
validator_auditor_reward_percent = 0.10
scientific_development_reward_percent = 0.03
```

En this L1, the Science percentage is recorded as a per-block reserve and not as a direct payment. The 3% of the Scientific Development Fund is recorded in a separate treasury, locked for 90 days per quarterly epoch.

### `POST /science/stake`

Registers or updates scientific access stake. The tier is derived automatically:

```text
researcher   3,141.6 PI    multiplier 1x    priority low
lab          31,416 PI     multiplier 10x   priority medium
institution  314,160 PI    multiplier 100x  priority high
```

```powershell
curl -X POST http://127.0.0.1:8000/science/stake `
  -H "Content-Type: application/json" `
  -d '{"address":"lab-1","amount":31416}'
```

### `POST /science/jobs`

Creates an L1 scientific job. It does not execute real compute or store heavy files: only `metadata_hash`, `storage_pointer`, abstract units, economic limit and status. The future L2 will certify `compute_units_used`; L1 will only settle payment if the job reaches `accepted` and the reserve is active.

```powershell
curl -X POST http://127.0.0.1:8000/science/jobs `
  -H "Content-Type: application/json" `
  -d '{"requester_address":"lab-1","job_type":"ai_inference","metadata_hash":"hash...","storage_pointer":"ipfs://payload","max_compute_units":10,"reward_per_compute_unit":0.25,"max_reward":2.5}'
```

The maximum payment is bounded by:

```text
payout_amount = min(compute_units_used * reward_per_compute_unit, max_reward)
```

### `POST /science/jobs/{job_id}/transition`

Advances the job state with transition validations. Supported states:

```text
created -> queued -> assigned -> committed -> submitted -> verified -> accepted -> paid
created/queued/assigned/committed/submitted/verified -> rejected/disputed/expired
```

`submitted`, `verified` and `accepted` requieren `worker_address`, `result_hash` and `proof_hash`. `accepted` also requires `compute_units_used`, certified in the future by L2. By default, the requester cannot be the worker of its own job.

### `POST /science/jobs/{job_id}/pay`

Pays the worker only if the job this `accepted`, it has not been paid before, has a worker and has `payout_amount > 0`. Jobs `rejected`, `disputed`, `expired`, `submitted`, `verified` or incomplete jobs do not pay. While `science reserve status != L2_ACTIVE`, `payouts_enabled = false` or `emergency_paused = true`, this endpoint is disabled and does not move funds.

### `GET /science/reserve`

Returns the scientific reserve for the current epoch:

```text
total_reserved
total_pending
total_paid
available
status
activation_requested_at
activation_available_at
activated_at
governance_approvals
authorized_signers
payouts_enabled
emergency_paused
max_reward_per_job
max_payout_per_epoch
max_pending_per_requester
```

### `GET /reserve/status`

Operational alias of `/science/reserve` to query the Science Compute Marketplace Reserve:

```text
total_reserved
total_pending
total_paid
available
status
payouts_enabled
emergency_paused
```

### `POST /reserve/pause`

Pauses emergency payments. Requires an authorized signer.

### `POST /reserve/unpause`

Removes the pause. If the reserve has already been activated by timelock + multisig vuelve a `L2_ACTIVE`; otherwise, it remains locked.

### `GET /treasury/status`

Returns the Scientific Development Fund:

```text
total_accumulated
total_claimed
locked_balance
unlocked_balance
claimable
current_epoch
next_unlock_at
treasury_wallet
governance_wallet
history
```

### `POST /treasury/claim`

Claims only the unlocked balance. Before 90 days, it responds with active timelock. The operation is audited en `ledger_entries` and `scientific_development_treasury_claims`.

```powershell
curl -X POST http://127.0.0.1:8000/treasury/claim `
  -H "Content-Type: application/json" `
  -d '{"requested_by":"picoin_governance_multisig","claim_to":"picoin_scientific_development_wallet"}'
```

### `GET /science/reserve/governance`

Returns the governance state of the scientific reserve. By defecto:

```text
status = RESERVE_LOCKED
threshold = 2
timelock_seconds = 86400
```

### `POST /science/reserve/governance/propose-activation`

Starts the timelocked L2 activation process. Keeps the reserve locked and records the first signature.

```powershell
curl -X POST http://127.0.0.1:8000/science/reserve/governance/propose-activation `
  -H "Content-Type: application/json" `
  -d '{"signer":"signer-1"}'
```

### `POST /science/reserve/governance/approve-activation`

Adds a multisig approval. The MVP requires 2 distinct signers.

### `POST /science/reserve/governance/execute-activation`

Activates the reserve only if the multisig threshold of authorized signers has been met and the timelock has expired. Before of that, `payouts_enabled = false` and not payment can be executed.

### `GET /science/events`

L1 events prepared for future L2 synchronization:

```text
ScienceStakeUpdated
ScienceJobCreated
ScienceJobAssigned
ScienceJobCommitted
ScienceJobSubmitted
ScienceJobVerified
ScienceJobAccepted
ScienceJobRejected
ScienceJobPaid
ScienceJobDisputed
ScienceReserveAccrued
ScienceReserveActivationProposed
ScienceReserveActivationApproved
ScienceReserveActivated
ScienceReserveLocked
ScienceReserveUnlocked
ScienceReservePaused
ScienceReserveUnpaused
ScientificTreasuryClaimed
```

### `GET /protocol/history`

Returns the history of protocol parameters stored in SQLite. Each retarget that changes difficulty deactivates the previous set and creates a new one.

### `GET /difficulty`

Returns the automatic retarget state: altura actual, last adjusted height, blocks remaining for the next epoch, active difficulty and active reward.

### `GET /difficulty/history`

Lists retarget events already executed.

### `GET /difficulty/preview`

Simulates the next retarget without changing the database. Returns whether the epoch is ready, observed average, proposed action (`increase`, `decrease`, `keep` or `wait`) and the proposed protocol.

```powershell
curl http://127.0.0.1:8000/difficulty/preview
```

### `POST /difficulty/retarget`

Executes the retarget if the epoch is complete. For local tests, you can use:

```powershell
curl -X POST "http://127.0.0.1:8000/difficulty/retarget?force=true"
```

`force=true` allows testing the logic with fewer blocks, but the normal flow does not need it: when accepting blocks, the coordinator automatically attempts retarget.

### `POST /miners/register`

Registers a miner.

```json
{
  "name": "alice",
  "public_key": "ed25519:base64url_public_key"
}
```

### `GET /tasks/next?miner_id=...`

Assigns the next range of hexadecimal pi positions. If the miner already has a non-expired active task, it returns that same task.

Assignment is not longer sequential. The server derives a seed with:

```text
previous_hash
miner_id
task_id
task_counter
nonce
segment_size
max_pi_position
algorithm
```

Then it converts that seed into `range_start` and searches for a range with not overlap with active, committed, or accepted tasks. The task stores:

```text
assignment_seed
assignment_mode = pseudo_random
```

### `POST /tasks/commit`

Receives the result commitment. It does not receive the full segment.

```json
{
  "task_id": "task_xxxxxxxxxxxxxxxx",
  "miner_id": "miner_xxxxxxxxxxxxxxxx",
  "result_hash": "64_hex_chars",
  "merkle_root": "64_hex_chars",
  "signature": "base64url_signature",
  "signed_at": "2026-05-10T15:00:00+00:00"
}
```

Response:

```json
{
  "accepted": true,
  "status": "committed",
  "challenge_seed": "64_hex_chars",
  "samples": [
    {"position": 12},
    {"position": 33}
  ]
}
```

### `POST /tasks/reveal`

Revela the samples pedidas and sus Merkle proofs.

```json
{
  "task_id": "task_xxxxxxxxxxxxxxxx",
  "miner_id": "miner_xxxxxxxxxxxxxxxx",
  "samples": [
    {
      "position": 12,
      "digit": "A",
      "proof": [
        {"side": "right", "hash": "64_hex_chars"}
      ]
    }
  ],
  "signature": "base64url_signature",
  "signed_at": "2026-05-10T15:01:00+00:00"
}
```

Expected response:

```json
{
  "accepted": true,
  "status": "validation_pending",
  "message": "reveal accepted; waiting for external validator"
}
```

### `POST /validators/register`

Registers an external validator.

```json
{
  "name": "val1",
  "public_key": "ed25519:base64url_public_key"
}
```

### `POST /faucet`

Credits demo coins from `genesis` to a registered account. This route is only for `network_id = local` and has a per-account limit to avoid abuse in demos.

```json
{
  "account_id": "miner_xxxxxxxxxxxxxxxx",
  "account_type": "miner",
  "amount": 10
}
```

### `POST /maintenance/expire-tasks`

Runs controlled cleanup of expired tasks and validation jobs.

```powershell
curl -X POST http://127.0.0.1:8000/maintenance/expire-tasks
```

### `GET /validators/{validator_id}`

Queries a validator identity, history, and reputation. Includes:

- `accepted_jobs`
- `rejected_jobs`
- `completed_jobs`
- `invalid_results`
- `trust_score`
- `cooldown_until`
- `avg_validation_ms`
- `is_banned`

### `GET /validators`

Lists validators ordered by selection score. With `eligible_only=true` returns only validators eligible to receive jobs.

```powershell
curl "http://127.0.0.1:8000/validators?eligible_only=true"
```

Each validator includes selection fields:

- `selection_score`
- `selection_weight`
- `recent_validation_votes`
- `availability_score`

### `GET /validation/jobs?validator_id=...`

Delivers the next pending job if the validator belongs to the selected pool for that job. The pool is calculated with reputation, stake, availability, and recent rotation. The same job can be delivered to several different validators until quorum is reached. A validator cannot vote twice on the same job.

### `POST /validation/results`

Receives the validator signed vote. The block is accepted only when `approvals >= required_validator_approvals`.

```json
{
  "job_id": "job_xxxxxxxxxxxxxxxx",
  "validator_id": "validator_xxxxxxxxxxxxxxxx",
  "approved": true,
  "reason": "external validator accepted samples",
  "signature": "base64url_signature",
  "signed_at": "2026-05-10T15:02:00+00:00"
}
```

Response before quorum:

```json
{
  "accepted": true,
  "status": "validation_pending",
  "approvals": 1,
  "required_approvals": 3,
  "block": null
}
```

### `POST /tasks/submit`

Legacy endpoint for full segment validation. The current miner uses `commit` and `reveal`.

### `GET /blocks`

Lists accepted blocks.

### `GET /blocks/verify`

Audits the local chain of accepted blocks. Verifies:

- `height` incremental
- `previous_hash`
- `block_hash`
- duplicate ranges
- `result_hash` duplicates

### `GET /blocks/{height}`

Queries a block by height.

### `GET /miners/{miner_id}`

Queries data, reputation, and simulated rewards for a miner.

### `GET /balances`

Lists persistent account balances `genesis`, miners and validators.

### `GET /balances/{account_id}`

Queries an account balance.

### `GET /ledger`

Lists local ledger movements. Can be filtered by account:

```powershell
curl "http://127.0.0.1:8000/ledger?account_id=genesis"
```

### `GET /audit/summary`

Returns summary of issuance, circulating supply, locked stake, slashed stake, accepted blocks, and eligible validators.

### `GET /audit/full`

Runs a full economic audit and returns verifiable JSON. Checks:

- total balance sum against `genesis_supply + block_rewards + science_reserve + validator_rewards + audit_rewards`
- total ledger sum against the same monetary policy
- each account balance against its ledger movements
- accepted blocks against table `rewards`
- block rewards against movements `block_reward`
- additional validator rewards against movements `validator_reward`
- locked stake and validator slashing against ledger

```powershell
curl http://127.0.0.1:8000/audit/full
```

If `valid = false`, the response includes `issues` with codes such as `account_balance_mismatch`, `total_balances_mismatch` or `rewards_table_mismatch`.

### `GET /audit/retroactive`

Lists recent retroactive audits. Each audit stores block, seed, sample count, expected hash, recalculated hash, whether it was automatic, reward, and result.

```powershell
curl "http://127.0.0.1:8000/audit/retroactive?limit=20"
```

### `POST /audit/retroactive/run`

Runs a manual audit on an accepted block, or on a specific height. By default it uses double the samples of the block active protocol. Manual audits do not issue rewards; the 20% reward only applies to the automatic audit scheduled every 314 blocks.

```powershell
curl -X POST "http://127.0.0.1:8000/audit/retroactive/run?sample_multiplier=2"
curl -X POST "http://127.0.0.1:8000/audit/retroactive/run?block_height=3&sample_multiplier=2"
```

In v0.16 a new block uses 32 samples during normal validation and 64 samples during retroactive audit. Because the MVP does not store the full pi segment or a complete cryptographic proof, the audit recalculates the audited block segment with BBP and compares the `result_hash` recorded.

### `GET /stats`

Returns global MVP statistics.

### `GET /stats/performance`

Returns speed metrics:

```json
{
  "accepted_blocks": 1,
  "avg_compute_ms": 589.0,
  "avg_assignment_ms": 1.0,
  "avg_commit_ms": 2.0,
  "avg_validation_ms": 4.0,
  "avg_total_task_ms": 900.0,
  "pending_validation_jobs": 0,
  "bbp_digit_cache_hits": 8,
  "bbp_digit_cache_misses": 64
}
```

## Blocks

Each accepted block contains:

- `height`
- `previous_hash`
- `miner_id`
- `range_start`
- `range_end`
- `algorithm`
- `result_hash`
- `merkle_root`
- `samples`
- `timestamp`
- `block_hash`
- `reward`
- `difficulty`
- `protocol_params_id`
- `protocol_version`
- `validation_mode`
- `total_task_ms`
- `validation_ms`

## MVP Security

Implemented:

- SHA-256 for results and blocks.
- Chaining by `previous_hash`.
- Miner identification by `miner_id`.
- Ed25519 identity per miner.
- Mandatory Ed25519 signature in commit and reveal.
- Ed25519 identity per validator.
- Mandatory Ed25519 signature in validation results.
- Quorum of multiple validators per block.
- Validator reputation with `trust_score`.
- Validator selection/gating by reputation and minimum stake.
- Intelligent validator selection by weighted score.
- Rotation to avoid excessive concentration of validations.
- Simulated validator stake and slashing for invalid signatures.
- Additional reward for approving validators.
- Persistent balances and auditable ledger.
- Full economic audit en `/audit/full`.
- Science Compute Access Layer en L1.
- Scientific staking by tiers `researcher`, `lab`, `institution`.
- Reserve `science_compute_reserve` accrued per block.
- Reserve Science locked by default with `RESERVE_LOCKED`.
- Future activation by timelock + multisig before any payment.
- Scientific job recording by hashes and external pointers.
- Payments to workers only for accepted jobs not previously paid.
- Science events for future L2 synchronization.
- Random retroactive audits en `/audit/retroactive/run`.
- Automatic retroactive audit every 314 blocks.
- Fraudulent block marking if a retroactive audit fails.
- Reinforced penalty and 1-hour cooldown for detected fraud.
- Cooldown and ban for repeated invalid signatures.
- Commit-reveal with `result_hash` and `merkle_root`.
- Merkle proofs for each revealed sample.
- Independent recalculation by external validator.
- Deterministic samples generated after commit.
- Tasks with expiration.
- Manual cleanup of expired tasks and jobs.
- Maximum of one active task per miner.
- Simple rate limit for task assignment per miner.
- Pseudo-random range assignment based on `previous_hash`.
- Rejection of overlaps with active or accepted ranges.
- Rejection of already submitted or expired tasks.
- Rejection of `result_hash` duplicado.
- Penalties for invalid result, duplicate, or invalid signature.
- `trust_score` by miner.
- Temporary cooldown if too many penalties accumulate.
- SQLite restrictions to avoid double block per task.
- LRU cache for BBP digits.
- Performance metrics by task, commit, validation, and block.
- Initial dynamic difficulty based on segment size, samples, and maximum position.
- Network mode with `PICOIN_NETWORK`.
- Faucet enabled only on local network.
- Faucet rate limit per account.

Intentional limits:

- Distributed consensus is still evolving; v0.18 adds the public deployment kit over peers, mempool, wallets, proposals, votes, finalization, and initial replay.
- The current P2P network is basic: REST/WebSocket, heartbeat and replay queue, not optimized gossip.
- Wallets sign transactions for mempool; `transfer`, `stake` and `science_job_create` are already settled in the ledger when entering a block.
- There is not real AI execution or heavy scientific computing on L1.
- There is not L2 marketplace yet; L1 only leaves access, reserve, jobs, states, and verified payments.
- Current validation is probabilistic by samples, not a complete cryptographic proof of the entire calculation.

## Performance

The BBP calculation uses in-memory LRU cache for individual hexadecimal digits. This accelerates repeated sample validations and audits over previously seen positions.

Stored metrics:

```text
tasks.assignment_ms
tasks.compute_ms
commitments.commit_ms
validation_jobs.validation_ms
blocks.total_task_ms
blocks.validation_ms
```

The miner measures `compute_ms` locally and sends it in `POST /tasks/commit`. The server measures assignment, commit, external validation, and total time until accepted block.

The miner can also calculate segments using parallel processes:

```powershell
python -m miner.client mine --once --workers 2
```

For small ranges, `--workers 1` is usually faster due to lower overhead. For heavier positions or segments, compare with the benchmark before changing protocol parameters.

Benchmark BBP:

```powershell
python -m app.tools.benchmark_bbp --start 5000 --length 32 --workers 1 --rounds 1
python -m app.tools.benchmark_bbp --start 5000 --length 32 --workers 2 --rounds 1
```

Example observed in this environment:

```text
start=5000 length=32 workers=1 avg_ms=311
start=5000 length=32 workers=2 avg_ms=302
```

## Validator Reputation

Each validator keeps local reputation in SQLite:

```text
completed_jobs = accepted_jobs + rejected_jobs
trust_score = (completed_jobs + 1) / (completed_jobs + 1 + invalid_results * 2)
```

Accepting or rejecting a correctly signed job counts as completed work. A rejection does not lower reputation by itself, because it can be an honest decision. Invalid signatures do lower `trust_score`; after 3 invalid results the validator enters cooldown, and after 9 it is banned.

Each validator receives an initial simulated stake of `31.416`, funded from the account `genesis`. To receive jobs it must maintain at least that stake and `trust_score >= 0.25`. Each invalid signature slashes `3.1416` from the stake and returns it to `genesis`.

This model is not yet real transferable staking. It is an MVP layer to prioritize reliable validators, add simulated anti-Sybil cost, and detect broken or malicious behavior.

## Validator Selection

Since v0.12, the coordinator does not deliver jobs only by order of arrival. For cada `validation_job`, it calculates a pool of selected validators with:

```text
selection_score =
  trust_score * 0.55
  + stake_score * 0.25
  + availability_score * 0.10
  + rotation_score * 0.10
```

`stake_score` is normalized against the minimum stake, `availability_score` increases when the validator has been recently active, and `rotation_score` decreases if that validator has already voted many times in the last hour. A small deterministic tie-breaker based on `challenge_seed` prevents ties from always favoring the same ID.

The pool size is:

```text
required_validator_approvals * 2
```

En v0.16, with `required_validator_approvals = 3`, the target pool is 6 validators. If there are fewer eligible validators, it uses all available validators. This maintains speed for local testnet, but reduces concentration when there are more validators than the minimum quorum.

## MVP Economics

Current rules:

```text
genesis_supply = 3.1416
block_emission = 3.1416
miner_reward = 2.104872
science_compute_reserve = 0.62832
validator_reward_pool = 0.31416
scientific_development_treasury = 0.094248
total_minted_per_accepted_block = 3.1416
validator_initial_stake = 31.416
validator_slash_invalid_signature = 3.1416
```

Genesis is recorded en `ledger_entries` with `block_height = 0` for local testnet compatibility, pero only by `3.1416`. Each accepted block creates a movement `block_reward` for the miner, `science_reserve_accrual` for the reserve compute, `scientific_development_treasury_accrual` for the time-locked treasury and movements `validator_reward` for the validators who approved the block. The initial validator stake is not funded from genesis; it is a simulated eligibility parameter until real staking is activated.

Audited monetary policy en v0.11:

```text
expected_total_balances =
  genesis_supply
  + accepted_block_rewards
  + science_compute_reserve_accruals
  + validator_rewards
  + scientific_development_treasury_accruals
  + retroactive_audit_rewards
```

`genesis`, faucet local, treasury claims and slashing are internal movements or testnet metadata. Miner, scientific reserve, treasury, validator, and audit rewards are new issuance. Therefore total balances can grow with each accepted block or automatic audit, while the endpoint `/audit/full` verifies that this growth exactly matches the sum of recorded rewards.

## Scientific Development Fund

The Scientific Development Fund replaces the concept of a large premine with a treasury continuously funded by real network activity. Receives the `3%` of each block base issuance and remains locked for `90` days before it can be claimed.

Uso previsto:

- protocol development;
- auditorias;
- infraestructura;
- investigacion;
- scientific grants;
- scientific/AI marketplace development;
- node support and tooling.

The treasury is separate from `science_compute_reserve`. The 20% of compute only pays scientific jobs `completed/verified/accepted` when the future L2 is activated; the 3% of treasury funds ecosystem development through auditable quarterly unlocks.

## Science Compute Access Layer

The Science layer is an L1 extension to prepare a future L2 marketplace for scientific computing and AI. It does not execute models, simulations, or heavy workloads on L1. Its goal is to coordinate access, reserve, jobs, states, and verifiable payments.

Main entities:

```text
science_stake_accounts
science_jobs
science_reward_reserve
scientific_development_treasury
scientific_development_treasury_epochs
scientific_development_treasury_claims
science_events
```

Access tiers:

```text
Researcher    stake 3,141.6 PI    multiplier 1x    priority low
Lab           stake 31,416 PI     multiplier 10x   priority medium
Institution   stake 314,160 PI    multiplier 100x  priority high
```

The `compute_multiplier` does not guarantee fixed compute. It is priority and proportional access so a future L2 can calculate quotas against real worker capacity. En the MVP L1, each job consumes an abstract monthly quota unit: `science_base_monthly_quota_units * compute_multiplier`.

Reglas:

- only active Science accounts can create jobs;
- if stake falls below minimum, it cannot create new jobs;
- unstake is not allowed if there are active jobs;
- jobs store hashes, pointers, and abstract compute units, not heavy data;
- `max_reward` is reserved as pending against `science_compute_reserve`;
- `payout_amount = min(compute_units_used * reward_per_compute_unit, max_reward)`;
- while `status != L2_ACTIVE`, `payouts_enabled = false` or `emergency_paused = true`, not se paga;
- jobs `rejected`, `disputed` or `expired` release pending budget and do not pay;
- workers are only paid if the job is `accepted` and luego queda `status = paid`;
- each job is paid only once;
- the limits `max_reward_per_job`, `max_payout_per_epoch` and `max_pending_per_requester` protect the reserve;
- by default the requester cannot be its own worker.

## Ed25519 Signature

The miner signs a canonical message with:

```text
task_id
miner_id
range_start
range_end
algorithm
result_hash
signed_at
```

The server reconstructs the same message from the stored task and verifies the signature with the `public_key` registrada. If someone changes the range, algorithm, hash, or attempts to send the result as another miner, the signature stops being valid.

## Commit-Reveal, Merkle Root, and Validators

The miner calculates the full segment locally, but does not send it to the server. En su lugar:

```text
result_hash = sha256(segment + range + algorithm)
merkle_root = root(leaves(position, digit))
```

Then it signs and sends the commit. The server generates the challenge with:

```text
challenge_seed = sha256(previous_hash + task_id + result_hash + merkle_root)
```

With that seed the server selects `sample_count` posiciones. The miner reveals only those positions:

```text
position
digit
merkle proof
```

The server verifies two things per sample:

1. The digit matches BBP for that position.
2. The Merkle proof connects that digit with the `merkle_root` comprometido.

If all samples pass, each validator signs an approval. The coordinator records one vote per validator en `validation_votes`. Only when the job reaches `required_validator_approvals` approvals from distinct validators is the block accepted. Signed rejections also accumulate; if they reach the same quorum, the task is rejected.

This avoids storing pi, avoids transmitting the full segment, separates the validation role from the coordination role and reduces the risk of depending on a single validator.

## Retroactive Audits

Normal validation checks 32 samples revealed by the miner. The retroactive audit checks an already accepted block with a new challenge and double the samples. En v0.16 that significa 64 posiciones.

The coordinator runs a random automatic audit every 314 accepted blocks. The audit selects an accepted block at random, not necessarily the latest one. If the audit was automatic, it issues an additional reward of the 20% of the audited block base reward: `0.62832` PICOIN in the current configuration. That reward is recorded as `retroactive_audit_reward` in the ledger and credited to the protocol account `audit_treasury` until external auditors exist.

The flujo es:

1. The coordinator selects an accepted block at random, or uses `block_height` if se indica.
2. Genera `audit_seed` aleatorio.
3. Recalculates the BBP segment of the audited block.
4. Checks that `hash_result(segmento, range, algoritmo)` matches the `result_hash` guardado.
5. Deriva 64 sample positions from `audit_seed` and stores the observed digits.
6. Records the result in `retroactive_audits` and exposes it as a recent event.

If the audit detects fraud:

- the block is marked with `fraudulent = true`;
- se guarda `fraud_reason` and `fraud_detected_at`;
- the miner receives `20` penalty points;
- the miner cooldown increases to 1 hour;
- each validator who approved that block adds `3` invalid results;
- those validators receive a 1-hour cooldown and lose reputation more aggressively.

This does not store full pi in the database. It only stores audit samples, hashes, and metadata. The current version recalculates the segment because the MVP ranges are small; later it can be replaced by more compact proofs without changing the audit interface.

## Pi Algorithm

The MVP uses `bbp_hex_v1`, based on the Bailey-Borwein-Plouffe formula:

```text
pi = sum(k=0..infinito) 1/16^k * (
  4/(8k+1) - 2/(8k+4) - 1/(8k+5) - 1/(8k+6)
)
```

Pi in hexadecimal begins like this:

```text
3.243F6A8885A308D313198A2E...
```

That is why the range `1..5` returns:

```text
243F6
```

BBP allows calculating a hexadecimal digit at a remote position without calculating all previous digits. That property makes it a better foundation for Picoin than traditional decimals.

To keep the local MVP fast, `max_pi_position` this en `10000`. It can be increased when we optimize BBP calculation or move heavy work to a more efficient implementation.

## Persistence

SQLite uses these tables:

```text
miners
validators
tasks
commitments
validation_jobs
validation_votes
submissions
blocks
retroactive_audits
science_stake_accounts
science_jobs
science_reward_reserve
science_reserve_governance
scientific_development_treasury
scientific_development_treasury_epochs
scientific_development_treasury_claims
science_events
protocol_params
retarget_events
rewards
balances
ledger_entries
penalties
rejected_submissions
```

The separacion permite auditar tasks, intentos, accepted blocks, rewards and castigos without mezclar conceptos.

## Full Flow

1. Start the server:

```powershell
uvicorn app.main:app --reload
```

2. Query protocol:

```powershell
curl http://127.0.0.1:8000/protocol
```

3. Run a miner:

```powershell
python -m miner.client register --name alice
python -m miner.client mine --once
```

4. Run three external validators:

```powershell
python -m validator.client --identity validator1.json register --name val1
python -m validator.client --identity validator1.json validate --once
python -m validator.client --identity validator2.json register --name val2
python -m validator.client --identity validator2.json validate --once
python -m validator.client --identity validator3.json register --name val3
python -m validator.client --identity validator3.json validate --once
```

5. Query blocks:

```powershell
curl http://127.0.0.1:8000/blocks
```

6. Verify the local chain:

```powershell
curl http://127.0.0.1:8000/blocks/verify
```

7. Query statistics:

```powershell
curl http://127.0.0.1:8000/stats
```

8. Query operational health:

```powershell
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/node/status
curl "http://127.0.0.1:8000/events?limit=20"
```

9. Query performance:

```powershell
curl http://127.0.0.1:8000/stats/performance
```

10. Query parameter history:

```powershell
curl http://127.0.0.1:8000/protocol/history
```

11. Query difficulty:

```powershell
curl http://127.0.0.1:8000/difficulty
curl http://127.0.0.1:8000/difficulty/preview
curl http://127.0.0.1:8000/difficulty/history
```

12. Query economics and audit:

```powershell
curl http://127.0.0.1:8000/balances
curl http://127.0.0.1:8000/ledger
curl http://127.0.0.1:8000/audit/summary
curl http://127.0.0.1:8000/audit/full
curl http://127.0.0.1:8000/audit/retroactive
curl -X POST "http://127.0.0.1:8000/audit/retroactive/run?sample_multiplier=2"
curl "http://127.0.0.1:8000/validators?eligible_only=true"
```

## Distributed Testnet v0.18

Picoin now includes an L1 base for a multi-node distributed testnet. This phase adds networking, peers, mempool, wallets, and signed transactions without activating real AI, L2 marketplace, bridges, zk proofs, or complex smart contracts.

New components:

- `network_peers`: peer registry with `node_id`, `peer_address`, tipo, version, `network_id`, `chain_id` and `genesis_hash`.
- `mempool_transactions`: Ed25519 signed transactions with nonce, fee, canonical payload, status, and expiration.
- `network_block_headers`: queue of propagated headers/blocks for distributed replay.
- `network_sync_events`: log of peers, heartbeats, tx, and received blocks.
- `consensus_block_proposals`: propagatable block proposals.
- `consensus_votes`: Ed25519 validator votes per proposal.
- `consensus_finalizations`: finalizations with quorum and import/replay.
- Wallets with addresses `PI...` derived from the Ed25519 public key.

Main endpoints:

```powershell
curl http://127.0.0.1:8000/node/identity
curl http://127.0.0.1:8000/node/peers
curl http://127.0.0.1:8000/node/sync-status
curl "http://127.0.0.1:8000/node/sync/blocks?from_height=0"
curl http://127.0.0.1:8000/mempool
curl http://127.0.0.1:8000/consensus/status
curl http://127.0.0.1:8000/consensus/proposals
```

Register peer:

```powershell
curl -X POST http://127.0.0.1:8000/node/peers/register `
  -H "Content-Type: application/json" `
  -d "{\"node_id\":\"validator-1\",\"peer_address\":\"http://validator-1:8000\",\"peer_type\":\"validator\",\"protocol_version\":\"0.18\",\"network_id\":\"local\",\"chain_id\":\"picoin-local-testnet\",\"genesis_hash\":\"0000000000000000000000000000000000000000000000000000000000000000\"}"
```

Distributed CLI:

```powershell
python -m picoin node peers
python -m picoin node sync-status
python -m picoin node doctor
python -m picoin node reconcile
python -m picoin node reconcile --peer http://peer-node:8000
python -m picoin node catch-up --peer http://peer-node:8000
python -m picoin node report --peer http://peer-node:8000
python -m picoin node checkpoint create --height 10
python -m picoin node checkpoint latest
python -m picoin node checkpoint verify --height 10
python -m picoin node checkpoint export --height 10 --output data/checkpoint-10.json
python -m picoin node checkpoint import --file data/checkpoint-10.json --source bootstrap-node
python -m picoin node checkpoint activate --snapshot-hash <snapshot_hash>
python -m picoin node checkpoint apply --snapshot-hash <snapshot_hash>
python -m picoin node checkpoint imports
python -m picoin wallet create --name alice --output data/alice-wallet.json
python -m picoin wallet balance --address PI...
python -m picoin wallet nonce --address PI...
python -m picoin tx send --wallet data/alice-wallet.json --to PI... --amount 1.5 --fee 0.01
python -m picoin tx send --wallet data/alice-wallet.json --type stake --amount 3141.6 --fee 0.01
python -m picoin tx send --wallet data/alice-wallet.json --type unstake --fee 0.01
python -m picoin tx send --wallet data/alice-wallet.json --type science_job_create --fee 0.01 --payload "{\"job_type\":\"ai_inference\",\"metadata_hash\":\"meta\",\"storage_pointer\":\"ipfs://job\",\"max_compute_units\":0,\"reward_per_compute_unit\":0,\"max_reward\":0}"
python -m picoin tx send --wallet data/signer-one.json --type governance_action --fee 0.01 --payload "{\"scope\":\"science_reserve\",\"action\":\"propose_activation\"}"
python -m picoin tx send --wallet data/signer-two.json --type governance_action --fee 0.01 --payload "{\"scope\":\"science_reserve\",\"action\":\"approve_activation\"}"
python -m picoin tx send --wallet data/signer-one.json --type governance_action --fee 0.01 --payload "{\"scope\":\"science_reserve\",\"action\":\"execute_activation\"}"
python -m picoin tx send --wallet data/owner.json --type treasury_claim --fee 0.01 --payload "{\"claim_to\":\"PI_TREASURY_WALLET\"}"
python -m picoin tx status --hash <tx_hash>
python -m picoin tx mempool --status pending
python -m picoin consensus status
python -m picoin consensus proposals
python -m picoin consensus votes --proposal-id ...
python -m picoin consensus propose-block --block data/block.json --proposer miner-node-1
python -m picoin consensus vote --proposal-id ... --identity data/testnet/identities/validator-one.json
python -m picoin consensus finalize --proposal-id ...
python -m picoin consensus replay
```

Distributed consensus v0.18:

1. A miner node automatically proposes the block when the mining flow reaches local quorum, and can also propose manually with `POST /consensus/proposals`.
2. Each validator signs an Ed25519 vote over `proposal_id`, `block_hash`, `height`, decision and reason.
3. Votes are propagated through best-effort HTTP gossip to connected peers.
4. If there are two proposals for the same height and the same `previous_hash`, the fork-choice treats them as real competitors.
5. The canonical rule is deterministic: higher `approval_weight`, lower `rejection_weight`, more plain approvals, fewer rejections, older proposal and lower `block_hash` lexicografico.
6. Proposals with the same height but different `previous_hash` are not mixed in the same fork-choice group; they depend on their ancestors and canonical replay.
7. A validator cannot vote for two competing proposals from the same fork.
8. When hay `required_validator_approvals = 3`, only the fork-choice winning proposal for ese `(height, previous_hash)` se finaliza.
9. Canonical replay validates `previous_hash`, recalcula `block_hash`, rejects duplicate ranges/results and creates the minimum missing context (`miner`, `task`) before inserting the block.
10. To the importar, applies deterministic accounting: miner reward, validator pool, Science Compute Reserve 20% and Scientific Development Treasury 3%.

Fork diagnostics:

```bash
python -m picoin consensus status
python -m picoin node report --peer http://BOOTSTRAP_PUBLIC_IP:8000
```

`/consensus/status` expone `fork_choice_rule`, `fork_group_count`, `competing_proposal_count`, `fork_groups` and `fork_choices`. `node report` marca `fork_choice` as warning if there are open competing proposals.

Automatic gossip:

- `POST /tx/submit` propagates to peers using `/tx/receive`.
- `POST /consensus/proposals` propagates to peers using `?gossip=false` to avoid loops.
- `POST /consensus/proposals/{proposal_id}/vote` propagates votes using `?gossip=false`.
- When `/tasks/submit`, `/tasks/reveal` or `/validation/results` produce an accepted block, the API automatically propagates a consensus proposal with the full block.

Useful variables:

```text
PICOIN_GOSSIP_ENABLED=1
PICOIN_GOSSIP_TIMEOUT_SECONDS=2.0
PICOIN_GOSSIP_MAX_PEERS=16
```

Reconciliacion:

- `POST /node/reconcile` queries connected peers and merges identity, peers, mempool, and proposals.
- `POST /node/reconcile?peer_address=http://peer:8000` forces reconciliation against a specific peer.
- Reconciliation is pull-based and complements gossip: if a message did not arrive, the node can recover state later.

This version already moves the protocol toward multi-node proposal/vote/finalization with gossip, reconciliation, and fork-choice weighted by reputation/stake. The next improvement is persistent WebSocket gossip, periodic background reconciliation jobs, and fork-choice with stricter slashing/finality.

Docker testnet:

```powershell
docker compose up
```

The `docker-compose.yml` starts:

- 1 bootstrap node
- 3 miner nodes
- 3 validator nodes
- 1 auditor node

Each node uses its own SQLite volume. The current layer synchronizes identity, peers, mempool, proposals, votes, finalizations, and initial canonical replay.

## Tests

```powershell
pytest
```

If you want to restart the demo from block 1:

```powershell
python -m app.tools.reset_db
```

## Roadmap to Mainnet

Picoin already has an experimental L1 with Proof of Pi, validators, audits, economics 67/20/10/3, timelocked treasury, Science Reserve locked, mempool, wallets, peers, gossip, proposals, votes, finalization, and initial canonical replay. It is not yet ready for mainnet with real value.

Fase 1 - Stable distributed testnet:

- Run real nodes on different machines, not only local Docker.
- Automatic periodic reconciliation in the background.
- Persistent WebSocket gossip with backoff, deduplication, and per-peer limits.
- Persist peer reputation and disconnect peers with spam or invalid data.
- Export chain snapshots and deterministic restore.

Fase 2 - Consensus and security:

- Define final fork-choice: weight by stake/reputation, age, finality, and penalties.
- Real slashing for double vote, invalid signature, vote on invalid block, and confirmed fraud.
- Clearly separate proposed block, pre-finalized block, and final block.
- Add finality delay and dispute/audit window before considering irreversible.
- Simulate network partitions, forks, malicious nodes, and offline validators.

Fase 3 - Transactions and state:

- Execute signed transfers from mempool inside blocks.
- Nonce/balance enforcement per account.
- Real fees and anti-spam policy.
- Include transaction Merkle root per block.
- Full state rebuild from genesis using only blocks.

Fase 4 - Nodes and operation:

- Operator CLI: backup, restore, snapshot, peer ban/unban, metrics.
- Observabilidad: Prometheus/structured logs/alerts.
- Public testnet configuration: seeds, chain_id, genesis, ports, domains.
- Versioned binaries or Docker images.
- Upgrade/migration plan by protocol version.

Fase 5 - Economic/protocol audit:

- Review total supply, rewards, treasury, reserve, and validator rewards under full replay.
- Freeze initial mainnet parameters: reward, block interval, quorum, epoch, slashing, faucet off.
- External audit of cryptography, signatures, replay, consensus, and accounting.
- Bug bounty on public testnet.

Fase 6 - Science L1 mainnet-ready:

- Keep Science Reserve locked until activating L2 through timelocked governance.
- Audit scientific staking, abstract jobs, quotas, and L2-ready events.
- Define exact conditions to activate the L2 marketplace in the future.
- Do not activate compute payments until workers/L2 verification are tested.

Fase 7 - Mainnet candidate:

- Public testnet with sustained uptime.
- Final reset of mainnet genesis and mainnet chain_id.
- Disable faucet and demo endpoints.
- Real multisig/governance for treasury and reserve.
- Publish protocol spec, explorer, and node guide.
