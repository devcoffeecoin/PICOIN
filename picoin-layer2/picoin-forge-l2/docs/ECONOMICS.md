# Economics

Picoin Forge L2 pays for verified compute capacity, not one-off task completion.

## Base Reward Formula

```text
reward_worker =
epoch_reward * worker_verified_compute / total_verified_compute
```

## Verified Compute Formula

```text
verified_compute_score =
cpu_score * cpu_weight
+ gpu_score * gpu_weight
+ ram_score * ram_weight
+ io_score * io_weight
+ uptime_score * uptime_weight
+ reliability_score * reliability_weight
- penalty_score
```

## MVP Weights

```text
cpu_weight = 1.0
gpu_weight = 2.5
ram_weight = 0.35
io_weight = 0.25
uptime_weight = 0.50
reliability_weight = 0.75
```

In the current MVP, passive GPU detection does not increase `gpu_score`. A worker can receive a bounded `gpu_score` only after passing a verified GPU workload challenge. Real-network caps should still be calibrated with worker data before production payments.

## Simulated Epoch Reward

The MVP uses:

```text
epoch_reward = 100 PI
```

No real PI is moved.

## Rounding Rule

When total verified compute is positive, the last reward row absorbs the small decimal remainder so the sum of worker rewards equals the configured `epoch_reward`.

If total verified compute is zero, all workers receive `0` in the simulated settlement.

## Why This Model

This model rewards workers for being useful available infrastructure. A worker with more verified compute and better uptime receives a larger share of the epoch. A worker with fake hardware claims, failed challenges, or poor uptime earns less.
