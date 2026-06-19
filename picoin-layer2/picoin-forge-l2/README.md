# Picoin Forge L2

Picoin Forge L2 is the base MVP for a future Picoin compute layer based on **Proof of Available Compute**.

This is not a task marketplace where a user creates one job and pays for that one job. The intended model is a distributed compute network:

1. A user installs `picoin-forge-worker`.
2. The worker registers a PI wallet.
3. The worker proves local CPU, optional GPU, optional full AI model capacity, RAM, IO, latency, uptime, and reliability.
4. The coordinator assigns random challenges to verify that compute capacity is real and available.
5. At epoch close, each worker earns simulated PI proportional to verified compute share.

Base formula:

```text
reward_worker = epoch_reward * worker_verified_compute / total_verified_compute
```

Where:

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

## Current MVP

The MVP is local and simulated:

- No real L1 payments.
- No real staking.
- No private keys.
- No dangerous system actions.
- Settlement is written to local JSON.

## Working Artifact

The implementation roadmap lives in:

- [docs/WORKING_ARTIFACT.md](docs/WORKING_ARTIFACT.md)
- [docs/AI_WORKER_RUNBOOK.md](docs/AI_WORKER_RUNBOOK.md)

## Install

```bash
cd picoin-layer2/picoin-forge-l2
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
```

## Worker Commands

```bash
picoin-forge-worker register --wallet PI_YOUR_ADDRESS
picoin-forge-worker benchmark
picoin-forge-worker ai-smoke
picoin-forge-worker start
picoin-forge-worker loop-once --coordinator-url http://127.0.0.1:9380
picoin-forge-worker loop --coordinator-url http://127.0.0.1:9380 --interval-seconds 30
picoin-forge-worker loop --coordinator-url http://127.0.0.1:9380 --challenge-type ai_model
picoin-forge-worker rotate-key --submit
picoin-forge-worker status
```

## Coordinator Commands

```bash
picoin-forge-coordinator start
picoin-forge-coordinator workers
picoin-forge-coordinator epoch close
picoin-forge-coordinator rewards
picoin-forge-coordinator export-events audit/events.jsonl
picoin-forge-coordinator l1-preview 1
picoin-forge-coordinator verify-settlement 1
picoin-forge-coordinator metrics-calibration
picoin-forge-coordinator metrics-calibration-session calibration-output
picoin-forge-coordinator expire-challenges
picoin-forge-coordinator ai capabilities
picoin-forge-coordinator ai create-request PI_REQUESTER "Explain Picoin Forge." 5 --capabilities chat
picoin-forge-coordinator ai status AI_REQUEST_ID
picoin-forge-coordinator ai result AI_REQUEST_ID
picoin-forge-coordinator ai receipt AI_REQUEST_ID
picoin-forge-coordinator ai export-request AI_REQUEST_ID
picoin-forge-coordinator demo --workers 3
picoin-forge-coordinator local-ai-demo --workers 2
picoin-forge-coordinator local-ai-http-demo --workers 1
picoin-forge-coordinator federation-demo --coordinators 2 --workers-per-coordinator 2
picoin-forge-coordinator verify-federation federation-manifest.json
```

## User HTTP Client

Use `picoin-forge-client` when the coordinator is running as a service, Docker
container, or remote devnet:

```bash
picoin-forge-client health --coordinator-url http://127.0.0.1:9380
picoin-forge-client ai capabilities --coordinator-url http://127.0.0.1:9380
picoin-forge-client ai summary --coordinator-url http://127.0.0.1:9380
picoin-forge-client ai create PI_REQUESTER "Explain Picoin Forge L2." 25 --capabilities chat,reasoning --coordinator-url http://127.0.0.1:9380
picoin-forge-client ai run PI_REQUESTER "Explain Picoin Forge L2." 25 --capabilities chat,reasoning --coordinator-url http://127.0.0.1:9380
picoin-forge-client ai status AI_REQUEST_ID --coordinator-url http://127.0.0.1:9380
picoin-forge-client ai result AI_REQUEST_ID --coordinator-url http://127.0.0.1:9380
picoin-forge-client ai receipt AI_REQUEST_ID --coordinator-url http://127.0.0.1:9380
picoin-forge-client ai export AI_REQUEST_ID --coordinator-url http://127.0.0.1:9380
```

If `PICOIN_FORGE_COORDINATOR_TOKEN` is set on the coordinator, pass `--token`
or export the same variable before using write commands.

`ai run` is the shortest devnet user flow: it creates a stake-gated request,
polls until the request reaches a terminal state, and returns the verified
result plus receipt when available.

Optional automatic challenge expiration:

```bash
PICOIN_FORGE_CHALLENGE_EXPIRER_SECONDS=30 picoin-forge-coordinator start
```

Optional write authentication:

```bash
PICOIN_FORGE_COORDINATOR_TOKEN=change-me picoin-forge-coordinator start
```

When set, write endpoints require:

```text
X-Picoin-Forge-Token: change-me
```

If the token is not set, the coordinator remains open for local MVP compatibility.

Optional worker request signatures:

```bash
PICOIN_FORGE_REQUIRE_WORKER_SIGNATURES=1 picoin-forge-coordinator start
```

Workers created by `picoin-forge-worker register` store a local Ed25519 private key in the worker state directory and sign write requests automatically during `loop` and `loop-once`.

If a worker key needs to be rotated:

```bash
picoin-forge-worker rotate-key --submit --coordinator-url http://127.0.0.1:9380
```

The worker keeps the same `worker_id`, writes a local backup of the previous key, generates a new Ed25519 key, and re-registers the updated public key when `--submit` is used.

## Local AI Devnet Demo

Run a complete one-command L2 AI flow without touching Picoin L1:

```bash
cd picoin-layer2/picoin-forge-l2
picoin-forge-coordinator local-ai-demo --workers 2
```

The command creates a local coordinator state directory, registers local AI
workers with a deterministic `test-ai-model` backend, verifies AI model
capacity through an `ai_model` challenge, creates one stake-gated AI request,
assigns it to a compatible worker, records the model output hash, verifies the
receipt, and closes a simulated epoch.

The report is written to:

```text
.picoin-forge-l2-local-demo/local_ai_devnet_report.json
```

For a stronger smoke test that exercises the actual FastAPI routes over local
HTTP, run:

```bash
picoin-forge-coordinator local-ai-http-demo --workers 1
```

That command starts a temporary local coordinator on `127.0.0.1`, uses the
normal worker HTTP client, solves an `ai_model` challenge, submits the AI
request result through the public API, verifies the receipt endpoint, closes a
simulated epoch through `/epochs/close`, and writes:

```text
.picoin-forge-l2-local-http-demo/local_ai_http_devnet_report.json
```

Important invariants preserved by the demo:

- No Picoin L1 transaction is created.
- No per-request worker payment is created.
- Access is gated by a stake snapshot.
- Workers earn simulated epoch rewards by verified contribution to the network.

## Local Docker Simulation

Run one coordinator and three workers:

```bash
cd picoin-layer2/picoin-forge-l2
docker compose up --build
```

Dashboard:

```text
http://127.0.0.1:9380/
```

AI portal:

```text
http://127.0.0.1:9380/ai/portal
```

The dashboard includes worker score bars, latest benchmark metric bars, challenge pass/fail counts, epoch history, and recent events.

The compose file includes:

- Coordinator health check on `/health`.
- Workers waiting for the coordinator to become healthy.
- Worker health checks for local registration/config files.
- Challenge expiration scheduler enabled every 30 seconds.

## Coordinator API

```text
GET  /health
GET  /
GET  /events
GET  /events/export
GET  /epochs
GET  /epochs/{epoch_id}
GET  /epochs/{epoch_id}/l1-preview
GET  /metrics/benchmarks
GET  /metrics/config
GET  /metrics/calibration
GET  /metrics/calibration/session
GET  /metrics/challenges
POST /workers/register
GET  /workers
GET  /workers/{worker_id}/metrics
POST /workloads
GET  /workloads
POST /workloads/claim
POST /workloads/{task_id}/submit
GET  /ai/capabilities
GET  /ai/portal
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
POST /benchmarks
POST /heartbeats
POST /challenges
GET  /challenges/{challenge_id}
GET  /workers/{worker_id}/challenges
POST /challenges/{challenge_id}/submit
POST /challenges/expire
POST /epochs/close
```

Challenge types currently accepted:

```text
cpu, ram, io, gpu, ai_model
```

## AI Model Capacity

Picoin Forge L2 treats AI model availability as network contribution, not as a per-task payment.

A worker can advertise a real model profile:

```bash
PICOIN_FORGE_AI_MODEL_PROVIDER=ollama
PICOIN_FORGE_AI_MODEL_NAME=llama-3.1-70b
PICOIN_FORGE_AI_MODEL_PARAMETERS_B=70
PICOIN_FORGE_AI_MODEL_CONTEXT_TOKENS=131072
PICOIN_FORGE_AI_MODEL_QUANTIZATION=q4_k_m
PICOIN_FORGE_AI_MODEL_CAPABILITIES=llm,chat,reasoning,tool-use
PICOIN_FORGE_AI_MODEL_ENDPOINT=http://127.0.0.1:11434
```

Before joining a coordinator, verify the local AI runtime:

```bash
picoin-forge-worker ai-smoke
```

The smoke command checks the configured model profile, runs an availability
proof, performs one tiny inference, and prints whether the worker is ready to
serve stake-gated AI access requests. It does not touch Picoin L1 and does not
create per-task payments.

The `ai_model` challenge proves that the advertised model backend is available. Passing it can assign a bounded `ai_model_score`, which contributes to epoch share together with uptime and reliability. This does not mean a user pays the worker for one prompt; prompts and workloads are evidence/audit flow, while rewards are based on verified contribution across the epoch.

Current proof backends:

```text
ollama
openai-compatible
vllm
llamacpp
```

`ollama` uses `/api/generate`. `openai-compatible`, `vllm`, and `llamacpp` use `/v1/chat/completions`. The challenge sends a tiny deterministic availability prompt with a short timeout; it is not a billable user workload.

## AI Access Layer

The MVP includes a local AI access queue:

```text
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
```

This is not a per-task payment rail. A requester includes a simulated `stake_snapshot_pi`, and the coordinator checks it against:

```bash
PICOIN_FORGE_AI_ACCESS_MIN_STAKE_PI=1
```

Queued AI requests are prioritized by `stake_snapshot_pi` first and creation time second. This gives PI staking utility as an access rule, not as a per-prompt worker payment.

`GET /ai/capabilities` summarizes ready model workers, providers, model names, context windows, parameter sizes, and available capabilities so an app can discover what the network can serve before creating a request.

Eligible workers are selected from verified model capacity: they must have `ai_model_score > 0`, an available model endpoint, and matching capabilities. Completed responses create an auditable receipt hash with:

```text
no_l1_transaction_created = true
no_per_task_payment = true
```

Future L1 staking can replace the simulated stake snapshot when L2 is stable.

AI routing is distributed by worker capacity. The coordinator ranks compatible workers by:

- verified `ai_model_score`
- model parameter count
- context window
- capabilities
- uptime
- reliability
- active assigned AI requests

`GET /ai/requests/{request_id}/routing` shows the selected worker and candidate list for an AI request.

`GET /ai/requests/{request_id}/status` is the compact polling endpoint for requesters. It reports queue state, assignment attempts, lease expiration, result readiness, receipt readiness, and failure reason.

Assigned AI requests use leases so a stalled worker cannot hold a request forever:

```bash
PICOIN_FORGE_AI_REQUEST_LEASE_SECONDS=120
PICOIN_FORGE_AI_REQUEST_MAX_ASSIGNMENTS=3
```

When a lease expires, `POST /ai/requests/expire` moves the request back to `queued` for another verified worker. After the maximum assignment count, the request becomes `failed`. This keeps request access reliable without changing the reward model: workers still earn by verified network contribution, not by charging per completed prompt.

`picoin-forge-worker loop-once` can claim one compatible AI request, run it against the worker's registered model profile, submit the output, and create a verified receipt. If the model endpoint is unavailable, the worker reports the local failure in its loop result and the request lease can expire for another worker.

`GET /ai/requests/{request_id}/receipt` reconstructs the canonical receipt payload and verifies that the stored `receipt_hash` still matches.

`GET /ai/requests/{request_id}/result` returns the verified model output once the worker has submitted it.

Requests can set `store_output=false` to avoid persisting the model output text on the coordinator. In that mode `/result` still returns `output_hash`, `receipt_hash`, and `output_stored=false`, but `output` is `null`.

`GET /ai/requests/{request_id}/export` returns a canonical audit artifact for one request. By default it redacts prompt and output content and exports hashes only; add `include_content=true` for a full debugging export.

`POST /ai/requests/{request_id}/cancel` cancels queued or assigned requests. Verified requests cannot be canceled because their receipt is already part of the L2 audit trail.

## L1 Boundary

Picoin L1 is not touched in this MVP. Future settlement would only register compact data:

- `worker_id`
- `wallet`
- `epoch_id`
- `verified_compute_score`
- `reward`
- `result_hash`
- validator signatures

The heavy compute and validation remain off-chain in L2.

## Audit Export

Coordinator events can be exported as canonical JSONL:

```bash
picoin-forge-coordinator export-events audit/events.jsonl
```

The command returns `event_count` and `export_hash`. The API equivalent is:

```text
GET /events/export
```

## Workload Queue Prototype

The MVP includes four verified workload types:

```text
hash_text
text_classify
batch_summarize
text_embed
```

`hash_text` verifies a basic deterministic task. `text_classify` verifies a deterministic keyword-based classification payload. `batch_summarize` verifies a deterministic extractive summary. `text_embed` verifies a lightweight hash-based embedding vector. These queue types prove the lifecycle and audit flow; full model availability is handled separately through the `ai_model` capacity profile and challenge.

Example `text_classify` payload:

```json
{
  "task_type": "text_classify",
  "payload": {
    "text": "This batch needs OCR and text extraction.",
    "labels": {
      "vision": ["ocr", "image"],
      "audio": ["speech", "transcription"]
    },
    "default_label": "general"
  }
}
```

Example `text_embed` payload:

```json
{
  "task_type": "text_embed",
  "payload": {
    "documents": [
      "Picoin Forge turns verified compute into useful capacity.",
      "Embedding jobs can be audited before heavier AI models arrive."
    ],
    "dimensions": 12
  }
}
```

Example `batch_summarize` payload:

```json
{
  "task_type": "batch_summarize",
  "payload": {
    "documents": [
      "Picoin Forge measures available compute. Workers prove uptime with heartbeats.",
      "Workers solve random challenges. Epoch settlement rewards verified compute."
    ],
    "max_sentences": 2
  }
}
```

## Benchmark Calibration

Benchmark normalization caps can be tuned without code changes:

```bash
PICOIN_FORGE_CPU_SCORE_CAP=1000
PICOIN_FORGE_GPU_SCORE_CAP=1000
PICOIN_FORGE_RAM_SCORE_CAP=10000
PICOIN_FORGE_IO_SCORE_CAP=10000
```

The current values are visible at:

```text
GET /metrics/config
```

The coordinator can also generate a read-only calibration report from observed metrics:

```bash
picoin-forge-coordinator metrics-calibration --percentile 0.95
```

Or through the API:

```text
GET /metrics/calibration?percentile=0.95
```

The report prints recommended env vars, but does not change configuration automatically.

For an operator-ready artifact:

```bash
picoin-forge-coordinator metrics-calibration-session calibration-output
```

This writes:

```text
calibration-output/calibration_session.json
calibration-output/recommended_caps.env
```

The session includes a `session_hash`. Applying `recommended_caps.env` is still a manual operator decision.

## Federated Coordinator Simulation

The MVP can simulate several independent L2 coordinators without touching Picoin L1:

```bash
picoin-forge-coordinator federation-demo --coordinators 2 --workers-per-coordinator 2
```

Each simulated coordinator closes its own epoch and produces its own L1 preview payload. The command returns a federation manifest with a root hash that can be audited locally.

To verify a saved manifest:

```bash
picoin-forge-coordinator verify-federation federation-manifest.json
```

## GPU Policy In MVP

The worker detects GPU presence passively with safe local checks such as `nvidia-smi` or `lspci` when available. Detection alone does not increase `gpu_score`.

The MVP supports a `gpu` challenge type that requires a verified GPU workload proof. Workers without a GPU backend fail this challenge cleanly. Passing the GPU challenge can improve reliability and assign a bounded `gpu_score` from verified proof data. Passive detection still does not increase `gpu_score`.
