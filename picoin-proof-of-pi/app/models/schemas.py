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
    balance: float = 0.0


class ValidatorRegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    public_key: str = Field(..., min_length=1, max_length=256)


class FaucetRequest(BaseModel):
    account_id: str = Field(..., min_length=1, max_length=128)
    account_type: str = Field("miner", pattern="^(miner|validator)$")
    amount: float | None = Field(default=None, gt=0)


class FaucetResponse(BaseModel):
    account_id: str
    account_type: str
    amount: float
    balance: float
    genesis_balance: float
    message: str


class ValidatorResponse(BaseModel):
    validator_id: str
    name: str
    public_key: str
    registered_at: datetime
    accepted_jobs: int = 0
    rejected_jobs: int = 0
    completed_jobs: int = 0
    invalid_results: int = 0
    trust_score: float = 1.0
    cooldown_until: datetime | None = None
    last_seen_at: datetime | None = None
    avg_validation_ms: float = 0.0
    stake_locked: float = 0.0
    slashed_amount: float = 0.0
    total_rewards: float = 0.0
    selection_score: float = 0.0
    selection_weight: float = 0.0
    recent_validation_votes: int = 0
    availability_score: float = 0.0
    balance: float = 0.0
    is_banned: bool = False


class TaskResponse(BaseModel):
    task_id: str
    miner_id: str
    range_start: int
    range_end: int
    algorithm: str
    status: str
    assignment_seed: str | None = None
    assignment_mode: str | None = None
    assignment_ms: int | None = None
    compute_ms: int | None = None
    protocol_params_id: int | None = None
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
    compute_ms: int | None = Field(default=None, ge=0)
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
    difficulty: float | None = None
    protocol_params_id: int | None = None
    protocol_version: str | None = None
    validation_mode: str | None = None
    total_task_ms: int | None = None
    validation_ms: int | None = None


class StatsResponse(BaseModel):
    miners: int
    tasks: int
    pending_tasks: int
    expired_tasks: int
    accepted_blocks: int
    rejected_submissions: int
    total_rewards: float
    total_validator_rewards: float
    total_minted_rewards: float
    circulating_supply: float
    genesis_balance: float
    latest_block_hash: str


class BalanceResponse(BaseModel):
    account_id: str
    account_type: str
    balance: float
    updated_at: datetime


class LedgerEntryResponse(BaseModel):
    id: int
    account_id: str
    account_type: str
    amount: float
    balance_after: float
    entry_type: str
    block_height: int | None = None
    related_id: str | None = None
    description: str | None = None
    created_at: datetime


class AuditSummaryResponse(BaseModel):
    genesis_supply: float
    circulating_supply: float
    genesis_balance: float
    total_miner_balances: float
    total_validator_balances: float
    total_locked_validator_stake: float
    total_slashed_validator_stake: float
    accepted_blocks: int
    pending_validation_jobs: int
    validator_count: int
    eligible_validator_count: int


class AuditIssue(BaseModel):
    code: str
    severity: str
    message: str
    details: dict[str, Any] = {}


class AuditFullResponse(BaseModel):
    valid: bool
    network_id: str
    protocol_version: str
    checked_at: datetime
    tolerance: float
    supply: dict[str, Any]
    ledger: dict[str, Any]
    rewards: dict[str, Any]
    validators: dict[str, Any]
    issues: list[AuditIssue]


class MaintenanceCleanupResponse(BaseModel):
    expired_tasks: int
    expired_validation_jobs: int
    message: str


class PerformanceStatsResponse(BaseModel):
    accepted_blocks: int
    avg_compute_ms: float
    avg_assignment_ms: float
    avg_commit_ms: float
    avg_validation_ms: float
    avg_total_task_ms: float
    pending_validation_jobs: int
    bbp_digit_cache_hits: int
    bbp_digit_cache_misses: int
    bbp_digit_cache_maxsize: int
    bbp_digit_cache_currsize: int


class ProtocolResponse(BaseModel):
    project: str
    protocol_version: str
    network_id: str
    algorithm: str
    validation_mode: str
    required_validator_approvals: int
    range_assignment_mode: str
    max_pi_position: int
    range_assignment_max_attempts: int
    segment_size: int
    sample_count: int
    task_expiration_seconds: int
    max_active_tasks_per_miner: int
    base_reward: float
    difficulty: float
    reward_per_block: float
    validator_reward_percent: float
    validator_reward_pool_per_block: float
    faucet_enabled: bool
    validator_selection_mode: str
    penalty_invalid_result: int
    penalty_duplicate: int
    penalty_invalid_signature: int
    cooldown_after_rejections: int
    cooldown_seconds: int


class ProtocolParamsResponse(BaseModel):
    id: int
    protocol_version: str
    algorithm: str
    validation_mode: str
    required_validator_approvals: int
    range_assignment_mode: str
    max_pi_position: int
    range_assignment_max_attempts: int
    segment_size: int
    sample_count: int
    task_expiration_seconds: int
    max_active_tasks_per_miner: int
    base_reward: float
    active: bool
    created_at: datetime
    difficulty: float
    reward_per_block: float


class RetargetStatusResponse(BaseModel):
    enabled: bool
    epoch_blocks: int
    target_block_ms: int
    tolerance: float
    current_height: int
    last_retarget_height: int
    current_epoch_block_count: int
    current_epoch_average_ms: float | None = None
    blocks_until_next_epoch: int
    active_difficulty: float
    active_reward_per_block: float


class RetargetPreviewResponse(BaseModel):
    ready: bool
    status: str
    message: str
    current_height: int
    last_retarget_height: int
    epoch_start_height: int | None = None
    epoch_end_height: int | None = None
    epoch_block_count: int
    epoch_blocks_required: int
    blocks_until_ready: int
    average_block_ms: float | None = None
    target_block_ms: int
    tolerance: float
    action: str
    reason: str
    adjustment_factor: float
    old_difficulty: float
    new_difficulty: float
    current_protocol: ProtocolResponse
    proposed_protocol: ProtocolResponse


class RetargetEventResponse(BaseModel):
    id: int
    previous_protocol_params_id: int | None
    new_protocol_params_id: int | None
    epoch_start_height: int
    epoch_end_height: int
    epoch_block_count: int
    average_block_ms: float
    target_block_ms: int
    old_difficulty: float
    new_difficulty: float
    adjustment_factor: float
    action: str
    reason: str
    created_at: datetime


class RetargetRunResponse(BaseModel):
    retargeted: bool
    status: str
    message: str
    event: RetargetEventResponse | None = None
    protocol: ProtocolResponse


class ValidationJobResponse(BaseModel):
    job_id: str
    task_id: str
    miner_id: str
    range_start: int
    range_end: int
    algorithm: str
    result_hash: str
    merkle_root: str
    challenge_seed: str
    samples: list[dict[str, Any]]
    status: str
    assigned_validator_id: str | None = None
    selection_score: float | None = None
    selection_rank: int | None = None
    approvals: int = 0
    rejections: int = 0
    required_approvals: int = 1
    required_rejections: int = 1
    created_at: datetime


class ValidationResultRequest(BaseModel):
    job_id: str
    validator_id: str
    approved: bool
    reason: str = Field(..., min_length=1, max_length=512)
    signature: str = Field(..., min_length=1, max_length=256)
    signed_at: datetime


class ValidationResultResponse(BaseModel):
    accepted: bool
    status: str
    message: str
    block: "BlockResponse | None" = None
    approvals: int = 0
    rejections: int = 0
    required_approvals: int = 1
    required_rejections: int = 1


class ChainVerificationIssue(BaseModel):
    height: int | None = None
    reason: str


class ChainVerificationResponse(BaseModel):
    valid: bool
    checked_blocks: int
    latest_block_hash: str
    issues: list[ChainVerificationIssue]


TaskSubmitResponse.model_rebuild()
