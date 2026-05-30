"""TaskStore task dict normalization helpers."""

from jvagent.memory.task_payload import (
    task_extension_data,
    task_record_id,
    task_trigger_at,
    task_trigger_condition,
)


def test_task_extension_data_prefers_data_over_metadata():
    task = {
        "data": {"context": "from data", "trigger_at": "2026-05-30T10:00"},
        "metadata": {"context": "legacy", "trigger_time": "2026-05-29T09:00"},
    }
    ext = task_extension_data(task)
    assert ext["context"] == "from data"
    assert ext["trigger_at"] == "2026-05-30T10:00"
    assert ext["trigger_time"] == "2026-05-29T09:00"


def test_task_record_id_accepts_legacy_task_id():
    assert task_record_id({"id": "task_abc"}) == "task_abc"
    assert task_record_id({"task_id": "legacy_1"}) == "legacy_1"
    assert task_record_id({}) is None


def test_task_trigger_helpers_read_data_and_legacy():
    task = {
        "data": {"trigger_at": "2026-05-30T10:00", "trigger_condition": "busy"},
    }
    assert task_trigger_at(task) == "2026-05-30T10:00"
    assert task_trigger_condition(task) == "busy"

    legacy = {
        "metadata": {"trigger_time": "2026-05-29T09:00", "trigger_condition": "none"}
    }
    assert task_trigger_at(legacy) == "2026-05-29T09:00"
    assert task_trigger_condition(legacy) == "none"
