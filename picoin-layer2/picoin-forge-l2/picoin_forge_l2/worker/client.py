from __future__ import annotations

import json
from pathlib import Path
from urllib import request

from picoin_forge_l2.common.models import BenchmarkResult, ChallengeResult, ComputeChallenge, Heartbeat, WorkerRegistration


class CoordinatorClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def register(self, registration: WorkerRegistration) -> dict:
        return self._post("/workers/register", registration.model_dump(mode="json"))

    def submit_benchmark(self, benchmark: BenchmarkResult) -> dict:
        return self._post("/benchmarks", benchmark.model_dump(mode="json"))

    def heartbeat(self, heartbeat: Heartbeat) -> dict:
        return self._post("/heartbeats", heartbeat.model_dump(mode="json"))

    def request_challenge(self, worker_id: str, challenge_type: str = "cpu", difficulty: int = 1) -> ComputeChallenge:
        payload = {"worker_id": worker_id, "challenge_type": challenge_type, "difficulty": difficulty}
        response = self._post("/challenges", payload)
        return ComputeChallenge.model_validate(response)

    def open_challenges(self, worker_id: str) -> list[ComputeChallenge]:
        response = self._get(f"/workers/{worker_id}/challenges?open_only=true")
        return [ComputeChallenge.model_validate(item) for item in response]

    def submit_challenge_result(self, challenge_id: str, result: ChallengeResult) -> dict:
        return self._post(f"/challenges/{challenge_id}/submit", result.model_dump(mode="json"))

    def _get(self, path: str) -> object:
        with request.urlopen(self.base_url + path, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post(self, path: str, payload: dict) -> object:
        body = json.dumps(payload, default=str).encode("utf-8")
        req = request.Request(
            self.base_url + path,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
