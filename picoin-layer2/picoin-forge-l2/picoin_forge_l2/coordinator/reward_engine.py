from __future__ import annotations

from picoin_forge_l2.common.models import EpochReward, WorkerState


def calculate_epoch_rewards(worker_states: list[WorkerState], epoch_reward: float) -> list[EpochReward]:
    total = sum(max(0.0, state.verified_compute_score) for state in worker_states)
    if total <= 0:
        return [
            EpochReward(
                worker_id=state.registration.worker_id,
                wallet=state.registration.wallet,
                verified_compute_score=round(state.verified_compute_score, 8),
                reward_pi=0.0,
            )
            for state in worker_states
        ]
    rewards: list[EpochReward] = []
    running_total = 0.0
    for index, state in enumerate(worker_states):
        share = state.verified_compute_score / total
        reward = round(epoch_reward * share, 8)
        if index == len(worker_states) - 1:
            reward = round(epoch_reward - running_total, 8)
        running_total = round(running_total + reward, 8)
        rewards.append(
            EpochReward(
                worker_id=state.registration.worker_id,
                wallet=state.registration.wallet,
                verified_compute_score=round(state.verified_compute_score, 8),
                reward_pi=max(0.0, reward),
            )
        )
    return rewards
