from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path

from picoin_forge_l2.common.crypto import simulated_public_key, worker_id_from_wallet
from picoin_forge_l2.common.models import MachineInfo, WorkerRegistration
from picoin_forge_l2.worker.gpu import detect_gpu_info, detect_ram_bytes


def detect_machine_info() -> MachineInfo:
    gpu = detect_gpu_info()
    return MachineInfo(
        hostname=platform.node() or "unknown",
        platform=platform.platform(),
        cpu_count=max(1, os.cpu_count() or 1),
        python_version=sys.version.split()[0],
        gpu_detected=gpu.detected,
        gpu_name=gpu.name,
        ram_bytes=detect_ram_bytes(),
    )


def register_worker(wallet: str, state_dir: str | Path, public_key: str | None = None) -> WorkerRegistration:
    clean_wallet = wallet.strip().upper()
    if not clean_wallet.startswith("PI"):
        raise ValueError("wallet must look like a Picoin address and start with PI")
    key = public_key or simulated_public_key(clean_wallet)
    registration = WorkerRegistration(
        worker_id=worker_id_from_wallet(clean_wallet, key),
        wallet=clean_wallet,
        public_key=key,
        machine_info=detect_machine_info(),
    )
    path = Path(state_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / "worker.json").write_text(registration.model_dump_json(indent=2), encoding="utf-8")
    return registration


def load_registration(state_dir: str | Path) -> WorkerRegistration:
    payload = json.loads((Path(state_dir) / "worker.json").read_text(encoding="utf-8"))
    return WorkerRegistration.model_validate(payload)
