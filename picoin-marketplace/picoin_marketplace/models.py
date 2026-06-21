from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


PICO_CURRENCY = "PICO"


class HardwareType(str, Enum):
    CPU = "cpu"
    GPU = "gpu"
    ASIC = "asic"


class ListingStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    RETIRED = "retired"


class BookingStatus(str, Enum):
    AWAITING_PAYMENT = "awaiting_payment"
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"
    CANCELED = "canceled"


class PaymentStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class PoolStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    RETIRED = "retired"


class ChainFamily(str, Enum):
    EVM = "evm"
    PICOIN = "picoin"


class AccountStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


class WalletStatus(str, Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    DISABLED = "disabled"


class DepositStatus(str, Enum):
    DETECTED = "detected"
    CONFIRMING = "confirming"
    CONFIRMED = "confirmed"
    CREDITED = "credited"
    REORGED = "reorged"


class LedgerDirection(str, Enum):
    CREDIT = "credit"
    DEBIT = "debit"


class AccountCreateRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    display_name: str | None = Field(default=None, max_length=140)


class Account(BaseModel):
    account_id: str
    email: str
    display_name: str | None = None
    status: AccountStatus = AccountStatus.ACTIVE
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ChainCreateRequest(BaseModel):
    chain_code: str = Field(min_length=2, max_length=32)
    display_name: str = Field(min_length=2, max_length=120)
    family: ChainFamily
    chain_id: int | None = None
    confirmations_required: int = Field(default=3, ge=1, le=10_000)
    deposit_address: str | None = Field(default=None, max_length=128)
    rpc_endpoints: list[str] = Field(default_factory=list)
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChainDefinition(ChainCreateRequest):
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TokenCreateRequest(BaseModel):
    chain_code: str = Field(min_length=2, max_length=32)
    token_symbol: str = Field(min_length=2, max_length=24)
    display_name: str = Field(min_length=2, max_length=120)
    decimals: int = Field(default=18, ge=0, le=36)
    token_type: str = Field(default="native", max_length=32)
    contract_address: str | None = Field(default=None, max_length=128)
    pico_rate: float | None = Field(
        default=None,
        gt=0,
        description="How many PICO one whole token buys; required for paying bookings with this token.",
    )
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class TokenDefinition(TokenCreateRequest):
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class WalletCreateRequest(BaseModel):
    chain_code: str = Field(min_length=2, max_length=32)
    address: str = Field(min_length=10, max_length=128)
    label: str | None = Field(default=None, max_length=120)


class Wallet(BaseModel):
    wallet_id: str
    account_id: str
    chain_code: str
    address: str
    label: str | None = None
    status: WalletStatus = WalletStatus.PENDING
    verified_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ScannerDepositCreateRequest(BaseModel):
    chain_code: str = Field(min_length=2, max_length=32)
    token_symbol: str = Field(min_length=2, max_length=24)
    from_address: str = Field(min_length=10, max_length=128)
    to_address: str = Field(min_length=10, max_length=128)
    amount_base_units: str = Field(min_length=1, max_length=96)
    tx_hash: str = Field(min_length=16, max_length=128)
    block_number: int = Field(gt=0)
    block_hash: str | None = Field(default=None, max_length=128)
    log_index: int = Field(default=0, ge=0)
    status: DepositStatus = DepositStatus.DETECTED


class Deposit(BaseModel):
    deposit_id: str
    account_id: str
    wallet_id: str
    chain_code: str
    token_symbol: str
    from_address: str
    to_address: str
    amount_base_units: str
    tx_hash: str
    block_number: int
    block_hash: str | None = None
    log_index: int = 0
    confirmations: int = 0
    status: DepositStatus = DepositStatus.DETECTED
    confirmed_at: datetime | None = None
    credited_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ConfirmationProcessRequest(BaseModel):
    latest_block_number: int = Field(gt=0)


class PicoinHistoryImportRequest(BaseModel):
    rows: list[dict[str, Any]] = Field(default_factory=list)
    latest_block_number: int | None = Field(default=None, gt=0)


class PicoinNodePollRequest(BaseModel):
    node_url: str | None = Field(default=None, max_length=240)
    address: str | None = Field(default=None, max_length=128)
    limit: int = Field(default=50, ge=1, le=500)
    confirmed_only: bool = True
    latest_block_number: int | None = Field(default=None, gt=0)


class LedgerEntry(BaseModel):
    entry_id: str
    account_id: str
    chain_code: str
    token_symbol: str
    direction: LedgerDirection
    amount_base_units: str
    entry_type: str = Field(max_length=48)
    reference_type: str = Field(max_length=48)
    reference_id: str = Field(max_length=128)
    created_at: datetime = Field(default_factory=utc_now)


class AccountBalance(BaseModel):
    account_id: str
    chain_code: str
    token_symbol: str
    decimals: int
    available_base_units: str
    available: str


class PayFromBalanceRequest(BaseModel):
    account_id: str = Field(min_length=1, max_length=80)
    chain_code: str = Field(default="picoin", min_length=2, max_length=32)
    token_symbol: str = Field(default=PICO_CURRENCY, min_length=2, max_length=24)


class MiningPoolCreateRequest(BaseModel):
    hardware_type: HardwareType
    paired_coin: str = Field(min_length=2, max_length=24)
    name: str | None = Field(default=None, max_length=140)
    picoin_capacity_percent: float = Field(default=10.0, ge=0.0, le=100.0)
    paired_capacity_percent: float = Field(default=90.0, ge=0.0, le=100.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MiningPool(BaseModel):
    pool_id: str
    hardware_type: HardwareType
    base_coin: str = PICO_CURRENCY
    paired_coin: str
    pair_symbol: str
    name: str
    picoin_capacity_percent: float = 10.0
    paired_capacity_percent: float = 90.0
    status: PoolStatus = PoolStatus.ACTIVE
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ListingCreateRequest(BaseModel):
    provider_id: str = Field(min_length=1, max_length=120)
    provider_wallet: str = Field(min_length=10, max_length=96)
    pool_id: str = Field(min_length=1, max_length=80)
    hardware_type: HardwareType
    title: str = Field(min_length=1, max_length=140)
    units_total: int = Field(default=1, ge=1, le=100000)
    price_pi_per_hour: float = Field(gt=0.0)
    min_booking_minutes: int = Field(default=30, ge=1, le=60 * 24 * 30)
    region: str | None = Field(default=None, max_length=80)
    capabilities: list[str] = Field(default_factory=list)
    cpu_threads: int | None = Field(default=None, ge=1)
    memory_gb: float | None = Field(default=None, ge=0.0)
    gpu_model: str | None = Field(default=None, max_length=120)
    gpu_count: int | None = Field(default=None, ge=1)
    gpu_vram_gb: float | None = Field(default=None, ge=0.0)
    asic_algorithm: str | None = Field(default=None, max_length=80)
    asic_hashrate_th_s: float | None = Field(default=None, ge=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Listing(BaseModel):
    listing_id: str
    pool_id: str
    pair_symbol: str
    paired_coin: str
    picoin_capacity_percent: float = 10.0
    paired_capacity_percent: float = 90.0
    provider_id: str
    provider_wallet: str
    hardware_type: HardwareType
    title: str
    units_total: int
    units_available: int
    price_pi_per_hour: float
    min_booking_minutes: int
    currency: str = PICO_CURRENCY
    region: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    cpu_threads: int | None = None
    memory_gb: float | None = None
    gpu_model: str | None = None
    gpu_count: int | None = None
    gpu_vram_gb: float | None = None
    asic_algorithm: str | None = None
    asic_hashrate_th_s: float | None = None
    status: ListingStatus = ListingStatus.ACTIVE
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class BookingCreateRequest(BaseModel):
    account_id: str | None = Field(default=None, max_length=80)
    requester_wallet: str = Field(min_length=10, max_length=96)
    payment_chain_code: str = Field(default="picoin", min_length=2, max_length=32)
    payment_token_symbol: str = Field(default=PICO_CURRENCY, min_length=2, max_length=24)
    hardware_type: HardwareType | None = None
    pool_id: str | None = Field(default=None, max_length=80)
    paired_coin: str | None = Field(default=None, max_length=24)
    units: int = Field(default=1, ge=1, le=100000)
    duration_minutes: int = Field(default=60, ge=1, le=60 * 24 * 30)
    required_capabilities: list[str] = Field(default_factory=list)
    preferred_region: str | None = Field(default=None, max_length=80)
    provider_id: str | None = Field(default=None, max_length=120)
    listing_id: str | None = Field(default=None, max_length=80)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BookingQuoteRequest(BaseModel):
    requester_wallet: str = Field(default="PI_QUOTE_ONLY", min_length=10, max_length=96)
    hardware_type: HardwareType | None = None
    pool_id: str | None = Field(default=None, max_length=80)
    paired_coin: str | None = Field(default=None, max_length=24)
    units: int = Field(default=1, ge=1, le=100000)
    duration_minutes: int = Field(default=60, ge=1, le=60 * 24 * 30)
    required_capabilities: list[str] = Field(default_factory=list)
    preferred_region: str | None = Field(default=None, max_length=80)
    provider_id: str | None = Field(default=None, max_length=120)
    listing_id: str | None = Field(default=None, max_length=80)


class BookingQuote(BaseModel):
    pool_id: str
    listing_id: str
    provider_id: str
    hardware_type: HardwareType
    pair_symbol: str
    paired_coin: str
    units: int
    duration_minutes: int
    price_pi_per_hour: float
    amount_pi: float
    currency: str = PICO_CURRENCY
    picoin_capacity_percent: float
    paired_capacity_percent: float
    picoin_capacity_units: float
    paired_capacity_units: float
    available_units_after_quote: int
    can_book: bool


class PaymentOrder(BaseModel):
    payment_id: str
    booking_id: str
    currency: str = PICO_CURRENCY
    amount_pi: float
    amount_base_units: str | None = None
    payment_chain_code: str = "picoin"
    payment_token_symbol: str = PICO_CURRENCY
    account_id: str | None = None
    pay_to_address: str
    memo: str
    status: PaymentStatus = PaymentStatus.PENDING
    tx_hash: str | None = None
    confirmations: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Booking(BaseModel):
    booking_id: str
    pool_id: str
    pair_symbol: str
    paired_coin: str
    picoin_capacity_percent: float = 10.0
    paired_capacity_percent: float = 90.0
    listing_id: str
    provider_id: str
    provider_wallet: str
    requester_wallet: str
    hardware_type: HardwareType
    units: int
    duration_minutes: int
    amount_pi: float
    currency: str = PICO_CURRENCY
    picoin_capacity_units: float = 0.0
    paired_capacity_units: float = 0.0
    status: BookingStatus = BookingStatus.AWAITING_PAYMENT
    payment_id: str
    starts_at: datetime | None = None
    expires_at: datetime = Field(default_factory=lambda: utc_now() + timedelta(minutes=30))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PaymentSubmitRequest(BaseModel):
    tx_hash: str = Field(min_length=16, max_length=128)
    confirmations: int = Field(default=0, ge=0)


class MarketplaceSummary(BaseModel):
    pool_count: int
    active_pool_count: int
    listing_count: int
    active_listing_count: int
    booking_count: int
    active_booking_count: int
    total_units_by_hardware: dict[str, int]
    available_units_by_hardware: dict[str, int]
    booked_units_by_hardware: dict[str, int]
    active_pairs: list[str]
    currency: str = PICO_CURRENCY


class PoolCard(BaseModel):
    pool_id: str
    hardware_type: HardwareType
    base_coin: str = PICO_CURRENCY
    paired_coin: str
    pair_symbol: str
    name: str
    algorithm: str | None = None
    picoin_capacity_percent: float
    paired_capacity_percent: float
    active_listing_count: int
    total_units: int
    available_units: int
    booked_units: int
    min_price_pi_per_hour: float | None = None
    estimated_one_hour_pi: float | None = None
    can_book: bool
    status: str
