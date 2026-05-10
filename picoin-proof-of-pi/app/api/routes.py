from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import (
    BlockResponse,
    ChainVerificationResponse,
    MinerRegisterRequest,
    MinerResponse,
    PerformanceStatsResponse,
    ProtocolParamsResponse,
    ProtocolResponse,
    StatsResponse,
    TaskCommitRequest,
    TaskCommitResponse,
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
from app.services.mining import (
    MiningError,
    commit_task,
    create_next_task,
    get_block,
    get_blocks,
    get_miner,
    get_performance_stats,
    get_protocol,
    get_protocol_history,
    get_stats,
    get_validation_job,
    get_validator,
    register_miner,
    register_validator,
    reveal_task,
    submit_validation_result,
    submit_task,
    verify_chain,
)


router = APIRouter()


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


@router.get("/validators/{validator_id}", response_model=ValidatorResponse)
def validator_by_id(validator_id: str) -> dict:
    validator = get_validator(validator_id)
    if validator is None:
        raise HTTPException(status_code=404, detail="validator not found")
    return validator


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
