from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from app.models.schemas import (
    AuditSummaryResponse,
    AuditFullResponse,
    BalanceResponse,
    BlockReceiveRequest,
    BlockReceiveResponse,
    BlockResponse,
    BlockSyncResponse,
    CanonicalCheckpointResponse,
    ChainVerificationResponse,
    CheckpointVerificationResponse,
    ConsensusBlockProposalRequest,
    ConsensusProposalResponse,
    ConsensusReplayResponse,
    ConsensusStatusResponse,
    ConsensusVoteResponse,
    ConsensusVoteRequest,
    FaucetRequest,
    FaucetResponse,
    HealthResponse,
    LedgerEntryResponse,
    MaintenanceCleanupResponse,
    MempoolTransactionResponse,
    MinerRegisterRequest,
    MinerResponse,
    NodeEventResponse,
    NodeIdentityResponse,
    NodeStatusResponse,
    NodeSyncStatusResponse,
    PerformanceStatsResponse,
    PeerRegisterRequest,
    PeerReconcileResponse,
    PeerResponse,
    ProtocolParamsResponse,
    ProtocolResponse,
    RetroactiveAuditResponse,
    RetroactiveAuditRunResponse,
    RetargetEventResponse,
    RetargetPreviewResponse,
    RetargetRunResponse,
    RetargetStatusResponse,
    ScienceCreateJobRequest,
    ScienceEventResponse,
    ScienceJobAcceptRequest,
    ScienceJobResponse,
    ScienceJobTransitionRequest,
    ScienceReserveGovernanceRequest,
    ScienceReserveGovernanceResponse,
    ScienceRewardReserveResponse,
    ScienceStakeAccountResponse,
    ScienceStakeRequest,
    ScientificDevelopmentTreasuryResponse,
    SignedTransactionRequest,
    SnapshotImportRequest,
    SnapshotImportResponse,
    StatsResponse,
    TaskCommitRequest,
    TaskCommitResponse,
    TreasuryClaimRequest,
    TaskRevealRequest,
    TaskResponse,
    TaskSubmitRequest,
    TaskSubmitResponse,
    ValidationJobResponse,
    ValidationResultRequest,
    ValidationResultResponse,
    ValidatorRegisterRequest,
    ValidatorResponse,
    WalletCreateRequest,
    WalletCreateResponse,
)
from app.services.consensus import (
    ConsensusError,
    consensus_status,
    finalize_proposal,
    get_block_proposal,
    list_consensus_votes,
    list_block_proposals,
    propose_block,
    replay_finalized_blocks,
    vote_on_proposal,
)
from app.services.network import (
    NetworkError,
    get_blocks_since,
    get_sync_status,
    heartbeat_peer,
    list_mempool,
    list_peers,
    node_identity,
    receive_block_header,
    reconcile_connected_peers,
    reconcile_peer,
    register_peer,
    gossip_json,
    submit_transaction,
)
from app.services.treasury import (
    TreasuryError,
    claim_scientific_development_treasury,
    get_scientific_development_treasury,
)
from app.services.state import (
    StateError,
    activate_imported_snapshot,
    apply_imported_snapshot_state,
    create_canonical_checkpoint,
    export_canonical_snapshot,
    get_checkpoint,
    import_canonical_snapshot,
    latest_checkpoint,
    list_imported_snapshots,
    list_checkpoints,
    validate_snapshot_document,
    verify_checkpoint,
)
from app.services.wallet import create_wallet
from app.services.science import (
    ScienceError,
    approve_science_reserve_activation,
    create_science_job,
    execute_science_reserve_activation,
    get_science_account,
    get_science_events,
    get_science_job,
    get_science_reserve,
    get_science_reserve_governance,
    list_science_accounts,
    list_science_jobs,
    pay_science_worker,
    pause_science_reserve,
    propose_science_reserve_activation,
    stake_science_access,
    transition_science_job,
    unpause_science_reserve,
    unstake_science_access,
)
from app.services.mining import (
    MiningError,
    cleanup_expired_tasks,
    commit_task,
    create_next_task,
    get_audit_summary,
    get_full_economic_audit,
    get_health_status,
    get_balance,
    get_balances,
    get_block,
    get_blocks,
    get_ledger_entries,
    get_miner,
    get_node_status,
    get_performance_stats,
    get_protocol,
    get_protocol_history,
    get_difficulty_status,
    get_retarget_history,
    get_recent_events,
    get_retroactive_audits,
    get_stats,
    get_validation_job,
    get_validator,
    get_validators,
    register_miner,
    register_validator,
    preview_retarget,
    request_faucet,
    reveal_task,
    run_retarget,
    run_retroactive_audit,
    submit_validation_result,
    submit_task,
    verify_chain,
)


router = APIRouter()


def _science_error(exc: ScienceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _treasury_error(exc: TreasuryError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _network_error(exc: NetworkError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _consensus_error(exc: ConsensusError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _state_error(exc: StateError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _gossip_block_from_response(response: dict) -> None:
    block = response.get("block") if isinstance(response, dict) else None
    if not block:
        return
    gossip_json(
        "/consensus/proposals?gossip=false",
        {"block": block, "proposer_node_id": block.get("miner_id", "local-miner")},
        "mined_block_proposal_gossip",
    )


@router.get("/health", response_model=HealthResponse)
def health() -> dict:
    return get_health_status()


@router.get("/node/status", response_model=NodeStatusResponse)
def node_status() -> dict:
    return get_node_status()


@router.get("/node/identity", response_model=NodeIdentityResponse)
def node_identity_route() -> dict:
    return node_identity()


@router.get("/node/peers", response_model=list[PeerResponse])
def node_peers(include_stale: bool = Query(True)) -> list[dict]:
    return list_peers(include_stale)


@router.post("/node/peers/register", response_model=PeerResponse, status_code=201)
def node_register_peer(payload: PeerRegisterRequest) -> dict:
    try:
        return register_peer(
            node_id=payload.node_id,
            peer_address=payload.peer_address,
            peer_type=payload.peer_type,
            protocol_version=payload.protocol_version,
            network_id=payload.network_id,
            chain_id=payload.chain_id,
            genesis_hash=payload.genesis_hash,
            metadata=payload.metadata,
        )
    except NetworkError as exc:
        raise _network_error(exc) from exc


@router.post("/node/peers/{peer_id}/heartbeat", response_model=PeerResponse)
def node_peer_heartbeat(peer_id: str) -> dict:
    try:
        return heartbeat_peer(peer_id)
    except NetworkError as exc:
        raise _network_error(exc) from exc


@router.get("/node/sync-status", response_model=NodeSyncStatusResponse)
def node_sync_status() -> dict:
    return get_sync_status()


@router.get("/node/sync/blocks", response_model=BlockSyncResponse)
def node_sync_blocks(
    from_height: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    try:
        return get_blocks_since(from_height, limit)
    except NetworkError as exc:
        raise _network_error(exc) from exc


@router.get("/node/checkpoints", response_model=list[CanonicalCheckpointResponse])
def node_checkpoints(limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    return list_checkpoints(limit)


@router.get("/node/checkpoints/latest", response_model=CanonicalCheckpointResponse | None)
def node_latest_checkpoint() -> dict | None:
    return latest_checkpoint()


@router.post("/node/checkpoints", response_model=CanonicalCheckpointResponse, status_code=201)
def node_create_checkpoint(
    height: int | None = Query(None, ge=1),
    trusted: bool = Query(True),
    source: str = Query("manual", min_length=1, max_length=80),
) -> dict:
    try:
        return create_canonical_checkpoint(height=height, trusted=trusted, source=source)
    except StateError as exc:
        raise _state_error(exc) from exc


@router.get("/node/checkpoints/{height}", response_model=CanonicalCheckpointResponse)
def node_checkpoint(height: int) -> dict:
    checkpoint = get_checkpoint(height)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail="checkpoint not found")
    return checkpoint


@router.post("/node/checkpoints/{height}/verify", response_model=CheckpointVerificationResponse)
def node_verify_checkpoint(height: int) -> dict:
    try:
        return verify_checkpoint(height)
    except StateError as exc:
        raise _state_error(exc) from exc


@router.get("/node/snapshots/export")
def node_export_snapshot(height: int | None = Query(None, ge=1)) -> dict:
    try:
        return export_canonical_snapshot(height)
    except StateError as exc:
        raise _state_error(exc) from exc


@router.post("/node/snapshots/validate")
def node_validate_snapshot(payload: SnapshotImportRequest) -> dict:
    return validate_snapshot_document(payload.snapshot)


@router.post("/node/snapshots/import", response_model=SnapshotImportResponse)
def node_import_snapshot(payload: SnapshotImportRequest) -> dict:
    try:
        return import_canonical_snapshot(payload.snapshot, source=payload.source)
    except StateError as exc:
        raise _state_error(exc) from exc


@router.get("/node/snapshots/imports")
def node_imported_snapshots(limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    return list_imported_snapshots(limit)


@router.post("/node/snapshots/{snapshot_hash}/activate")
def node_activate_snapshot(snapshot_hash: str) -> dict:
    try:
        return activate_imported_snapshot(snapshot_hash)
    except StateError as exc:
        raise _state_error(exc) from exc


@router.post("/node/snapshots/{snapshot_hash}/apply")
def node_apply_snapshot(snapshot_hash: str) -> dict:
    try:
        return apply_imported_snapshot_state(snapshot_hash)
    except StateError as exc:
        raise _state_error(exc) from exc


@router.post("/node/blocks/receive", response_model=BlockReceiveResponse)
def node_receive_block(payload: BlockReceiveRequest) -> dict:
    try:
        return receive_block_header(payload.block, payload.source_peer_id)
    except NetworkError as exc:
        raise _network_error(exc) from exc


@router.post("/node/reconcile", response_model=PeerReconcileResponse)
def node_reconcile(
    peer_address: str | None = Query(None),
    limit: int = Query(16, ge=1, le=100),
) -> dict:
    if peer_address:
        result = reconcile_peer(peer_address)
        return {
            "attempted": 1,
            "transactions_imported": result["transactions_imported"],
            "proposals_imported": result["proposals_imported"],
            "blocks_imported": result["blocks_imported"],
            "peers_seen": result["peers_seen"],
            "errors": len(result["errors"]),
            "results": [result],
        }
    return reconcile_connected_peers(limit)


@router.get("/consensus/status", response_model=ConsensusStatusResponse)
def consensus_status_route() -> dict:
    return consensus_status()


@router.get("/consensus/proposals", response_model=list[ConsensusProposalResponse])
def consensus_proposals(
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict]:
    return list_block_proposals(status, limit)


@router.get("/consensus/proposals/{proposal_id}", response_model=ConsensusProposalResponse)
def consensus_proposal(proposal_id: str) -> dict:
    proposal = get_block_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="consensus proposal not found")
    return proposal


@router.get("/consensus/proposals/{proposal_id}/votes", response_model=list[ConsensusVoteResponse])
def consensus_votes(proposal_id: str) -> list[dict]:
    if get_block_proposal(proposal_id) is None:
        raise HTTPException(status_code=404, detail="consensus proposal not found")
    return list_consensus_votes(proposal_id)


@router.post("/consensus/proposals", response_model=ConsensusProposalResponse, status_code=201)
def consensus_propose(payload: ConsensusBlockProposalRequest, gossip: bool = Query(True)) -> dict:
    try:
        return propose_block(payload.block, payload.proposer_node_id, gossip=gossip)
    except ConsensusError as exc:
        raise _consensus_error(exc) from exc


@router.post("/consensus/proposals/{proposal_id}/vote", response_model=ConsensusProposalResponse)
def consensus_vote(proposal_id: str, payload: ConsensusVoteRequest, gossip: bool = Query(True)) -> dict:
    try:
        return vote_on_proposal(
            proposal_id,
            payload.validator_id,
            payload.approved,
            payload.reason,
            payload.signature,
            payload.signed_at.isoformat(),
            gossip=gossip,
        )
    except ConsensusError as exc:
        raise _consensus_error(exc) from exc


@router.post("/consensus/proposals/{proposal_id}/finalize", response_model=ConsensusProposalResponse)
def consensus_finalize(proposal_id: str, gossip: bool = Query(True)) -> dict:
    try:
        proposal = finalize_proposal(proposal_id)
        if gossip:
            gossip_json(
                f"/consensus/proposals/{proposal_id}/finalize?gossip=false",
                {},
                "consensus_finalization_gossip",
            )
        return proposal
    except ConsensusError as exc:
        raise _consensus_error(exc) from exc


@router.post("/consensus/replay", response_model=ConsensusReplayResponse)
def consensus_replay(limit: int = Query(100, ge=1, le=500)) -> dict:
    try:
        return replay_finalized_blocks(limit)
    except ConsensusError as exc:
        raise _consensus_error(exc) from exc


@router.get("/mempool", response_model=list[MempoolTransactionResponse])
def mempool(status: str | None = Query(None), limit: int = Query(100, ge=1, le=500)) -> list[dict]:
    return list_mempool(status, limit)


@router.post("/tx/submit", response_model=MempoolTransactionResponse, status_code=201)
def tx_submit(payload: SignedTransactionRequest) -> dict:
    try:
        return submit_transaction(payload.model_dump(mode="json"), propagated=False)
    except NetworkError as exc:
        raise _network_error(exc) from exc


@router.post("/tx/receive", response_model=MempoolTransactionResponse, status_code=201)
def tx_receive(payload: SignedTransactionRequest) -> dict:
    try:
        return submit_transaction(payload.model_dump(mode="json"), propagated=True)
    except NetworkError as exc:
        raise _network_error(exc) from exc


@router.post("/wallet/create", response_model=WalletCreateResponse, status_code=201)
def wallet_create(payload: WalletCreateRequest) -> dict:
    return create_wallet(payload.name)


@router.websocket("/p2p/ws")
async def p2p_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        await websocket.send_json({"type": "hello", "node": node_identity()})
        while True:
            message = await websocket.receive_json()
            message_type = message.get("type")
            if message_type == "ping":
                await websocket.send_json({"type": "pong", "node": node_identity()})
            elif message_type == "sync_status":
                await websocket.send_json({"type": "sync_status", "payload": get_sync_status()})
            elif message_type == "tx":
                tx = submit_transaction(message.get("payload") or {}, propagated=True)
                await websocket.send_json({"type": "tx_ack", "payload": tx})
            elif message_type == "block":
                block = receive_block_header(message.get("payload") or {}, message.get("source_peer_id"))
                await websocket.send_json({"type": "block_ack", "payload": block})
            elif message_type == "block_proposal":
                proposal = propose_block(
                    message.get("payload") or {},
                    message.get("proposer_node_id") or "p2p-peer",
                    gossip=False,
                )
                await websocket.send_json({"type": "block_proposal_ack", "payload": proposal})
            else:
                await websocket.send_json({"type": "error", "detail": "unsupported p2p message type"})
    except WebSocketDisconnect:
        return
    except NetworkError as exc:
        await websocket.send_json({"type": "error", "detail": exc.detail, "status_code": exc.status_code})
        await websocket.close(code=1008)
    except ConsensusError as exc:
        await websocket.send_json({"type": "error", "detail": exc.detail, "status_code": exc.status_code})
        await websocket.close(code=1008)


@router.get("/events", response_model=list[NodeEventResponse])
def recent_events(limit: int = Query(30, ge=1, le=100)) -> list[dict]:
    return get_recent_events(limit)


@router.post("/science/stake", response_model=ScienceStakeAccountResponse, status_code=201)
def science_stake(payload: ScienceStakeRequest) -> dict:
    try:
        return stake_science_access(payload.address, payload.amount)
    except ScienceError as exc:
        raise _science_error(exc) from exc


@router.post("/science/unstake", response_model=ScienceStakeAccountResponse)
def science_unstake(address: str = Query(..., min_length=1)) -> dict:
    try:
        return unstake_science_access(address)
    except ScienceError as exc:
        raise _science_error(exc) from exc


@router.get("/science/accounts", response_model=list[ScienceStakeAccountResponse])
def science_accounts(limit: int = Query(100, ge=1, le=500)) -> list[dict]:
    return list_science_accounts(limit)


@router.get("/science/accounts/{address}", response_model=ScienceStakeAccountResponse)
def science_account(address: str) -> dict:
    account = get_science_account(address)
    if account is None:
        raise HTTPException(status_code=404, detail="science stake account not found")
    return account


@router.post("/science/jobs", response_model=ScienceJobResponse, status_code=201)
def science_create_job(payload: ScienceCreateJobRequest) -> dict:
    try:
        return create_science_job(
            payload.requester_address,
            payload.job_type,
            payload.metadata_hash,
            payload.storage_pointer,
            payload.reward_budget,
            payload.max_compute_units,
            payload.reward_per_compute_unit,
            payload.max_reward,
        )
    except ScienceError as exc:
        raise _science_error(exc) from exc


@router.get("/science/jobs", response_model=list[ScienceJobResponse])
def science_jobs(
    address: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict]:
    return list_science_jobs(address, limit)


@router.get("/science/jobs/{job_id}", response_model=ScienceJobResponse)
def science_job(job_id: str) -> dict:
    job = get_science_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="science job not found")
    return job


@router.post("/science/jobs/{job_id}/transition", response_model=ScienceJobResponse)
def science_transition_job(job_id: str, payload: ScienceJobTransitionRequest) -> dict:
    try:
        return transition_science_job(
            job_id,
            payload.status,
            payload.worker_address,
            payload.result_hash,
            payload.proof_hash,
            payload.compute_units_used,
        )
    except ScienceError as exc:
        raise _science_error(exc) from exc


@router.post("/science/jobs/{job_id}/accept", response_model=ScienceJobResponse)
def science_accept_job(job_id: str, payload: ScienceJobAcceptRequest) -> dict:
    try:
        return transition_science_job(
            job_id,
            "accepted",
            payload.worker_address,
            payload.result_hash,
            payload.proof_hash,
            payload.compute_units_used,
        )
    except ScienceError as exc:
        raise _science_error(exc) from exc


@router.post("/science/jobs/{job_id}/pay", response_model=ScienceJobResponse)
def science_pay_job(job_id: str) -> dict:
    try:
        return pay_science_worker(job_id)
    except ScienceError as exc:
        raise _science_error(exc) from exc


@router.get("/science/reserve", response_model=ScienceRewardReserveResponse)
def science_reserve(epoch: str | None = Query(None)) -> dict:
    return get_science_reserve(epoch)


@router.get("/reserve/status", response_model=ScienceRewardReserveResponse)
def reserve_status(epoch: str | None = Query(None)) -> dict:
    return get_science_reserve(epoch)


@router.post("/reserve/pause", response_model=ScienceReserveGovernanceResponse)
def reserve_pause(payload: ScienceReserveGovernanceRequest) -> dict:
    try:
        return pause_science_reserve(payload.signer)
    except ScienceError as exc:
        raise _science_error(exc) from exc


@router.post("/reserve/unpause", response_model=ScienceReserveGovernanceResponse)
def reserve_unpause(payload: ScienceReserveGovernanceRequest) -> dict:
    try:
        return unpause_science_reserve(payload.signer)
    except ScienceError as exc:
        raise _science_error(exc) from exc


@router.get("/science/reserve/governance", response_model=ScienceReserveGovernanceResponse)
def science_reserve_governance() -> dict:
    return get_science_reserve_governance()


@router.post("/science/reserve/governance/propose-activation", response_model=ScienceReserveGovernanceResponse)
def science_reserve_propose_activation(payload: ScienceReserveGovernanceRequest) -> dict:
    try:
        return propose_science_reserve_activation(payload.signer)
    except ScienceError as exc:
        raise _science_error(exc) from exc


@router.post("/science/reserve/governance/approve-activation", response_model=ScienceReserveGovernanceResponse)
def science_reserve_approve_activation(payload: ScienceReserveGovernanceRequest) -> dict:
    try:
        return approve_science_reserve_activation(payload.signer)
    except ScienceError as exc:
        raise _science_error(exc) from exc


@router.post("/science/reserve/governance/execute-activation", response_model=ScienceReserveGovernanceResponse)
def science_reserve_execute_activation() -> dict:
    try:
        return execute_science_reserve_activation()
    except ScienceError as exc:
        raise _science_error(exc) from exc


@router.get("/science/events", response_model=list[ScienceEventResponse])
def science_events(limit: int = Query(30, ge=1, le=100)) -> list[dict]:
    return get_science_events(limit)


@router.get("/treasury/status", response_model=ScientificDevelopmentTreasuryResponse)
def treasury_status() -> dict:
    return get_scientific_development_treasury()


@router.post("/treasury/claim", response_model=ScientificDevelopmentTreasuryResponse)
def treasury_claim(payload: TreasuryClaimRequest | None = None) -> dict:
    payload = payload or TreasuryClaimRequest()
    try:
        return claim_scientific_development_treasury(payload.requested_by, payload.claim_to)
    except TreasuryError as exc:
        raise _treasury_error(exc) from exc


@router.get("/audit/retroactive", response_model=list[RetroactiveAuditResponse])
def retroactive_audits(limit: int = Query(20, ge=1, le=100)) -> list[dict]:
    return get_retroactive_audits(limit)


@router.post("/audit/retroactive/run", response_model=RetroactiveAuditRunResponse)
def retroactive_audit_run(
    block_height: int | None = Query(None, ge=1),
    sample_multiplier: int = Query(2, ge=1, le=8),
) -> dict:
    try:
        return run_retroactive_audit(block_height, sample_multiplier)
    except MiningError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/miners/register", response_model=MinerResponse, status_code=201)
def register_miner_endpoint(payload: MinerRegisterRequest) -> dict:
    try:
        return register_miner(payload.name, payload.public_key)
    except MiningError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/validators/register", response_model=ValidatorResponse, status_code=201)
def register_validator_endpoint(payload: ValidatorRegisterRequest) -> dict:
    try:
        return register_validator(payload.name, payload.public_key)
    except MiningError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/validators", response_model=list[ValidatorResponse])
def validators(limit: int = Query(100, ge=1, le=500), eligible_only: bool = Query(False)) -> list[dict]:
    return get_validators(limit, eligible_only)


@router.get("/validators/{validator_id}", response_model=ValidatorResponse)
def validator_by_id(validator_id: str) -> dict:
    validator = get_validator(validator_id)
    if validator is None:
        raise HTTPException(status_code=404, detail="validator not found")
    return validator


@router.post("/faucet", response_model=FaucetResponse)
def faucet(payload: FaucetRequest) -> dict:
    try:
        return request_faucet(payload.account_id, payload.account_type, payload.amount)
    except MiningError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/tasks/next", response_model=TaskResponse)
def next_task(miner_id: str = Query(..., min_length=1)) -> dict:
    try:
        task = create_next_task(miner_id)
    except MiningError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    if task is None:
        raise HTTPException(status_code=404, detail="miner not found")
    return task


@router.post("/tasks/submit", response_model=TaskSubmitResponse)
def submit_task_endpoint(payload: TaskSubmitRequest) -> dict:
    response = submit_task(
        task_id=payload.task_id,
        miner_id=payload.miner_id,
        result_hash=payload.result_hash,
        segment=payload.segment,
        signature=payload.signature,
        signed_at=payload.signed_at.isoformat(),
    )
    _gossip_block_from_response(response)
    return response


@router.post("/tasks/commit", response_model=TaskCommitResponse)
def commit_task_endpoint(payload: TaskCommitRequest) -> dict:
    return commit_task(
        task_id=payload.task_id,
        miner_id=payload.miner_id,
        result_hash=payload.result_hash,
        merkle_root=payload.merkle_root,
        signature=payload.signature,
        signed_at=payload.signed_at.isoformat(),
        compute_ms=payload.compute_ms,
    )


@router.post("/tasks/reveal", response_model=TaskSubmitResponse)
def reveal_task_endpoint(payload: TaskRevealRequest) -> dict:
    response = reveal_task(
        task_id=payload.task_id,
        miner_id=payload.miner_id,
        revealed_samples=[sample.model_dump() for sample in payload.samples],
        signature=payload.signature,
        signed_at=payload.signed_at.isoformat(),
    )
    _gossip_block_from_response(response)
    return response


@router.get("/validation/jobs", response_model=ValidationJobResponse | None)
def validation_job(validator_id: str = Query(..., min_length=1)) -> dict | None:
    try:
        return get_validation_job(validator_id)
    except MiningError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/validation/results", response_model=ValidationResultResponse)
def validation_result(payload: ValidationResultRequest) -> dict:
    try:
        response = submit_validation_result(
            job_id=payload.job_id,
            validator_id=payload.validator_id,
            approved=payload.approved,
            reason=payload.reason,
            signature=payload.signature,
            signed_at=payload.signed_at.isoformat(),
        )
        _gossip_block_from_response(response)
        return response
    except MiningError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/blocks", response_model=list[BlockResponse])
def blocks() -> list[dict]:
    return get_blocks()


@router.get("/blocks/verify", response_model=ChainVerificationResponse)
def verify_blocks() -> dict:
    return verify_chain()


@router.get("/blocks/{height}", response_model=BlockResponse)
def block_by_height(height: int) -> dict:
    block = get_block(height)
    if block is None:
        raise HTTPException(status_code=404, detail="block not found")
    return block


@router.get("/miners/{miner_id}", response_model=MinerResponse)
def miner_by_id(miner_id: str) -> dict:
    miner = get_miner(miner_id)
    if miner is None:
        raise HTTPException(status_code=404, detail="miner not found")
    return miner


@router.get("/balances", response_model=list[BalanceResponse])
def balances(limit: int = Query(100, ge=1, le=500)) -> list[dict]:
    return get_balances(limit)


@router.get("/balances/{account_id}", response_model=BalanceResponse)
def balance_by_account(account_id: str) -> dict:
    balance = get_balance(account_id)
    if balance is None:
        raise HTTPException(status_code=404, detail="balance not found")
    return balance


@router.get("/ledger", response_model=list[LedgerEntryResponse])
def ledger(account_id: str | None = Query(None), limit: int = Query(100, ge=1, le=500)) -> list[dict]:
    return get_ledger_entries(account_id, limit)


@router.get("/audit/summary", response_model=AuditSummaryResponse)
def audit_summary() -> dict:
    return get_audit_summary()


@router.get("/audit/full", response_model=AuditFullResponse)
def audit_full() -> dict:
    return get_full_economic_audit()


@router.post("/maintenance/expire-tasks", response_model=MaintenanceCleanupResponse)
def maintenance_expire_tasks() -> dict:
    return cleanup_expired_tasks()


@router.get("/stats", response_model=StatsResponse)
def stats() -> dict:
    return get_stats()


@router.get("/stats/performance", response_model=PerformanceStatsResponse)
def performance_stats() -> dict:
    return get_performance_stats()


@router.get("/protocol", response_model=ProtocolResponse)
def protocol() -> dict:
    return get_protocol()


@router.get("/protocol/history", response_model=list[ProtocolParamsResponse])
def protocol_history() -> list[dict]:
    return get_protocol_history()


@router.get("/difficulty", response_model=RetargetStatusResponse)
def difficulty_status() -> dict:
    return get_difficulty_status()


@router.get("/difficulty/history", response_model=list[RetargetEventResponse])
def difficulty_history(limit: int = Query(20, ge=1, le=100)) -> list[dict]:
    return get_retarget_history(limit)


@router.get("/difficulty/preview", response_model=RetargetPreviewResponse)
def difficulty_preview(force: bool = Query(False)) -> dict:
    return preview_retarget(force)


@router.post("/difficulty/retarget", response_model=RetargetRunResponse)
def retarget_difficulty(force: bool = Query(False)) -> dict:
    return run_retarget(force)
