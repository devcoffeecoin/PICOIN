from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import (
    AuditSummaryResponse,
    AuditFullResponse,
    BalanceResponse,
    BlockResponse,
    ChainVerificationResponse,
    FaucetRequest,
    FaucetResponse,
    HealthResponse,
    LedgerEntryResponse,
    MaintenanceCleanupResponse,
    MinerRegisterRequest,
    MinerResponse,
    NodeEventResponse,
    NodeStatusResponse,
    PerformanceStatsResponse,
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
)
from app.services.treasury import (
    TreasuryError,
    claim_scientific_development_treasury,
    get_scientific_development_treasury,
)
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


@router.get("/health", response_model=HealthResponse)
def health() -> dict:
    return get_health_status()


@router.get("/node/status", response_model=NodeStatusResponse)
def node_status() -> dict:
    return get_node_status()


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
    return submit_task(
        task_id=payload.task_id,
        miner_id=payload.miner_id,
        result_hash=payload.result_hash,
        segment=payload.segment,
        signature=payload.signature,
        signed_at=payload.signed_at.isoformat(),
    )


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
    return reveal_task(
        task_id=payload.task_id,
        miner_id=payload.miner_id,
        revealed_samples=[sample.model_dump() for sample in payload.samples],
        signature=payload.signature,
        signed_at=payload.signed_at.isoformat(),
    )


@router.get("/validation/jobs", response_model=ValidationJobResponse | None)
def validation_job(validator_id: str = Query(..., min_length=1)) -> dict | None:
    try:
        return get_validation_job(validator_id)
    except MiningError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/validation/results", response_model=ValidationResultResponse)
def validation_result(payload: ValidationResultRequest) -> dict:
    try:
        return submit_validation_result(
            job_id=payload.job_id,
            validator_id=payload.validator_id,
            approved=payload.approved,
            reason=payload.reason,
            signature=payload.signature,
            signed_at=payload.signed_at.isoformat(),
        )
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
