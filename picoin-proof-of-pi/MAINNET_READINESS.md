# Picoin Mainnet Readiness

This document tracks what must be true before Picoin can launch a mainnet with real value. It is intentionally operational: each area has a status, launch criteria, checks, and remaining work.

Status key:

- `ready`: acceptable for mainnet launch.
- `testnet-ready`: works on public testnet, but needs longer observation or hardening.
- `needs-work`: known gaps remain.
- `blocked`: cannot launch until resolved.

## Current Snapshot

| Area | Status | Notes |
| --- | --- | --- |
| Public testnet | `testnet-ready` | Public HTTPS API is live at `https://api.picoin.science`. Mining and external validation are running. |
| Validator topology | `testnet-ready` | Three validators run on separate droplets, one validator per machine. |
| Consensus quorum | `needs-work` | Current `3/3` quorum proves correctness but does not tolerate a validator outage. |
| Chain replay and sync | `testnet-ready` | Catch-up, checkpoints, repair, and peer restore flows exist and have been used operationally. |
| Economic accounting | `testnet-ready` | Reward repair and full audit are available; must remain clean under sustained public mining. |
| Slashing and validator penalties | `needs-work` | Invalid signatures/fraud penalties exist, but no-show validator handling needs mainnet rules. |
| Peer discovery | `needs-work` | Bootstrap peers exist, but public peer discovery is not yet decentralized. |
| Node operations | `testnet-ready` | Systemd, logs, backups, smoke checks, and refresh scripts exist. |
| Security review | `needs-work` | Needs adversarial tests, endpoint hardening, and external review before real value. |
| Mainnet genesis | `blocked` | Must freeze parameters, disable faucet, define genesis, and create mainnet chain ID. |

## Mainnet Blockers

Mainnet launch is blocked until these are done:

- Define final `mainnet` `network_id`, `chain_id`, genesis hash, and launch timestamp.
- Freeze protocol parameters: reward, target block time, quorum, epoch length, slashing, faucet off, and treasury rules.
- Decide whether launch quorum remains `3/3` temporarily or moves to a fault-tolerant quorum such as `3/4` or `5/7`.
- Add rules for validator no-shows, delayed validation, and repeated downtime.
- Run sustained public testnet for multiple days with clean `/health`, `/audit/full`, and sync reports.
- Publish mainnet operating policy: validator requirements, miner rules, reward rules, reserve/treasury policy, incident process.
- Complete a security pass over consensus, validation, identity, transaction, and restore flows.

## Public Testnet Exit Criteria

The public testnet should satisfy all of these before mainnet:

- `https://api.picoin.science/health` returns `status=ok`.
- `https://api.picoin.science/node/sync-status` shows current height, no stale peers, and no pending replay backlog.
- `https://api.picoin.science/consensus/status` shows no unresolved forks or stuck competing proposals.
- At least three independent validators are online.
- Validator logs show successful quorum:
  - `approvals=2/3`
  - `approvals=3/3`
- Miner logs show new tasks and accepted reveals.
- Full audit reports `valid=true`.
- No reward mismatches for at least one sustained test period.
- Node restore from peer snapshot has been tested.
- Backup and restore from SQLite have been tested.
- Fresh node onboarding from public docs has been tested by someone other than the original operator.

Suggested checks:

```bash
curl https://api.picoin.science/health
curl https://api.picoin.science/node/sync-status
curl https://api.picoin.science/consensus/status
curl https://api.picoin.science/validators?limit=100

cd /opt/picoin/picoin-proof-of-pi
.venv/bin/python -m picoin node audit --server http://127.0.0.1:8000
.venv/bin/python -m picoin node report \
  --server http://127.0.0.1:8000 \
  --peer https://api.picoin.science
```

## Consensus And Validation

Current state:

- External commit/reveal validation is active.
- Validators submit signed votes through `POST /validation/results`.
- Duplicate votes are blocked by `UNIQUE(job_id, validator_id)`.
- Jobs reach `approved` only after quorum.
- Jobs reach `rejected` only after rejection quorum.
- Tasks and pending validation jobs expire after the task expiration window.

Mainnet work:

- Decide final quorum model:
  - Testnet now: `3/3`.
  - Safer mainnet direction: `3/4`, `5/7`, or larger as validator count grows.
- Add or formalize no-show handling:
  - Track validator job lease.
  - Penalize repeated GET-without-POST behavior.
  - Reduce selection score for repeated missed validations.
- Add validation deadlines directly to `validation_jobs`.
- Reject late validator votes after expiration.
- Add rescue validation when selected validators fail to respond.
- Add metrics for jobs stuck at partial quorum.

Tests to add:

- Validator gets a job then disconnects.
- Validator submits result to wrong method or wrong endpoint.
- Validator POST times out.
- Validator votes after task expiration.
- One validator rejects while two approve.
- Same validator attempts duplicate vote.
- Validator identity file is reused on two machines.
- Validator public key changes for same ID.

## Network And Peer Layer

Current state:

- Public API uses HTTPS.
- Bootstrap peer is `https://api.picoin.science`.
- Validator nodes can catch up from bootstrap.
- Checkpoint restore from peer exists.

Mainnet work:

- Add public peer discovery or signed seed list.
- Run more than one bootstrap/API endpoint.
- Document internal fallback behavior without exposing raw IPs publicly.
- Add peer scoring and stale peer eviction policy.
- Add signed checkpoint policy.
- Add explorer-facing node status endpoints that do not expose sensitive internals.

Launch criteria:

- At least two bootstrap-grade nodes are online.
- A fresh public node can sync from docs without manual intervention.
- Peer restore and catch-up produce identical block height/hash.
- Explorer and public API agree on height/hash.

## Economic Readiness

Current state:

- Rewards split is implemented.
- Ledger audit verifies balances and reward totals.
- Science Compute Reserve and Scientific Development Treasury accounting exist.
- Repair command exists for older testnet reward rows.

Mainnet work:

- Freeze emission schedule.
- Decide total supply narrative and reward decay, if any.
- Disable faucet on mainnet.
- Replace simulated validator stake with real staking or explicitly define initial validator collateral policy.
- Define treasury unlock governance.
- Define reserve activation governance.
- Publish economic spec.

Launch criteria:

- `/audit/full` is clean after sustained mining.
- Reward table, ledger entries, and balances match exactly.
- Genesis supply and allocations are documented.
- Treasury and reserve accounts are documented.
- No hidden premine or undocumented allocation.

## Security Readiness

Mainnet work:

- Review every write endpoint:
  - `/tasks/next`
  - `/tasks/commit`
  - `/tasks/reveal`
  - `/validation/results`
  - `/tx/submit`
  - `/consensus/proposals`
  - `/consensus/votes`
  - checkpoint restore/repair endpoints
- Add stronger rate limits for miners, validators, wallets, and peer registration.
- Add request body size limits.
- Add replay protection review for all signed payloads.
- Add adversarial tests for malformed signatures, stale timestamps, wrong chain IDs, duplicate nonces, and invalid keys.
- Review private key storage guidance.
- Ensure public docs never include private keys.
- Add incident playbooks for halted chain, bad validator, bad miner, fork, DB corruption, and API outage.

Launch criteria:

- No known critical/high security issues.
- All signed payloads include enough domain separation.
- All identity examples use placeholders.
- Mainnet deployment has secrets outside git.
- Backups are encrypted or access-controlled.

## Operations Readiness

Current state:

- Systemd units exist.
- Refresh script preserves state.
- Health, sync, audit, report, catch-up, repair, and restore commands exist.
- Public testnet docs are in `deploy/README-public-testnet.md`.

Mainnet work:

- Add mainnet env templates.
- Add mainnet systemd runbook.
- Add monitoring dashboard.
- Add alert thresholds:
  - API down.
  - block height stalled.
  - validator offline.
  - stuck validation jobs.
  - peer mismatch.
  - audit invalid.
  - disk space low.
  - backup failure.
- Add release checklist.
- Add rollback and emergency pause policy.

Launch criteria:

- Every mainnet service can be restarted from runbook.
- Backup restore has been tested.
- A new operator can deploy a validator using only public docs.
- Logs are understandable and include validator/miner names.

## Product And Public Launch

Mainnet work:

- Public website updated.
- Explorer stable.
- ANN draft written.
- Public docs finalized.
- Validator/miner onboarding finalized.
- Risk disclosure written.
- Mainnet launch phases defined.

Launch phases:

1. Private mainnet rehearsal with known validators.
2. Public read-only explorer/API.
3. Limited public mining.
4. Community validator onboarding.
5. Reserve/treasury governance activation only after network stability.

## Immediate Next Tasks

Recommended order:

1. Run the current public testnet for sustained uptime and collect metrics.
2. Add stuck validation job monitoring.
3. Add tests for validator disconnects and POST failures.
4. Decide quorum roadmap: keep `3/3` for testnet, plan `3/4` or `5/7` before mainnet.
5. Draft `MAINNET_PARAMETERS.md`.
6. Draft `MAINNET_GENESIS.md`.
7. Add mainnet `.env` templates after parameters are frozen.
8. Prepare explorer/website ANN links.

Mainnet parameter decisions are tracked in `MAINNET_PARAMETERS.md`.

## Mainnet Go / No-Go

Mainnet can be considered only when:

- Public testnet has clean sustained operation.
- Consensus tolerates expected validator failures or the launch explicitly accepts the risk.
- Economic audit is clean.
- Genesis and chain parameters are frozen.
- Operators can restore nodes from backups and peer snapshots.
- Public docs are complete.
- Security review has no open critical blockers.
