"""
Benchmark engram retrieve_context latency.

Runs N queries against live services and reports p50/p95/p99 latency in ms.

Usage:
    python scripts/benchmark.py [--queries N] [--warmup W] [--project PROJECT]

Requires: Qdrant, Neo4j, and Ollama running locally.
"""

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

# Ensure the package is importable when run directly from the repo root.
sys.path.insert(0, str(Path(__file__).parent.parent))

from engram_mcp.tools.retrieve import retrieve_context

_QUERIES = [
    "What feedback has been given about code style or formatting?",
    "Are there any known bugs or error patterns in the project?",
    "What decisions were made about the project architecture?",
    "What external tools or references are used in this project?",
    "What are the user preferences for working on this project?",
    "How should retry logic be implemented for transient failures?",
    "What is the memory storage and retrieval flow?",
    "What are the Neo4j graph schema conventions?",
    "What embedding model is used and why?",
    "Summarise recent project decisions and rationale.",
]


def _percentile(sorted_data: list[float], p: float) -> float:
    if not sorted_data:
        return 0.0
    idx = (len(sorted_data) - 1) * p / 100
    lo, hi = int(idx), min(int(idx) + 1, len(sorted_data) - 1)
    frac = idx - lo
    return sorted_data[lo] * (1 - frac) + sorted_data[hi] * frac


async def _run_query(query: str, project: str | None) -> float:
    t0 = time.perf_counter()
    results = await retrieve_context(query=query, limit=10, project=project)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return elapsed_ms


async def benchmark(n_queries: int, n_warmup: int, project: str | None) -> None:
    print(f"Engram retrieve_context benchmark")
    print(f"  queries={n_queries}  warmup={n_warmup}  project={project or 'all'}")
    print()

    # Build query list by cycling through the sample set
    all_queries = [_QUERIES[i % len(_QUERIES)] for i in range(n_queries + n_warmup)]

    # Warmup — results discarded
    if n_warmup:
        print(f"Warming up ({n_warmup} queries)...", end="", flush=True)
        for q in all_queries[:n_warmup]:
            await _run_query(q, project)
        print(" done")

    # Timed runs
    print(f"Running {n_queries} timed queries...", end="", flush=True)
    latencies: list[float] = []
    for q in all_queries[n_warmup:]:
        ms = await _run_query(q, project)
        latencies.append(ms)
    print(" done\n")

    latencies.sort()
    mean = statistics.mean(latencies)
    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)
    p99 = _percentile(latencies, 99)
    lo = latencies[0]
    hi = latencies[-1]

    print(f"{'Metric':<10} {'ms':>8}")
    print("-" * 20)
    print(f"{'min':<10} {lo:>8.1f}")
    print(f"{'mean':<10} {mean:>8.1f}")
    print(f"{'p50':<10} {p50:>8.1f}")
    print(f"{'p95':<10} {p95:>8.1f}")
    print(f"{'p99':<10} {p99:>8.1f}")
    print(f"{'max':<10} {hi:>8.1f}")
    print()
    print(f"Total queries: {n_queries}  Total time: {sum(latencies):.0f} ms")


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark Engram retrieve_context.")
    parser.add_argument("--queries", type=int, default=20,
                        help="Number of timed queries (default: 20)")
    parser.add_argument("--warmup", type=int, default=3,
                        help="Warmup queries to discard (default: 3)")
    parser.add_argument("--project", type=str, default=None,
                        help="Scope queries to a specific project (optional)")
    args = parser.parse_args()

    asyncio.run(benchmark(args.queries, args.warmup, args.project))


if __name__ == "__main__":
    main()
