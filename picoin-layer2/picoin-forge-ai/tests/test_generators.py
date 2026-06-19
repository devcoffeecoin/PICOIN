from __future__ import annotations

from picoin_forge.analyzer import explain_repo
from picoin_forge.fixer import generate_fix_suggestions
from picoin_forge.health import generate_health_assets
from picoin_forge.installer import generate_install_plan
from picoin_forge.service_generator import generate_systemd_services


def test_generators_create_expected_files(tmp_path):
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (tmp_path / "validator").mkdir()
    (tmp_path / "validator" / "worker.py").write_text("print('validator')\n", encoding="utf-8")

    install_path = generate_install_plan(tmp_path)
    service_paths = generate_systemd_services(tmp_path)
    health_paths = generate_health_assets(tmp_path)
    explanation = explain_repo(tmp_path)

    assert install_path.exists()
    assert "pip install -e ." in install_path.read_text(encoding="utf-8")
    assert service_paths
    assert all(path.exists() for path in service_paths)
    assert health_paths["script"].exists()
    assert health_paths["report"].exists()
    assert "Primary language" in explanation


def test_fix_suggestions_reads_logs(tmp_path):
    logs = tmp_path / ".picoin-forge" / "logs"
    logs.mkdir(parents=True)
    (logs / "node.log").write_text("OperationalError: database is locked\n", encoding="utf-8")

    report = generate_fix_suggestions(tmp_path)

    assert "database is locked" in report.read_text(encoding="utf-8")
