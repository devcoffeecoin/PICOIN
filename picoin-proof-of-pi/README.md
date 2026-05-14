# picoin-proof-of-pi

A functional MVP of **Proof of Pi** featuring a Layer 1 (L1) extension known as the **Science Compute Access Layer**. The system architecture utilizes a coordinator to assign granular ranges of pi hexadecimal digits; miners calculate these segments using the Bailey–Borwein–Plouffe (BBP) algorithm, while independent validators recalculate and verify the results. Accepted blocks are recorded on-chain with simulated rewards. The **Science Layer** establishes the foundational network infrastructure for a future Layer 2 (L2) marketplace focused on scientific computing and Artificial Intelligence.

This project is a high-level coordinator and does not execute heavy scientific workloads. Since v0.18, it includes a **Public Testnet Deployment Kit** for deploying nodes on production servers (Droplets) with systemd integration, automated health checks, and backup protocols. At the L1 level, it orchestrates staking, access control, job scheduling, cryptographic hashing, and verified payouts.

---

## Protocol Specifications (v0.18)

### Network Parameters

| Parameter | Value |
| --- | --- |
| **Protocol Version** | 0.18 |
| **Algorithm** | bbp_hex_v1 |
| **Validation Mode** | External Commit-Reveal |
| **Required Quorum** | 3 Approvals |
| **Segment Size** | 64 Digits |
| **Sample Count** | 32 Samples |
| **Base Block Reward** | 3.1416 PI |
| **Retroactive Audit Interval** | Every 314 Blocks |
| **Min. Validator Stake** | 31.416 PI |

### Reward Distribution (67/20/10/3 Model)

The `base_reward` is distributed according to a fixed economic policy to ensure network sustainability:

* **67% Miner (Proof of Pi):** 2.104872 PI.
* **20% Science Compute Reserve:** 0.62832 PI (Time-locked for future L2).
* **10% Validator/Auditor Pool:** 0.31416 PI.
* **3% Scientific Development Treasury:** 0.094248 PI (90-day release cycle).

The `GET /protocol` endpoint exposes these parameters to ensure all nodes adhere to the active consensus rules. Difficulty is dynamically calculated to regulate work intensity based on segment size, sample count, and pi position depth.

---

## Core Infrastructure & Features

### 1. Transaction Engine & State Management

PICOIN utilizes **Ed25519** signatures and `PI...` prefixed addresses. The L1 accounting system includes:

* **Mempool:** Validates transaction hashes, signatures, chain IDs, and nonces.
* **State Root:** A SHA-256 fingerprint of the accounting ledger. The `verify_chain()` function performs a full ledger replay to ensure the local state matches the canonical root.
* **Checkpoints & Snapshots:** Supports fast-sync by allowing nodes to import verified snapshots, validating `balances_hash` and `snapshot_hash` before applying the state.

### 2. Science Compute Access Layer

This L1 extension prepares the network for future AI/Scientific workloads through:

* **Staking Tiers:** Access is prioritized via staking: *Researcher* (3.1k PI), *Lab* (31.4k PI), and *Institution* (314.1k PI).
* **Reserve Governance:** The 20% science reserve remains `RESERVE_LOCKED` until a future L2 activation via multisig and a mandatory timelock.
* **Job Orchestration:** Tracks job metadata, storage pointers (IPFS), and abstract compute units without storing heavy payloads on-chain.

### 3. Validator Reputation & Selection

Selection is not linear but based on a **Weighted Score**:


$$SelectionScore = (Trust \times 0.55) + (Stake \times 0.25) + (Availability \times 0.10) + (Rotation \times 0.10)$$

* **Rotation Score:** Prevents centralisation by penalising nodes that have validated frequently in the recent hour.
* **Slashing:** Malicious signatures or fraudulent approvals result in immediate stake slashing (3.1416 PI) and potential banning after 9 violations.

---

## System Architecture

```text
picoin-proof-of-pi/
├── app/
│   ├── api/          # FastAPI REST Endpoints
│   ├── core/         # SHA-256 Hashing & BBP Pi Calculation
│   ├── services/     # Task, Block, Reward, & Tx Logic
│   └── web/          # Static Dashboard (FastAPI served)
├── validator/        # Independent Proof of Pi verification client
├── miner/            # Mining client for segment calculation
└── deploy/           # Systemd services & Ubuntu deployment scripts

```

---

## Technical Installation

### Environment Setup

```bash
python -m venv .venv
source .venv/bin/activate  # .venv\Scripts\Activate.ps1 on Windows
pip install -r requirements.txt

```

### Node Deployment (Production/Ubuntu)

```bash
# Install as a system service
sudo PICOIN_REPO_DIR=/opt/picoin/picoin-proof-of-pi \
./deploy/scripts/install-systemd-service.sh

# Monitor health
python -m picoin node doctor --require-checkpoint

```

---

## Operations Guide

### Unified CLI

PICOIN provides a comprehensive CLI for node management and network interaction:

* **Node Control:** `python -m picoin node start | status | audit | doctor`
* **Mining:** `python -m picoin miner register | mine --loops 10`
* **Validation:** `python -m picoin validator register | validate`
* **Wallet:** `python -m picoin wallet create | tx send --to PI... --amount 1.5`

### Distributed Consensus (v0.18)

The network operates a distributed consensus mechanism:

1. **Proposal:** Nodes propose blocks via gossip protocols.
2. **Voting:** Validators issue Ed25519-signed votes on block proposals.
3. **Fork-Choice:** Deterministic resolution based on approval weight, age, and lexicographical hash comparison.
4. **Finalization:** Blocks reaching quorum are replayed and committed to the local ledger.

---

## Security Model

* **Cryptographic Integrity:** SHA-256 block chaining and Ed25519 identity verification.
* **Commit-Reveal Scheme:** Prevents front-running; miners commit a `merkle_root` before the server issues a sampling challenge.
* **Retroactive Auditing:** An automated "double-sample" audit every 314 blocks recalibrates segments to detect long-range fraudulent computations.
* **Economic Guardrails:** Multi-layered penalties including cooldown periods, reputation degradation, and stake slashing.

---

## Project Roadmap

### Phase 1: Stability

* Persistent WebSocket Gossip protocols.
* Background peer reconciliation.
* Optimized LRU caching for BBP digits.

### Phase 2: Consensus Hardening

* Implementing strict Finality Delays.
* Real Slashing for double-voting (Equivocation).
* State Rebuild engine from Genesis.

### Phase 3: L2 Integration

* Activation of the Science Compute Marketplace.
* Verifiable compute proofs for AI Inference.
* Treasury governance via On-chain voting.

