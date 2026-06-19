from __future__ import annotations

from pathlib import Path


def test_docker_compose_has_health_checks():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert "healthcheck:" in compose
    assert "http://127.0.0.1:9380/health" in compose
    assert "condition: service_healthy" in compose
    assert "PICOIN_FORGE_CHALLENGE_EXPIRER_SECONDS" in compose


def test_ai_docker_compose_overlay_configures_real_ai_worker():
    compose = Path("docker-compose.ai.yml").read_text(encoding="utf-8")
    env_example = Path(".env.ai.example").read_text(encoding="utf-8")

    assert "ai-worker-ollama:" in compose
    assert "profiles:" in compose
    assert "picoin-forge-worker ai-smoke" in compose
    assert "--challenge-type ai_model" in compose
    assert "PICOIN_FORGE_AI_MODEL_PROVIDER" in compose
    assert "PICOIN_FORGE_AI_MODEL_ENDPOINT" in compose
    assert "host.docker.internal:host-gateway" in compose
    assert "PICOIN_FORGE_AI_MODEL_NAME" in env_example
    assert "PICOIN_FORGE_AI_WORKER_WALLET" in env_example
