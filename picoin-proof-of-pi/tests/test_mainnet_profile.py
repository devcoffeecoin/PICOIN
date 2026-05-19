import json
import os
import subprocess
import sys


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
from app.db.database import init_db
from app.services.mining import MiningError, get_balance, get_protocol, request_faucet

init_db()
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
    assert payload["protocol"]["reward_per_block"] == 3.1416
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
        },
    )

    assert result.returncode != 0
    assert "mainnet faucet is frozen off" in result.stderr
