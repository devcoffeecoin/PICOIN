from __future__ import annotations

from pathlib import Path
from typing import Any

from .scanner import scan_repo
from .utils import forge_dir, load_project_map, repo_root, write_text_safe


def explain_repo(repo_path: str | Path) -> str:
    root = repo_root(repo_path)
    project_map = load_project_map(root) or scan_repo(root)
    lines = [
        "# Picoin Forge Architecture Explanation",
        "",
        f"Repository: `{project_map['repo_path']}`",
        f"Primary language: `{project_map.get('primary_language') or 'unknown'}`",
        "",
        "## Services",
    ]
    services = project_map.get("services") or []
    if services:
        for service in services:
            hints = ", ".join(service.get("hints") or [])
            lines.append(f"- `{service['name']}` confidence={service.get('confidence')} hints={hints}")
    else:
        lines.append("- No obvious long-running services detected.")
    lines.extend(["", "## Dependencies"])
    for dep in project_map.get("dependency_files") or []:
        lines.append(f"- `{dep}`")
    if not project_map.get("dependency_files"):
        lines.append("- No dependency files detected.")
    lines.extend(["", "## Probable Ports"])
    ports = project_map.get("probable_ports") or []
    lines.append(", ".join(str(port) for port in ports) if ports else "No probable ports detected.")
    lines.extend(["", "## Install And Run Signals"])
    for manager in project_map.get("package_managers") or []:
        lines.append(f"- `{manager}` project detected.")
    lines.extend(["", "## Important Files"])
    for item in project_map.get("important_files") or []:
        lines.append(f"- `{item}`")
    report = "\n".join(lines) + "\n"
    write_text_safe(forge_dir(root) / "reports" / "architecture.md", report)
    return report
