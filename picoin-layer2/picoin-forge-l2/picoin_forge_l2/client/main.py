from __future__ import annotations

from .http import ForgeHTTPClient

try:  # pragma: no cover - exercised when optional CLI deps are installed.
    import typer
    from rich.console import Console
except ModuleNotFoundError:  # pragma: no cover
    typer = None
    Console = None


if typer is not None:
    app = typer.Typer(help="Picoin Forge L2 user HTTP client.")
    ai_app = typer.Typer(help="Stake-gated AI access over HTTP.")
    app.add_typer(ai_app, name="ai")
    console = Console()

    def _client(
        coordinator_url: str,
        token: str | None,
        timeout_seconds: float,
    ) -> ForgeHTTPClient:
        return ForgeHTTPClient(coordinator_url, token=token, timeout_seconds=timeout_seconds)

    @app.command()
    def health(
        coordinator_url: str = "http://127.0.0.1:9380",
        token: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        console.print_json(data=_client(coordinator_url, token, timeout_seconds).health())

    @ai_app.command("capabilities")
    def ai_capabilities(
        coordinator_url: str = "http://127.0.0.1:9380",
        token: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        console.print_json(data=_client(coordinator_url, token, timeout_seconds).ai_capabilities())

    @ai_app.command("summary")
    def ai_summary(
        coordinator_url: str = "http://127.0.0.1:9380",
        token: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        console.print_json(data=_client(coordinator_url, token, timeout_seconds).ai_summary())

    @ai_app.command("list")
    def ai_list(
        coordinator_url: str = "http://127.0.0.1:9380",
        token: str | None = None,
        timeout_seconds: float = 30.0,
        limit: int = 20,
        requester_wallet: str | None = None,
    ) -> None:
        console.print_json(
            data=_client(coordinator_url, token, timeout_seconds).ai_list(
                limit=limit,
                requester_wallet=requester_wallet,
            )
        )

    @ai_app.command("create")
    def ai_create(
        requester_wallet: str,
        prompt: str,
        stake_snapshot_pi: float,
        coordinator_url: str = "http://127.0.0.1:9380",
        token: str | None = None,
        timeout_seconds: float = 30.0,
        capabilities: str = "chat",
        model_hint: str | None = None,
        min_parameter_count_b: float = 0.0,
        min_context_tokens: int = 0,
        preferred_provider: str | None = None,
        max_tokens: int = 256,
        store_output: bool = True,
    ) -> None:
        console.print_json(
            data=_client(coordinator_url, token, timeout_seconds).ai_create_request(
                requester_wallet=requester_wallet,
                prompt=prompt,
                stake_snapshot_pi=stake_snapshot_pi,
                required_capabilities=_csv(capabilities),
                model_hint=model_hint,
                min_parameter_count_b=min_parameter_count_b,
                min_context_tokens=min_context_tokens,
                preferred_provider=preferred_provider,
                max_tokens=max_tokens,
                store_output=store_output,
            )
        )

    @ai_app.command("run")
    def ai_run(
        requester_wallet: str,
        prompt: str,
        stake_snapshot_pi: float,
        coordinator_url: str = "http://127.0.0.1:9380",
        token: str | None = None,
        timeout_seconds: float = 30.0,
        capabilities: str = "chat",
        model_hint: str | None = None,
        min_parameter_count_b: float = 0.0,
        min_context_tokens: int = 0,
        preferred_provider: str | None = None,
        max_tokens: int = 256,
        store_output: bool = True,
        poll_interval_seconds: float = 2.0,
        wait_timeout_seconds: float = 120.0,
        include_receipt: bool = True,
    ) -> None:
        console.print_json(
            data=_client(coordinator_url, token, timeout_seconds).ai_run(
                requester_wallet=requester_wallet,
                prompt=prompt,
                stake_snapshot_pi=stake_snapshot_pi,
                required_capabilities=_csv(capabilities),
                model_hint=model_hint,
                min_parameter_count_b=min_parameter_count_b,
                min_context_tokens=min_context_tokens,
                preferred_provider=preferred_provider,
                max_tokens=max_tokens,
                store_output=store_output,
                poll_interval_seconds=poll_interval_seconds,
                wait_timeout_seconds=wait_timeout_seconds,
                include_receipt=include_receipt,
            )
        )

    @ai_app.command("status")
    def ai_status(
        request_id: str,
        coordinator_url: str = "http://127.0.0.1:9380",
        token: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        console.print_json(data=_client(coordinator_url, token, timeout_seconds).ai_status(request_id))

    @ai_app.command("routing")
    def ai_routing(
        request_id: str,
        coordinator_url: str = "http://127.0.0.1:9380",
        token: str | None = None,
        timeout_seconds: float = 30.0,
        limit: int = 10,
    ) -> None:
        console.print_json(data=_client(coordinator_url, token, timeout_seconds).ai_routing(request_id, limit=limit))

    @ai_app.command("result")
    def ai_result(
        request_id: str,
        coordinator_url: str = "http://127.0.0.1:9380",
        token: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        console.print_json(data=_client(coordinator_url, token, timeout_seconds).ai_result(request_id))

    @ai_app.command("receipt")
    def ai_receipt(
        request_id: str,
        coordinator_url: str = "http://127.0.0.1:9380",
        token: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        console.print_json(data=_client(coordinator_url, token, timeout_seconds).ai_receipt(request_id))

    @ai_app.command("export")
    def ai_export(
        request_id: str,
        coordinator_url: str = "http://127.0.0.1:9380",
        token: str | None = None,
        timeout_seconds: float = 30.0,
        include_content: bool = False,
    ) -> None:
        console.print_json(
            data=_client(coordinator_url, token, timeout_seconds).ai_export(
                request_id,
                include_content=include_content,
            )
        )

    @ai_app.command("cancel")
    def ai_cancel(
        request_id: str,
        coordinator_url: str = "http://127.0.0.1:9380",
        token: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        console.print_json(data=_client(coordinator_url, token, timeout_seconds).ai_cancel(request_id))
else:  # pragma: no cover
    app = None


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]
