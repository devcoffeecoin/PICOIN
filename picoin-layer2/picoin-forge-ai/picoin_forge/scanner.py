from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from .utils import (
    forge_dir,
    ignored_sensitive_report,
    iter_repo_files,
    relative_to_repo,
    repo_root,
    utc_now,
    write_json_safe,
    write_text_safe,
)

LANGUAGE_EXTENSIONS = {
    ".py": "Python",
    ".js": "Node/JavaScript",
    ".mjs": "Node/JavaScript",
    ".cjs": "Node/JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".sol": "Solidity",
    ".html": "Web",
    ".css": "Web",
}

IMPORTANT_FILES = {
    "README.md",
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "Dockerfile",
    "docker-compose.yml",
    ".env.example",
    "go.mod",
    "Cargo.toml",
    "nginx.conf",
}

SERVICE_HINTS = {
    "api": ["api", "server", "fastapi", "flask", "express"],
    "node": ["node", "chain", "consensus"],
    "miner": ["miner", "mining"],
    "validator": ["validator", "validation"],
    "explorer": ["explorer", "web"],
    "wallet": ["wallet"],
}

PORT_RE = re.compile(r"(?i)(?:port|listen|localhost|127\.0\.0\.1|0\.0\.0\.0)[^0-9]{0,16}([1-9][0-9]{2,4})")


def scan_repo(repo_path: str | Path) -> dict[str, Any]:
    root = repo_root(repo_path)
    files = list(iter_repo_files(root))
    rel_files = [relative_to_repo(path, root) for path in files]
    language_counts = _detect_languages(files)
    services = _detect_services(rel_files)
    project_map = {
        "schema": "picoin-forge.project-map.v1",
        "repo_path": str(root),
        "generated_at": utc_now(),
        "file_count": len(files),
        "primary_language": _primary_language(language_counts),
        "languages": dict(language_counts),
        "important_files": _important_files(root, rel_files),
        "dependency_files": _dependency_files(rel_files),
        "service_files": _service_files(rel_files),
        "services": services,
        "probable_ports": _detect_ports(root, files),
        "package_managers": _package_managers(rel_files),
        "ignored_sensitive_files": ignored_sensitive_report(root),
    }
    out_dir = forge_dir(root)
    write_json_safe(out_dir / "project-map.json", project_map)
    write_text_safe(
        out_dir / "ignored-sensitive-files.txt",
        "\n".join(project_map["ignored_sensitive_files"]) + ("\n" if project_map["ignored_sensitive_files"] else ""),
    )
    return project_map


def _detect_languages(files: list[Path]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for path in files:
        language = LANGUAGE_EXTENSIONS.get(path.suffix.lower())
        if language:
            counts[language] += 1
    return counts


def _primary_language(language_counts: Counter[str]) -> str | None:
    if not language_counts:
        return None
    return language_counts.most_common(1)[0][0]


def _important_files(root: Path, rel_files: list[str]) -> list[str]:
    lower_lookup = {Path(rel).name.lower(): rel for rel in rel_files}
    found = []
    for file_name in IMPORTANT_FILES:
        rel = lower_lookup.get(file_name.lower())
        if rel:
            found.append(rel)
    found.extend(rel for rel in rel_files if rel.endswith(".service"))
    return sorted(set(found))


def _dependency_files(rel_files: list[str]) -> list[str]:
    wanted = {"pyproject.toml", "requirements.txt", "package.json", "go.mod", "Cargo.toml"}
    return sorted(rel for rel in rel_files if Path(rel).name in wanted)


def _service_files(rel_files: list[str]) -> list[str]:
    return sorted(
        rel
        for rel in rel_files
        if rel.endswith(".service") or "systemd" in rel.lower() or "nginx" in rel.lower()
    )


def _detect_services(rel_files: list[str]) -> list[dict[str, Any]]:
    services: list[dict[str, Any]] = []
    joined = "\n".join(rel_files).lower()
    for service, hints in SERVICE_HINTS.items():
        matches = [hint for hint in hints if hint in joined]
        if matches:
            services.append({"name": service, "confidence": min(1.0, 0.35 + 0.2 * len(matches)), "hints": matches})
    return services


def _detect_ports(root: Path, files: list[Path]) -> list[int]:
    ports: set[int] = set()
    readable_names = {"README.md", "package.json", "pyproject.toml", "docker-compose.yml", ".env.example"}
    for path in files:
        if path.name not in readable_names and path.suffix.lower() not in {".py", ".js", ".ts", ".yml", ".yaml", ".toml"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")[:100_000]
        except OSError:
            continue
        for match in PORT_RE.findall(text):
            port = int(match)
            if 1 <= port <= 65535:
                ports.add(port)
    return sorted(ports)


def _package_managers(rel_files: list[str]) -> list[str]:
    managers = []
    names = {Path(rel).name for rel in rel_files}
    if "requirements.txt" in names or "pyproject.toml" in names:
        managers.append("python")
    if "package.json" in names:
        managers.append("node")
    if "go.mod" in names:
        managers.append("go")
    if "Cargo.toml" in names:
        managers.append("rust")
    return managers
