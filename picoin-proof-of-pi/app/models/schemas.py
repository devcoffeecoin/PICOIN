from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MinerRegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    public_key: str | None = Field(default=None, max_length=256)


class MinerResponse(BaseModel):
    miner_id: str
    name: str
    public_key: str | None
    registered_at: datetime
    trust_score: float = 1.0
    cooldown_until: datetime | None = None
    is_banned: bool = False
    accepted_blocks: int = 0
    rejected_submissions: int = 0
    total_rewards: float = 0.0


class TaskResponse(BaseModel):
    task_id: str
    miner_id: str
    range_start: int
    range_end: int
    algorithm: str
    status: str
    created_at: datetime
    expires_at: datetime | None = None


class TaskSubmitRequest(BaseModel):
    task_id: str
    miner_id: str
    result_hash: str = Field(..., min_length=64, max_length=64)
    segment: str = Field(..., min_length=1)
    signature: str | None = Field(default=None, max_length=256)


class TaskSubmitResponse(BaseModel):
    accepted: bool
    status: str
    message: str
    block: "BlockResponse | None" = None
    validation: dict[str, Any]


class BlockResponse(BaseModel):
    height: int
    previous_hash: str
    miner_id: str
    range_start: int
    range_end: int
    algorithm: str
    result_hash: str
    samples: list[dict[str, Any]]
    timestamp: datetime
    block_hash: str
    reward: float


class StatsResponse(BaseModel):
    miners: int
    tasks: int
    pending_tasks: int
    expired_tasks: int
    accepted_blocks: int
    rejected_submissions: int
    total_rewards: float
    latest_block_hash: str


class ProtocolResponse(BaseModel):
    project: str
    protocol_version: str
    algorithm: str
    validation_mode: str
    segment_size: int
    sample_count: int
    task_expiration_seconds: int
    max_active_tasks_per_miner: int
    reward_per_block: float
    penalty_invalid_result: int
    penalty_duplicate: int
    penalty_invalid_signature: int
    cooldown_after_rejections: int
    cooldown_seconds: int


class ChainVerificationIssue(BaseModel):
    height: int | None = None
    reason: str


class ChainVerificationResponse(BaseModel):
    valid: bool
    checked_blocks: int
    latest_block_hash: str
    issues: list[ChainVerificationIssue]


TaskSubmitResponse.model_rebuild()
