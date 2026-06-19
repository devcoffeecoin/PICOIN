from __future__ import annotations

from picoin_forge_l2.worker.benchmark import benchmark_gpu
from picoin_forge_l2.worker.config import load_worker_config, save_worker_config
from picoin_forge_l2.worker.gpu import GPUInfo
from picoin_forge_l2.worker.registration import detect_machine_info


def test_worker_config_round_trip(tmp_path):
    saved = save_worker_config(
        tmp_path,
        wallet="piworkerconfig",
        coordinator_url="http://coordinator:9380/",
        interval_seconds=12,
        benchmark_scale=2,
    )
    loaded = load_worker_config(tmp_path)

    assert loaded is not None
    assert saved.wallet == "PIWORKERCONFIG"
    assert loaded.coordinator_url == "http://coordinator:9380"
    assert loaded.interval_seconds == 12
    assert loaded.benchmark_scale == 2


def test_gpu_detection_is_passive_and_does_not_increase_score(monkeypatch):
    monkeypatch.setattr(
        "picoin_forge_l2.worker.registration.detect_gpu_info",
        lambda: GPUInfo(detected=True, name="Test GPU", source="unit-test"),
    )
    monkeypatch.setattr("picoin_forge_l2.worker.registration.detect_ram_bytes", lambda: 123456)

    machine = detect_machine_info()

    assert machine.gpu_detected is True
    assert machine.gpu_name == "Test GPU"
    assert machine.ram_bytes == 123456
    assert benchmark_gpu(True) == 0.0
