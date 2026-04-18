"""Tests for Conversation task accessors backed by TaskService."""

import uuid

import pytest

from jvagent.memory.conversation import Conversation
from jvagent.memory.services.task_service import TaskService


def _unique_session_id():
    return f"test-sess-{uuid.uuid4().hex[:12]}"


@pytest.mark.asyncio
async def test_task_service_start_persists_task(test_db):
    """TaskService.start persists a new active task on the conversation."""
    conv = await Conversation.create(
        session_id=_unique_session_id(),
        user_id="user1",
        channel="default",
    )
    try:
        svc = TaskService(conv)
        await svc.start(
            description="SignupInterviewInteractAction",
            task_type="INTERVIEW",
            action_name="SignupInterviewInteractAction",
        )
        assert len(conv.active_tasks) == 1
        t = conv.active_tasks[0]
        assert t["description"] == "SignupInterviewInteractAction"
        assert t["action_name"] == "SignupInterviewInteractAction"
        assert t["status"] == "active"
        assert (
            t["task_id"].startswith("SignupInterviewInteractAction:")
            and len(t["task_id"]) > 35
        )
        assert "created_at" in t
        assert "updated_at" in t
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_singleton_action_supersedes_previous_task(test_db):
    """singleton_action transitions the prior active task to 'superseded'."""
    conv = await Conversation.create(
        session_id=_unique_session_id(),
        user_id="user1",
        channel="default",
    )
    try:
        svc = TaskService(conv)
        first = await svc.start(
            description="SignupInterviewInteractAction",
            task_type="INTERVIEW",
            action_name="SignupInterviewInteractAction",
            singleton_action=True,
        )
        second = await svc.start(
            description="SignupInterviewInteractAction",
            task_type="INTERVIEW",
            action_name="SignupInterviewInteractAction",
            metadata={"state": "REVIEW"},
            singleton_action=True,
        )

        assert len(conv.active_tasks) == 2
        by_id = {t["task_id"]: t for t in conv.active_tasks}
        assert by_id[first.task_id]["status"] == "superseded"
        assert by_id[second.task_id]["status"] == "active"
        assert by_id[second.task_id]["metadata"]["state"] == "REVIEW"
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_update_status_transitions_task_and_preserves_history(test_db):
    """update_status transitions a task and keeps it in active_tasks for audit."""
    conv = await Conversation.create(
        session_id=_unique_session_id(),
        user_id="user1",
        channel="default",
    )
    try:
        svc = TaskService(conv)
        await svc.start(
            description="Signup interview",
            task_type="INTERVIEW",
            action_name="SignupInterviewInteractAction",
        )
        assert conv.active_tasks[0]["status"] == "active"

        updated = await svc.update_status(
            status="cancelled",
            action_name="SignupInterviewInteractAction",
        )
        assert updated is True
        assert len(conv.active_tasks) == 1
        assert conv.active_tasks[0]["status"] == "cancelled"
        assert "updated_at" in conv.active_tasks[0]
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_complete_marks_task_completed(test_db):
    """complete transitions a task to the completed terminal status."""
    conv = await Conversation.create(
        session_id=_unique_session_id(),
        user_id="user1",
        channel="default",
    )
    try:
        svc = TaskService(conv)
        handle = await svc.start(
            description="Signup interview",
            task_type="INTERVIEW",
            action_name="SignupInterviewInteractAction",
        )
        assert await handle.complete() is True
        assert conv.active_tasks[0]["status"] == "completed"
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_update_status_distinguishes_tasks_by_id_or_description(test_db):
    """update_status with task_id or description targets a single task."""
    conv = await Conversation.create(
        session_id=_unique_session_id(),
        user_id="user1",
        channel="default",
    )
    try:
        svc = TaskService(conv)
        first = await svc.start(
            description="Task A",
            task_type="TASK",
            action_name="Action1",
        )
        await svc.start(
            description="Task B",
            task_type="TASK",
            action_name="Action2",
        )
        assert len(conv.active_tasks) == 2

        assert await svc.update_status(status="cancelled", task_id=first.task_id)
        assert conv.active_tasks[0]["status"] == "cancelled"
        assert conv.active_tasks[1]["status"] == "active"

        assert await svc.update_status(status="completed", description="Task B")
        assert conv.active_tasks[1]["status"] == "completed"
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_update_status_returns_false_when_not_found(test_db):
    """update_status returns False when no matching task exists."""
    conv = await Conversation.create(
        session_id=_unique_session_id(),
        user_id="user1",
        channel="default",
    )
    try:
        svc = TaskService(conv)
        assert (
            await svc.update_status(status="completed", action_name="NoSuchAction")
            is False
        )
    finally:
        await conv.delete(cascade=True)


def test_get_active_tasks_returns_all():
    """get_active_tasks returns all tasks when no filters provided."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.active_tasks = [
        {
            "task_id": "1",
            "description": "Task1",
            "action_name": "Action1",
            "status": "active",
        },
        {
            "task_id": "2",
            "description": "Task2",
            "action_name": "Action2",
            "status": "inactive",
        },
    ]
    result = conv.get_active_tasks()
    assert len(result) == 2


def test_get_active_tasks_filters_by_status():
    """get_active_tasks filters by status when provided."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.active_tasks = [
        {
            "task_id": "1",
            "description": "Task1",
            "action_name": "Action1",
            "status": "active",
        },
        {
            "task_id": "2",
            "description": "Task2",
            "action_name": "Action2",
            "status": "inactive",
        },
    ]
    result = conv.get_active_tasks(status="active")
    assert len(result) == 1
    assert result[0]["description"] == "Task1"


def test_get_active_tasks_filters_by_action_name():
    """get_active_tasks filters by action_name when provided."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.active_tasks = [
        {
            "task_id": "1",
            "description": "Task1",
            "action_name": "Action1",
            "status": "active",
        },
        {
            "task_id": "2",
            "description": "Task2",
            "action_name": "Action2",
            "status": "active",
        },
    ]
    result = conv.get_active_tasks(action_name="Action2")
    assert len(result) == 1
    assert result[0]["action_name"] == "Action2"


def test_get_active_task_by_description():
    """get_active_task returns matching task when filtering by description."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.active_tasks = [
        {"task_id": "1", "description": "Task1", "status": "active"},
        {"task_id": "2", "description": "Task2", "status": "active"},
    ]
    t = conv.get_active_task(description="Task2")
    assert t is not None
    assert t["description"] == "Task2"


def test_get_active_task_returns_none_when_description_not_found():
    """get_active_task returns None when no match for description."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.active_tasks = [{"task_id": "1", "description": "Task1", "status": "active"}]
    t = conv.get_active_task(description="Task2")
    assert t is None


def test_get_active_task_by_action_name():
    """get_active_task returns matching task when filtering by action_name."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.active_tasks = [
        {
            "task_id": "1",
            "description": "Task1",
            "action_name": "Action1",
            "status": "active",
        },
        {
            "task_id": "2",
            "description": "Task2",
            "action_name": "Action2",
            "status": "active",
        },
    ]
    t = conv.get_active_task(action_name="Action2")
    assert t is not None
    assert t["action_name"] == "Action2"


def test_get_active_task_returns_none_when_action_name_not_found():
    """get_active_task returns None when no match for action_name."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.active_tasks = [
        {
            "task_id": "1",
            "description": "Task1",
            "action_name": "Action1",
            "status": "active",
        }
    ]
    t = conv.get_active_task(action_name="Action2")
    assert t is None


def test_get_active_tasks_for_context_returns_active_descriptions():
    """get_active_tasks_for_context returns only active task descriptions."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.active_tasks = [
        {"task_id": "1", "description": "Task1", "status": "active"},
        {"task_id": "2", "description": "Task2", "status": "inactive"},
    ]
    result = conv.get_active_tasks_for_context()
    assert result == ["Task1"]


def test_get_active_task_by_task_type_returns_action_name():
    """get_active_task with task_type and status returns task; use .get('action_name') for name."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.active_tasks = [
        {
            "task_id": "1",
            "description": "Guide user to complete SignupInterviewInteractAction",
            "action_name": "SignupInterviewInteractAction",
            "status": "active",
            "task_type": "INTERVIEW",
        },
    ]
    t = conv.get_active_task(task_type="INTERVIEW", status="active")
    assert t is not None
    assert t.get("action_name") == "SignupInterviewInteractAction"


def test_get_active_task_returns_none_when_no_matching_task_type():
    """get_active_task returns None when no active task of given type."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.active_tasks = [
        {
            "task_id": "1",
            "description": "Task1",
            "action_name": "OtherAction",
            "status": "active",
        },
    ]
    t = conv.get_active_task(task_type="INTERVIEW", status="active")
    assert t is None


def test_get_active_task_returns_none_when_task_completed():
    """get_active_task with status=active returns None when matching task is completed."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.active_tasks = [
        {
            "task_id": "1",
            "description": "Guide user to complete SignupInterviewInteractAction",
            "action_name": "SignupInterviewInteractAction",
            "status": "completed",
            "task_type": "INTERVIEW",
        },
    ]
    t = conv.get_active_task(task_type="INTERVIEW", status="active")
    assert t is None


def test_get_active_task_supports_custom_task_type():
    """get_active_task supports arbitrary task_type for flexibility."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.active_tasks = [
        {
            "task_id": "1",
            "description": "Custom flow",
            "action_name": "CustomFlowAction",
            "status": "active",
            "task_type": "CUSTOM_FLOW",
        },
    ]
    t = conv.get_active_task(task_type="CUSTOM_FLOW", status="active")
    assert t is not None
    assert t.get("action_name") == "CustomFlowAction"
    assert conv.get_active_task(task_type="INTERVIEW", status="active") is None


@pytest.mark.asyncio
async def test_task_service_start_with_task_type_stores_top_level(test_db):
    """task_type provided at start time is stored as a top-level property."""
    conv = await Conversation.create(
        session_id=_unique_session_id(),
        user_id="user1",
        channel="default",
    )
    try:
        svc = TaskService(conv)
        await svc.start(
            description="Guide user to complete SignupInterviewInteractAction",
            task_type="INTERVIEW",
            action_name="SignupInterviewInteractAction",
        )
        assert len(conv.active_tasks) == 1
        t = conv.active_tasks[0]
        assert t["task_type"] == "INTERVIEW"
        t = conv.get_active_task(task_type="INTERVIEW", status="active")
        assert t is not None
        assert t.get("action_name") == "SignupInterviewInteractAction"
    finally:
        await conv.delete(cascade=True)
