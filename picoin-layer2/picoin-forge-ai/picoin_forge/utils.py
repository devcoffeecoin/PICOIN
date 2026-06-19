from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

FORGE_DIR_NAME = ".picoin-forge"
SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "wallet.json",
    "wallet.key",
    "private.key",
    "seed.txt",
    "mnemonic.txt",
}
SENSITIVE_PARTS = {
    "private",
    "secret",
    "secrets",
    "seed",
    "mnemonic",
    "wallet",
    "keys",
    "identity",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def repo_root(repo_path: str | Path) -> Path:
    root = Path(repo_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"repo_path does not exist or is not a directory: {root}")
    return root


def forge_dir(repo_path: str | Path) -> Path:
    root = repo_root(repo_path)
    path = root / FORGE_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_text_safe(path: Path, content: str, *, backup: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and backup:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        shutil.copy2(path, path.with_name(f"{path.name}.bak-{stamp}"))
    path.write_text(content, encoding="utf-8")
    return path


def write_json_safe(path: Path, payload: dict[str, Any], *, backup: bool = True) -> Path:
    return write_text_safe(path, json.dumps(payload, indent=2, sort_keys=True) + "\n", backup=backup)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def is_sensitive_path(path: Path) -> bool:
    lower_parts = [part.lower() for part in path.parts]
    if path.name.lower() in SENSITIVE_NAMES:
        return True
    return any(part in SENSITIVE_PARTS for part in lower_parts)


def iter_repo_files(repo_path: str | Path, *, max_files: int = 5000) -> Iterable[Path]:
    root = repo_root(repo_path)
    skipped_dirs = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".picoin-forge",
        "target",
        "dist",
        "build",
    }
    yielded = 0
    for current_root, dirs, files in os.walk(root):
        current = Path(current_root)
        dirs[:] = [d for d in dirs if d not in skipped_dirs and not is_sensitive_path(current / d)]
        for file_name in files:
            path = current / file_name
            if is_sensitive_path(path):
                continue
            yielded += 1
            if yielded > max_files:
                return
            yield path


def relative_to_repo(path: Path, repo_path: str | Path) -> str:
    return path.resolve().relative_to(repo_root(repo_path)).as_posix()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_repo_tree(repo_path: str | Path) -> str:
    root = repo_root(repo_path)
    digest = hashlib.sha256()
    for path in sorted(iter_repo_files(root), key=lambda p: relative_to_repo(p, root)):
        rel = relative_to_repo(path, root)
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hash_file(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def load_project_map(repo_path: str | Path) -> dict[str, Any] | None:
    return read_json(forge_dir(repo_path) / "project-map.json", default=None)


def ignored_sensitive_report(repo_path: str | Path) -> list[str]:
    root = repo_root(repo_path)
    ignored: list[str] = []
    for current_root, dirs, files in os.walk(root):
        current = Path(current_root)
        for name in list(dirs) + list(files):
            path = current / name
            if is_sensitive_path(path):
                ignored.append(relative_to_repo(path, root))
    return sorted(set(ignored))
