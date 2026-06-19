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

## MVP L1 Preview Payload

The current MVP can also produce a signature-ready preview payload:

```text
GET /epochs/{epoch_id}/l1-preview
picoin-forge-coordinator l1-preview <epoch_id>
```

Example shape:

```json
{
  "schema_version": "picoin-forge-l2-settlement-preview-v1",
  "payload_type": "l2_epoch_settlement_preview",
  "epoch_id": 1,
  "epoch_reward": 100.0,
  "total_verified_compute": 1234.56,
  "worker_count": 2,
  "settlement_result_hash": "settlement_hash",
  "worker_rewards": [],
  "payload_hash": "canonical_preview_hash",
  "signatures": [],
  "no_l1_transaction_created": true
}
```

This preview is not submitted to Picoin L1. It exists to stabilize the future payload format before real integration.

## Offline Verification

Any closed epoch can be checked locally:

```text
picoin-forge-coordinator verify-settlement <epoch_id>
```

The verifier recalculates:

- `total_verified_compute`
- settlement `result_hash`
- reward sum
- preview `payload_hash`
- `no_l1_transaction_created`
