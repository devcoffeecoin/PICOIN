from __future__ import annotations

import json
import os
from pathlib import Path

from picoin_forge_l2.common.constants import DEFAULT_WORKER_STATE_DIR
from picoin_forge_l2.common.models import WorkerConfig, utc_now

CONFIG_FILE = "config.json"


def worker_state_dir(path: str | Path | None = None) -> Path:
    value = path or os.getenv("PICOIN_FORGE_WORKER_STATE_DIR") or DEFAULT_WORKER_STATE_DIR
    resolved = Path(value).expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def save_worker_config(
    state_dir: str | Path | None,
    *,
    wallet: str,
    coordinator_url: str = "http://127.0.0.1:9380",
    interval_seconds: float = 30.0,
    benchmark_scale: int = 1,
    request_challenges: bool = True,
) -> WorkerConfig:
    state_path = worker_state_dir(state_dir)
    existing = load_worker_config(state_path, required=False)
    config = WorkerConfig(
        wallet=wallet.strip().upper(),
        coordinator_url=coordinator_url.rstrip("/"),
        interval_seconds=max(1.0, float(interval_seconds)),
        benchmark_scale=max(1, min(int(benchmark_scale), 10)),
        request_challenges=bool(request_challenges),
        created_at=existing.created_at if existing else utc_now(),
        updated_at=utc_now(),
    )
    (state_path / CONFIG_FILE).write_text(config.model_dump_json(indent=2), encoding="utf-8")
    return config


def load_worker_config(state_dir: str | Path | None = None, *, required: bool = True) -> WorkerConfig | None:
    state_path = worker_state_dir(state_dir)
    path = state_path / CONFIG_FILE
    if not path.exists():
        if required:
            raise FileNotFoundError(f"worker config not found: {path}")
        return None
    return WorkerConfig.model_validate(json.loads(path.read_text(encoding="utf-8")))
