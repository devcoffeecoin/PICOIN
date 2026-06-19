from __future__ import annotations

from picoin_forge_l2.common.models import Heartbeat


def build_heartbeat(worker_id: str) -> Heartbeat:
    return Heartbeat(worker_id=worker_id)
