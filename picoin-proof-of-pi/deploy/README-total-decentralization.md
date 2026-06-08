# Picoin Total Decentralization Roadmap

This document starts the post-Phase-8 decentralization track on branch
`codex/total-decentralization-roadmap`.

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

Remaining before closing Phase 10:

- Run the three-node drill where validators heartbeat to different nodes and
  converge through peer reconcile.
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

## Phase 11: Distributed Validation Job Discovery

Goal: validators can discover and vote on jobs created by any full node.

Today, a miner can reveal to a node, but validators must be able to find that
job across the network. Otherwise the local node creates pending jobs that never
close.

Implementation work:

- Make validation jobs signed, canonical, and gossipable.
- Add `/validation/jobs/inventory` and `/validation/jobs/receive`.
- Add deterministic job id rules for the same task reveal.
- Allow validators to poll their configured node and receive jobs created on
  peer nodes.
- Gossip validator votes and make vote import idempotent.
- Preserve vote payloads needed for finality certificate construction.

Acceptance gates:

- Miner reveals to node A.
- Validators connected to nodes A, B, and C all discover the same job.
- Votes submitted to B and C reach A.
- All nodes converge on the same job status and vote set.
- Duplicate job/vote gossip returns idempotent success without noisy errors.

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

## Phase 13: Distributed Block Finality And Orphan Handling

Goal: finalized blocks are selected by quorum certificates, explicit chain
branch inheritance, and deterministic conflict rules.

Current finality certificates exist, but automatic reorg/orphan handling is not
complete enough for full decentralization.

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

- Work continues on `codex/total-decentralization-roadmap`.
- `main` remains production-stable.
- Each phase must merge forward from `main` before deployment testing.
- Each phase must preserve all prior phase tests.
- A phase is not closed by code merge alone; it closes only after recorded
  operational evidence.

## Immediate Next Engineering Slice

Start with Phase 9 and Phase 10 together:

1. Add explicit write-readiness fields so operators can see why a full node
   cannot mine.
2. Add signed validator heartbeat gossip so a full node can build local
   validator quorum from the network.
3. Add a pool local-node preflight that refuses unsafe local mining until quorum
   is visible.

That slice directly addresses the current failure: Pool 1 had a synced full
node, but the node could not safely mine because it had no local validator
quorum.
