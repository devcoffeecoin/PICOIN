from __future__ import annotations

from pathlib import Path

from .utils import forge_dir, repo_root, write_text_safe

PATTERNS = {
    "permission denied": "Check file ownership, service user, and state directory permissions.",
    "database is locked": "Stop competing workers or add retry/busy-timeout around SQLite writes.",
    "connection refused": "Confirm the target service is running and listening on the configured port.",
    "timeout": "Increase timeout, check network latency, or reduce batch sizes.",
    "traceback": "Review the Python stack trace and add a focused regression test before changing code.",
    "unauthorized": "Check signatures, keys, nonces, and canonical payload generation.",
    "forbidden": "Check liveness, eligibility, permissions, or validator/miner identity state.",
}


def generate_fix_suggestions(repo_path: str | Path) -> Path:
    root = repo_root(repo_path)
    logs_dir = forge_dir(root) / "logs"
    suggestions: list[str] = []
    for path in sorted(logs_dir.glob("**/*")) if logs_dir.exists() else []:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for pattern, suggestion in PATTERNS.items():
            if pattern in text:
                suggestions.append(f"- `{path.name}` matched `{pattern}`: {suggestion}")
    if not suggestions:
        suggestions.append("- No known error patterns found in `.picoin-forge/logs/`.")
    report = "# Picoin Forge Fix Suggestions\n\n" + "\n".join(sorted(set(suggestions))) + "\n"
    path = forge_dir(root) / "reports" / "fix-suggestions.md"
    write_text_safe(path, report)
    return path
