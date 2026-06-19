from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request


@dataclass
class ForgeHTTPClient:
    base_url: str = "http://127.0.0.1:9380"
    token: str | None = None
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        if self.token is None:
            self.token = os.getenv("PICOIN_FORGE_COORDINATOR_TOKEN") or None

    def health(self) -> dict:
        return self.get("/health")

    def ai_capabilities(self) -> dict:
        return self.get("/ai/capabilities")

    def ai_summary(self) -> dict:
        return self.get("/ai/summary")

    def ai_list(self, *, limit: int = 20, requester_wallet: str | None = None) -> list[dict]:
        query: dict[str, Any] = {"limit": limit}
        if requester_wallet:
            query["requester_wallet"] = requester_wallet
        return self.get("/ai/requests", query=query)

    def ai_create_request(
        self,
        *,
        requester_wallet: str,
        prompt: str,
        stake_snapshot_pi: float,
        required_capabilities: list[str] | None = None,
        model_hint: str | None = None,
        min_parameter_count_b: float = 0.0,
        min_context_tokens: int = 0,
        preferred_provider: str | None = None,
        max_tokens: int = 256,
        store_output: bool = True,
    ) -> dict:
        return self.post(
            "/ai/requests",
            {
                "requester_wallet": requester_wallet,
                "stake_snapshot_pi": stake_snapshot_pi,
                "prompt": prompt,
                "required_capabilities": required_capabilities or [],
                "model_hint": model_hint,
                "min_parameter_count_b": min_parameter_count_b,
                "min_context_tokens": min_context_tokens,
                "preferred_provider": preferred_provider,
                "max_tokens": max_tokens,
                "store_output": store_output,
            },
        )

    def ai_run(
        self,
        *,
        requester_wallet: str,
        prompt: str,
        stake_snapshot_pi: float,
        required_capabilities: list[str] | None = None,
        model_hint: str | None = None,
        min_parameter_count_b: float = 0.0,
        min_context_tokens: int = 0,
        preferred_provider: str | None = None,
        max_tokens: int = 256,
        store_output: bool = True,
        poll_interval_seconds: float = 2.0,
        wait_timeout_seconds: float = 120.0,
        include_receipt: bool = True,
    ) -> dict:
        request_item = self.ai_create_request(
            requester_wallet=requester_wallet,
            prompt=prompt,
            stake_snapshot_pi=stake_snapshot_pi,
            required_capabilities=required_capabilities,
            model_hint=model_hint,
            min_parameter_count_b=min_parameter_count_b,
            min_context_tokens=min_context_tokens,
            preferred_provider=preferred_provider,
            max_tokens=max_tokens,
            store_output=store_output,
        )
        request_id = str(request_item["request_id"])
        status = self.ai_wait(
            request_id,
            poll_interval_seconds=poll_interval_seconds,
            wait_timeout_seconds=wait_timeout_seconds,
        )
        payload: dict[str, Any] = {
            "request": request_item,
            "status": status,
            "result": None,
            "receipt": None,
            "timed_out": status.get("timed_out", False),
            "no_l1_transaction_created": True,
            "no_per_task_payment": True,
        }
        if status.get("status") == "verified":
            payload["result"] = self.ai_result(request_id)
            if include_receipt:
                payload["receipt"] = self.ai_receipt(request_id)
        return payload

    def ai_wait(
        self,
        request_id: str,
        *,
        poll_interval_seconds: float = 2.0,
        wait_timeout_seconds: float = 120.0,
    ) -> dict:
        deadline = time.monotonic() + max(0.0, wait_timeout_seconds)
        last_status = self.ai_status(request_id)
        while last_status.get("status") not in {"verified", "failed", "canceled"}:
            if time.monotonic() >= deadline:
                last_status["timed_out"] = True
                return last_status
            time.sleep(max(0.1, poll_interval_seconds))
            last_status = self.ai_status(request_id)
        last_status["timed_out"] = False
        return last_status

    def ai_status(self, request_id: str) -> dict:
        return self.get(f"/ai/requests/{parse.quote(request_id)}/status")

    def ai_routing(self, request_id: str, *, limit: int = 10) -> dict:
        return self.get(f"/ai/requests/{parse.quote(request_id)}/routing", query={"limit": limit})

    def ai_result(self, request_id: str) -> dict:
        return self.get(f"/ai/requests/{parse.quote(request_id)}/result")

    def ai_receipt(self, request_id: str) -> dict:
        return self.get(f"/ai/requests/{parse.quote(request_id)}/receipt")

    def ai_export(self, request_id: str, *, include_content: bool = False) -> dict:
        return self.get(
            f"/ai/requests/{parse.quote(request_id)}/export",
            query={"include_content": str(include_content).lower()},
        )

    def ai_cancel(self, request_id: str) -> dict:
        return self.post(f"/ai/requests/{parse.quote(request_id)}/cancel", {})

    def get(self, path: str, *, query: dict[str, Any] | None = None) -> Any:
        url = self._url(path, query=query)
        req = request.Request(url, method="GET", headers=self._headers())
        return self._open_json(req)

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        body = json.dumps(payload).encode("utf-8")
        headers = self._headers()
        headers["Content-Type"] = "application/json"
        req = request.Request(self._url(path), data=body, method="POST", headers=headers)
        return self._open_json(req)

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.token:
            headers["X-Picoin-Forge-Token"] = self.token
        return headers

    def _url(self, path: str, *, query: dict[str, Any] | None = None) -> str:
        clean_path = path if path.startswith("/") else f"/{path}"
        url = self.base_url + clean_path
        if query:
            items = {key: value for key, value in query.items() if value is not None}
            if items:
                url += "?" + parse.urlencode(items)
        return url

    def _open_json(self, req: request.Request) -> Any:
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"network error: {exc.reason}") from exc
        return json.loads(body or "{}")
