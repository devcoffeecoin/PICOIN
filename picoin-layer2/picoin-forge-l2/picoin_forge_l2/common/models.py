from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ChallengeType(str, Enum):
    CPU = "cpu"
    RAM = "ram"
    IO = "io"
    GPU = "gpu"
    AI_MODEL = "ai_model"


class ChallengeStatus(str, Enum):
    CREATED = "created"
    ASSIGNED = "assigned"
    PASSED = "passed"
    FAILED = "failed"
    EXPIRED = "expired"


class WorkerStatus(str, Enum):
    REGISTERED = "registered"
    ONLINE = "online"
    OFFLINE = "offline"
    PENALIZED = "penalized"


class WorkloadType(str, Enum):
    HASH_TEXT = "hash_text"
    TEXT_CLASSIFY = "text_classify"
    BATCH_SUMMARIZE = "batch_summarize"
    TEXT_EMBED = "text_embed"


class WorkloadStatus(str, Enum):
    QUEUED = "queued"
    ASSIGNED = "assigned"
    SUBMITTED = "submitted"
    VERIFIED = "verified"
    FAILED = "failed"


class AIRequestStatus(str, Enum):
    QUEUED = "queued"
    ASSIGNED = "assigned"
    SUBMITTED = "submitted"
    VERIFIED = "verified"
    FAILED = "failed"
    CANCELED = "canceled"


class AIModelProfile(BaseModel):
    provider: str = "none"
    model_name: str | None = None
    parameter_count_b: float = 0.0
    context_tokens: int = 0
    quantization: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    endpoint: str | None = None
    available: bool = False


class MachineInfo(BaseModel):
    hostname: str
    platform: str
    cpu_count: int
    python_version: str
    gpu_detected: bool = False
    gpu_name: str | None = None
    ram_bytes: int | None = None


class WorkerConfig(BaseModel):
    wallet: str
    coordinator_url: str = "http://127.0.0.1:9380"
    interval_seconds: float = 30.0
    benchmark_scale: int = Field(default=1, ge=1, le=10)
    request_challenges: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class WorkerRegistration(BaseModel):
    worker_id: str
    wallet: str
    public_key: str
    machine_info: MachineInfo
    ai_model_profile: AIModelProfile | None = None
    status: WorkerStatus = WorkerStatus.REGISTERED
    registered_at: datetime = Field(default_factory=utc_now)


class BenchmarkResult(BaseModel):
    worker_id: str
    cpu_score: float = 0.0
    gpu_score: float = 0.0
    ram_score: float = 0.0
    io_score: float = 0.0
    benchmark_score: float = 0.0
    result_hash: str
    measured_at: datetime = Field(default_factory=utc_now)
    details: dict[str, Any] = Field(default_factory=dict)


class ComputeChallenge(BaseModel):
    challenge_id: str
    worker_id: str
    challenge_type: ChallengeType
    seed: str
    difficulty: int = Field(ge=1)
    expected_hash: str
    deadline: datetime
    status: ChallengeStatus = ChallengeStatus.CREATED
    created_at: datetime = Field(default_factory=utc_now)


class ChallengeResult(BaseModel):
    challenge_id: str
    worker_id: str
    result_hash: str
    passed: bool
    submitted_at: datetime = Field(default_factory=utc_now)
    elapsed_ms: float = 0.0
    proof: dict[str, Any] = Field(default_factory=dict)


class ChallengeCreateRequest(BaseModel):
    worker_id: str
    challenge_type: ChallengeType = ChallengeType.CPU
    difficulty: int = Field(default=1, ge=1, le=100)


class Heartbeat(BaseModel):
    worker_id: str
    timestamp: datetime = Field(default_factory=utc_now)
    status: WorkerStatus = WorkerStatus.ONLINE


class WorkloadCreateRequest(BaseModel):
    task_type: WorkloadType = WorkloadType.HASH_TEXT
    payload: dict[str, Any] = Field(default_factory=dict)
    requester_wallet: str | None = None


class WorkloadTask(BaseModel):
    task_id: str
    task_type: WorkloadType
    payload: dict[str, Any] = Field(default_factory=dict)
    status: WorkloadStatus = WorkloadStatus.QUEUED
    expected_result_hash: str
    assigned_worker_id: str | None = None
    result_hash: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class WorkloadClaimRequest(BaseModel):
    worker_id: str


class WorkloadResult(BaseModel):
    task_id: str
    worker_id: str
    result_hash: str
    submitted_at: datetime = Field(default_factory=utc_now)


class AIInferenceCreateRequest(BaseModel):
    requester_wallet: str
    stake_snapshot_pi: float = Field(default=0.0, ge=0.0)
    prompt: str = Field(min_length=1, max_length=16000)
    required_capabilities: list[str] = Field(default_factory=list)
    model_hint: str | None = None
    min_parameter_count_b: float = Field(default=0.0, ge=0.0)
    min_context_tokens: int = Field(default=0, ge=0)
    preferred_provider: str | None = None
    max_tokens: int = Field(default=256, ge=1, le=4096)
    store_output: bool = True


class AIInferenceRequest(BaseModel):
    request_id: str
    requester_wallet: str
    stake_snapshot_pi: float = 0.0
    required_stake_pi: float = 0.0
    prompt: str
    prompt_hash: str
    required_capabilities: list[str] = Field(default_factory=list)
    model_hint: str | None = None
    min_parameter_count_b: float = 0.0
    min_context_tokens: int = 0
    preferred_provider: str | None = None
    max_tokens: int = 256
    store_output: bool = True
    status: AIRequestStatus = AIRequestStatus.QUEUED
    assigned_worker_id: str | None = None
    assigned_at: datetime | None = None
    lease_expires_at: datetime | None = None
    assignment_attempts: int = 0
    assignment_history: list[str] = Field(default_factory=list)
    model_profile: AIModelProfile | None = None
    output: str | None = None
    output_hash: str | None = None
    receipt_hash: str | None = None
    failure_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    no_l1_transaction_created: bool = True
    no_per_task_payment: bool = True


class AIInferenceClaimRequest(BaseModel):
    worker_id: str


class AIInferenceResult(BaseModel):
    request_id: str
    worker_id: str
    output: str = Field(min_length=1, max_length=64000)
    submitted_at: datetime = Field(default_factory=utc_now)


class AIChatSessionCreateRequest(BaseModel):
    requester_wallet: str
    stake_snapshot_pi: float = Field(default=0.0, ge=0.0)
    title: str | None = Field(default=None, max_length=120)
    required_capabilities: list[str] = Field(default_factory=list)
    model_hint: str | None = None
    min_parameter_count_b: float = Field(default=0.0, ge=0.0)
    min_context_tokens: int = Field(default=0, ge=0)
    preferred_provider: str | None = None
    max_tokens: int = Field(default=256, ge=1, le=4096)
    store_output: bool = True


class AIChatSession(BaseModel):
    session_id: str
    requester_wallet: str
    stake_snapshot_pi: float = 0.0
    title: str | None = None
    required_capabilities: list[str] = Field(default_factory=list)
    model_hint: str | None = None
    min_parameter_count_b: float = 0.0
    min_context_tokens: int = 0
    preferred_provider: str | None = None
    max_tokens: int = 256
    store_output: bool = True
    message_count: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    no_l1_transaction_created: bool = True
    no_per_task_payment: bool = True


class AIChatMessageCreateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=16000)


class AIChatMessage(BaseModel):
    message_id: str
    session_id: str
    role: str
    content: str | None = Field(default=None, max_length=64000)
    status: str = "created"
    request_id: str | None = None
    prompt_hash: str | None = None
    output_hash: str | None = None
    receipt_hash: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    no_l1_transaction_created: bool = True
    no_per_task_payment: bool = True


class WorkerState(BaseModel):
    registration: WorkerRegistration
    benchmark: BenchmarkResult | None = None
    last_heartbeat_at: datetime | None = None
    passed_challenges: int = 0
    failed_challenges: int = 0
    penalty_score: float = 0.0
    uptime_score: float = 0.0
    reliability_score: float = 50.0
    ai_model_score: float = 0.0
    verified_compute_score: float = 0.0


class ScoreWeights(BaseModel):
    cpu_weight: float = 1.0
    gpu_weight: float = 2.5
    ai_model_weight: float = 3.0
    ram_weight: float = 0.35
    io_weight: float = 0.25
    uptime_weight: float = 0.50
    reliability_weight: float = 0.75


class EpochReward(BaseModel):
    worker_id: str
    wallet: str
    verified_compute_score: float
    reward_pi: float


class EpochSettlement(BaseModel):
    epoch_id: int
    epoch_reward: float
    total_verified_compute: float
    workers: list[EpochReward]
    result_hash: str
    timestamp: datetime = Field(default_factory=utc_now)
    l1_settlement_ready: bool = False
    l1_note: str = "Simulated only; no Picoin L1 transaction was created."


class SettlementPayloadPreview(BaseModel):
    schema_version: str = "picoin-forge-l2-settlement-preview-v1"
    payload_type: str = "l2_epoch_settlement_preview"
    epoch_id: int
    epoch_reward: float
    total_verified_compute: float
    worker_count: int
    settlement_result_hash: str
    worker_rewards: list[EpochReward]
    payload_hash: str
    signatures: list[dict[str, Any]] = Field(default_factory=list)
    no_l1_transaction_created: bool = True
    note: str = "Preview only. This payload is not submitted to Picoin L1."


class DemoResult(BaseModel):
    workers_created: int
    challenges_passed: int
    settlement: EpochSettlement


class CoordinatorEvent(BaseModel):
    event_id: str
    event_type: str
    subject_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
