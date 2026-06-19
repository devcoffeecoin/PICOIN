from __future__ import annotations

from pathlib import Path

from .scanner import scan_repo
from .utils import forge_dir, load_project_map, repo_root, write_text_safe


def generate_systemd_services(repo_path: str | Path) -> list[Path]:
    root = repo_root(repo_path)
    project_map = load_project_map(root) or scan_repo(root)
    services = project_map.get("services") or [{"name": "app", "hints": ["generic"]}]
    output_paths: list[Path] = []
    for service in services:
        name = safe_service_name(service["name"])
        content = render_systemd(
            service_name=f"picoin-{name}",
            working_directory=str(root),
            exec_start=suggest_exec_start(name, project_map),
            description=f"Picoin Forge generated service for {name}",
        )
        path = forge_dir(root) / "systemd" / f"picoin-{name}.service"
        write_text_safe(path, content)
        output_paths.append(path)
    return output_paths


def safe_service_name(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in value.lower()).strip("-") or "app"


def suggest_exec_start(service_name: str, project_map: dict) -> str:
    managers = set(project_map.get("package_managers") or [])
    if "python" in managers:
        if service_name in {"node", "miner", "validator"}:
            return f"{project_map['repo_path']}/.venv/bin/python -m picoin {service_name}"
        return f"{project_map['repo_path']}/.venv/bin/python -m app"
    if "node" in managers:
        return "/usr/bin/npm start"
    if "go" in managers:
        return f"{project_map['repo_path']}/bin/{service_name}"
    if "rust" in managers:
        return f"{project_map['repo_path']}/target/release/{service_name}"
    return "/usr/bin/env bash -lc 'echo configure ExecStart before enabling this service'"


def render_systemd(service_name: str, working_directory: str, exec_start: str, description: str) -> str:
    return f"""[Unit]
Description={description}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=picoin
WorkingDirectory={working_directory}
ExecStart={exec_start}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
