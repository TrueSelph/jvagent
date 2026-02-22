"""Benchmarking utilities for measuring performance improvements."""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class PerformanceMetrics:
    """Container for performance metrics."""

    def __init__(self):
        self.db_reads: int = 0
        self.db_writes: int = 0
        self.total_latency_ms: float = 0.0
        self.interaction_saves: int = 0
        self.conversation_saves: int = 0
        self.cache_hits: int = 0
        self.cache_misses: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to dictionary."""
        return {
            "db_reads": self.db_reads,
            "db_writes": self.db_writes,
            "total_latency_ms": self.total_latency_ms,
            "interaction_saves": self.interaction_saves,
            "conversation_saves": self.conversation_saves,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
        }

    def __repr__(self) -> str:
        return (
            f"PerformanceMetrics("
            f"reads={self.db_reads}, writes={self.db_writes}, "
            f"latency={self.total_latency_ms:.2f}ms, "
            f"interaction_saves={self.interaction_saves}, "
            f"conversation_saves={self.conversation_saves}, "
            f"cache_hits={self.cache_hits}, cache_misses={self.cache_misses}"
            f")"
        )


@asynccontextmanager
async def measure_latency(metrics: Optional[PerformanceMetrics] = None):
    """Context manager to measure latency of an operation.

    Usage:
        async with measure_latency(metrics) as timer:
            # Your code here
            pass
        # metrics.total_latency_ms will be updated

    Args:
        metrics: Optional PerformanceMetrics instance to update

    Yields:
        Timer function that can be called to get elapsed time
    """
    start_time = time.perf_counter()

    def get_elapsed_ms() -> float:
        return (time.perf_counter() - start_time) * 1000

    try:
        yield get_elapsed_ms
    finally:
        elapsed_ms = get_elapsed_ms()
        if metrics:
            metrics.total_latency_ms = elapsed_ms


def log_metrics(metrics: PerformanceMetrics, operation: str = "operation") -> None:
    """Log performance metrics.

    Args:
        metrics: PerformanceMetrics instance to log
        operation: Name of the operation being measured
    """
    logger.info(
        f"Performance metrics for {operation}: "
        f"{metrics.db_reads} reads, {metrics.db_writes} writes, "
        f"{metrics.total_latency_ms:.2f}ms latency, "
        f"{metrics.interaction_saves} interaction saves, "
        f"{metrics.conversation_saves} conversation saves, "
        f"{metrics.cache_hits} cache hits, {metrics.cache_misses} cache misses"
    )


async def compare_performance(
    baseline_func: Callable,
    optimized_func: Callable,
    iterations: int = 10,
) -> Dict[str, Any]:
    """Compare performance between baseline and optimized implementations.

    Args:
        baseline_func: Async function to measure baseline performance
        optimized_func: Async function to measure optimized performance
        iterations: Number of iterations to run (default: 10)

    Returns:
        Dictionary with comparison results including:
        - baseline_metrics: Average metrics for baseline
        - optimized_metrics: Average metrics for optimized
        - improvement_percent: Percentage improvement
        - speedup: Speedup factor (baseline/optimized)
    """
    baseline_times = []
    optimized_times = []

    for i in range(iterations):
        # Baseline
        start = time.perf_counter()
        await baseline_func()
        baseline_times.append((time.perf_counter() - start) * 1000)

        # Small delay between runs
        await asyncio.sleep(0.1)

        # Optimized
        start = time.perf_counter()
        await optimized_func()
        optimized_times.append((time.perf_counter() - start) * 1000)

        # Small delay between runs
        await asyncio.sleep(0.1)

    baseline_avg = sum(baseline_times) / len(baseline_times)
    optimized_avg = sum(optimized_times) / len(optimized_times)

    improvement = ((baseline_avg - optimized_avg) / baseline_avg) * 100
    speedup = baseline_avg / optimized_avg if optimized_avg > 0 else float("inf")

    return {
        "baseline_avg_ms": baseline_avg,
        "optimized_avg_ms": optimized_avg,
        "improvement_percent": improvement,
        "speedup": speedup,
        "iterations": iterations,
    }
