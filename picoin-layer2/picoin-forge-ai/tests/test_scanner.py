from __future__ import annotations

import json

from picoin_forge.scanner import scan_repo


def test_scan_repo_creates_project_map(tmp_path):
    (tmp_path / "README.md").write_text("# Demo\nRun on port 8000.\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "server.py").write_text("PORT = 8000\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=do-not-read\n", encoding="utf-8")

    project_map = scan_repo(tmp_path)

    assert project_map["primary_language"] == "Python"
    assert "README.md" in project_map["important_files"]
    assert "requirements.txt" in project_map["dependency_files"]
    assert "python" in project_map["package_managers"]
    assert any(service["name"] == "api" for service in project_map["services"])
    assert ".env" in project_map["ignored_sensitive_files"]

    saved = json.loads((tmp_path / ".picoin-forge" / "project-map.json").read_text(encoding="utf-8"))
    assert saved["schema"] == "picoin-forge.project-map.v1"
