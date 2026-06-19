# Future Picoin L1 Settlement

This MVP does not touch Picoin L1.

Future L1 settlement would register compact epoch data after L2 validation.

## Data To Register

- `worker_id`
- `wallet`
- `epoch_id`
- `verified_compute_score`
- `reward`
- `result_hash`
- future validator signatures

## What Stays Off-Chain

- Full benchmark logs.
- Raw machine metrics.
- Challenge execution traces.
- IO/RAM temporary data.
- Heavy AI or compute tasks.

## Why

L1 should remain light and secure. L2 does heavy measurement and verification. L1 stores final hashes, reputation, and payment settlement records.

## MVP Settlement JSON

The current MVP writes:

```text
settlement_epoch_<n>.json
```

This file contains:

- `epoch_id`
- `epoch_reward`
- `total_verified_compute`
- worker rewards
- `result_hash`
- timestamp
- note that no real L1 transaction was created
