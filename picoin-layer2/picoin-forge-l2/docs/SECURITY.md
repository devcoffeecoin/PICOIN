# Security

Picoin Forge L2 must not trust declared hardware. Compute score only grows through benchmarks, challenges, uptime, and reliability.

## Threats

## Fake Hardware

A worker may claim a GPU or high resources it does not have.

Mitigation:

- Ignore declared hardware unless backed by benchmarks and random challenges.
- Use future validator audits.
- In the MVP, passive GPU detection alone does not increase `gpu_score`.
- The MVP `gpu` challenge is handshake-only and does not increase reliability or reward score.

## Replay Attack

A worker may reuse old challenge results.

Mitigation:

- Each challenge has a unique seed and deadline.
- Result hash binds to the challenge.

## Sybil Attack

A participant may create many low-quality workers.

Mitigation:

- Future staking/pledging on L1.
- Reputation per worker.
- Reward proportional to verified compute, not worker count.

## Forged Benchmark

A worker may submit fake benchmark numbers.

Mitigation:

- Random challenges after benchmark.
- Penalize mismatch between benchmark score and challenge performance.
- Future sandboxed benchmark attestations.

## Collusion

Workers and validators may collude.

Mitigation:

- Redundant validation.
- Random validator assignment.
- Future validator signatures on L1.
- Slashing and reputation penalties.

## Heartbeat Spoofing

A worker may send heartbeat while not actually providing compute.

Mitigation:

- Heartbeats only affect uptime.
- Compute score still depends on benchmark and challenges.
- Random challenge failures reduce reliability and add penalties.

## Metrics Safety

Benchmark and challenge metric tables are observational in the MVP.

Mitigation:

- Metrics support dashboards, calibration, and fraud analysis.
- Metrics do not trigger payments.
- Metrics do not create L1 transactions.

## Coordinator Write Token

The coordinator can require a shared write token for worker submissions.

Mitigation:

- Set `PICOIN_FORGE_COORDINATOR_TOKEN` to protect write endpoints.
- Workers send `X-Picoin-Forge-Token`.
- If the token is unset, the MVP remains open for local development compatibility.

## Worker Request Signatures

Workers can sign coordinator write requests with a local Ed25519 key.

Mitigation:

- `picoin-forge-worker register` creates a local private key.
- The coordinator stores only the worker public key.
- Write requests include worker id, timestamp, and signature headers.
- Set `PICOIN_FORGE_REQUIRE_WORKER_SIGNATURES=1` to require signatures.
- Replayed signatures are rejected through a local replay cache.
- This is still L2-only and does not create L1 transactions.
