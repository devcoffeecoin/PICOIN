from __future__ import annotations

from fastapi.testclient import TestClient

from picoin_forge_l2.common.models import BenchmarkResult
from picoin_forge_l2.coordinator import main as coordinator_main
from picoin_forge_l2.coordinator.calibration import build_benchmark_calibration_report
from picoin_forge_l2.coordinator.storage import CoordinatorStorage
from picoin_forge_l2.coordinator.storage import benchmark_normalization_caps, normalize_benchmark_score


def test_benchmark_normalization_caps_can_be_configured(monkeypatch):
    monkeypatch.setenv("PICOIN_FORGE_CPU_SCORE_CAP", "100")
    monkeypatch.setenv("PICOIN_FORGE_GPU_SCORE_CAP", "200")
    monkeypatch.setenv("PICOIN_FORGE_RAM_SCORE_CAP", "300")
    monkeypatch.setenv("PICOIN_FORGE_IO_SCORE_CAP", "400")

    caps = benchmark_normalization_caps()
    score = normalize_benchmark_score(
        BenchmarkResult(
            worker_id="worker_metric",
            cpu_score=100,
            gpu_score=100,
            ram_score=150,
            io_score=400,
            benchmark_score=750,
            result_hash="hash",
        )
    )

    assert caps == {"cpu_score": 100.0, "gpu_score": 200.0, "ram_score": 300.0, "io_score": 400.0}
    assert score == 75.0


def test_metrics_config_api_returns_caps(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PICOIN_FORGE_CPU_SCORE_CAP", "123")
    client = TestClient(coordinator_main.api)

    response = client.get("/metrics/config")

    assert response.status_code == 200
    assert response.json()["benchmark_normalization_caps"]["cpu_score"] == 123.0


def test_benchmark_calibration_report_recommends_caps_from_metrics():
    report = build_benchmark_calibration_report(
        [
            {"cpu_score": 10, "gpu_score": 0, "ram_score": 100, "io_score": 50},
            {"cpu_score": 20, "gpu_score": 5, "ram_score": 200, "io_score": 75},
        ],
        percentile=1.0,
    )

    assert report["ready"] is True
    assert report["recommended_caps"]["cpu_score"] == 20.0
    assert report["recommended_caps"]["gpu_score"] == 5.0
    assert report["recommended_env"]["PICOIN_FORGE_CPU_SCORE_CAP"] == "20.0"


def test_metrics_calibration_api_returns_report(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path))
    storage = CoordinatorStorage(tmp_path)
    storage.record_benchmark_metric(
        BenchmarkResult(
            worker_id="worker_metric",
            cpu_score=11,
            gpu_score=0,
            ram_score=22,
            io_score=33,
            benchmark_score=66,
            result_hash="hash-a",
        )
    )
    client = TestClient(coordinator_main.api)

    response = client.get("/metrics/calibration?limit=100&percentile=1.0")

    assert response.status_code == 200
    body = response.json()
    assert body["sample_count"] == 1
    assert body["recommended_caps"]["cpu_score"] == 11.0
    assert body["components"]["ram_score"]["max"] == 22.0
