# Picoin Forge AI Layer 2 Design

Picoin Forge AI is planned as a useful compute layer above Picoin L1.

## Picoin L1 Responsibilities

Picoin L1 remains the secure settlement layer:

- Stores payments in PI.
- Maintains worker and validator identity.
- Tracks staking and reputation.
- Records job hashes and validation hashes.
- Releases rewards after validated completion.
- Provides final settlement and auditability.

Layer 1 should not run full AI models or heavy Linux deployment tasks inside every node. That would make consensus expensive and fragile.

## Picoin Forge AI L2 Responsibilities

Picoin Forge AI handles off-chain execution:

- Job Manager: creates and tracks tasks.
- Repo Analyzer: scans source repositories and maps architecture.
- Linux Sandbox: runs controlled setup and health checks in future versions.
- Worker Network: agents execute jobs.
- Validator Network: independent agents verify evidence.
- Result Storage: scripts, reports, hashes, logs, and artifacts.
- Payment Engine: future interface to Picoin L1 settlement.

## MVP Flow

1. User creates a task.
2. Worker scans the repository.
3. Worker generates installation and service configuration.
4. Worker generates health and fix reports.
5. Validator checks generated files, hashes, and evidence.
6. A deterministic `result_hash` is produced.
7. Future L1 integration records state and releases payment.

## Initial Job Types

- `setup_node`: prepare a full node deployment plan.
- `setup_validator`: prepare validator service files and health checks.
- `setup_miner`: prepare miner execution plan.
- `setup_explorer`: prepare explorer/API service plan.
- `debug_logs`: review logs and suggest fixes.

## Verification Model

The MVP verification is local and simulated:

- Confirms `project-map.json` exists.
- Confirms install script exists.
- Confirms health report exists.
- Hashes repository state and evidence.
- Updates job state to `verified` or `needs_evidence`.

Future verification can add:

- Container sandbox execution.
- Reproducible script hashes.
- Validator quorum signatures.
- L1 anchored job receipts.
- Worker reputation updates.

## What This MVP Does Not Do

- No real Picoin L1 transaction submission.
- No automatic payment.
- No remote script execution.
- No automatic system modification.
- No private key handling.
