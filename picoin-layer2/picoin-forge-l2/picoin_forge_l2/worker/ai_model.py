from __future__ import annotations

import os
import json
from dataclasses import dataclass
from urllib import error, request

from picoin_forge_l2.common.hashing import hash_json, sha256_text
from picoin_forge_l2.common.models import AIModelProfile


@dataclass(frozen=True)
class AIModelProof:
    verified: bool
    prompt_hash: str
    proof_hash: str
    backend: str
    model_profile: dict
    output_hash: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class AIInferenceOutput:
    accepted: bool
    output: str
    backend: str
    output_hash: str | None = None
    reason: str | None = None


def detect_ai_model_profile() -> AIModelProfile | None:
    provider = os.getenv("PICOIN_FORGE_AI_MODEL_PROVIDER", "").strip()
    model_name = os.getenv("PICOIN_FORGE_AI_MODEL_NAME", "").strip()
    endpoint = os.getenv("PICOIN_FORGE_AI_MODEL_ENDPOINT", "").strip()
    capabilities = _csv_env("PICOIN_FORGE_AI_MODEL_CAPABILITIES")
    if not provider and not model_name and not endpoint and not capabilities:
        return None
    profile = AIModelProfile(
        provider=provider or "custom",
        model_name=model_name or None,
        parameter_count_b=_float_env("PICOIN_FORGE_AI_MODEL_PARAMETERS_B", 0.0),
        context_tokens=_int_env("PICOIN_FORGE_AI_MODEL_CONTEXT_TOKENS", 0),
        quantization=os.getenv("PICOIN_FORGE_AI_MODEL_QUANTIZATION", "").strip() or None,
        capabilities=capabilities,
        endpoint=endpoint or None,
        available=bool(model_name or endpoint),
    )
    return profile


def run_ai_model_smoke(
    *,
    prompt: str = "Reply with one short Picoin Forge worker readiness sentence.",
    max_tokens: int = 64,
    seed: str = "picoin-forge-ai-smoke",
) -> dict:
    profile = detect_ai_model_profile()
    proof = run_ai_model_availability_challenge(seed, difficulty=1, profile=profile)
    inference = run_ai_inference(prompt, max_tokens=max_tokens, profile=profile)
    return {
        "schema": "picoin-forge-ai-model-smoke-v1",
        "ready": bool(profile and proof.verified and inference.accepted),
        "model_profile": profile.model_dump(mode="json") if profile else None,
        "availability": {
            "verified": proof.verified,
            "backend": proof.backend,
            "prompt_hash": proof.prompt_hash,
            "proof_hash": proof.proof_hash,
            "output_hash": proof.output_hash,
            "reason": proof.reason,
        },
        "inference": {
            "accepted": inference.accepted,
            "backend": inference.backend,
            "output_hash": inference.output_hash,
            "output_preview": inference.output[:240],
            "reason": inference.reason,
        },
        "no_l1_transaction_created": True,
        "no_per_task_payment": True,
    }


def run_ai_inference(prompt: str, max_tokens: int = 256, profile: AIModelProfile | None = None) -> AIInferenceOutput:
    resolved = profile or detect_ai_model_profile()
    if not resolved or not resolved.available or not resolved.endpoint or not resolved.model_name:
        return AIInferenceOutput(False, "", "none", reason="no available AI model endpoint configured")
    model_profile = resolved.model_dump(mode="json")
    if os.getenv("PICOIN_FORGE_TEST_AI_MODEL_BACKEND") == "1":
        output = (
            "Picoin Forge local AI demo response. "
            f"model={model_profile.get('model_name')} "
            f"prompt_hash={sha256_text(prompt)[:16]} "
            f"max_tokens={max(1, int(max_tokens))}"
        )
        return _inference_output("test-ai-model", output)
    provider = str(model_profile.get("provider") or "").lower()
    if provider == "ollama":
        endpoint = _endpoint_url(str(model_profile["endpoint"]), "/api/generate")
        payload = {
            "model": model_profile["model_name"],
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0, "num_predict": max(1, int(max_tokens))},
        }
        try:
            response = _post_json(endpoint, payload)
            output = str(response.get("response") or "").strip()
        except (OSError, TimeoutError, ValueError, error.URLError) as exc:
            return AIInferenceOutput(False, "", "ollama", reason=str(exc))
        return _inference_output("ollama", output)
    if provider in {"openai-compatible", "vllm", "llamacpp"}:
        endpoint = _endpoint_url(str(model_profile["endpoint"]), "/v1/chat/completions")
        payload = {
            "model": model_profile["model_name"],
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": max(1, int(max_tokens)),
        }
        try:
            response = _post_json(endpoint, payload)
            choices = response.get("choices") or []
            first = choices[0] if choices else {}
            message = first.get("message") or {}
            output = str(message.get("content") or first.get("text") or "").strip()
        except (OSError, TimeoutError, ValueError, error.URLError, IndexError) as exc:
            return AIInferenceOutput(False, "", "openai-compatible", reason=str(exc))
        return _inference_output("openai-compatible", output)
    return AIInferenceOutput(False, "", provider or "unknown", reason=f"unsupported AI model provider: {provider}")


def ai_model_challenge_prompt(seed: str, difficulty: int) -> dict:
    return {
        "schema": "picoin-forge-ai-model-availability-v1",
        "seed": seed,
        "difficulty": max(1, difficulty),
        "instruction": (
            "Prove that a configured AI model endpoint or model runtime is available. "
            "This is availability evidence for epoch scoring, not a paid user task."
        ),
    }


def ai_model_expected_prompt_hash(seed: str, difficulty: int) -> str:
    return hash_json(ai_model_challenge_prompt(seed, difficulty))


def run_ai_model_availability_challenge(
    seed: str,
    difficulty: int,
    profile: AIModelProfile | None = None,
) -> AIModelProof:
    prompt_hash = ai_model_expected_prompt_hash(seed, difficulty)
    model_profile = (profile or detect_ai_model_profile() or AIModelProfile()).model_dump(mode="json")
    if os.getenv("PICOIN_FORGE_TEST_AI_MODEL_BACKEND") == "1":
        output_hash = sha256_text(
            "test-ai-model:"
            f"{prompt_hash}:"
            f"{model_profile.get('provider')}:"
            f"{model_profile.get('model_name')}:"
            f"{model_profile.get('parameter_count_b')}:"
            f"{model_profile.get('context_tokens')}"
        )
        proof_hash = hash_json(
            {
                "schema": "picoin-forge-ai-model-proof-v1",
                "prompt_hash": prompt_hash,
                "output_hash": output_hash,
                "model_profile": model_profile,
                "backend": "test-ai-model",
            }
        )
        return AIModelProof(
            verified=True,
            prompt_hash=prompt_hash,
            proof_hash=proof_hash,
            backend="test-ai-model",
            model_profile=model_profile,
            output_hash=output_hash,
        )

    if model_profile.get("available") and model_profile.get("endpoint") and model_profile.get("model_name"):
        return _run_endpoint_model_challenge(prompt_hash, model_profile)

    reason = "no verified AI model backend configured"
    if model_profile.get("available"):
        reason = "AI model advertised, but no availability proof backend is configured"
    proof_hash = hash_json(
        {
            "schema": "picoin-forge-ai-model-proof-v1",
            "prompt_hash": prompt_hash,
            "model_profile": model_profile,
            "backend": "none",
            "verified": False,
            "reason": reason,
        }
    )
    return AIModelProof(
        verified=False,
        prompt_hash=prompt_hash,
        proof_hash=proof_hash,
        backend="none",
        model_profile=model_profile,
        reason=reason,
    )


def _run_endpoint_model_challenge(prompt_hash: str, model_profile: dict) -> AIModelProof:
    provider = str(model_profile.get("provider") or "").lower()
    if provider == "ollama":
        return _run_ollama_challenge(prompt_hash, model_profile)
    if provider in {"openai-compatible", "vllm", "llamacpp"}:
        return _run_openai_compatible_challenge(prompt_hash, model_profile)
    reason = f"unsupported AI model provider for endpoint proof: {provider or 'unknown'}"
    proof_hash = hash_json(
        {
            "schema": "picoin-forge-ai-model-proof-v1",
            "prompt_hash": prompt_hash,
            "model_profile": model_profile,
            "backend": provider or "unknown",
            "verified": False,
            "reason": reason,
        }
    )
    return AIModelProof(
        verified=False,
        prompt_hash=prompt_hash,
        proof_hash=proof_hash,
        backend=provider or "unknown",
        model_profile=model_profile,
        reason=reason,
    )


def _run_ollama_challenge(prompt_hash: str, model_profile: dict) -> AIModelProof:
    endpoint = _endpoint_url(str(model_profile["endpoint"]), "/api/generate")
    payload = {
        "model": model_profile["model_name"],
        "prompt": f"Return one short deterministic availability marker for Picoin Forge: {prompt_hash[:16]}",
        "stream": False,
        "options": {"temperature": 0, "num_predict": 8},
    }
    try:
        response = _post_json(endpoint, payload)
        output = str(response.get("response") or "").strip()
    except (OSError, TimeoutError, ValueError, error.URLError) as exc:
        return _failed_endpoint_proof(prompt_hash, model_profile, "ollama", str(exc))
    return _successful_endpoint_proof(prompt_hash, model_profile, "ollama", output)


def _run_openai_compatible_challenge(prompt_hash: str, model_profile: dict) -> AIModelProof:
    endpoint = _endpoint_url(str(model_profile["endpoint"]), "/v1/chat/completions")
    payload = {
        "model": model_profile["model_name"],
        "messages": [
            {
                "role": "user",
                "content": f"Return one short deterministic availability marker for Picoin Forge: {prompt_hash[:16]}",
            }
        ],
        "temperature": 0,
        "max_tokens": 8,
    }
    try:
        response = _post_json(endpoint, payload)
        choices = response.get("choices") or []
        first = choices[0] if choices else {}
        message = first.get("message") or {}
        output = str(message.get("content") or first.get("text") or "").strip()
    except (OSError, TimeoutError, ValueError, error.URLError, IndexError) as exc:
        return _failed_endpoint_proof(prompt_hash, model_profile, "openai-compatible", str(exc))
    return _successful_endpoint_proof(prompt_hash, model_profile, "openai-compatible", output)


def _successful_endpoint_proof(prompt_hash: str, model_profile: dict, backend: str, output: str) -> AIModelProof:
    if not output:
        return _failed_endpoint_proof(prompt_hash, model_profile, backend, "empty model response")
    output_hash = sha256_text(output)
    proof_hash = hash_json(
        {
            "schema": "picoin-forge-ai-model-proof-v1",
            "prompt_hash": prompt_hash,
            "output_hash": output_hash,
            "model_profile": model_profile,
            "backend": backend,
        }
    )
    return AIModelProof(
        verified=True,
        prompt_hash=prompt_hash,
        proof_hash=proof_hash,
        backend=backend,
        model_profile=model_profile,
        output_hash=output_hash,
    )


def _inference_output(backend: str, output: str) -> AIInferenceOutput:
    if not output:
        return AIInferenceOutput(False, "", backend, reason="empty model response")
    return AIInferenceOutput(True, output, backend, output_hash=sha256_text(output))


def _failed_endpoint_proof(prompt_hash: str, model_profile: dict, backend: str, reason: str) -> AIModelProof:
    proof_hash = hash_json(
        {
            "schema": "picoin-forge-ai-model-proof-v1",
            "prompt_hash": prompt_hash,
            "model_profile": model_profile,
            "backend": backend,
            "verified": False,
            "reason": reason,
        }
    )
    return AIModelProof(
        verified=False,
        prompt_hash=prompt_hash,
        proof_hash=proof_hash,
        backend=backend,
        model_profile=model_profile,
        reason=reason,
    )


def _post_json(url: str, payload: dict) -> dict:
    timeout = _float_env("PICOIN_FORGE_AI_MODEL_TIMEOUT_SECONDS", 5.0)
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    data = json.loads(body or "{}")
    if not isinstance(data, dict):
        raise ValueError("AI model endpoint returned non-object JSON")
    return data


def _endpoint_url(base: str, default_path: str) -> str:
    clean = base.rstrip("/")
    if clean.endswith(default_path.rstrip("/")):
        return clean
    if default_path.startswith("/v1/") and clean.endswith("/v1"):
        return clean + default_path.removeprefix("/v1")
    if default_path.startswith("/api/") and clean.endswith("/api"):
        return clean + default_path.removeprefix("/api")
    return clean + default_path


def _csv_env(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
