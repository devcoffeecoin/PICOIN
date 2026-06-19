# Picoin Forge L2 - Working Artifact

This document is the living implementation plan for **Picoin Forge L2**, the Picoin compute and AI layer based on **Proof of Available Compute**.

Main rule: **do not touch Picoin L1 yet**. L1 integration starts only after the L2 worker, coordinator, challenges, scoring, epochs, and simulated settlement are stable.

## Vision

Picoin Forge L2 is not a marketplace where a user creates one task and pays for that individual task.

The target model is a distributed compute network:

1. Anyone installs a Linux worker.
2. The worker registers a PI wallet.
3. The worker contributes CPU, optional GPU, RAM, IO, and availability.
4. The network verifies that capacity through benchmarks and random challenges.
5. Each epoch calculates the verified compute power of all workers.
6. Each worker earns PI proportionally to its verified compute share in the network.

Base formula:

```text
reward_worker =
epoch_reward * worker_verified_compute / total_verified_compute
```

Compute formula:

```text
worker_verified_compute =
CPU_score
+ GPU_score
+ RAM_score
+ IO_score
+ uptime_score
+ reliability_score
- penalty_score
```

## Principles

- L1 stays lightweight: security, payments, identity, staking, reputation, hashes, and settlement.
- L2 handles measurement, challenges, availability, scoring, epochs, and reports.
- Score cannot depend only on declared hardware.
- Random challenges are mandatory to reduce fraud.
- Initial settlement is local JSON, not real payment.
- Any dangerous change must be simulated and tested first.
- No private keys are handled in the MVP.
- Real L1 integration is reserved for later phases.

## Current MVP State

Module created:

```text
picoin-layer2/picoin-forge-l2
```

Existing components:

- Worker registration.
- CPU/RAM/IO benchmark.
- GPU placeholder set to `0`.
- CPU/RAM/IO challenge engine.
- Heartbeat.
- Worker registry.
- Score engine.
- Epoch engine.
- Reward engine.
- Local JSON settlement.
- Base coordinator API with FastAPI.
- CLI wrappers prepared.
- Base documentation.
- Basic tests.
- SQLite coordinator persistence.
- Challenge API endpoints.
- Worker loop-once coordinator client.
- Missed heartbeat penalty.
- Demo network command.
- Coordinator event log in SQLite.
- Challenge expiration and penalty.
- Configurable worker loop.
- Minimal HTML coordinator dashboard.
- Persistent worker config.
- Passive GPU detection.
- Local Docker Compose with coordinator plus three workers.
- GPU challenge placeholder with no reward signal.

Current verification:

```text
14 tests passed
```

## Phase 0 - Model Definition

Goal: finalize the technical and economic model before integrating payments or L1.

Deliverables:

- Official scoring formula.
- Initial weights for CPU/GPU/RAM/IO/uptime/reliability.
- Penalty rules.
- Epoch duration.
- Simulated reward per epoch.
- Definition of a valid worker.
- Definition of a valid challenge.
- Definition of a valid settlement.

Acceptance criteria:

- The model can be explained in one page.
- Epoch rewards always sum exactly to `epoch_reward`.
- A worker without benchmark or challenges cannot meaningfully increase score.
- `ECONOMICS.md` reflects the final formula for this phase.

Status: started.

## Phase 1 - Local Worker

Goal: allow a user to install a worker and test local capacity without touching L1.

Deliverables:

- `picoin-forge-worker register --wallet <PI_WALLET>`
- `picoin-forge-worker benchmark`
- `picoin-forge-worker start`
- `picoin-forge-worker status`
- Local worker state file.
- Reproducible CPU/RAM/IO benchmark.
- Basic GPU detection.
- Clear local logs.

Acceptance criteria:

- A new worker can register with a PI wallet.
- Benchmark generates `result_hash`.
- Worker does not execute dangerous commands.
- Worker can generate heartbeats.
- Sensitive data is not written to logs.

Status: initial MVP implemented.

## Phase 2 - Centralized Test Coordinator

Goal: run an L2 coordinator that receives workers, benchmarks, and heartbeats.

Deliverables:

- Public FastAPI API.
- Worker registration.
- Online/offline worker inventory.
- Benchmark submission.
- Heartbeat submission.
- Local persistence.
- Health endpoints.
- State report.

Acceptance criteria:

- Coordinator registers multiple workers.
- State survives restart.
- Coordinator can list workers.
- Coordinator does not require Picoin L1.

Status: initial MVP implemented with local JSON.
Update: SQLite persistence has been added for workers, challenges, and epoch state. Settlement remains JSON by design for audit/export.

## Phase 3 - Verifiable Challenges

Goal: prevent a worker from earning rewards by declaring fake hardware.

Deliverables:

- Deterministic CPU challenge.
- Deterministic RAM challenge.
- Deterministic IO challenge.
- Optional GPU challenge.
- Deadline per challenge.
- Automatic verification.
- Penalty on failure.
- Challenge history per worker.

Acceptance criteria:

- A correct challenge passes.
- A modified challenge fails.
- A failed challenge increases `penalty_score`.
- A correct challenge improves `reliability_score`.
- Challenges use unique seeds to prevent replay.

Status: initial MVP implemented for CPU/RAM/IO.
Update: API endpoints now exist for challenge creation, listing, lookup, and submission.
Update: challenge expiration is implemented and expired challenges penalize workers.
Update: `gpu` challenge type exists as a handshake-only placeholder. It does not increase reliability, `gpu_score`, or reward score.

## Phase 4 - Scoring And Epochs

Goal: calculate verified compute power and simulated rewards per epoch.

Deliverables:

- `verified_compute_score`.
- `total_verified_compute`.
- `epoch_reward = 100 PI` in simulation.
- Epoch close.
- Settlement JSON.
- Deterministic `result_hash`.
- Worker report.

Acceptance criteria:

- Sum of epoch rewards equals `epoch_reward`.
- A worker with a higher score earns more.
- A penalized worker earns less.
- A worker without benchmark earns `0` or close to `0`.
- Settlement can be verified by hash.

Status: initial MVP implemented.
Update: missed heartbeat penalty is implemented in the coordinator registry and applied during epoch close.
Update: reward rounding now preserves exact epoch reward totals when total verified compute is positive.

## Phase 5 - Internal L2 Testnet

Goal: run several real workers and calibrate weights.

Deliverables:

- 3 to 5 real Linux workers.
- Coordinator on a separate droplet.
- Uptime tests.
- Benchmark tests.
- Challenge tests.
- First simple dashboard.
- Simulated epoch reports.

Acceptance criteria:

- Workers survive restarts.
- Coordinator detects offline workers.
- Score does not spike through false positives.
- Settlement is generated across multiple consecutive epochs.
- Bottlenecks are identified.

Status: pending.

## Phase 6 - Fraud Hardening

Goal: make the model more resistant before paying real PI.

Deliverables:

- Benchmark normalization by hardware type.
- Score growth limits.
- Penalties for missed heartbeats.
- Penalties for challenge timeouts.
- Detection of impossible scores.
- Anti-replay challenge handling.
- Random audits.
- Evidence records.

Acceptance criteria:

- A worker cannot increase score infinitely through benchmark alone.
- An offline worker loses uptime.
- A worker that fails challenges loses reliability.
- Repeated challenges cannot be accepted twice.
- Score remains traceable.

Status: pending.

## Phase 7 - Simulated L1 Settlement

Goal: prepare the exact format that may later be registered on Picoin L1, without sending real transactions.

Deliverables:

- Canonical settlement payload.
- Epoch hash.
- Worker/reward list.
- Future signature format.
- Future reputation format.
- Offline verification script.

Candidate data for L1:

```text
worker_id
wallet
epoch_id
verified_compute_score
reward
result_hash
validator_signatures
```

Acceptance criteria:

- Payload is deterministic.
- Same settlement generates the same hash.
- L1 does not need to store heavy logs.
- L1 would only receive compact data.

Status: pending.

## Phase 8 - Minimal L1 Integration

Goal: register hashes and states on Picoin L1. This phase must not start until phases 5, 6, and 7 are stable.

Deliverables:

- L1 endpoint or transaction to register epoch hash.
- Worker/reward record.
- Settlement confirmation.
- Explorer query.
- No automatic payments at first.

Acceptance criteria:

- L1 does not execute compute.
- L1 does not receive heavy data.
- L2 registration does not break L1 consensus.
- Old nodes remain compatible.

Status: blocked until L2 validation.

## Phase 9 - Real PI Payments

Goal: pay real PI to workers according to approved settlement.

Deliverables:

- Reward fund/reserve.
- Payment queue.
- Confirmations.
- Double-payment prevention.
- Safe retries.
- Audit trail.
- Public report.

Acceptance criteria:

- No settlement is paid twice.
- Failed payments retry without duplication.
- Each payment has a transaction hash.
- Explorer can show epoch/reward/payment.

Status: future.

## Phase 10 - Real AI On The Network

Goal: use the compute network for real AI workloads without trying to compete with ChatGPT or Gemini at the beginning.

Viable initial workloads:

- Embeddings.
- Text classification.
- OCR.
- Lightweight transcription.
- Batch summarization.
- Inference with small open-source models.
- Redundant output validation.

Deliverables:

- GPU worker profile.
- Internal workload queue.
- Sampling-based verification.
- Cost/capacity report.
- Staking-based access rules.

Acceptance criteria:

- The network runs useful workloads.
- Compute usage does not break scoring.
- Results are verifiable or auditable.
- Access can be connected to PI staking in a later phase.

Status: future.

## Recommended Current Decision

The next technical phase should be:

```text
Strengthen Phase 1 + Phase 2 + Phase 3
```

Immediate priorities:

1. Add normalized benchmark/challenge metrics tables.
2. Add dashboard charts and epoch history.
3. Add challenge timeout scheduler/background task.
4. Add coordinator auth for worker submissions.
5. Add Docker health checks.
6. Replace GPU placeholder with verified GPU workload challenge.

## Criteria For Touching L1

Do not touch L1 until all of this is true:

- 3 real workers running.
- 10 simulated epochs completed.
- Deterministic settlement.
- Penalties working.
- Challenges working.
- No dependency on real payments.
- L1 payload documentation approved.

## Decision Log

| Date | Decision | Reason |
| --- | --- | --- |
| 2026-06-19 | Create Picoin Forge L2 outside L1 | Avoid risk to current consensus and network |
| 2026-06-19 | Use epoch-based model, not individual task marketplace | The goal is available compute capacity, not per-task freelancing |
| 2026-06-19 | Start with local JSON settlement | Allows economic testing without moving real PI |
| 2026-06-19 | Integrate L1 later | L2 must be proven before touching payments or consensus |

## Technical Backlog

- SQLite for coordinator.
- Durable worker polling loop. MVP implemented.
- API for challenge assignment and resolution. Done in MVP form.
- Coordinator event log. MVP implemented.
- Challenge expiration. MVP implemented.
- Minimal dashboard. MVP implemented.
- Hardware-normalized score.
- Downtime penalties.
- Worker config file. MVP implemented.
- Passive GPU detection. MVP implemented.
- Docker Compose local simulation. MVP implemented.
- GPU challenge placeholder. MVP implemented.
- Structured logs.
- Web dashboard.
- Local Docker Compose.
- Replay/attack tests.
- Network simulator with N workers.
- Settlement export for future L1 integration.
