from __future__ import annotations

import math
from statistics import mean
from typing import Any

from .storage import benchmark_normalization_caps


BENCHMARK_COMPONENTS = ("cpu_score", "gpu_score", "ram_score", "io_score")
BENCHMARK_CAP_ENV = {
    "cpu_score": "PICOIN_FORGE_CPU_SCORE_CAP",
    "gpu_score": "PICOIN_FORGE_GPU_SCORE_CAP",
    "ram_score": "PICOIN_FORGE_RAM_SCORE_CAP",
    "io_score": "PICOIN_FORGE_IO_SCORE_CAP",
}


def build_benchmark_calibration_report(metrics: list[dict[str, Any]], *, percentile: float = 0.95) -> dict[str, Any]:
    clamped_percentile = min(max(float(percentile), 0.5), 1.0)
    current_caps = benchmark_normalization_caps()
    component_reports: dict[str, dict[str, Any]] = {}
    recommended_caps: dict[str, float] = {}
    recommended_env: dict[str, str] = {}

    for component in BENCHMARK_COMPONENTS:
        values = [float(metric.get(component) or 0.0) for metric in metrics if float(metric.get(component) or 0.0) >= 0.0]
        if values:
            percentile_value = _nearest_rank_percentile(values, clamped_percentile)
            recommended_cap = round(max(percentile_value, 1.0), 8)
            component_reports[component] = {
                "count": len(values),
                "min": round(min(values), 8),
                "max": round(max(values), 8),
                "average": round(mean(values), 8),
                "percentile_value": round(percentile_value, 8),
                "recommended_cap": recommended_cap,
                "current_cap": current_caps[component],
            }
        else:
            recommended_cap = current_caps[component]
            component_reports[component] = {
                "count": 0,
                "min": 0.0,
                "max": 0.0,
                "average": 0.0,
                "percentile_value": 0.0,
                "recommended_cap": recommended_cap,
                "current_cap": current_caps[component],
            }
        recommended_caps[component] = recommended_cap
        recommended_env[BENCHMARK_CAP_ENV[component]] = str(recommended_cap)

    return {
        "sample_count": len(metrics),
        "ready": bool(metrics),
        "percentile": clamped_percentile,
        "current_caps": current_caps,
        "recommended_caps": recommended_caps,
        "recommended_env": recommended_env,
        "components": component_reports,
        "note": "Operator review required. This report does not change running configuration.",
    }


def _nearest_rank_percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[min(rank - 1, len(ordered) - 1)]
