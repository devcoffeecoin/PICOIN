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
picoin-forge-coordinator demo --workers 3
```

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

## Coordinator API

```text
GET  /health
GET  /
GET  /events
POST /workers/register
GET  /workers
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

## GPU Policy In MVP

The worker detects GPU presence passively with safe local checks such as `nvidia-smi` or `lspci` when available. Detection alone does not increase `gpu_score`.

The MVP also supports a `gpu` challenge type as a handshake-only placeholder. Passing that challenge proves the worker supports the GPU challenge path, but it does **not** increase reliability, `gpu_score`, or simulated rewards yet. Real GPU rewards require a future verified GPU workload challenge.
