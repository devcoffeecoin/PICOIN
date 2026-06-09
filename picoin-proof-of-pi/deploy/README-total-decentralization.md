# Picoin Total Decentralization Roadmap

This document tracks the post-Phase-8 decentralization work on branch
`codex/total-decentralization`.

The first eight phases reduced single-server risk for reads, snapshots,
exchange full nodes, mempool propagation, deterministic block construction, and
finality certificates. They did not finish total decentralization. A full node
that can only sync and serve local reads is useful, but it is not enough if
miners, validators, pools, and finality still need `api.picoin.science` as the
live coordinator.

## Problem Statement

The pool full-node test exposed the real boundary:

- A local full node can restore a mainnet snapshot, catch up, and stay replay
  healthy.
- The same node still reports `mining_ready=false` when it has no locally
  reachable validator quorum.
- The node can see validators from state, but validator heartbeats, job
  discovery, votes, and quorum are still effectively bootstrap-local.
- Pointing the pool at that local node is unsafe until the node can assign work,
  gather validator votes, finalize a block, and propagate the winning result
  without relying on the bootstrap.

Total decentralization means any healthy full node can participate in the write
path. Reads alone do not qualify.

## Total Decentralization Definition

Picoin is considered totally decentralized only when all of these are true:

- A miner can request, commit, and reveal work through any healthy full node.
- A validator can discover validation jobs, vote, and keep heartbeat presence
  through any healthy full node.
- A pool can mine against its own full node and use other full nodes only as
  peers or failover targets.
- A wallet or exchange can submit signed transactions to any healthy full node.
- A new full node can join and catch up from peers without requiring the
  bootstrap as the only source of snapshots, peers, mempool, blocks, or finality.
- Stopping `api.picoin.science` does not stop new block production as long as a
  peer quorum of full nodes and validators remains online.
- Conflicting blocks, orphan blocks, duplicate transactions, stale tasks, and
  divergent peers are resolved by deterministic protocol rules, not manual
  SQLite intervention.

## Required Chain Branch Model

Total decentralization requires explicit chain branch handling. A node must not
pretend that there is only one linear chain while peers can deliver blocks in
different orders.

Required model:

- Every block stores `height`, `block_hash`, `parent_hash`,
  `finality_certificate_hash`, `branch_id`, `branch_status`, and
  `inherited_state_root`.
- The canonical branch is the branch selected by deterministic fork choice from
  valid finality certificates, not by local arrival order.
- A child block inherits balances, validator state, pending rewards, protocol
  params, nonce state, and mempool settlement state from its selected parent.
- A block whose parent is unknown is `orphan_pending_parent`, not divergent.
- A block with a known parent but losing fork choice is `orphan_losing_branch`.
- A block with invalid transactions, invalid state root, invalid certificate, or
  wrong network identity is `rejected_invalid`, not orphan.
- Orphan blocks are stored for audit and later adoption if their ancestors arrive
  and fork choice selects them.
- Applied state is updated only by the canonical branch. Losing branches never
  mutate wallet balances, validator rewards, pool payout accounting, or nonce
  state.
- Reorg is allowed only to a valid branch with a stronger deterministic finality
  proof and a known finalized ancestor. Reorg must rollback derived state to the
  ancestor, then replay the winning branch.

This branch model is a protocol requirement, not an explorer feature. Explorer
labels are only a view over the consensus branch state.

## Non-Negotiable Mainnet Safety Rules

- Do not remove the bootstrap from production until all bootstrap-off drills
  pass on an isolated network and then on a controlled mainnet shadow set.
- Do not point Pool 1 production mining at a local full node until Phase 14
  closes.
- Do not deploy validator write-path changes to every validator at once.
- Do not run schema-changing migrations on mainnet without a tested backup and
  restore drill.
- Do not accept a phase as closed unless the acceptance command is reproducible
  and its output is recorded.

## Phase 9: Write-Capable Full Node Baseline

Goal: define and enforce the difference between a read-only full node and a
write-capable full node.

Current full nodes can be healthy while still unable to mine because they lack
validator quorum. That must become an explicit role, not an operator surprise.

Implementation work:

- Add node capability fields to `/health`, `/protocol`, and `/node/sync-status`.
- Report `read_ready`, `tx_submit_ready`, `task_assign_ready`,
  `validation_job_ready`, and `block_finalize_ready` separately.
- Add config flags for `PICOIN_NODE_ROLE=read_only|write_candidate|validator_node|pool_node`.
- Refuse pool/miner local mode unless the target node reports
  `task_assign_ready=true` and `block_finalize_ready=true`.
- Add a full-node self-test command that explains exactly which dependency is
  missing.

Acceptance gates:

- A read-only full node still passes exchange smoke with
  `block_finalize_ready=false`.
- A pool-node preflight fails clearly when local validator quorum is absent.
- No miner, pool, validator, or exchange script mistakes read health for write
  readiness.

## Phase 10: Validator Presence Gossip

Goal: validator online/synced/eligible status is network-visible, not
bootstrap-local.

Today, a full node restored from snapshot knows validator records, but all
validators appear offline unless they heartbeat to that exact node. That blocks
local block finalization.

Implementation work:

- Sign validator heartbeat payloads with validator identity keys.
- Gossip heartbeat inventory between peers.
- Store heartbeat observations with source peer, observed timestamp, validator
  public key, node id, advertised address, height, sync status, and signature.
- Calculate eligibility from signed fresh heartbeats, stake, sync lag, and
  protocol version.
- Deduplicate heartbeats deterministically by validator id and signed sequence.
- Add anti-spam limits and stale heartbeat pruning.

Acceptance gates:

- Validator A can heartbeat to node A, validator B to node B, validator C to node
  C, and all three nodes see the same eligible validator set after peer gossip.
- Restarting one node does not erase network validator presence after reconcile.
- A fake heartbeat with the wrong signature is rejected and not gossiped.
- A stale heartbeat cannot make a validator eligible.

Branch implementation status:

- Added persistent `validator_heartbeats` storage with deterministic
  `heartbeat_id` deduplication.
- Added optional signed `heartbeat_at` support in validator heartbeats while
  keeping older heartbeat payloads accepted.
- Added `/validators/heartbeat/inventory` and
  `/validators/heartbeat/receive`.
- Added peer reconcile import of validator heartbeat inventory, preserving the
  original observation timestamp so stale heartbeats do not refresh eligibility.
- Covered invalid signatures, duplicate imports, stale imports, and reconcile
  imports with focused tests.

Phase 10 closure:

- Three fresh candidate nodes restored from mainnet snapshots.
- Validators A, B, and C heartbeated to different local nodes.
- Heartbeat inventory converged across A/B/C through peer reconcile.
- The same three validator ids became online, synced, and eligible on all
  three candidates after local lab stake was applied.

Remaining hardening after closure:

- Add signed monotonic heartbeat sequence for stronger replay protection.
- Add pruning/rate limits for high-volume heartbeat history.

Operator drill helper:

```bash
python3 deploy/scripts/phase10-readiness-heartbeat-drill.py \
  --reconcile \
  --required 3 \
  A=http://NODE_A:8000 \
  B=http://NODE_B:8000 \
  C=http://NODE_C:8000
```

When known, pass the three validator ids with `--expected-validator` so the
drill proves that those exact validators converged through heartbeat gossip.
For an unstaked lab drill, pass `--heartbeat-only`; this still requires signed
heartbeat convergence for the expected validators, but does not require those
validators to be wallet-staked or eligible for block finalization.

After heartbeat-only convergence passes on disposable candidate nodes, a lab can
exercise eligible quorum by applying identical synthetic wallet-backed stake to
the three local candidate DBs:

```bash
python3 deploy/scripts/phase10-lab-stake-validators.py \
  --db /var/lib/picoin/phase10-candidate/picoin.sqlite3 \
  --validator validator_A \
  --validator validator_B \
  --validator validator_C
```

This helper refuses non-`phase10-candidate` DB paths by default and does not
submit mainnet transactions. It is only for the isolated Phase 10/11 lab.

## Phase 11: Distributed Validation Job Discovery

Goal: validators can discover and vote on jobs created by any full node.

Today, a miner can reveal to a node, but validators must be able to find that
job across the network. Otherwise the local node creates pending jobs that never
close.

Implementation work:

- Make validation jobs canonical and gossipable.
- Add `/validation/jobs/inventory` and `/validation/jobs/receive`.
- Add deterministic job id rules for the same task reveal.
- Allow validators to poll their configured node and receive jobs created on
  peer nodes.
- Add `/validation/votes/inventory` and `/validation/votes/receive`.
- Gossip validator votes and make vote import idempotent with signature
  verification.
- Preserve vote payloads needed for finality certificate construction.
- Import the minimal miner/task rows required for a remote validation job to be
  locally valid without manual DB intervention.

Branch implementation status:

- Added validation job inventory/receive API.
- Added validation vote inventory/receive API.
- Added peer reconcile import for validation jobs and votes.
- Job gossip imports the associated task and miner stub when the receiving node
  has not seen the miner's original `/tasks/next` request.
- Vote gossip verifies the validator signature before inserting a vote.
- Duplicate job/vote gossip returns `duplicate` instead of raising noisy
  conflicts.
- A node that receives enough gossiped votes can run the existing quorum
  finalization path and create the finality certificate locally.
- Added focused tests for job import, vote signature verification, and
  idempotent duplicate vote handling.

Acceptance gates:

- Miner reveals to node A.
- Validators connected to nodes A, B, and C all discover the same job.
- Votes submitted to B and C reach A.
- All nodes converge on the same job status and vote set.
- Duplicate job/vote gossip returns idempotent success without noisy errors.

Phase 11 closure evidence:

- Date: 2026-06-09 UTC.
- Branch: `codex/total-decentralization`.
- Candidate A: `http://159.89.90.163:8000`, node id `phase10-a`,
  validator `validator_b1b25c2436b64aa4`.
- Candidate B: `http://68.183.113.210:8000`, node id `phase10-b`,
  validator `validator_9c28627a04ab43d6`.
- Candidate C: `http://159.223.96.125:8000`, node id `phase10-c`,
  validator `validator_20c12a849f384ea7`.
- A/B/C were restored from mainnet snapshots, reconciled with each other, and
  observed at the same canonical tip during the drill, for example height
  `11485` with hash
  `569c9850105486dc88ef5e8b7bb3b885cc315383d3ce9e648b5dac521a82640c`.
- All three candidate validators became online, synced, and eligible with
  `required_validator_approvals=3` and `eligible_validators=3`.
- A miner revealed work through candidate A.
- Validation jobs were closed with three validator approvals and finality
  certificates were written locally.
- Recorded certificates:
  - block `11497`, task `task_d48288cdf0d51045`,
    job `job_d69103a05ebe4971`, `required_approvals=3`,
    `approval_count=3`.
  - block `11498`, task `task_df91e17c4e2b357b`,
    job `job_12b8a679c7684e5d`, `required_approvals=3`,
    `approval_count=3`.
- Recorded votes for block `11498`:
  - `validator_9c28627a04ab43d6` approved at
    `2026-06-09T01:36:05.978246+00:00`.
  - `validator_20c12a849f384ea7` approved at
    `2026-06-09T01:36:14.491532+00:00`.
  - `validator_b1b25c2436b64aa4` approved at
    `2026-06-09T01:37:24.797324+00:00`.

Additional candidate drill evidence:

- Date: 2026-06-09 UTC.
- Branch: `codex/total-decentralization`, commit `bb44470`.
- Candidate nodes:
  - A: `http://159.89.90.163:8000`, node id `phase10-a`,
    validator `validator_b1b25c2436b64aa4`.
  - B: `http://68.183.113.210:8000`, node id `phase10-b`,
    validator `validator_9c28627a04ab43d6`.
  - C: `http://159.223.96.125:8000`, node id `phase10-c`,
    validator `validator_20c12a849f384ea7`.
- B initially had the validation job locally but did not vote because its
  validator service executed an older CLI path. The log showed
  `picoin: error: unrecognized arguments: --poll-seconds 1 --heartbeat-interval 30`.
  The service was fixed by forcing `PICOIN_HOME`, `PYTHONPATH`, and
  `PICOIN_PYTHON` to the git checkout under
  `/opt/picoin/src/PICOIN/picoin-proof-of-pi`.
- C then failed eligibility because its local node was replay-divergent:
  `finalized block previous_hash does not match local chain tip`. C was
  recovered from an A snapshot and returned to `replay=healthy`,
  `divergent=False`, `pending=0`.
- After B and C were healthy, a fresh block mined through A finalized with all
  three validator votes:
  - block `11502`, task `task_7a9cceed77c3a0e1`,
    job `job_37e4bfe293794db6`, `required_approvals=3`.
  - votes:
    - `validator_9c28627a04ab43d6` at
      `2026-06-09T15:50:24.177122+00:00`.
    - `validator_20c12a849f384ea7` at
      `2026-06-09T15:50:28.715839+00:00`.
    - `validator_b1b25c2436b64aa4` at
      `2026-06-09T15:50:29.431580+00:00`.
- A two-block follow-up drill was run with only two mining attempts because
  the droplets were CPU constrained:
  - block `11503`, task `task_8c730ad1d508ad3f`,
    job `job_4e741fcd2d2941b4`, `required_approvals=3`,
    `approval_count=3`.
  - job `job_799bf12dc7114bcd`, task `task_ea358b03b26ba865`, reached
    quorum with votes from C, A, and B at
    `2026-06-09T16:13:51.072126+00:00`.
- During the follow-up drill, B also diverged with
  `finalized block previous_hash does not match local chain tip`. B was
  recovered from an A snapshot and then contributed the missing third vote.

Phase 11 conclusion:

- Distributed validator presence, job visibility, vote gossip, and 3-of-3
  certificate finalization were proven across A/B/C.
- The candidate network still required manual snapshot recovery when a node
  received a finalized block whose parent did not match the local tip.
- That manual recovery is not acceptable for total decentralization. Automatic
  orphan/reorg handling is now the next blocking protocol task.

Operational fixes included in the closure:

- Finality import now matches imported certificates to the certificate task
  instead of accidentally reusing a same-range local task.
- Validator quorum remains fixed at protocol quorum. It no longer silently
  collapses from `3` to the currently online validator count, preventing unsafe
  `1/1` certificates during liveness loss.
- Reconciler can continuously reconcile multiple configured peers instead of a
  single peer.
- Validator worker defaults to the local node as coordinator, so a validator
  attached to a candidate node does not silently return to bootstrap.
- Validator result submission exposes a longer timeout for slow finalization.
- Validator loop iterations are intentionally short so heartbeat freshness is
  not lost while one worker process waits on long polling/finalization.
- Heartbeat probe timeouts no longer stop validators from polling available
  validation jobs.
- Validator sample verification can run with local process workers. This is
  CPU parallelism inside one validator; it does not increase vote count or
  change quorum.

Candidate validator service profile used in the closing drill:

```bash
PICOIN_VALIDATOR_SERVER=http://127.0.0.1:8000
PICOIN_VALIDATOR_NODE_SERVER=http://127.0.0.1:8000
PICOIN_VALIDATOR_NODE_TIMEOUT=30
PICOIN_VALIDATOR_SUBMIT_TIMEOUT=90
PICOIN_VALIDATOR_LOOPS=1
PICOIN_VALIDATOR_SLEEP=3
PICOIN_VALIDATOR_WORKERS=4
```

Candidate validator systemd path hardening used after the B/C mismatch:

```ini
[Service]
WorkingDirectory=/opt/picoin/src/PICOIN/picoin-proof-of-pi
ExecStart=
ExecStart=/usr/bin/bash -lc 'export PICOIN_HOME=/opt/picoin/src/PICOIN/picoin-proof-of-pi; export PYTHONPATH=/opt/picoin/src/PICOIN/picoin-proof-of-pi; export PICOIN_PYTHON=/opt/picoin/picoin-proof-of-pi/.venv/bin/python; exec /opt/picoin/src/PICOIN/picoin-proof-of-pi/deploy/scripts/picoin-worker-loop.sh'
```

Candidate reconciler profile used in the closing drill:

```bash
PICOIN_RECONCILER_MODE=reconcile
PICOIN_RECONCILE_PEERS=http://159.89.90.163:8000,http://68.183.113.210:8000,http://159.223.96.125:8000
PICOIN_RECONCILE_LIMIT=64
PICOIN_RECONCILER_SLEEP_SECONDS=15
```

Known Phase 11 boundary:

- The drill proves distributed validator presence, validation job/vote gossip,
  fixed quorum certificates, and multi-peer reconciliation.
- It does not yet prove full task assignment independence. Task assignment and
  reveal convergence are Phase 12.
- It does not yet prove automatic orphan/reorg recovery. Branch inheritance,
  orphan queues, and deterministic reorg are Phase 13.

## Phase 12: Decentralized Task Assignment

Goal: every healthy write-capable full node can serve canonical mining work for
the same competitive round.

Phase 5 proved miner task independence in a lab. This phase hardens it for
production with validator/job gossip attached.

Implementation work:

- Derive task ids from chain height, previous block hash, round seed, miner id,
  protocol params, and canonical mempool commitment.
- Ensure `/tasks/next` returns the same canonical work from all synced
  write-capable nodes.
- Add task inventory gossip so revealed/pending/stale states converge.
- Add first-valid-reveal rules for duplicate reveals across nodes.
- Ensure idle/rate-limit responses never crash Linux or Desktop miners.

Acceptance gates:

- The same miner can request work from A, B, or C and receive compatible task
  state.
- Commit on A and reveal on B succeeds when the task is canonical.
- Late reveal after a finalized winner becomes stale everywhere.
- Bootstrap-off drill still assigns work from non-bootstrap nodes.

Branch implementation status:

- Added `/tasks/inventory` and `/tasks/receive`.
- Task gossip exports the task row plus the associated miner identity,
  commitment, and mempool task snapshot when present.
- Reconcile now imports task state before validation jobs so a remote revealed
  job can be validated without manual SQLite repair.
- Task receive verifies miner commitment signatures before importing committed
  or revealed state.
- Duplicate task gossip is idempotent and returns duplicate success instead of
  noisy conflicts.
- Added a focused test proving a task committed on one node can be imported on
  another node with commitment and snapshot data, then revealed locally.

Operator drill helper:

Run this first to prove A/B/C expose the Phase 12 routes and still agree on
height, tip hash, replay health, and validator quorum:

```bash
python3 deploy/scripts/phase12-task-gossip-drill.py \
  --reconcile \
  --required 3 \
  A=http://159.89.90.163:8000 \
  B=http://68.183.113.210:8000 \
  C=http://159.223.96.125:8000
```

After mining or revealing a specific task, pass the task id to prove the task
state converged across all three peers:

```bash
python3 deploy/scripts/phase12-task-gossip-drill.py \
  --reconcile \
  --required 3 \
  --task-id task_xxx \
  --expect-task-status assigned \
  --expect-task-status committed \
  --expect-task-status revealed \
  --expect-task-status accepted \
  --expect-task-status stale \
  A=http://159.89.90.163:8000 \
  B=http://68.183.113.210:8000 \
  C=http://159.223.96.125:8000
```

The drill fails on replay divergence, tip mismatch, missing validator quorum, a
missing task inventory route, or a task state that does not converge. Commitment
and snapshot presence are warnings because assigned-only tasks legitimately do
not have those records yet.

## Phase 13: Distributed Block Finality And Orphan Handling

Goal: finalized blocks are selected by quorum certificates, explicit chain
branch inheritance, and deterministic conflict rules.

Current finality certificates exist, but automatic reorg/orphan handling is not
complete enough for full decentralization.

Lab observation from Phase 11:

- Candidates B and C both reached states where replay marked the node
  divergent with `finalized block previous_hash does not match local chain tip`.
- In both cases, the operator recovered the node manually by restoring a fresh
  snapshot from candidate A.
- This proves the missing protocol behavior: a finalized block that does not
  connect to the local tip must be stored and resolved through deterministic
  orphan/reorg logic. It must not leave the node permanently divergent and
  dependent on manual snapshot restore.

Implementation work:

- Add `block_branches` metadata or equivalent schema for `branch_id`,
  `parent_hash`, `branch_status`, `ancestor_height`, `ancestor_hash`,
  `inherited_state_root`, and `selected_at`.
- Store every received block as one of: `canonical`, `orphan_pending_parent`,
  `orphan_losing_branch`, `reorg_candidate`, `reorged_out`, or
  `rejected_invalid`.
- Add a deterministic fork-choice rule for same-height or competing descendant
  branches. The rule must use finality certificate validity, certificate height,
  quorum weight, block hash tie-break, and protocol params. It must not use local
  receive time.
- Reject two different certificates at the same height only when they cannot be
  resolved by fork choice; otherwise persist the losing certificate as orphan or
  slashable evidence.
- Quarantine conflicting block payloads and expose orphan status through API,
  CLI, audit, and explorer views.
- Add slashable evidence for validators signing conflicting blocks at the same
  height.
- Add automatic rollback to the latest valid finalized ancestor when a local
  node imports a losing branch.
- Add replay from parent/certificate chain, not only header order.
- Add orphan adoption: if a missing parent arrives later and validates, the node
  reevaluates descendants and adopts the branch only if fork choice selects it.
- Add state inheritance checks so balances, nonces, validator eligibility,
  pending rewards, mempool selected/released status, and pool payout
  transactions derive from the selected parent branch.
- Add branch pruning rules: keep enough orphan and reorg evidence for audit and
  slashing, but prune old non-canonical payloads after a retention window.
- Add migration code that marks all pre-Phase-13 existing mainnet blocks as the
  initial canonical branch without changing balances.

Acceptance gates:

- A node receiving a block before its ancestor queues it without divergence.
- A node receiving two block candidates at one height picks the same winner as
  every peer.
- A losing valid block is marked `orphan_losing_branch` and never applied to
  balances.
- An invalid block is marked `rejected_invalid`, not orphan.
- A block whose parent arrives later moves from `orphan_pending_parent` to either
  canonical, orphan losing branch, or rejected invalid after deterministic
  reevaluation.
- Reorg from branch A to branch B rolls back to the shared ancestor and replays
  branch B with matching state root on every peer.
- Pool payout accounting, wallet nonces, validator rewards, and transaction
  status remain tied to the canonical branch after reorg.
- A divergent test node recovers automatically to the latest finalized ancestor.
- Explorer shows canonical, orphan, rejected, and reorged-out blocks without
  mixing them into the main height history.

## Phase 14: Pool As A True Local Full-Node Miner

Goal: Pool 1 can mine against its own local full node without using
`api.picoin.science` as the task server.

This is the phase that answers the current pool concern directly.

Implementation work:

- Add pool preflight that refuses local-node mode unless Phase 9 readiness is
  true.
- Point pool task, commit, reveal, status, payout transaction submission, and
  reconcile reads to the local full node.
- Let the local full node gossip tasks, reveals, validation jobs, votes, blocks,
  and payouts to peers.
- Keep pool accounting isolated from node chain database.
- Add failover list for local/peer nodes but never silently fall back to
  bootstrap-only mode without logging a warning.

Acceptance gates:

- Pool local node has healthy sync, fresh validator quorum, and write readiness.
- Pool mines a block through `http://127.0.0.1:8000`.
- The block is finalized by validators connected to multiple nodes.
- Bootstrap stopped: pool still gets tasks, validates, finalizes, and gossips a
  block through non-bootstrap peers.
- Pool payouts submit through local node and confirm on the network.

## Phase 15: Bootstrap Demotion

Goal: `api.picoin.science` becomes one ordinary public full node.

Implementation work:

- Replace hardcoded primary bootstrap assumptions in miners, validators,
  desktop apps, pool, explorer, and wallet with peer/failover profiles.
- Add signed peer registry and DNS seed support.
- Add endpoint health scoring for reads and writes.
- Make default clients try multiple compatible peers before declaring network
  unavailable.
- Keep a bootstrap compatibility endpoint for older clients during the migration
  window.

Acceptance gates:

- With `api.picoin.science` stopped, Desktop miner, Linux miner, validators,
  explorer reads, wallet balance checks, and wallet submit all continue through
  other peers.
- A new install receives a peer list from at least two independent sources.
- No production service has `api.picoin.science` as its only configured path.

## Phase 16: Peer Recovery, Reorg Recovery, And Slashing Evidence

Goal: the network survives bad peers, stale peers, and conflicting peers without
manual intervention.

Implementation work:

- Add peer scoring for invalid payloads, wrong identity, stale height, gossip
  spam, and conflicting certificates.
- Add automatic restore from a quorum of compatible peers when one node
  diverges.
- Add evidence export for conflicting validator signatures.
- Add operator commands for quarantine, unquarantine, and forensic reports.
- Add alerts for replay stalls, orphan growth, and quorum degradation.

Acceptance gates:

- A peer serving invalid blocks is quarantined automatically.
- A node with a corrupted replay queue restores from healthy peers without
  manual SQLite edits.
- Conflicting validator signatures are stored as evidence and exposed through an
  audit endpoint.

## Phase 17: Public Operator Network

Goal: independent community nodes can join the network without private
coordination.

Implementation work:

- Publish full-node, validator-node, pool-node, and exchange-node profiles.
- Add signed node identity registration.
- Add public peer inventory with liveness, role, protocol, height, and service
  capability fields.
- Add an operator conformance test suite.
- Add dashboards that show only healthy current peers by default and hide dead
  historical candidates from primary explorer views.

Acceptance gates:

- A new operator can start a full node from public docs and catch up from peers.
- A validator operator can prove stake, heartbeat, receive jobs, vote, and earn
  rewards without connecting only to bootstrap.
- The explorer no longer scares users with zeroed deleted candidates in the
  primary view.

## Phase 18: Bootstrap-Off Mainnet Shadow Drill

Goal: rehearse total decentralization on production-like state before touching
production bootstrap dependency.

Implementation work:

- Build a shadow network from recent mainnet snapshot.
- Run at least three full nodes, three validators, one pool, one miner, one
  explorer, and one exchange hot-wallet smoke.
- Disable the shadow bootstrap endpoint during the drill.
- Mine and finalize multiple blocks while the bootstrap is offline.
- Re-enable bootstrap and verify it catches up from peers.

Acceptance gates:

- At least ten blocks finalize with bootstrap offline.
- Pool local-node mining succeeds.
- Validators on different nodes vote and close jobs.
- Wallet submit and exchange withdrawal smoke confirm through non-bootstrap
  peers.
- Reintroduced bootstrap catches up without manual database edits.

## Phase 19: Controlled Mainnet Cutover

Goal: move production to total decentralization without losing data.

Cutover order:

1. Back up bootstrap, pool, explorer, and validator databases.
2. Update non-authoritative full nodes first.
3. Update independent validators one at a time.
4. Update pool but keep bootstrap fallback disabled until local-node preflight
   passes.
5. Update explorer and wallet failover.
6. Update bootstrap last.
7. Run a planned bootstrap-off window.
8. Keep rollback snapshots until the network has finalized enough blocks under
   the new model.

Acceptance gates:

- Required validator quorum stays online during the rollout.
- Mainnet health remains `status=ok`.
- Replay stays healthy and `divergence_detected=false` everywhere.
- Blocks continue advancing with certificates.
- Pool payouts and wallet nonces remain consistent.
- Bootstrap can be stopped and restarted without stopping block production.

## Required Test Matrix

Each implementation phase must add or update tests in the matching layer:

- Unit tests for deterministic ids, signatures, canonical ordering, fork choice,
  and idempotent imports.
- Branch inheritance tests for parent/child state roots, nonce state, validator
  rewards, pending rewards, and mempool settlement.
- Orphan tests for missing parent, late parent arrival, losing branch,
  invalid-block rejection, and orphan retention/pruning.
- Reorg tests for rollback to shared ancestor, replay of winning branch, and
  wallet/pool/validator accounting after reorg.
- SQLite replay tests for two or more nodes with different local receive order.
- API tests for inventory, receive, status, and duplicate submission endpoints.
- Operational scripts for A/B/C node drills.
- Restart tests proving persistence from disk.
- Bootstrap-off tests proving no hidden dependency remains.
- Mainnet-shadow smoke tests before production rollout.

## Branch Policy

This section is for Git branch policy. Chain branch handling is specified above
in `Required Chain Branch Model` and Phase 13.

- Work continues on `codex/total-decentralization`.
- `main` remains production-stable.
- Each phase must merge forward from `main` before deployment testing.
- Each phase must preserve all prior phase tests.
- A phase is not closed by code merge alone; it closes only after recorded
  operational evidence.

## Immediate Next Engineering Slice

Phase 10 and Phase 11 are now lab-proven on the A/B/C candidate set. Phase 12
has its first implementation slice merged on the branch: task-state inventory,
receive, reconcile import, and a drill helper for A/B/C convergence.

The next natural slice is to close the remaining Phase 12 acceptance gates:

1. Make task identity deterministic across synced write-capable peers.
2. Prove commit on A and reveal/status/finality convergence on B/C.
3. Add deterministic first-valid-reveal conflict handling across nodes.
4. Prove a late miner reveal becomes stale everywhere after another valid block
   wins the round.
5. Keep the current quorum rule fixed: no block certificate is acceptable unless
   it reaches the configured protocol quorum.

This slice directly addresses the remaining lab behavior where a miner can hold
or reuse a pending task while the network progresses. That behavior should be
resolved by protocol task-state gossip, not by manual database cleanup or by
lowering quorum.

## Phase 12 Lab Evidence: Deterministic Candidate Finalization

The A/B/C lab exposed a consensus safety bug before automatic reorg work:
validators could approve the same validation job, but different nodes could
create different block hashes for that job because finalization used each
node's local clock for `timestamp` and `total_block_ms`.

Fix `fa42f21` makes validation-job block finalization deterministic by deriving
the block timestamp from `validation_jobs.job_created_at`. After all three
candidates were restored to the same tip, A mined block `11506`, the job reached
`required_approvals=3`, and A/B/C all converged to the same block hash:

- Height: `11506`
- Hash: `5508b9c077583b932b3de81ed1d68fc43701057b8d30eadb862561205a094a4f`
- Job: `job_79fc45d9bdfb46b0`
- Task: `task_c930b6fec6fd2f95`
- Votes: A/B/C validator quorum, `3/3`
- Replay: healthy on all candidates

## Phase 13 Slice: Orphan/Fork Detection Before Reorg

Goal: detect the losing local branch before implementing destructive reorg.

Implementation work:

- Add a read-only orphan detector that looks for queued certified blocks whose
  `previous_hash` points away from the local parent block.
- Surface local orphan candidates through `/consensus/status` and
  `/consensus/orphans`.
- Include local block hash, remote parent hash, remote parent availability,
  child certificate quorum, and recommended recovery action.

This is intentionally diagnostic only. The next slice will use this detector to
perform bounded canonical reorg from a shared ancestor, with accounting rollback
tests before any mainnet use.

## Phase 13.2 Slice: Bounded Reorg Plan

Goal: make orphan recovery actionable before enabling destructive mutation.

Implementation work:

- Add `/consensus/orphans/reorg-plan`.
- Require the local orphan to be the current local tip.
- Require the remote replacement parent and its certified child to be present.
- Require both remote blocks to carry quorum-met finality certificates.
- Limit the first supported recovery depth to one local block by default.
- Return the exact accounting tables that must be rewound before importing the
  remote canonical branch.

This slice is still dry-run only. It exists so A/B/C candidate nodes can explain
why a pending ancestor chain is safe or unsafe to recover. The apply phase must
add rollback tests for balances, ledger entries, account nonces, mempool
transactions, rewards, finality certificates, and block import before any
automatic reorg is enabled.
