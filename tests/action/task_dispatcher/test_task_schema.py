"""TaskDispatcher reads TaskStore task ids from ``id``, not legacy ``task_id``."""

from __future__ import annotations

from jvagent.memory.task_payload import (
    task_extension_data,
    task_record_id,
    task_trigger_at,
)


def test_dispatch_context_reads_id_and_data():
    task = {
        "id": "task_abc123",
        "description": "Follow up on signup",
        "data": {
            "context": "User asked for a reminder",
            "channel": "web",
            "trigger_at": "2026-05-30T10:00",
        },
    }
    assert task_record_id(task) == "task_abc123"
    ext = task_extension_data(task)
    assert ext["context"] == "User asked for a reminder"
    assert ext["channel"] == "web"


def test_dispatch_context_legacy_metadata_fallback():
    task = {
        "task_id": "legacy_99",
        "title": "Legacy follow up",
        "metadata": {
            "context": "Legacy context",
            "channel": "sms",
            "trigger_time": "2026-05-30T09:00",
        },
    }
    assert task_record_id(task) == "legacy_99"
    ext = task_extension_data(task)
    assert ext["context"] == "Legacy context"
    assert task_trigger_at(task) == "2026-05-30T09:00"
