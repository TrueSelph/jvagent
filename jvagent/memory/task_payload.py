"""Normalize TaskStore task dict reads across current and legacy schemas."""

from __future__ import annotations

from typing import Any, Dict, Optional


def task_extension_data(task: Dict[str, Any]) -> Dict[str, Any]:
    """Return the task extension bag, preferring ``data`` over legacy ``metadata``."""
    data = dict(task.get("data") or {})
    legacy = task.get("metadata")
    if isinstance(legacy, dict):
        for key, value in legacy.items():
            data.setdefault(key, value)
    return data


def task_record_id(task: Dict[str, Any]) -> Optional[str]:
    """Return the task id from current or legacy keys."""
    tid = task.get("id") or task.get("task_id")
    return str(tid) if tid else None


def task_trigger_at(task: Dict[str, Any]) -> Optional[str]:
    """Return scheduled trigger time from ``data`` or legacy ``metadata``."""
    ext = task_extension_data(task)
    raw = ext.get("trigger_at") or ext.get("trigger_time")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def task_trigger_condition(task: Dict[str, Any]) -> str:
    """Return trigger condition keyword/mood, defaulting to ``none``."""
    ext = task_extension_data(task)
    return str(ext.get("trigger_condition") or "none").lower()
