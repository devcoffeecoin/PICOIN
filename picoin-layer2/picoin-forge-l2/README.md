# Picoin Forge L2

Picoin Forge L2 is the base MVP for a future Picoin compute layer based on **Proof of Available Compute**.

This is not a task marketplace where a user creates one job and pays for that one job. The intended model is a distributed compute network:

1. A user installs `picoin-forge-worker`.
2. The worker registers a PI wallet.
3. The worker proves local CPU, optional GPU, RAM, IO, latency, uptime, and reliability.
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
picoin-forge-worker start
picoin-forge-worker loop-once --coordinator-url http://127.0.0.1:9380
picoin-forge-worker loop --coordinator-url http://127.0.0.1:9380 --interval-seconds 30
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
picoin-forge-coordinator expire-challenges
picoin-forge-coordinator demo --workers 3
picoin-forge-coordinator federation-demo --coordinators 2 --workers-per-coordinator 2
picoin-forge-coordinator verify-federation federation-manifest.json
```

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
GET  /metrics/challenges
POST /workers/register
GET  /workers
GET  /workers/{worker_id}/metrics
POST /workloads
GET  /workloads
POST /workloads/claim
POST /workloads/{task_id}/submit
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
cpu, ram, io, gpu
```

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

The MVP includes three verified workload types:

```text
hash_text
text_classify
batch_summarize
```

`hash_text` verifies a basic deterministic task. `text_classify` verifies a deterministic keyword-based classification payload. `batch_summarize` verifies a deterministic extractive summary. These are intentionally basic and prove the lifecycle of useful workloads without introducing heavy AI execution, payments, or L1 writes yet.

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
