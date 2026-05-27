from time import perf_counter


def now_perf() -> float:
    return perf_counter()


def elapsed_ms(start: float) -> int:
    return max(0, round((perf_counter() - start) * 1000))
