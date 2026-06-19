from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from picoin_forge_l2.common.models import ChallengeType
from picoin_forge_l2.coordinator.challenge_engine import ChallengeEngine
from picoin_forge_l2.coordinator.worker_registry import WorkerRegistry
from picoin_forge_l2.worker.benchmark import run_benchmark
from picoin_forge_l2.worker.ai_model import run_ai_inference
from picoin_forge_l2.worker.challenges import solve_challenge
from picoin_forge_l2.worker.registration import register_worker


def test_ai_model_challenge_can_verify_ollama_compatible_endpoint(tmp_path, monkeypatch):
    server = _start_ollama_mock()
    try:
        host, port = server.server_address
        monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_PROVIDER", "ollama")
        monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_NAME", "llama-3.1-70b")
        monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_PARAMETERS_B", "70")
        monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_CONTEXT_TOKENS", "131072")
        monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_CAPABILITIES", "llm,chat,reasoning")
        monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_ENDPOINT", f"http://{host}:{port}")
        monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_TIMEOUT_SECONDS", "2")
        registry = WorkerRegistry(tmp_path)
        registration = register_worker("PIAIOLLAMAENDPOINT", tmp_path / "worker")
        registry.register(registration)
        registry.update_benchmark(run_benchmark(registration.worker_id, scale=1))
        engine = ChallengeEngine(tmp_path, registry)

        challenge = engine.create_challenge(registration.worker_id, ChallengeType.AI_MODEL, difficulty=1)
        result = solve_challenge(challenge)
        passed = engine.verify_result(result)
        state = registry.get(registration.worker_id)

        assert passed is True
        assert result.proof["backend"] == "ollama"
        assert result.proof["ai_model_verified"] is True
        assert state.ai_model_score > 0
    finally:
        server.shutdown()
        server.server_close()


def test_ai_inference_uses_ollama_compatible_endpoint(monkeypatch):
    server = _start_ollama_mock()
    try:
        host, port = server.server_address
        monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_PROVIDER", "ollama")
        monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_NAME", "llama-3.1-70b")
        monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_ENDPOINT", f"http://{host}:{port}")
        monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_TIMEOUT_SECONDS", "2")

        result = run_ai_inference("Explain Picoin Forge in one sentence.", max_tokens=32)

        assert result.accepted is True
        assert result.backend == "ollama"
        assert "picoin-forge-model-online:llama-3.1-70b" in result.output
        assert result.output_hash
    finally:
        server.shutdown()
        server.server_close()


def _start_ollama_mock() -> HTTPServer:
    server = HTTPServer(("127.0.0.1", 0), _OllamaMockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


class _OllamaMockHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/api/generate":
            self.send_error(404)
            return
        length = int(self.headers.get("content-length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        model = payload.get("model") or "unknown"
        body = json.dumps({"response": f"picoin-forge-model-online:{model}"}).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return
