from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MinerRegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    public_key: str = Field(..., min_length=1, max_length=256)


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
    assignment_seed: str | None = None
    assignment_mode: str | None = None
    created_at: datetime
    expires_at: datetime | None = None


class TaskSubmitRequest(BaseModel):
    task_id: str
    miner_id: str
    result_hash: str = Field(..., min_length=64, max_length=64)
    segment: str = Field(..., min_length=1)
    signature: str = Field(..., min_length=1, max_length=256)
    signed_at: datetime


class TaskCommitRequest(BaseModel):
    task_id: str
    miner_id: str
    result_hash: str = Field(..., min_length=64, max_length=64)
    merkle_root: str = Field(..., min_length=64, max_length=64)
    signature: str = Field(..., min_length=1, max_length=256)
    signed_at: datetime


class TaskCommitResponse(BaseModel):
    accepted: bool
    status: str
    message: str
    challenge_seed: str | None = None
    samples: list[dict[str, Any]] = []


class MerkleProofNode(BaseModel):
    side: str
    hash: str = Field(..., min_length=64, max_length=64)


class SampleReveal(BaseModel):
    position: int
    digit: str = Field(..., min_length=1, max_length=1)
    proof: list[MerkleProofNode]


class TaskRevealRequest(BaseModel):
    task_id: str
    miner_id: str
    samples: list[SampleReveal]
    signature: str = Field(..., min_length=1, max_length=256)
    signed_at: datetime


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
    merkle_root: str | None = None
    samples: list[dict[str, Any]]
    timestamp: datetime
    block_hash: str
    reward: float
    protocol_version: str | None = None
    validation_mode: str | None = None


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
    range_assignment_mode: str
    max_pi_position: int
    range_assignment_max_attempts: int
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
