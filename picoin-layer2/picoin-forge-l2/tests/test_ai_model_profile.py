from __future__ import annotations

from picoin_forge_l2.worker.registration import register_worker


def test_worker_registration_includes_ai_model_profile_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_NAME", "llama-3.1-70b")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_PARAMETERS_B", "70")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_CONTEXT_TOKENS", "131072")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_QUANTIZATION", "q4_k_m")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_CAPABILITIES", "llm,chat,reasoning")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_ENDPOINT", "http://127.0.0.1:11434")

    registration = register_worker("PIAIPROFILE123", tmp_path / "worker")

    assert registration.ai_model_profile is not None
    assert registration.ai_model_profile.provider == "ollama"
    assert registration.ai_model_profile.model_name == "llama-3.1-70b"
    assert registration.ai_model_profile.parameter_count_b == 70
    assert registration.ai_model_profile.context_tokens == 131072
    assert registration.ai_model_profile.quantization == "q4_k_m"
    assert registration.ai_model_profile.capabilities == ["llm", "chat", "reasoning"]
    assert registration.ai_model_profile.available is True
