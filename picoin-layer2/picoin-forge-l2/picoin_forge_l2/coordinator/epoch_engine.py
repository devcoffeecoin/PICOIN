from __future__ import annotations

import json
from pathlib import Path

from picoin_forge_l2.common.constants import DEFAULT_EPOCH_REWARD_PI
from picoin_forge_l2.common.models import EpochSettlement

from .reward_engine import calculate_epoch_rewards
from .score_engine import refresh_worker_score
from .settlement import build_settlement, write_settlement
from .storage import CoordinatorStorage
from .worker_registry import WorkerRegistry


class EpochEngine:
    def __init__(self, state_dir: str | Path, registry: WorkerRegistry):
        self.state_dir = Path(state_dir)
        self.registry = registry
        self.storage = CoordinatorStorage(self.state_dir)

    def close_epoch(self, epoch_reward: float = DEFAULT_EPOCH_REWARD_PI) -> EpochSettlement:
        with self.storage.connect() as connection:
            row = connection.execute("SELECT current_epoch FROM epoch_state WHERE id = 1").fetchone()
            epoch_id = int(row["current_epoch"] if row else 0) + 1
        workers = []
        self.registry.penalize_missed_heartbeats()
        for state in self.registry.all():
            refreshed = refresh_worker_score(state)
            self.registry.put(refreshed)
            workers.append(refreshed)
        rewards = calculate_epoch_rewards(workers, epoch_reward)
        settlement = build_settlement(epoch_id, epoch_reward, rewards)
        write_settlement(self.state_dir, settlement)
        with self.storage.connect() as connection:
            connection.execute("UPDATE epoch_state SET current_epoch = ? WHERE id = 1", (epoch_id,))
        self.storage.record_event(
            "epoch.closed",
            str(epoch_id),
            {
                "epoch_reward": epoch_reward,
                "total_verified_compute": settlement.total_verified_compute,
                "result_hash": settlement.result_hash,
                "worker_count": len(settlement.workers),
            },
        )
        return settlement
