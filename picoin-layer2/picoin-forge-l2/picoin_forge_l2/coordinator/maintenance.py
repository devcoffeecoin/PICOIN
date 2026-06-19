from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable

from .challenge_engine import ChallengeEngine
from .worker_registry import WorkerRegistry


def expire_challenges_once(state_dir: str | Path) -> dict:
    registry = WorkerRegistry(state_dir)
    engine = ChallengeEngine(state_dir, registry)
    expired = engine.expire_open_challenges()
    return {
        "expired": len(expired),
        "challenge_ids": [challenge.challenge_id for challenge in expired],
    }


async def challenge_expiration_loop(
    state_dir: str | Path,
    *,
    interval_seconds: float,
    on_error: Callable[[Exception], None] | None = None,
) -> None:
    safe_interval = max(float(interval_seconds), 1.0)
    while True:
        try:
            expire_challenges_once(state_dir)
        except Exception as exc:  # pragma: no cover - defensive background path.
            if on_error is not None:
                on_error(exc)
        await asyncio.sleep(safe_interval)
