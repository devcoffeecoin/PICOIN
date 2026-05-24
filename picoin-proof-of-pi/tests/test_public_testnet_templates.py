import json
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key] = value
    return values


def test_public_testnet_env_templates_pin_signature_network() -> None:
    env_files = [
        PROJECT_ROOT / ".env.miner.example",
        PROJECT_ROOT / ".env.node.example",
        PROJECT_ROOT / ".env.public-testnet.example",
        PROJECT_ROOT / ".env.validator.example",
        PROJECT_ROOT / "deploy" / "public-testnet.env.example",
    ]

    for env_file in env_files:
        values = _read_env(env_file)
        assert values["PICOIN_NETWORK"] == "public-testnet", env_file
        assert values["PICOIN_CHAIN_ID"] == "picoin-public-testnet-v018", env_file
        assert values["PICOIN_PROTOCOL_VERSION"] == "0.18", env_file


def test_requirements_include_ed25519_dependencies() -> None:
    requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8").lower().splitlines()

    assert "cryptography==44.0.0" in requirements
    assert "pynacl==1.5.0" in requirements


def test_mainnet_env_template_pins_identity_and_safety_flags() -> None:
    values = _read_env(PROJECT_ROOT / "deploy" / "mainnet.env.example")

    assert values["PICOIN_NETWORK"] == "mainnet"
    assert values["PICOIN_CHAIN_ID"] == "picoin-mainnet-v1"
    assert values["PICOIN_PROTOCOL_VERSION"] == "1.0"
    assert values["PICOIN_GENESIS_ALLOCATIONS_FILE"] == "deploy/mainnet-genesis.allocations.draft.json"
    assert values["PICOIN_REQUIRED_VALIDATOR_APPROVALS"] == "3"
    assert values["PICOIN_RETARGET_MAX_PI_POSITION"] == "1000000000000000"
    assert values["PICOIN_FAUCET_ALLOWED_NETWORKS"] == ""
    assert values["PICOIN_ALLOW_DIRECT_SCIENCE_GOVERNANCE"] == "0"
    assert values["PICOIN_ALLOW_DIRECT_TREASURY_CLAIM"] == "0"
    assert "CHANGE_ME" in values["PICOIN_TREASURY_WALLET"]
    assert "CHANGE_ME" in values["PICOIN_GOVERNANCE_WALLET"]


def test_mainnet_genesis_draft_matches_mainnet_identity_and_supply() -> None:
    genesis = json.loads(
        (PROJECT_ROOT / "deploy" / "mainnet-genesis.allocations.draft.json").read_text(encoding="utf-8")
    )
    allocations = genesis["allocations"]
    total_units = sum(int(Decimal(str(row["amount"])) * Decimal("1000000")) for row in allocations)

    assert genesis["network_id"] == "mainnet"
    assert genesis["chain_id"] == "picoin-mainnet-v1"
    assert total_units == 300_000_000
    assert any(row["account_type"] == "validator" for row in allocations)


def test_mainnet_runbook_references_preflight_and_env_source() -> None:
    runbook = (PROJECT_ROOT / "deploy" / "README-mainnet.md").read_text(encoding="utf-8")
    installer = (PROJECT_ROOT / "deploy" / "scripts" / "install-systemd-service.sh").read_text(encoding="utf-8")

    assert "PICOIN_ENV_SOURCE" in runbook
    assert "mainnet-preflight" in runbook
    assert "PICOIN_TREASURY_WALLET" in runbook
    assert "PICOIN_GOVERNANCE_WALLET" in runbook
    assert "genesis-hash" in runbook
    assert "PICOIN_ENV_SOURCE" in installer
