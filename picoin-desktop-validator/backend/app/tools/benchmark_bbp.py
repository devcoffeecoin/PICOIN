import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from statistics import mean

from app.core.performance import elapsed_ms, now_perf
from app.core.pi import calculate_pi_segment, pi_cache_info


def calculate_single_position(args: tuple[int, str]) -> tuple[int, str]:
    position, algorithm = args
    return position, calculate_pi_segment(position, position, algorithm)


def calculate_segment(range_start: int, range_end: int, algorithm: str, workers: int) -> str:
    if workers <= 1:
        return calculate_pi_segment(range_start, range_end, algorithm)

    positions = list(range(range_start, range_end + 1))
    with ProcessPoolExecutor(max_workers=workers) as executor:
        results = executor.map(calculate_single_position, ((position, algorithm) for position in positions))
    digits = {position: digit for position, digit in results}
    return "".join(digits[position] for position in positions)


def run_benchmark(range_start: int, length: int, algorithm: str, workers: int, rounds: int) -> dict:
    range_end = range_start + length - 1
    durations = []
    last_segment = ""

    for _ in range(rounds):
        started = now_perf()
        last_segment = calculate_segment(range_start, range_end, algorithm, workers)
        durations.append(elapsed_ms(started))

    return {
        "algorithm": algorithm,
        "range_start": range_start,
        "range_end": range_end,
        "length": length,
        "workers": workers,
        "rounds": rounds,
        "durations_ms": durations,
        "avg_ms": round(mean(durations), 2),
        "min_ms": min(durations),
        "max_ms": max(durations),
        "segment_preview": last_segment[:16],
        **pi_cache_info(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark Picoin BBP hexadecimal calculation.")
    parser.add_argument("--start", type=int, default=1, help="1-based hexadecimal pi position")
    parser.add_argument("--length", type=int, default=64, help="Number of hex digits to calculate")
    parser.add_argument("--algorithm", default="bbp_hex_v1", help="Pi algorithm")
    parser.add_argument("--workers", type=int, default=1, help="Parallel worker processes")
    parser.add_argument("--rounds", type=int, default=1, help="Benchmark rounds")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_benchmark(args.start, args.length, args.algorithm, args.workers, args.rounds)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
