from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path

from picoin_forge_l2.common.crypto import generate_ed25519_private_key, public_key_from_private_key, worker_id_from_wallet
from picoin_forge_l2.common.models import MachineInfo, WorkerRegistration, utc_now
from picoin_forge_l2.worker.ai_model import detect_ai_model_profile
from picoin_forge_l2.worker.gpu import detect_gpu_info, detect_ram_bytes

WORKER_KEY_FILE = "worker-key.json"
PREVIOUS_WORKER_KEY_FILE = "worker-key.previous.json"


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
    path = Path(state_dir)
    path.mkdir(parents=True, exist_ok=True)
    private_key = load_or_create_private_key(path)
    key = public_key or public_key_from_private_key(private_key)
    registration = WorkerRegistration(
        worker_id=worker_id_from_wallet(clean_wallet, key),
        wallet=clean_wallet,
        public_key=key,
        machine_info=detect_machine_info(),
        ai_model_profile=detect_ai_model_profile(),
    )
    (path / "worker.json").write_text(registration.model_dump_json(indent=2), encoding="utf-8")
    return registration


def rotate_worker_key(state_dir: str | Path) -> WorkerRegistration:
    path = Path(state_dir)
    existing = load_registration(path)
    key_path = path / WORKER_KEY_FILE
    previous_key = load_private_key(path)
    if previous_key:
        previous_payload = {
            "private_key": previous_key,
            "public_key": existing.public_key,
            "worker_id": existing.worker_id,
            "rotated_at": utc_now().isoformat(),
        }
        (path / PREVIOUS_WORKER_KEY_FILE).write_text(json.dumps(previous_payload, indent=2) + "\n", encoding="utf-8")

    private_key = generate_ed25519_private_key()
    public_key = public_key_from_private_key(private_key)
    key_path.write_text(json.dumps({"private_key": private_key}, indent=2) + "\n", encoding="utf-8")
    registration = WorkerRegistration(
        worker_id=existing.worker_id,
        wallet=existing.wallet,
        public_key=public_key,
        machine_info=detect_machine_info(),
        ai_model_profile=detect_ai_model_profile(),
        status=existing.status,
        registered_at=existing.registered_at,
    )
    (path / "worker.json").write_text(registration.model_dump_json(indent=2), encoding="utf-8")
    return registration


def load_registration(state_dir: str | Path) -> WorkerRegistration:
    payload = json.loads((Path(state_dir) / "worker.json").read_text(encoding="utf-8"))
    return WorkerRegistration.model_validate(payload)


def load_or_create_private_key(state_dir: str | Path) -> str:
    path = Path(state_dir)
    path.mkdir(parents=True, exist_ok=True)
    key_path = path / WORKER_KEY_FILE
    if key_path.exists():
        return json.loads(key_path.read_text(encoding="utf-8"))["private_key"]
    private_key = generate_ed25519_private_key()
    key_path.write_text(json.dumps({"private_key": private_key}, indent=2) + "\n", encoding="utf-8")
    return private_key


def load_private_key(state_dir: str | Path) -> str | None:
    key_path = Path(state_dir) / WORKER_KEY_FILE
    if not key_path.exists():
        return None
    return json.loads(key_path.read_text(encoding="utf-8"))["private_key"]
