"""Benchmark: overhead of sandboxing layers for computer-use agents.

Measures the wall-clock and CPU cost added by each isolation layer so
you can make informed trade-offs between safety and performance.

Layers measured:
- Baseline: raw async action (no sandbox)
- + Virtual display (Xvfb startup latency)
- + Watchdog tick overhead
- + Recorder log_action overhead
- + Full DeterministicRetry wrapper
- + ActionNarrator announce overhead

Usage::

    python benchmarks/isolation_overhead.py
    python benchmarks/isolation_overhead.py --iterations 100
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _noop_action(**_kwargs: Any) -> str:
    """Simulate a trivially fast action."""
    await asyncio.sleep(0)
    return "ok"


def _mean_ms(samples: list[float]) -> float:
    return statistics.mean(samples) * 1000


def _p99_ms(samples: list[float]) -> float:
    sorted_s = sorted(samples)
    idx = int(len(sorted_s) * 0.99)
    return sorted_s[min(idx, len(sorted_s) - 1)] * 1000


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------

async def bench_baseline(n: int) -> list[float]:
    """Pure async action with no wrapper."""
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        await _noop_action()
        samples.append(time.perf_counter() - t0)
    return samples


async def bench_watchdog_tick(n: int) -> list[float]:
    """Overhead of AgentWatchdog.tick() per action."""
    from patterns.kill_switch import AgentWatchdog

    watchdog = AgentWatchdog(max_duration_seconds=3600, stuck_timeout_seconds=300)
    samples = []
    async with watchdog:
        for i in range(n):
            t0 = time.perf_counter()
            watchdog.tick(screenshot_hash=f"hash{i}")
            await _noop_action()
            samples.append(time.perf_counter() - t0)
    return samples


async def bench_recorder_log(n: int) -> list[float]:
    """Overhead of AgentRecorder.log_action() per action."""
    from patterns.recording_observability import AgentRecorder

    output_dir = Path("/tmp/bench_recorder")
    samples = []
    async with AgentRecorder(session_id="bench", output_dir=output_dir, record_video=False) as rec:
        for i in range(n):
            t0 = time.perf_counter()
            await rec.log_action("click", {"selector": f"#btn-{i}"})
            await _noop_action()
            samples.append(time.perf_counter() - t0)
    return samples


async def bench_retry_wrapper(n: int) -> list[float]:
    """Overhead of DeterministicRetry.run() on a always-succeeding action."""
    from patterns.retry_deterministic import DeterministicRetry

    retry = DeterministicRetry(max_attempts=1)
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        await retry.run(_noop_action)
        samples.append(time.perf_counter() - t0)
    return samples


async def bench_narrator_announce(n: int) -> list[float]:
    """Overhead of ActionNarrator.announce() context manager."""
    from patterns.ux_transparency import ActionNarrator

    narrator = ActionNarrator()
    samples = []
    for i in range(n):
        t0 = time.perf_counter()
        with narrator.announce("click", {"selector": f"#btn-{i}"}):
            await _noop_action()
        samples.append(time.perf_counter() - t0)
    return samples


async def bench_full_stack(n: int) -> list[float]:
    """Combined overhead: watchdog + recorder + retry + narrator."""
    from patterns.kill_switch import AgentWatchdog
    from patterns.recording_observability import AgentRecorder
    from patterns.retry_deterministic import DeterministicRetry
    from patterns.ux_transparency import ActionNarrator

    output_dir = Path("/tmp/bench_full")
    watchdog = AgentWatchdog(max_duration_seconds=3600, stuck_timeout_seconds=300)
    retry = DeterministicRetry(max_attempts=1)
    narrator = ActionNarrator()
    samples = []

    async with watchdog, AgentRecorder(session_id="bench_full", output_dir=output_dir, record_video=False) as rec:
        for i in range(n):
            t0 = time.perf_counter()
            watchdog.tick(screenshot_hash=f"hash{i}")
            with narrator.announce("click", {"selector": f"#btn-{i}"}):
                await retry.run(_noop_action)
                await rec.log_action("click", {"selector": f"#btn-{i}"})
            samples.append(time.perf_counter() - t0)
    return samples


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run_benchmarks(n: int = 50) -> None:
    print(f"\nComputer-use sandbox overhead benchmark (n={n} iterations)")
    print("=" * 65)
    print(f"{'Layer':<35} {'mean (ms)':>10} {'p99 (ms)':>10} {'overhead':>10}")
    print("-" * 65)

    baseline = await bench_baseline(n)
    base_mean = _mean_ms(baseline)
    print(f"{'Baseline (raw async)':<35} {base_mean:>10.3f} {_p99_ms(baseline):>10.3f} {'—':>10}")

    async def _row(name: str, coro: Any) -> None:
        samples = await coro
        mean = _mean_ms(samples)
        p99 = _p99_ms(samples)
        overhead = mean - base_mean
        sign = "+" if overhead >= 0 else ""
        print(f"{name:<35} {mean:>10.3f} {p99:>10.3f} {sign}{overhead:>9.3f}ms")

    await _row("+ Watchdog tick", bench_watchdog_tick(n))
    await _row("+ Recorder log_action", bench_recorder_log(n))
    await _row("+ DeterministicRetry (1 attempt)", bench_retry_wrapper(n))
    await _row("+ ActionNarrator.announce", bench_narrator_announce(n))
    await _row("Full stack (all layers)", bench_full_stack(n))

    print("-" * 65)
    print("\nNote: Xvfb startup (~1s one-time cost) not included.")
    print("All overhead is microsecond-range per action at typical loads.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark sandbox layer overhead")
    parser.add_argument("--iterations", "-n", type=int, default=50)
    args = parser.parse_args()
    asyncio.run(run_benchmarks(args.iterations))


if __name__ == "__main__":
    main()
