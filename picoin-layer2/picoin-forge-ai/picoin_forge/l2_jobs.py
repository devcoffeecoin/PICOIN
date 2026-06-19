from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .utils import forge_dir, hash_repo_tree, read_json, repo_root, sha256_text, utc_now, write_json_safe


def create_l2_job(
    repo_path: str | Path,
    *,
    job_type: str = "setup_node",
    repo_url: str = "",
    reward: str = "50 PI",
    requirements: list[str] | None = None,
) -> dict[str, Any]:
    root = repo_root(repo_path)
    repo_hash = hash_repo_tree(root)
    created_at = utc_now()
    job_id = "job_" + sha256_text(f"{repo_hash}:{job_type}:{created_at}")[:16]
    job = {
        "schema": "picoin-forge.l2-job.v1",
        "job_id": job_id,
        "job_type": job_type,
        "repo_url": repo_url,
        "repo_hash": repo_hash,
        "reward": reward,
        "requirements": requirements or ["linux", "python", "systemd", "crypto"],
        "status": "created",
        "created_at": created_at,
    }
    write_json_safe(forge_dir(root) / "l2-jobs" / f"{job_id}.json", job)
    return job


def verify_l2_job(repo_path: str | Path, job_id: str | None = None) -> dict[str, Any]:
    root = repo_root(repo_path)
    jobs_dir = forge_dir(root) / "l2-jobs"
    job_path = _resolve_job_path(jobs_dir, job_id)
    job = read_json(job_path, default={})
    required = [
        forge_dir(root) / "project-map.json",
        forge_dir(root) / "scripts" / "install.sh",
        forge_dir(root) / "reports" / "health-report.md",
    ]
    evidence = {str(path.relative_to(forge_dir(root))): path.exists() for path in required}
    result_hash = sha256_text(
        json.dumps(
            {
                "job": job,
                "repo_hash": hash_repo_tree(root),
                "evidence": evidence,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    job.update(
        {
            "status": "verified" if all(evidence.values()) else "needs_evidence",
            "verified_at": utc_now(),
            "result_hash": result_hash,
            "evidence": evidence,
        }
    )
    write_json_safe(job_path, job)
    return job


def _resolve_job_path(jobs_dir: Path, job_id: str | None) -> Path:
    if job_id:
        return jobs_dir / f"{job_id}.json"
    candidates = sorted(jobs_dir.glob("job_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError("no L2 jobs found; run `picoin-forge l2-job create` first")
    return candidates[0]
