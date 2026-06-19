from __future__ import annotations

import tempfile
import time
from pathlib import Path

from picoin_forge_l2.common.hashing import hash_json, sha256_text
from picoin_forge_l2.common.models import BenchmarkResult
from picoin_forge_l2.worker.gpu import detect_gpu_info


def run_benchmark(worker_id: str, *, scale: int = 1) -> BenchmarkResult:
    safe_scale = max(1, min(int(scale), 10))
    cpu_score = benchmark_cpu(safe_scale)
    ram_score = benchmark_ram(safe_scale)
    io_score = benchmark_io(safe_scale)
    gpu = detect_gpu_info()
    gpu_score = benchmark_gpu(gpu.detected)
    benchmark_score = cpu_score + ram_score + io_score + gpu_score
    details = {
        "scale": safe_scale,
        "gpu": {
            "detected": gpu.detected,
            "name": gpu.name,
            "source": gpu.source,
            "score_policy": "placeholder_zero_until_verified_gpu_challenge",
        },
        "algorithm": "picoin-forge-l2-mvp",
    }
    result_hash = hash_json(
        {
            "worker_id": worker_id,
            "cpu_score": cpu_score,
            "ram_score": ram_score,
            "io_score": io_score,
            "gpu_score": gpu_score,
            "details": details,
        }
    )
    return BenchmarkResult(
        worker_id=worker_id,
        cpu_score=cpu_score,
        gpu_score=gpu_score,
        ram_score=ram_score,
        io_score=io_score,
        benchmark_score=benchmark_score,
        result_hash=result_hash,
        details=details,
    )


def benchmark_cpu(scale: int = 1) -> float:
    started = time.perf_counter()
    value = "picoin-forge-cpu"
    iterations = 2500 * max(1, scale)
    for _ in range(iterations):
        value = sha256_text(value)
    elapsed = max(time.perf_counter() - started, 0.000001)
    return round(iterations / elapsed / 1000.0, 4)


def benchmark_ram(scale: int = 1) -> float:
    started = time.perf_counter()
    size = 128 * 1024 * max(1, scale)
    data = bytearray(size)
    for idx in range(0, size, 4096):
        data[idx] = idx % 251
    elapsed = max(time.perf_counter() - started, 0.000001)
    return round((size / 1024 / 1024) / elapsed, 4)


def benchmark_io(scale: int = 1) -> float:
    payload = b"picoin-forge-io" * (4096 * max(1, scale))
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="picoin-forge-bench-") as tmp:
        path = Path(tmp) / "bench.bin"
        path.write_bytes(payload)
        _ = path.read_bytes()
    elapsed = max(time.perf_counter() - started, 0.000001)
    return round((len(payload) / 1024 / 1024) / elapsed, 4)


def benchmark_gpu(gpu_detected: bool) -> float:
    # MVP policy: detection alone does not earn GPU score.
    # A future GPU challenge must verify useful GPU capacity first.
    return 0.0
