from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

from picoin_forge_l2.common.hashing import sha256_text


@dataclass(frozen=True)
class GPUInfo:
    detected: bool
    name: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class GPUWorkloadProof:
    verified: bool
    result_hash: str
    backend: str
    device_name: str | None = None
    reason: str | None = None


def detect_gpu_info() -> GPUInfo:
    """Detect GPU presence without running heavy workloads."""

    nvidia = _detect_nvidia_smi()
    if nvidia.detected:
        return nvidia
    lspci = _detect_lspci_gpu()
    if lspci.detected:
        return lspci
    return GPUInfo(detected=False, name=None, source=None)


def _detect_nvidia_smi() -> GPUInfo:
    binary = shutil.which("nvidia-smi")
    if not binary:
        return GPUInfo(detected=False)
    try:
        result = subprocess.run(
            [binary, "--query-gpu=name", "--format=csv,noheader"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return GPUInfo(detected=False)
    names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not names:
        return GPUInfo(detected=False)
    return GPUInfo(detected=True, name=", ".join(names), source="nvidia-smi")


def _detect_lspci_gpu() -> GPUInfo:
    binary = shutil.which("lspci")
    if not binary:
        return GPUInfo(detected=False)
    try:
        result = subprocess.run(
            [binary],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return GPUInfo(detected=False)
    candidates = []
    for line in result.stdout.splitlines():
        lower = line.lower()
        if "vga compatible controller" in lower or "3d controller" in lower or "display controller" in lower:
            candidates.append(line.strip())
    if not candidates:
        return GPUInfo(detected=False)
    return GPUInfo(detected=True, name="; ".join(candidates[:3]), source="lspci")


def detect_ram_bytes() -> int | None:
    if hasattr(os, "sysconf"):
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            if isinstance(pages, int) and isinstance(page_size, int):
                return int(pages * page_size)
        except (ValueError, OSError, AttributeError):
            return None
    return None


def gpu_expected_workload_hash(seed: str, difficulty: int) -> str:
    values = _gpu_workload_values(seed, difficulty)
    checksum = sum(values) % 1_000_000_007
    return sha256_text(f"gpu-workload-v1:{seed}:{max(1, difficulty)}:{checksum}:{values[-1]}")


def run_gpu_workload_challenge(seed: str, difficulty: int) -> GPUWorkloadProof:
    if os.getenv("PICOIN_FORGE_TEST_GPU_BACKEND") == "1":
        return GPUWorkloadProof(
            verified=True,
            result_hash=gpu_expected_workload_hash(seed, difficulty),
            backend="test-gpu",
            device_name="test-gpu-backend",
        )
    cupy = _load_cupy()
    if cupy is None:
        return GPUWorkloadProof(
            verified=False,
            result_hash=sha256_text(f"gpu-unavailable:{seed}:{max(1, difficulty)}"),
            backend="none",
            reason="cupy backend unavailable",
        )
    try:
        values = _gpu_workload_values(seed, difficulty)
        gpu_values = cupy.asarray(values, dtype=cupy.uint64)
        checksum = int(cupy.asnumpy(gpu_values.sum() % 1_000_000_007))
        tail = int(cupy.asnumpy(gpu_values[-1]))
        raw_name = cupy.cuda.runtime.getDeviceProperties(0).get("name", b"gpu")
        device_name = raw_name.decode("utf-8", "ignore") if isinstance(raw_name, bytes) else str(raw_name)
    except Exception as exc:  # pragma: no cover - depends on optional GPU runtime.
        return GPUWorkloadProof(
            verified=False,
            result_hash=sha256_text(f"gpu-error:{seed}:{max(1, difficulty)}:{exc}"),
            backend="cupy",
            reason=str(exc),
        )
    return GPUWorkloadProof(
        verified=True,
        result_hash=sha256_text(f"gpu-workload-v1:{seed}:{max(1, difficulty)}:{checksum}:{tail}"),
        backend="cupy",
        device_name=device_name,
    )


def _gpu_workload_values(seed: str, difficulty: int) -> list[int]:
    size = max(32, max(1, difficulty) * 64)
    value = sha256_text(seed)
    values: list[int] = []
    for idx in range(size):
        value = sha256_text(f"{value}:{idx}")
        values.append(int(value[:12], 16))
    for _ in range(max(1, difficulty) * 16):
        values = [((item * 1_103_515_245 + 12_345) % 2_147_483_647) for item in values]
    return values


def _load_cupy():
    try:
        import cupy

        return cupy
    except Exception:
        return None
