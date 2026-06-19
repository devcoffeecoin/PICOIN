from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class GPUInfo:
    detected: bool
    name: str | None = None
    source: str | None = None


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
