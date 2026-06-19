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
- Verifying full AI model availability as network capacity.
- Routing stake-gated AI requests to verified model workers.
- Running a minimal verified workload queue.
- Closing epochs.
- Producing settlement JSON and result hashes.

## Worker

The worker runs on Linux and provides:

- Wallet registration.
- Basic machine profile.
- CPU/RAM/IO benchmark.
- Optional GPU score path, enabled only after a verified GPU workload proof.
- Optional full AI model profile and availability proof.
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

`/metrics/calibration` and `picoin-forge-coordinator metrics-calibration` generate read-only recommended benchmark caps from observed worker data. `/metrics/calibration/session` and `picoin-forge-coordinator metrics-calibration-session <dir>` produce a reviewable calibration session JSON plus `recommended_caps.env`. Operators must review and apply env vars manually.

The coordinator also includes an AI access queue. Requesters submit prompts with a simulated stake snapshot. Queued requests are ordered by stake snapshot first and creation time second, making staking an access utility rather than a direct payment. Workers can claim only if they have verified AI model capacity, an endpoint, and matching capabilities. A completed AI request produces a receipt hash and explicitly records that no L1 transaction and no per-task payment were created.

`/ai/capabilities` summarizes ready model workers by provider, model, capability, max context, and max parameter count. This lets clients discover what the verified network can serve before creating an AI request.

AI routing is capacity-aware. Compatible workers are ranked by verified model score, parameter count, context window, capabilities, uptime, reliability, and current assigned AI load. This keeps the network distributed by node capacity rather than assigning every request to the first worker that polls.

AI request assignments are lease based. If a worker claims a request and does not return before `PICOIN_FORGE_AI_REQUEST_LEASE_SECONDS`, the request can be requeued for another compatible worker through `/ai/requests/expire`. After `PICOIN_FORGE_AI_REQUEST_MAX_ASSIGNMENTS`, the request is marked failed. This failover protects user access while preserving the core economics: reward is for verified contribution to the network, not per request.

AI requests expose a compact status endpoint for polling. `/ai/requests/{request_id}/status` reports the current status, assignment attempts, lease expiration, result readiness, receipt readiness, and failure reason. Verified AI requests also expose result and receipt endpoints. `/ai/requests/{request_id}/result` returns the verified model output and hashes. `/ai/requests/{request_id}/receipt` reconstructs the canonical receipt payload from the stored request state and confirms that the computed hash matches the saved `receipt_hash`.

Requesters can set `store_output=false` when creating an AI request. The coordinator still stores output hashes and receipt hashes, but does not persist the output text. This is a basic MVP privacy control for pilots; stronger requester encryption can be added later.

Queued or assigned AI requests can be canceled through `/ai/requests/{request_id}/cancel`. A verified request cannot be canceled because the output and receipt are already audit artifacts.

Single request audit exports are available through `/ai/requests/{request_id}/export`. The default export redacts prompt and output content and includes hashes, status, route, model profile, and no-payment flags. `include_content=true` can be used for debugging exports when the requester accepts that prompt/output content will be included.

Current verified workload types:

- `hash_text`: deterministic text hash.
- `text_classify`: deterministic keyword-based text classification.
- `batch_summarize`: deterministic extractive batch summarization.
- `text_embed`: deterministic lightweight hash-based embedding vector.

These workload types prove queue lifecycle and audit behavior. The L2 reward model is still capacity based: useful prompts and workloads are evidence, while epoch rewards come from verified contribution to the network.

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
- AI model challenge: verified full model availability proof. Advertised model size, context, capabilities, and uptime can improve `ai_model_score` only after a valid proof. The current proof adapter supports Ollama-style `/api/generate` and OpenAI-compatible `/v1/chat/completions` endpoints.

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
GET  /metrics/calibration/session
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
GET  /ai/capabilities
POST /ai/requests
GET  /ai/requests
GET  /ai/summary
GET  /ai/requests/{request_id}/routing
GET  /ai/requests/{request_id}/status
GET  /ai/requests/{request_id}/receipt
GET  /ai/requests/{request_id}/result
GET  /ai/requests/{request_id}/export
POST /ai/requests/expire
POST /ai/requests/claim
POST /ai/requests/{request_id}/cancel
POST /ai/requests/{request_id}/submit
POST /challenges
GET  /challenges/{challenge_id}
GET  /workers/{worker_id}/challenges
POST /challenges/{challenge_id}/submit
POST /challenges/expire
POST /epochs/close
```

The background challenge expiration scheduler is disabled by default. It can be enabled with `PICOIN_FORGE_CHALLENGE_EXPIRER_SECONDS`.
