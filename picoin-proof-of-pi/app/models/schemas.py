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
    tx_merkle_root: str | None = None
    tx_count: int = 0
    tx_hashes: list[str] = Field(default_factory=list)
    fee_reward: float = 0.0
    state_root: str | None = None
    difficulty: float | None = None
    protocol_params_id: int | None = None
    protocol_version: str | None = None
    validation_mode: str | None = None
    total_task_ms: int | None = None
    validation_ms: int | None = None
    fraudulent: bool = False
    fraud_reason: str | None = None
    fraud_detected_at: datetime | None = None


class StatsResponse(BaseModel):
    miners: int
    tasks: int
    pending_tasks: int
    expired_tasks: int
    accepted_blocks: int
    rejected_submissions: int
    total_rewards: float
    total_validator_rewards: float
    total_audit_rewards: float = 0.0
    total_science_reserve_rewards: float = 0.0
    total_scientific_development_rewards: float = 0.0
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


class RetroactiveAuditResponse(BaseModel):
    id: int
    block_height: int
    block_hash: str
    audit_seed: str
    sample_count: int
    samples: list[dict[str, Any]]
    expected_hash: str
    actual_hash: str
    passed: bool
    reason: str
    created_at: datetime


class RetroactiveAuditRunResponse(BaseModel):
    accepted: bool
    audit: RetroactiveAuditResponse


class ScienceStakeRequest(BaseModel):
    address: str
    amount: float


class ScienceStakeAccountResponse(BaseModel):
    account_id: str
    address: str
    stake_amount: float
    tier: str | None = None
    compute_multiplier: int
    monthly_quota_used: float
    monthly_quota_epoch: str
    monthly_quota_limit: float
    priority: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime


class ScienceCreateJobRequest(BaseModel):
    requester_address: str
    job_type: str
    metadata_hash: str
    storage_pointer: str
    reward_budget: float | None = None
    max_compute_units: float | None = Field(default=None, ge=0)
    reward_per_compute_unit: float | None = Field(default=None, ge=0)
    max_reward: float | None = Field(default=None, ge=0)


class ScienceJobTransitionRequest(BaseModel):
    status: str
    worker_address: str | None = None
    result_hash: str | None = None
    proof_hash: str | None = None
    compute_units_used: float | None = Field(default=None, ge=0)


class ScienceJobAcceptRequest(BaseModel):
    worker_address: str | None = None
    result_hash: str | None = None
    proof_hash: str | None = None
    compute_units_used: float = Field(..., gt=0)


class ScienceJobResponse(BaseModel):
    job_id: str
    requester_address: str
    tier_at_creation: str
    job_type: str
    metadata_hash: str
    storage_pointer: str
    reward_budget: float
    max_compute_units: float
    reward_per_compute_unit: float
    max_reward: float
    compute_units_used: float = 0.0
    payout_amount: float = 0.0
    status: str
    worker_address: str | None = None
    result_hash: str | None = None
    proof_hash: str | None = None
    paid: bool = False
    paid_amount: float = 0.0
    paid_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ScienceRewardReserveResponse(BaseModel):
    epoch: str
    total_reserved: float
    total_paid: float
    total_pending: float
    available: float
    status: str
    activation_requested_at: datetime | None = None
    activation_available_at: datetime | None = None
    activated_at: datetime | None = None
    governance_approvals: list[str] = []
    authorized_signers: list[str] = []
    governance_threshold: int
    payouts_enabled: bool = False
    emergency_paused: bool = False
    max_reward_per_job: float
    max_payout_per_epoch: float
    max_pending_per_requester: float
    updated_at: datetime


class ScienceReserveGovernanceRequest(BaseModel):
    signer: str


class ScienceReserveGovernanceResponse(BaseModel):
    id: int
    status: str
    activation_requested_at: datetime | None = None
    activation_available_at: datetime | None = None
    activated_at: datetime | None = None
    approvals: list[str]
    authorized_signers: list[str]
    payouts_enabled: bool
    emergency_paused: bool
    threshold: int
    timelock_seconds: int
    updated_at: datetime


class TreasuryClaimRequest(BaseModel):
    requested_by: str | None = None
    claim_to: str | None = None


class TreasuryClaimResponse(BaseModel):
    claim_id: str
    amount: float
    claim_to: str
    requested_by: str
    created_at: datetime


class ScientificDevelopmentTreasuryEpochResponse(BaseModel):
    epoch: str
    start_block: int
    end_block: int
    locked_amount: float
    unlocked_amount: float
    claimed_amount: float
    unlock_at: datetime
    status: str
    created_at: datetime
    updated_at: datetime


class ScientificDevelopmentTreasuryResponse(BaseModel):
    treasury_id: str
    total_accumulated: float
    total_claimed: float
    locked_balance: float
    unlocked_balance: float
    claimable: float
    current_epoch: str
    epoch_start_block: int
    epoch_end_block: int
    next_unlock_at: datetime
    last_claim_at: datetime | None = None
    treasury_wallet: str
    governance_wallet: str
    unlock_interval_days: int
    reward_percent: float
    created_at: datetime
    updated_at: datetime
    history: list[ScientificDevelopmentTreasuryEpochResponse] = []
    claim: TreasuryClaimResponse | None = None


class ScienceEventResponse(BaseModel):
    id: int
    type: str
    title: str
    message: str
    severity: str
    created_at: datetime
    related_id: str | None = None
    block_height: int | None = None
    actor_id: str | None = None
    metadata: dict[str, Any] = {}


class PeerRegisterRequest(BaseModel):
    node_id: str = Field(..., min_length=1, max_length=128)
    peer_address: str = Field(..., min_length=1, max_length=256)
    peer_type: str = Field("full", pattern="^(full|miner|validator|auditor|bootstrap)$")
    protocol_version: str
    network_id: str
    chain_id: str
    genesis_hash: str
    metadata: dict[str, Any] = {}


class PeerResponse(BaseModel):
    peer_id: str
    node_id: str
    peer_address: str
    peer_type: str
    protocol_version: str
    network_id: str
    chain_id: str
    genesis_hash: str
    connected_at: datetime
    last_seen: datetime
    status: str
    metadata: dict[str, Any] = {}


class NodeIdentityResponse(BaseModel):
    project: str
    node_id: str
    peer_id: str
    peer_address: str
    peer_type: str
    protocol_version: str
    network_id: str
    chain_id: str
    genesis_hash: str
    bootstrap_peers: list[str] = []


class NodeSyncStatusResponse(NodeIdentityResponse):
    latest_block_height: int
    latest_block_hash: str
    latest_checkpoint: dict[str, Any] | None = None
    active_snapshot_base: dict[str, Any] | None = None
    peer_counts: dict[str, Any]
    mempool: dict[str, int]
    pending_replay_blocks: int
    consensus: dict[str, int] = {}
    sync_mode: str
    checked_at: datetime


class SignedTransactionRequest(BaseModel):
    tx_hash: str = Field(..., min_length=64, max_length=64)
    tx_type: str = Field(..., pattern="^(transfer|stake|unstake|science_job_create|governance_action|treasury_claim)$")
    sender: str = Field(..., min_length=3, max_length=80)
    recipient: str | None = Field(default=None, max_length=80)
    amount: float = Field(default=0, ge=0)
    nonce: int = Field(..., ge=0)
    fee: float = Field(default=0, ge=0)
    payload: dict[str, Any] = {}
    timestamp: datetime
    network_id: str
    chain_id: str
    public_key: str = Field(..., min_length=1, max_length=256)
    signature: str = Field(..., min_length=1, max_length=256)


class MempoolTransactionResponse(BaseModel):
    tx_hash: str
    tx_type: str
    sender: str
    recipient: str | None = None
    amount: float
    nonce: int
    fee: float
    payload: dict[str, Any] = {}
    network_id: str
    chain_id: str
    timestamp: datetime
    public_key: str
    signature: str
    status: str
    propagated: bool
    block_height: int | None = None
    rejection_reason: str | None = None
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


class BlockReceiveRequest(BaseModel):
    block: dict[str, Any]
    source_peer_id: str | None = None


class BlockReceiveResponse(BaseModel):
    accepted: bool
    status: str
    reason: str
    block_hash: str


class BlockSyncResponse(BaseModel):
    from_height: int
    count: int
    blocks: list[dict[str, Any]]


class CanonicalCheckpointResponse(BaseModel):
    checkpoint_id: str
    height: int
    block_hash: str
    previous_hash: str
    state_root: str
    balances_hash: str
    snapshot_hash: str
    balances_count: int
    ledger_entries_count: int
    total_balance: float
    trusted: bool
    source: str
    created_at: datetime
    verified_at: datetime | None = None
    payload: dict[str, Any]


class CheckpointVerificationResponse(BaseModel):
    valid: bool
    height: int
    checkpoint: CanonicalCheckpointResponse
    issues: list[str]
    computed: dict[str, Any]


class SnapshotImportRequest(BaseModel):
    snapshot: dict[str, Any]
    source: str = Field("api", min_length=1, max_length=80)


class SnapshotImportResponse(BaseModel):
    imported: bool
    snapshot: dict[str, Any]
    validation: dict[str, Any]


class PeerReconcileResponse(BaseModel):
    attempted: int = 0
    transactions_imported: int = 0
    proposals_imported: int = 0
    blocks_imported: int = 0
    peers_seen: int = 0
    errors: int = 0
    results: list[dict[str, Any]] = []


class ConsensusBlockProposalRequest(BaseModel):
    block: dict[str, Any]
    proposer_node_id: str = Field(..., min_length=1, max_length=128)


class ConsensusVoteRequest(BaseModel):
    validator_id: str = Field(..., min_length=1, max_length=128)
    approved: bool
    reason: str = Field(..., min_length=1, max_length=512)
    signature: str = Field(..., min_length=1, max_length=256)
    signed_at: datetime


class ConsensusProposalResponse(BaseModel):
    proposal_id: str
    block_hash: str
    height: int
    previous_hash: str
    proposer_node_id: str
    status: str
    payload: dict[str, Any]
    approvals: int
    rejections: int
    rejection_reason: str | None = None
    finalized_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ConsensusVoteResponse(BaseModel):
    vote_id: str
    proposal_id: str
    block_hash: str
    validator_id: str
    approved: bool
    reason: str
    signature: str
    signed_at: datetime
    created_at: datetime
    trust_score: float | None = None
    stake_locked: float | None = None
    weight: float = 0.0


class ConsensusReplayResponse(BaseModel):
    imported: int
    skipped: int
    headers_imported: int = 0
    headers_skipped: int = 0
    normalized: int = 0
    errors: list[str] = Field(default_factory=list)


class ConsensusStatusResponse(BaseModel):
    required_validator_approvals: int
    latest_block_height: int
    latest_block_hash: str
    proposals: dict[str, int]
    finalizations: int
    fork_choices: list[dict[str, Any]] = []
    checked_at: datetime


class WalletCreateRequest(BaseModel):
    name: str = Field("picoin-wallet", min_length=1, max_length=80)


class WalletCreateResponse(BaseModel):
    name: str
    address: str
    public_key: str
    private_key: str
    network_id: str
    chain_id: str
    created_at: datetime


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


class HealthResponse(BaseModel):
    status: str
    project: str
    protocol_version: str
    network_id: str
    checked_at: datetime
    started_at: datetime
    uptime_seconds: int
    database: dict[str, Any]
    chain: dict[str, Any]
    audit: dict[str, Any]
    latest_block_height: int
    latest_block_hash: str
    can_assign_tasks: bool
    mining_ready: bool
    issues: list[str]


class NodeStatusResponse(BaseModel):
    project: str
    protocol_version: str
    network_id: str
    started_at: datetime
    checked_at: datetime
    uptime_seconds: int
    latest_block_height: int
    latest_block_hash: str
    chain_valid: bool
    audit_valid: bool
    mining_ready: bool
    counts: dict[str, Any]
    protocol: dict[str, Any]
    performance: dict[str, Any]
    economy: dict[str, Any]


class NodeEventResponse(BaseModel):
    id: str
    type: str
    title: str
    message: str
    severity: str
    created_at: datetime
    related_id: str | None = None
    block_height: int | None = None
    actor_id: str | None = None
    metadata: dict[str, Any] = {}


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
    proof_of_pi_reward_per_block: float
    proof_of_pi_reward_percent: float
    science_compute_reward_percent: float
    science_compute_reserve_per_block: float
    science_reserve_account_id: str
    science_base_monthly_quota_units: int
    validator_auditor_reward_percent: float
    validator_reward_percent: float
    validator_reward_pool_per_block: float
    scientific_development_reward_percent: float
    scientific_development_treasury_per_block: float
    scientific_development_treasury_account_id: str
    scientific_development_treasury_wallet: str
    scientific_development_governance_wallet: str
    scientific_development_unlock_interval_days: int
    retroactive_audit_interval_blocks: int
    retroactive_audit_sample_multiplier: int
    retroactive_audit_reward_percent: float
    retroactive_audit_reward_per_audit: float
    fraud_miner_penalty_points: int
    fraud_validator_invalid_results: int
    fraud_cooldown_seconds: int
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
    automatic: bool = False
    reward: float = 0.0
    reward_account_id: str | None = None
    fraud_detected: bool = False
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
