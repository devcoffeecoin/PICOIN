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
