# Picoin Forge L2 - Working Artifact

This document is the living implementation plan for **Picoin Forge L2**, the Picoin compute and AI layer based on **Proof of Available Compute**.

Main rule: **do not touch Picoin L1 yet**. L1 integration starts only after the L2 worker, coordinator, challenges, scoring, epochs, and simulated settlement are stable.

## Vision

Picoin Forge L2 is not a marketplace where a user creates one task and pays for that individual task.

The target model is a distributed compute network:

1. Anyone installs a Linux worker.
2. The worker registers a PI wallet.
3. The worker contributes CPU, optional GPU, optional full AI model capacity, RAM, IO, and availability.
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
+ AI_model_score
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
- Passive GPU detection remains `0`; verified GPU challenges can assign bounded GPU score.
- Optional AI model profile; verified AI model availability can assign bounded AI model score.
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
- Verified GPU workload challenge.
- Normalized benchmark metrics table.
- Challenge metrics table.
- Metrics API endpoints.
- Epoch history API endpoints.
- Dashboard epoch history table.
- Optional challenge expiration scheduler.
- CLI command for one-shot challenge expiration.
- Optional coordinator write-token auth.
- Docker Compose health checks.
- Docker Compose coordinator readiness dependency.
- L1 settlement preview payload.
- L1 preview API and CLI command.
- Ed25519 worker request signatures.
- Optional signature enforcement flag.
- Offline settlement verifier.
- Worker signature replay cache.
- Minimal verified workload queue.
- First workload type: `hash_text`.
- Configurable benchmark normalization caps.
- Metrics config API endpoint.
- Dashboard worker score bars and metric charts.
- Federated multi-coordinator simulation manifest.
- Worker Ed25519 key rotation.
- Second workload type: `text_classify`.
- Federated manifest offline verifier.
- Bounded GPU score from verified GPU challenges.
- Benchmark calibration report API and CLI.
- Third workload type: `batch_summarize`.
- Canonical JSONL event audit export.
- Operator calibration session artifact and `.env` export.
- Fourth workload type: `text_embed`.
- AI model capacity challenge type: `ai_model`.
- Ollama/OpenAI-compatible endpoint availability proof for full model workers.
- Stake-gated AI access queue with auditable receipts and no per-task payment.
- AI worker runbook for Ollama/OpenAI-compatible pilots.
- Capacity-aware AI routing with candidate ranking and active-load balancing.
- AI request assignment leases and failover requeue.
- Worker loop execution for compatible AI requests against the registered model profile.
- Stake-weighted AI access queue priority.
- Verifiable AI request receipt endpoint.
- Verified AI request result endpoint.
- Compact AI request status polling endpoint.
- AI request cancellation before verification.
- AI network capability discovery endpoint.
- Single AI request audit export endpoint.
- Coordinator CLI commands for AI access operations.
- Optional non-persistence of AI output content.

Current verification:

```text
61 tests passed, 1 skipped when optional Typer CLI dependency is unavailable
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
Update: `gpu` challenge type now requires a verified GPU workload proof. Workers without a GPU backend fail cleanly. A verified GPU challenge can assign bounded `gpu_score`; passive detection still cannot.
Update: `ai_model` challenge type verifies full model availability. An advertised model profile does not earn score by itself; only a valid availability proof can assign bounded `ai_model_score`.
Update: AI model proof can call an Ollama-compatible `/api/generate` endpoint or an OpenAI-compatible `/v1/chat/completions` endpoint with a tiny deterministic availability prompt.

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
Update: normalized benchmark metrics and challenge metrics are stored in SQLite for future calibration and dashboard views.
Update: settlement history is exposed through `/epochs` and `/epochs/{epoch_id}` and displayed in the dashboard.
Update: optional background challenge expiration can be enabled with `PICOIN_FORGE_CHALLENGE_EXPIRER_SECONDS`.
Update: optional write-token auth can be enabled with `PICOIN_FORGE_COORDINATOR_TOKEN`; when unset, local MVP compatibility is preserved.
Update: Docker Compose now includes coordinator and worker health checks, and workers wait for coordinator health before starting.
Update: settlement previews can be generated through `/epochs/{epoch_id}/l1-preview` or `picoin-forge-coordinator l1-preview <epoch_id>`. These previews do not create L1 transactions.
Update: workers now generate a local Ed25519 key and can sign write requests. Coordinators can require signatures with `PICOIN_FORGE_REQUIRE_WORKER_SIGNATURES=1`.
Update: `picoin-forge-coordinator verify-settlement <epoch_id>` recalculates settlement and preview hashes locally.
Update: repeated worker signatures are rejected through a local replay cache.
Update: a minimal verified workload queue exists with `hash_text` tasks. This proves queue lifecycle before real AI workloads.
Update: benchmark normalization caps are configurable through env vars and visible through `/metrics/config`.
Update: dashboard now includes worker score bars, challenge pass/fail counters, and latest benchmark metric bars.
Update: `picoin-forge-coordinator federation-demo` simulates multiple independent coordinators and produces a federated manifest hash without touching L1.
Update: `picoin-forge-worker rotate-key --submit` preserves `worker_id`, rotates the local Ed25519 key, and can re-register the new public key.
Update: workload queue now supports `text_classify`, a deterministic keyword-based classification task.
Update: `picoin-forge-coordinator verify-federation <manifest.json>` validates coordinator previews and the federation root hash offline.
Update: GPU score calibration has started with a conservative proof-based score assigned only after a verified GPU challenge.
Update: `/metrics/calibration` and `picoin-forge-coordinator metrics-calibration` recommend benchmark caps from observed metrics without changing configuration automatically.
Update: `/metrics/calibration/session` and `picoin-forge-coordinator metrics-calibration-session <dir>` produce `calibration_session.json` plus `recommended_caps.env` for operator review.
Update: workload queue now supports `batch_summarize`, a deterministic extractive summarization task for batch text.
Update: workload queue now supports `text_embed`, a deterministic lightweight hash-based embedding vector.
Update: `/events/export` and `picoin-forge-coordinator export-events <path>` export canonical JSONL events with a hash for audit.
Update: the scoring model now includes `ai_model_score * ai_model_weight`, making full model availability a first-class network contribution.
Update: AI access queue is available through `/ai/requests`, `/ai/requests/claim`, and `/ai/requests/{request_id}/submit`. Access uses a simulated stake snapshot. Completed requests produce receipt hashes and explicitly mark `no_per_task_payment`.
Update: `/ai/summary` and the dashboard show AI workers, model readiness, request status counts, latest AI requests, and receipt hashes.
Update: `docs/AI_WORKER_RUNBOOK.md` documents how to run a real Ollama or OpenAI-compatible worker pilot.
Update: `/ai/requests/{request_id}/routing` previews compatible workers and the selected route. Claiming now respects routing rank, so requests are distributed by verified capacity and active load instead of first-compatible polling.
Update: AI requests now use configurable assignment leases. `POST /ai/requests/expire` requeues expired requests for another verified worker, preferring workers not already attempted, and marks them failed after `PICOIN_FORGE_AI_REQUEST_MAX_ASSIGNMENTS`. This improves access reliability without introducing per-task worker payments.
Update: `picoin-forge-worker loop-once` can claim a compatible AI request, execute it against the worker's registered model profile, submit the output, and create a verified receipt. Endpoint failures are reported locally and rely on request lease failover instead of submitting fake success.
Update: queued AI requests are prioritized by `stake_snapshot_pi` first and creation time second. This gives PI staking direct access utility without paying a worker per individual prompt.
Update: `/ai/requests/{request_id}/receipt` reconstructs the canonical AI request receipt payload and validates the stored `receipt_hash`.
Update: `/ai/requests/{request_id}/result` returns the verified model output, output hash, receipt hash, model profile, and no-payment flags after worker submission.
Update: `/ai/requests/{request_id}/status` provides compact polling fields for request state, lease state, attempts, result readiness, receipt readiness, and failure reason.
Update: `/ai/requests/{request_id}/cancel` cancels queued or assigned requests. Verified requests cannot be canceled because their result and receipt remain audit artifacts.
Update: `/ai/capabilities` summarizes ready model workers by provider, model, capability, max context, and max parameter count for frontend/SDK discovery.
Update: `/ai/requests/{request_id}/export` returns a canonical single-request audit artifact. It redacts prompt/output by default and can include content with `include_content=true` for debugging.
Update: `picoin-forge-coordinator ai ...` commands now support capabilities discovery, request creation, status polling, result lookup, receipt verification, and request export for local pilots.
Update: AI request creation supports `store_output=false`, allowing the coordinator to keep output hashes and receipts without persisting the model output text.

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

## Phase 10 - Full AI On The Network

Goal: use the compute network for full AI model capacity, not only small demo jobs. The first production target is verified access to open model runtimes contributed by workers, with staking-based access for users and epoch-based rewards for contributors.

Model capacity requirements:

- Worker advertises model provider, name, parameter count, context window, quantization, capabilities, and endpoint.
- Coordinator verifies availability through randomized `ai_model` challenges.
- Capacity, uptime, and reliability increase epoch share.
- Individual prompts or jobs do not create direct per-task payouts.
- User access is controlled by PI staking or future access policy, not direct worker billing.
- In the MVP, `PICOIN_FORGE_AI_ACCESS_MIN_STAKE_PI` gates access through a local stake snapshot until real L1 staking proofs are integrated.
- The local stake snapshot also controls queue priority: higher stake gets earlier access, then older requests.
- Routing chooses workers by capabilities, model size, context window, AI score, uptime, reliability, and current active requests.
- Request leases prevent a stalled worker from holding user access indefinitely; expired work is reassigned or failed after a bounded number of attempts.

Useful workloads on top of that capacity:

- LLM chat and completion from open models.
- Embeddings and retrieval.
- Classification and moderation.
- OCR plus document extraction.
- Transcription and summarization.
- Tool-use workflows.
- Redundant output validation across multiple workers.

Deliverables:

- GPU and AI model worker profile.
- Internal workload queue.
- Sampling-based verification.
- Cost/capacity report.
- Staking-based access rules.
- AI request receipt hash.
- AI request lease and failover handling.
- Worker-side AI request execution and receipt submission.
- Stake-weighted access priority.
- Receipt verification endpoint.
- Result polling endpoint.
- Compact request status endpoint.
- Request cancellation endpoint before verification.
- Capability discovery endpoint.
- Single request audit export endpoint.
- Coordinator AI CLI commands.
- Optional `store_output=false` privacy control.

Acceptance criteria:

- The network proves full model capacity before exposing useful workloads.
- Compute usage does not break scoring.
- Results are verifiable or auditable.
- Access can be connected to PI staking in a later phase.
- No prompt or job creates a direct worker payout.
- A stalled worker cannot hold an AI request forever.

Status: MVP path in progress. Model profiles, availability proof, capability discovery, stake-gated requests, stake-weighted queue priority, routing, worker execution, status/result polling, optional output non-persistence, cancellation, verifiable receipts, single-request exports, coordinator CLI, and lease failover are implemented locally without touching L1.

## Recommended Current Decision

The next technical phase should be:

```text
Strengthen Phase 1 + Phase 2 + Phase 3
```

Immediate priorities:

1. Run real-worker calibration sessions and apply reviewed env caps.
2. Add real-worker federation test runs.
3. Run real Ollama/vLLM worker pilots and calibrate `ai_model_score` caps.
4. Add useful workload families on top of verified model capacity: chat/completion, embeddings, OCR, transcription, and tool-use.

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
| 2026-06-19 | Reward network contribution, not individual tasks | Jobs and prompts are evidence/audit flow; epoch rewards come from capacity, uptime, and reliability |
| 2026-06-19 | Make full AI model capacity first-class | The L2 goal is a real AI model network, not only small utility tasks |

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
- Verified GPU workload challenge. MVP implemented.
- Normalized benchmark metrics. MVP implemented.
- Challenge metrics. MVP implemented.
- Epoch history API and dashboard table. MVP implemented.
- Challenge expiration scheduler. MVP implemented.
- Optional coordinator write-token auth. MVP implemented.
- Docker Compose health checks. MVP implemented.
- L1 settlement preview payload. MVP implemented.
- Worker request signatures. MVP implemented.
- Offline settlement verifier. MVP implemented.
- Worker signature replay cache. MVP implemented.
- Minimal verified workload queue. MVP implemented.
- Configurable benchmark normalization caps. MVP implemented.
- Dashboard worker and metric charts. MVP implemented.
- Multi-coordinator federation demo. MVP implemented.
- Worker signature key rotation. MVP implemented.
- `text_classify` workload. MVP implemented.
- Federated manifest verifier. MVP implemented.
- Proof-based GPU score calibration. MVP implemented.
- Benchmark calibration report. MVP implemented.
- Operator calibration session artifact. MVP implemented.
- `batch_summarize` workload. MVP implemented.
- `text_embed` workload. MVP implemented.
- Structured event audit export. MVP implemented.
- AI model profile. MVP implemented.
- AI model availability challenge. MVP implemented.
- AI model score in epoch formula. MVP implemented.
- Ollama/OpenAI-compatible model proof adapter. MVP implemented.
- Stake-gated AI access queue. MVP implemented.
- AI request receipt hash. MVP implemented.
- AI dashboard and summary endpoint. MVP implemented.
- AI worker runbook. MVP implemented.
- AI route preview endpoint. MVP implemented.
- Capacity-aware AI request claiming. MVP implemented.
- AI request assignment leases and failover endpoint. MVP implemented.
- Worker loop AI request execution. MVP implemented.
- Stake-weighted AI access priority. MVP implemented.
- AI request receipt verification endpoint. MVP implemented.
- AI request result endpoint. MVP implemented.
- AI request status polling endpoint. MVP implemented.
- AI request cancellation endpoint. MVP implemented.
- AI capability discovery endpoint. MVP implemented.
- AI request audit export endpoint. MVP implemented.
- Coordinator AI CLI commands. MVP implemented.
- Optional AI output non-persistence. MVP implemented.
- Web dashboard.
- Local Docker Compose.
- Replay/attack tests.
- Network simulator with N workers.
- Settlement export for future L1 integration.
