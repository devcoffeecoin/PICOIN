import os


def http_timeout_seconds(
    *,
    default: float = 20.0,
    env_names: tuple[str, ...] = ("PICOIN_HTTP_TIMEOUT_SECONDS", "PICOIN_SMOKE_TIMEOUT"),
) -> float:
    for name in env_names:
        value = os.getenv(name)
        if value:
            try:
                return max(1.0, float(value))
            except ValueError:
                return default
    return default


def worker_http_timeout_seconds(*, default: float = 90.0) -> float:
    return http_timeout_seconds(
        default=default,
        env_names=(
            "PICOIN_WORKER_HTTP_TIMEOUT_SECONDS",
            "PICOIN_HTTP_TIMEOUT_SECONDS",
            "PICOIN_SMOKE_TIMEOUT",
        ),
    )
