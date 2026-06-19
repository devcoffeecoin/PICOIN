from __future__ import annotations

from picoin_forge.health import generate_health_assets
from picoin_forge.installer import generate_install_plan
from picoin_forge.l2_jobs import create_l2_job, verify_l2_job
from picoin_forge.scanner import scan_repo


def test_l2_job_create_and_verify(tmp_path):
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("pytest\n", encoding="utf-8")

    scan_repo(tmp_path)
    generate_install_plan(tmp_path)
    generate_health_assets(tmp_path)

    job = create_l2_job(tmp_path, repo_url="https://github.com/devcoffeecoin/PICOIN")
    verified = verify_l2_job(tmp_path, job_id=job["job_id"])

    assert verified["status"] == "verified"
    assert verified["result_hash"]
    assert all(verified["evidence"].values())
