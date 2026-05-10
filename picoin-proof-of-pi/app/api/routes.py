from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import (
    BlockResponse,
    MinerRegisterRequest,
    MinerResponse,
    StatsResponse,
    TaskResponse,
    TaskSubmitRequest,
    TaskSubmitResponse,
)
from app.services.mining import create_next_task, get_block, get_blocks, get_miner, get_stats, register_miner, submit_task


router = APIRouter()


@router.post("/miners/register", response_model=MinerResponse, status_code=201)
def register_miner_endpoint(payload: MinerRegisterRequest) -> dict:
    return register_miner(payload.name, payload.public_key)


@router.get("/tasks/next", response_model=TaskResponse)
def next_task(miner_id: str = Query(..., min_length=1)) -> dict:
    task = create_next_task(miner_id)
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
    )


@router.get("/blocks", response_model=list[BlockResponse])
def blocks() -> list[dict]:
    return get_blocks()


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
