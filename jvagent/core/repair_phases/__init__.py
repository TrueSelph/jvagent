"""Repair phase facades.

This package provides stable import points while graph repair logic is
incrementally decomposed from ``graph_repair_job.py``.
"""

from .engine import run_repair_session

__all__ = ["run_repair_session"]
