# Architecture

Picoin Forge L2 is a compute availability layer above Picoin L1.

## Picoin L1

L1 is responsible for:

- Security.
- Identity.
- PI payments.
- Staking and reputation.
- Hash registration.
- Future validator signatures.
- Final settlement.

L1 should not run benchmarks, GPU workloads, or heavy AI workloads inside every consensus node.

## Picoin Forge L2

L2 is responsible for:

- Measuring worker compute capacity.
- Assigning random challenges.
- Tracking uptime and reliability.
- Calculating verified compute scores.
- Running a minimal verified workload queue.
- Closing epochs.
- Producing settlement JSON and result hashes.

## Worker

The worker runs on Linux and provides:

- Wallet registration.
- Basic machine profile.
- CPU/RAM/IO benchmark.
- Optional GPU score path, enabled only after a verified GPU workload proof.
- Heartbeat generation.
- Challenge solving.
- Persistent config with wallet and coordinator URL.
- Passive GPU detection.

## Coordinator

The coordinator provides:

- Worker registry.
- Challenge engine.
- Optional challenge expiration scheduler.
- Score engine.
- Epoch engine.
- Reward engine.
- Simulated settlement.

The MVP coordinator stores workers, challenges, and epoch state in local SQLite. Settlement remains exported as JSON so it can later become an L1 payload artifact.

The coordinator also stores an append-only event log in SQLite. Events are used for auditability and future L1 settlement proofs.

`/events/export` and `picoin-forge-coordinator export-events` export events as canonical JSONL with an export hash. This creates a portable audit artifact without changing L1.

The coordinator stores normalized benchmark metrics and challenge metrics separately from events. Metrics are intended for calibration, dashboards, and fraud analysis. They do not change L1 and do not move PI.

`/metrics/calibration` and `picoin-forge-coordinator metrics-calibration` generate read-only recommended benchmark caps from observed worker data. Operators must review and apply env vars manually.

Current verified workload types:

- `hash_text`: deterministic text hash.
- `text_classify`: deterministic keyword-based text classification.
- `batch_summarize`: deterministic extractive batch summarization.

## Federated Simulation

`picoin-forge-coordinator federation-demo` runs multiple independent local coordinators, closes one epoch per coordinator, and builds a federation manifest hash. This is not L1 consensus; it is an L2 audit artifact for testing multi-coordinator settlement shape before any real network integration.

## Local Simulation

The MVP includes Docker Compose for a local network with one coordinator and three workers. This is for L2 development only and does not connect to Picoin L1.

## Challenges

Challenges prove availability after registration:

- CPU challenge: deterministic hash loop.
- RAM challenge: deterministic memory buffer.
- IO challenge: deterministic temporary write/read hash.
- GPU challenge: verified workload proof. Workers without a supported GPU backend fail cleanly.

## Epochs

An epoch closes by:

1. Refreshing each worker score.
2. Summing total verified compute.
3. Distributing `epoch_reward` proportionally.
4. Writing `settlement_epoch_<n>.json`.
5. Computing a deterministic `result_hash`.

## MVP API

```text
GET  /health
GET  /
GET  /events
GET  /events/export
GET  /epochs
GET  /epochs/{epoch_id}
GET  /epochs/{epoch_id}/l1-preview
GET  /metrics/benchmarks
GET  /metrics/calibration
GET  /metrics/challenges
POST /workers/register
GET  /workers
GET  /workers/{worker_id}/metrics
POST /benchmarks
POST /heartbeats
POST /workloads
GET  /workloads
POST /workloads/claim
POST /workloads/{task_id}/submit
POST /challenges
GET  /challenges/{challenge_id}
GET  /workers/{worker_id}/challenges
POST /challenges/{challenge_id}/submit
POST /challenges/expire
POST /epochs/close
```

The background challenge expiration scheduler is disabled by default. It can be enabled with `PICOIN_FORGE_CHALLENGE_EXPIRER_SECONDS`.
