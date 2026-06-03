"""Bulk evaluation and bulk-prediction support for GeoLens."""

from geolens.batch.metrics import EngineMetrics, EvalSummary, compute_summary
from geolens.batch.runner import BatchInput, BatchRow, run_batch

__all__ = [
    "BatchInput",
    "BatchRow",
    "EngineMetrics",
    "EvalSummary",
    "compute_summary",
    "run_batch",
]
