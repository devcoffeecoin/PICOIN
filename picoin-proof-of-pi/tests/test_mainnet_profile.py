import json
import os
import subprocess
import sys


MAINNET_TREASURY_WALLET = "PIEB149E99DCD64653088B68F92D6790068428462919DD96"
MAINNET_GOVERNANCE_WALLET = "PI6D17B68D576E0543CD5814D39F09FB58E3D35CAD355BB1"


def _mainnet_wallet_env() -> dict[str, str]:
    return {
        "PICOIN_TREASURY_WALLET": MAINNET_TREASURY_WALLET,
        "PICOIN_GOVERNANCE_WALLET": MAINNET_GOVERNANCE_WALLET,
    }


def _run_isolated(code: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy()
    run_env.update(env)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=run_env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_mainnet_profile_freezes_launch_parameters(tmp_path) -> None:
    db_path = tmp_path / "mainnet-profile.sqlite3"
    code = """
import json
from app.core import settings
from app.core.signatures import generate_keypair, sign_payload
from app.db.database import get_connection, init_db
from app.services.mining import (
    MiningError,
    get_balance,
    get_full_economic_audit,
    get_protocol,
    get_validators,
    get_validators_status,
    record_validator_heartbeat,
    register_validator,
    request_faucet,
)

init_db()
validator_keys = generate_keypair()
validator = register_validator("mainnet-validator", validator_keys["public_key"])
heartbeat = {
    "validator_id": validator["validator_id"],
    "node_id": "mainnet-validator-node",
    "public_key": validator_keys["public_key"],
    "address": "http://mainnet-validator.node:8000",
    "local_height": 1,
    "effective_height": 1,
    "latest_block_hash": "a" * 64,
    "pending_replay_blocks": 0,
    "sync_lag": 0,
    "version": settings.PROTOCOL_VERSION,
}
heartbeat["signature"] = sign_payload(validator_keys["private_key"], heartbeat)
record_validator_heartbeat(heartbeat)
eligible_without_stake = len(get_validators(eligible_only=True))
with get_connection() as connection:
    connection.execute(
        "UPDATE validators SET stake_locked = ?, wallet_stake_locked = 0 WHERE validator_id = ?",
        (settings.MIN_VALIDATOR_STAKE, validator["validator_id"]),
    )
eligible_with_legacy_only = len(get_validators(eligible_only=True))
legacy_audit_issue_codes = [issue["code"] for issue in get_full_economic_audit()["issues"]]
legacy_status = next(
    item for item in get_validators_status()["validators"] if item["validator_id"] == validator["validator_id"]
)
with get_connection() as connection:
    connection.execute(
        "UPDATE validators SET stake_locked = ?, wallet_stake_locked = ?, stake_owner_address = ? WHERE validator_id = ?",
        (
            settings.MIN_VALIDATOR_STAKE,
            settings.MIN_VALIDATOR_STAKE,
            "PI_MAINNET_STAKER",
            validator["validator_id"],
        ),
    )
eligible_with_wallet_stake = get_validators(eligible_only=True)
faucet_error = None
try:
    request_faucet("PI_MAINNET_TEST", "wallet", 1.0)
except MiningError as exc:
    faucet_error = {"status_code": exc.status_code, "detail": exc.detail}

payload = {
    "network_id": settings.NETWORK_ID,
    "chain_id": settings.CHAIN_ID,
    "protocol_version": settings.PROTOCOL_VERSION,
    "genesis_supply": settings.GENESIS_SUPPLY,
    "genesis_balance": get_balance("genesis")["balance"],
    "faucet_allowed_networks": sorted(settings.FAUCET_ALLOWED_NETWORKS),
    "protocol": get_protocol(),
    "validator_id": validator["validator_id"],
    "validator_registration_stake": validator["stake_locked"],
    "validator_wallet_stake": validator["wallet_stake_locked"],
    "eligible_without_stake": eligible_without_stake,
    "eligible_with_legacy_only": eligible_with_legacy_only,
    "legacy_audit_issue_codes": legacy_audit_issue_codes,
    "legacy_only_reason": legacy_status["reason_if_not_eligible"],
    "legacy_only_eligibility_stake_source": legacy_status["eligibility_stake_source"],
    "eligible_with_wallet_stake": [item["validator_id"] for item in eligible_with_wallet_stake],
    "faucet_error": faucet_error,
}
print(json.dumps(payload, sort_keys=True))
"""
    result = _run_isolated(
        code,
        {
            "PICOIN_NETWORK": "mainnet",
            "PICOIN_DB_PATH": str(db_path),
            "PICOIN_DATA_DIR": str(tmp_path),
            **_mainnet_wallet_env(),
        },
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["network_id"] == "mainnet"
    assert payload["chain_id"] == "picoin-mainnet-v1"
    assert payload["protocol_version"] == "1.0"
    assert payload["genesis_supply"] == 300.0
    assert payload["genesis_balance"] == 300.0
    assert payload["faucet_allowed_networks"] == []
    assert payload["protocol"]["network_id"] == "mainnet"
    assert payload["protocol"]["chain_id"] == "picoin-mainnet-v1"
    assert payload["protocol"]["protocol_version"] == "1.0"
    assert payload["protocol"]["faucet_enabled"] is False
    assert payload["protocol"]["required_validator_approvals"] == 3
    assert payload["protocol"]["RETARGET_MAX_PI_POSITION"] == 10**15
    assert payload["protocol"]["reward_per_block"] == 3.1416
    assert payload["protocol"]["validator_eligibility_stake_field"] == "wallet_stake_locked"
    assert payload["protocol"]["validator_eligibility_stake_source"] == "wallet"
    assert payload["protocol"]["scientific_development_treasury_wallet"] == MAINNET_TREASURY_WALLET
    assert payload["protocol"]["scientific_development_governance_wallet"] == MAINNET_GOVERNANCE_WALLET
    assert payload["validator_registration_stake"] == 0.0
    assert payload["validator_wallet_stake"] == 0.0
    assert payload["eligible_without_stake"] == 0
    assert payload["eligible_with_legacy_only"] == 0
    assert "mainnet_legacy_validator_stake" in payload["legacy_audit_issue_codes"]
    assert payload["legacy_only_reason"] == "validator wallet-backed stake is below the minimum required"
    assert payload["legacy_only_eligibility_stake_source"] == "wallet"
    assert payload["eligible_with_wallet_stake"] == [payload["validator_id"]]
    assert payload["faucet_error"]["status_code"] == 403


def test_mainnet_rejects_chain_id_override() -> None:
    result = _run_isolated(
        "import app.core.settings",
        {
            "PICOIN_NETWORK": "mainnet",
            "PICOIN_CHAIN_ID": "picoin-public-testnet-v018",
        },
    )

    assert result.returncode != 0
    assert "mainnet chain_id is frozen" in result.stderr


def test_mainnet_rejects_protocol_version_override() -> None:
    result = _run_isolated(
        "import app.core.settings",
        {
            "PICOIN_NETWORK": "mainnet",
            "PICOIN_PROTOCOL_VERSION": "0.18",
        },
    )

    assert result.returncode != 0
    assert "mainnet protocol_version is frozen" in result.stderr


def test_mainnet_rejects_faucet_enablement() -> None:
    result = _run_isolated(
        "import app.core.settings",
        {
            "PICOIN_NETWORK": "mainnet",
            "PICOIN_FAUCET_ALLOWED_NETWORKS": "mainnet",
            **_mainnet_wallet_env(),
        },
    )

    assert result.returncode != 0
    assert "mainnet faucet is frozen off" in result.stderr


def test_mainnet_rejects_validator_quorum_override() -> None:
    result = _run_isolated(
        "import app.core.settings",
        {
            "PICOIN_NETWORK": "mainnet",
            "PICOIN_REQUIRED_VALIDATOR_APPROVALS": "2",
        },
    )

    assert result.returncode != 0
    assert "mainnet validator quorum is frozen" in result.stderr


def test_mainnet_rejects_RETARGET_MAX_PI_POSITION_override() -> None:
    result = _run_isolated(
        "import app.core.settings",
        {
            "PICOIN_NETWORK": "mainnet",
            "PICOIN_RETARGET_MAX_PI_POSITION": "1000000",
            **_mainnet_wallet_env(),
        },
    )

    assert result.returncode != 0
    assert "mainnet RETARGET_MAX_PI_POSITION is frozen" in result.stderr


def test_mainnet_requires_treasury_and_governance_wallets() -> None:
    result = _run_isolated(
        "import app.core.settings",
        {
            "PICOIN_NETWORK": "mainnet",
        },
    )

    assert result.returncode != 0
    assert "mainnet PICOIN_TREASURY_WALLET is required" in result.stderr


def test_mainnet_rejects_treasury_governance_placeholders() -> None:
    result = _run_isolated(
        "import app.core.settings",
        {
            "PICOIN_NETWORK": "mainnet",
            "PICOIN_TREASURY_WALLET": "picoin_scientific_development_wallet",
            "PICOIN_GOVERNANCE_WALLET": MAINNET_GOVERNANCE_WALLET,
        },
    )

    assert result.returncode != 0
    assert "mainnet PICOIN_TREASURY_WALLET must not use a placeholder" in result.stderr


def test_mainnet_rejects_non_canonical_treasury_wallet() -> None:
    result = _run_isolated(
        "import app.core.settings",
        {
            "PICOIN_NETWORK": "mainnet",
            "PICOIN_TREASURY_WALLET": "PI123",
            "PICOIN_GOVERNANCE_WALLET": MAINNET_GOVERNANCE_WALLET,
        },
    )

    assert result.returncode != 0
    assert "mainnet PICOIN_TREASURY_WALLET must be a canonical Picoin wallet address" in result.stderr


def test_mainnet_rejects_same_treasury_and_governance_wallet() -> None:
    result = _run_isolated(
        "import app.core.settings",
        {
            "PICOIN_NETWORK": "mainnet",
            "PICOIN_TREASURY_WALLET": MAINNET_TREASURY_WALLET,
            "PICOIN_GOVERNANCE_WALLET": MAINNET_TREASURY_WALLET,
        },
    )

    assert result.returncode != 0
    assert "mainnet treasury and governance wallets must be distinct" in result.stderr


def test_public_testnet_defaults_to_two_validator_approvals(tmp_path) -> None:
    db_path = tmp_path / "public-profile.sqlite3"
    code = """
import json
from app.core import settings
from app.db.database import init_db
from app.services.mining import get_protocol

init_db()
print(json.dumps({
    "required_validator_approvals": settings.REQUIRED_VALIDATOR_APPROVALS,
    "RETARGET_MAX_PI_POSITION": settings.RETARGET_MAX_PI_POSITION,
    "protocol_required_validator_approvals": get_protocol()["required_validator_approvals"],
    "protocol_RETARGET_MAX_PI_POSITION": get_protocol()["RETARGET_MAX_PI_POSITION"],
}))
"""
    result = _run_isolated(
        code,
        {
            "PICOIN_NETWORK": "public-testnet",
            "PICOIN_DB_PATH": str(db_path),
            "PICOIN_DATA_DIR": str(tmp_path),
        },
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["required_validator_approvals"] == 2
    assert payload["RETARGET_MAX_PI_POSITION"] == 1_000_000
    assert payload["protocol_required_validator_approvals"] == 2
    assert payload["protocol_RETARGET_MAX_PI_POSITION"] == 1_000_000


def test_public_testnet_allows_validator_quorum_override(tmp_path) -> None:
    db_path = tmp_path / "public-profile-override.sqlite3"
    code = """
import json
from app.core import settings
from app.db.database import init_db
from app.services.mining import get_protocol

init_db()
print(json.dumps({
    "required_validator_approvals": settings.REQUIRED_VALIDATOR_APPROVALS,
    "protocol_required_validator_approvals": get_protocol()["required_validator_approvals"],
}))
"""
    result = _run_isolated(
        code,
        {
            "PICOIN_NETWORK": "public-testnet",
            "PICOIN_REQUIRED_VALIDATOR_APPROVALS": "3",
            "PICOIN_DB_PATH": str(db_path),
            "PICOIN_DATA_DIR": str(tmp_path),
        },
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["required_validator_approvals"] == 3
    assert payload["protocol_required_validator_approvals"] == 3
