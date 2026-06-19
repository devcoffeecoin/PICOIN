from __future__ import annotations

from picoin_forge_l2.worker.ai_model import run_ai_model_smoke


def test_ai_model_smoke_passes_with_test_backend(monkeypatch):
    monkeypatch.setenv("PICOIN_FORGE_TEST_AI_MODEL_BACKEND", "1")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_PROVIDER", "test-ai-model")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_NAME", "picoin-forge-test-llm")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_PARAMETERS_B", "8")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_CONTEXT_TOKENS", "8192")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_CAPABILITIES", "llm,chat,reasoning")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_ENDPOINT", "local://picoin-forge-test-ai-model")

    result = run_ai_model_smoke(prompt="Say ready.", max_tokens=16)

    assert result["schema"] == "picoin-forge-ai-model-smoke-v1"
    assert result["ready"] is True
    assert result["model_profile"]["provider"] == "test-ai-model"
    assert result["availability"]["verified"] is True
    assert result["availability"]["backend"] == "test-ai-model"
    assert result["inference"]["accepted"] is True
    assert result["inference"]["backend"] == "test-ai-model"
    assert result["inference"]["output_hash"]
    assert result["no_l1_transaction_created"] is True
    assert result["no_per_task_payment"] is True


def test_ai_model_smoke_reports_not_ready_without_model(monkeypatch):
    monkeypatch.delenv("PICOIN_FORGE_TEST_AI_MODEL_BACKEND", raising=False)
    monkeypatch.delenv("PICOIN_FORGE_AI_MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("PICOIN_FORGE_AI_MODEL_NAME", raising=False)
    monkeypatch.delenv("PICOIN_FORGE_AI_MODEL_ENDPOINT", raising=False)
    monkeypatch.delenv("PICOIN_FORGE_AI_MODEL_CAPABILITIES", raising=False)

    result = run_ai_model_smoke()

    assert result["ready"] is False
    assert result["model_profile"] is None
    assert result["availability"]["verified"] is False
    assert result["inference"]["accepted"] is False
