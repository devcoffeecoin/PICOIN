from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"


def test_deploy_env_example_contains_required_keys():
    env_text = (DEPLOY / "picoin-marketplace.env.example").read_text(encoding="utf-8")

    required = [
        "PICOIN_MARKETPLACE_STATE_DIR=",
        "PICOIN_MARKETPLACE_ESCROW_ADDRESS=",
        "PICOIN_MARKETPLACE_EVM_ESCROW_ADDRESS=",
        "PICOIN_MARKETPLACE_PICOIN_NODE_URL=",
        "PICOIN_MARKETPLACE_EVM_RPC_URL=",
        "PICOIN_MARKETPLACE_SCANNER_PICOIN_ENABLED=",
        "PICOIN_MARKETPLACE_SCANNER_EVM_TOKENS_ENABLED=",
        "PICOIN_MARKETPLACE_SCANNER_EVM_NATIVE_ENABLED=",
        "PICOIN_MARKETPLACE_MAINTENANCE_INTERVAL_SECONDS=",
        "PICOIN_MARKETPLACE_WORKER_STALE_AFTER_SECONDS=",
    ]
    for key in required:
        assert key in env_text


def test_systemd_units_use_expected_commands():
    api_unit = (DEPLOY / "picoin-marketplace.service").read_text(encoding="utf-8")
    scanner_unit = (DEPLOY / "picoin-marketplace-scanner.service").read_text(encoding="utf-8")
    maintenance_unit = (DEPLOY / "picoin-marketplace-maintenance.service").read_text(encoding="utf-8")

    assert "EnvironmentFile=/etc/picoin-marketplace/picoin-marketplace.env" in api_unit
    assert "ExecStart=/opt/picoin-marketplace/.venv/bin/picoin-marketplace" in api_unit
    assert "ExecStart=/opt/picoin-marketplace/.venv/bin/picoin-marketplace-scanner" in scanner_unit
    assert "ExecStart=/opt/picoin-marketplace/.venv/bin/picoin-marketplace-maintenance" in maintenance_unit
    assert "Restart=always" in scanner_unit


def test_install_script_installs_services():
    script = (DEPLOY / "install-marketplace.sh").read_text(encoding="utf-8")

    assert "systemctl daemon-reload" in script
    assert "systemctl enable picoin-marketplace.service picoin-marketplace-scanner.service picoin-marketplace-maintenance.service" in script
    assert "pip\" install -e" in script
    assert "picoin-marketplace.env.example" in script
