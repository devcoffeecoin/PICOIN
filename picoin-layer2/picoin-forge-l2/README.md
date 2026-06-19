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
picoin-forge-worker status
```

## Coordinator Commands

```bash
picoin-forge-coordinator start
picoin-forge-coordinator workers
picoin-forge-coordinator epoch close
picoin-forge-coordinator rewards
picoin-forge-coordinator l1-preview 1
picoin-forge-coordinator verify-settlement 1
picoin-forge-coordinator expire-challenges
picoin-forge-coordinator demo --workers 3
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
GET  /epochs
GET  /epochs/{epoch_id}
GET  /epochs/{epoch_id}/l1-preview
GET  /metrics/benchmarks
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

## Workload Queue Prototype

The MVP includes one verified workload type:

```text
hash_text
```

This is intentionally basic. It proves the lifecycle of a useful workload queue without introducing heavy AI execution, payments, or L1 writes yet.

## GPU Policy In MVP

The worker detects GPU presence passively with safe local checks such as `nvidia-smi` or `lspci` when available. Detection alone does not increase `gpu_score`.

The MVP also supports a `gpu` challenge type as a handshake-only placeholder. Passing that challenge proves the worker supports the GPU challenge path, but it does **not** increase reliability, `gpu_score`, or simulated rewards yet. Real GPU rewards require a future verified GPU workload challenge.
