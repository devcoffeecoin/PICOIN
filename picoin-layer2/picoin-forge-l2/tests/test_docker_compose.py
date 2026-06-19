from __future__ import annotations

from pathlib import Path


def test_docker_compose_has_health_checks():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert "healthcheck:" in compose
    assert "http://127.0.0.1:9380/health" in compose
    assert "condition: service_healthy" in compose
    assert "PICOIN_FORGE_CHALLENGE_EXPIRER_SECONDS" in compose
