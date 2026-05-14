# PICOIN

Experimental useful-compute blockchain protocol.

Picoin is an early distributed public testnet implementing:

* Proof of Pi mining
* probabilistic validation
* validator quorum
* retroactive audits
* signed transactions
* deterministic replay
* distributed networking
* scientific compute accounting
* treasury and reserve coordination

The protocol explores a blockchain architecture where deterministic mathematical computation replaces arbitrary hashing.

---

# Current Status

| Parameter                  | Value                                         |
| -------------------------- | --------------------------------------------- |
| Protocol Version           | v0.18                                         |
| Network Status             | Early Public Testnet                          |
| Consensus Model            | Proof of Pi + Validators + Retroactive Audits |
| Mining Algorithm           | bbp_hex_v1                                    |
| Block Time Target          | 60 seconds                                    |
| Validation Mode            | external_commit_reveal                        |
| Validator Quorum           | 3 approvals                                   |
| Validation Samples         | 32                                            |
| Retroactive Audit Samples  | 64                                            |
| Retroactive Audit Interval | every 314 blocks                              |
| Wallet Cryptography        | Ed25519                                       |
| Address Format             | PI...                                         |
| State Validation           | state_root + tx_merkle_root                   |
| Science Marketplace        | Disabled                                      |
| Science Reserve Status     | RESERVE_LOCKED                                |
| Treasury Model             | Time-Locked                                   |
| Mainnet Status             | NOT LAUNCHED                                  |

---

# Architecture

Picoin separates protocol coordination from future compute execution.

## L1 Responsibilities

The current L1 handles:

* consensus
* accounting
* validator coordination
* treasury accounting
* reserve accounting
* staking
* transaction settlement
* scientific job registration
* replayable blockchain state

## Future L2 Responsibilities

Future compute infrastructure is expected to handle:

* AI inference
* distributed GPU compute
* scientific workloads
* compute scheduling
* distributed execution

The L2 is not currently active.

---

# Consensus Model

Picoin combines:

* Proof of Pi computation
* validator approvals
* probabilistic validation
* retroactive audits

## Mining Flow

```text
task assignment
→ deterministic computation
→ commit
→ reveal
→ validator approvals
→ block acceptance
→ retroactive audits
```

---

# Proof of Pi

Picoin miners compute deterministic hexadecimal ranges of π using:

```text
bbp_hex_v1
```

The protocol uses the Bailey–Borwein–Plouffe formula for direct hexadecimal digit computation.

## BBP Formula

```text
π = Σ (1 / 16^k) × (
    4 / (8k + 1)
  - 2 / (8k + 4)
  - 1 / (8k + 5)
  - 1 / (8k + 6)
)
```

The BBP algorithm allows direct access to distant hexadecimal digits without computing all previous digits.

This property makes distributed probabilistic verification practical.

---

# Difficulty Adjustment

Picoin difficulty regulates computational workload rather than token issuance.

Difficulty depends on:

* segment size
* validation sample count
* maximum Pi position range

## Current Formula

```text
difficulty =
  (segment_size / 64)
  * (sample_count / 8)
  * (log10(max_pi_position) / log10(10000))
```

Current retarget configuration:

```text
epoch_blocks = 5
target_block_ms = 60000
tolerance = 20%
max_adjustment_factor = 1.25
```

The protocol adjusts future task difficulty based on observed block timing.

---

# Segment Length

Mining tasks contain finite hexadecimal computation ranges.

Example:

```text
positions 3667..3746
segment_length = 80
algorithm = bbp_hex_v1
```

Longer segments increase:

* miner compute time
* verification complexity
* Merkle tree size
* audit coverage

The protocol stores:

* result hash
* Merkle root
* validation samples
* proofs
* validator approvals

The protocol does not store all computed digits.

---

# Security

Picoin currently uses multiple security layers:

* commit-reveal
* deterministic validation samples
* Merkle proofs
* validator quorum
* retroactive audits
* replayable state verification

---

# Commit-Reveal

Mining uses a two-phase workflow.

## Commit Phase

The miner submits:

* result hash
* Merkle root
* metadata

## Reveal Phase

The protocol generates deterministic validation samples.

The miner must reveal:

* requested positions
* values
* Merkle proofs

This reduces selective computation attacks.

---

# Validation Samples

Current validation configuration:

```text
32 validation samples
3 validator approvals required
```

Validators independently verify sample correctness.

---

# Retroactive Audits

Picoin executes automatic retroactive audits every:

```text
314 accepted blocks
```

The protocol randomly selects a previously accepted historical block and recalculates:

```text
64 audit samples
```

If the audit succeeds, the protocol generates an additional reward equal to:

```text
20% of the original block reward
```

Current audit reward:

```text
0.62832 PI
```

Audit rewards are currently credited to:

```text
audit_treasury
```

until distributed external auditor nodes are activated.

---

# Transactions

Picoin supports signed deterministic transactions.

Current transaction types include:

* transfer
* stake
* unstake
* science_job_create
* governance_action
* treasury_claim

Transactions include:

* tx_hash
* sender
* nonce
* signature
* timestamp
* fee

Wallet cryptography uses:

```text
Ed25519
```

---

# Mempool

The protocol includes a distributed mempool supporting:

* pending transactions
* propagation
* replay protection
* nonce validation
* duplicate rejection

---

# Networking

Picoin is evolving toward distributed peer-to-peer networking.

Current networking features include:

* peer synchronization
* block propagation
* transaction propagation
* gossip messaging
* checkpoints
* snapshot synchronization
* replayable state rebuild

---

# Fork Choice

The protocol includes deterministic fork-choice handling.

Competing proposals may be evaluated using:

* approval weight
* rejection weight
* proposal age
* deterministic hash tie-breaks

---

# Science Compute Layer

Picoin includes a reserved scientific compute accounting layer.

Current L1 functionality includes:

* staking
* reserve accounting
* job registration
* treasury coordination

The compute marketplace remains disabled.

## Reserve Status

```text
science_reserve_status = RESERVE_LOCKED
payouts_enabled = false
```

This prevents premature compute payouts before distributed compute verification infrastructure is fully implemented.

---

# Reward Distribution

Current protocol reward allocation:

| Allocation                  | Percentage |
| --------------------------- | ---------- |
| Miners                      | 67%        |
| Science Compute Reserve     | 20%        |
| Validators & Auditors       | 10%        |
| Scientific Development Fund | 3%         |

---

# Emission Model

| Time Period | Total Blocks | Mining Rewards (PI) | Retroactive Audits | Audit Rewards (PI) | Total PI Issued |
| ----------- | -----------: | ------------------: | -----------------: | -----------------: | --------------: |
| 1 Year      |      525,600 |        1,651,848.96 |           1,673.89 |           1,051.50 |    1,652,900.46 |
| 10 Years    |    5,256,000 |       16,518,489.60 |          16,738.85 |          10,515.00 |   16,529,004.60 |
| 20 Years    |   10,512,000 |       33,036,979.20 |          33,477.71 |          21,030.00 |   33,058,009.20 |
| 40 Years    |   21,024,000 |       66,073,958.40 |          66,955.41 |          42,060.00 |   66,116,018.40 |

---

# Quick Start

## Clone Repository

```bash
git clone https://github.com/devcoffeecoin/PICOIN.git
cd PICOIN/picoin-proof-of-pi
```

## Create Virtual Environment

```bash
python -m venv .venv
```

## Activate Environment

### Windows

```powershell
.\.venv\Scripts\activate
```

### Linux / macOS

```bash
source .venv/bin/activate
```

## Install Dependencies

```bash
pip install -r requirements.txt
```

---

# Run API

```bash
uvicorn app.main:app --reload
```

---

# Run Local Testnet

```bash
python -m picoin testnet continuous --miners 3 --loops 3 --workers 1
```

---

# Mining Commands

## Mine Once

```bash
python -m picoin mine once
```

## Continuous Mining

```bash
python -m picoin mine continuous
```

---

# Science Commands

## Stake

```bash
python -m picoin science stake --amount 31416
```

## Create Scientific Job

```bash
python -m picoin science create-job --type ai_inference
```

## View Jobs

```bash
python -m picoin science jobs
```

---

# Treasury Commands

## Treasury Status

```bash
python -m picoin treasury status
```

## Treasury Claim

```bash
python -m picoin treasury claim
```

---

# Health Checks

The protocol includes integrated chain verification and health checks.

Current checks include:

* blockchain integrity
* balance consistency
* validator availability
* reserve accounting
* treasury accounting
* replay validation

---

# Docker Testnet

Future versions are expected to support distributed Docker orchestration with:

* bootstrap nodes
* miner nodes
* validator nodes
* auditor nodes

---

# Development Status

## Implemented

* Proof of Pi mining
* commit-reveal
* validator quorum
* retroactive audits
* mempool
* signed transactions
* deterministic replay
* checkpoints
* snapshots
* treasury accounting
* reserve accounting
* scientific job accounting
* gossip networking
* fork-choice
* state validation

## In Progress

* distributed networking hardening
* peer reputation
* validator decentralization
* distributed auditor nodes
* snapshot synchronization
* governance improvements

## Planned

* public distributed validators
* slashing
* multisig governance
* L2 compute execution
* AI inference coordination
* distributed GPU compute
* scientific compute marketplace

---

# Disclaimer

Picoin is experimental software under active development.

Current versions operate as:

* research infrastructure
* distributed systems experimentation
* early blockchain testnet software

The protocol should not be considered production-ready infrastructure.

