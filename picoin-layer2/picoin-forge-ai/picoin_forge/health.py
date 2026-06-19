from __future__ import annotations

from pathlib import Path

from .scanner import scan_repo
from .utils import forge_dir, load_project_map, repo_root, write_text_safe


def generate_health_assets(repo_path: str | Path) -> dict[str, Path]:
    root = repo_root(repo_path)
    project_map = load_project_map(root) or scan_repo(root)
    ports = project_map.get("probable_ports") or []
    services = [service["name"] for service in project_map.get("services") or []]
    health_script = _health_script(ports, services)
    report = _health_report(project_map, ports, services)
    script_path = forge_dir(root) / "scripts" / "health_check.sh"
    report_path = forge_dir(root) / "reports" / "health-report.md"
    write_text_safe(script_path, health_script)
    write_text_safe(report_path, report)
    return {"script": script_path, "report": report_path}


def _health_script(ports: list[int], services: list[str]) -> str:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "echo 'Picoin Forge health check'",
        "echo 'Processes:'",
        "ps -eo pid,etime,pcpu,pmem,cmd | head -20",
        "",
    ]
    for port in ports:
        lines.append(f"echo 'Checking port {port}'")
        lines.append(f"(ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null || true) | grep ':{port} ' || true")
    for service in services:
        lines.append(f"systemctl status picoin-{service} --no-pager -l | head -40 || true")
    return "\n".join(lines) + "\n"


def _health_report(project_map: dict, ports: list[int], services: list[str]) -> str:
    lines = [
        "# Picoin Forge Health Report",
        "",
        "This MVP report is static. Run `.picoin-forge/scripts/health_check.sh` on Linux for live checks.",
        "",
        "## Services",
    ]
    lines.extend(f"- `{service}`" for service in services) if services else lines.append("- None detected.")
    lines.extend(["", "## Probable Ports"])
    lines.append(", ".join(str(port) for port in ports) if ports else "No probable ports detected.")
    lines.extend(["", "## Suggested Endpoints"])
    for port in ports:
        lines.append(f"- `http://127.0.0.1:{port}/health`")
    return "\n".join(lines) + "\n"
