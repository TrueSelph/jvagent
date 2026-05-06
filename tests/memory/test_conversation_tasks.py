"""Tests for Conversation task accessors backed by TaskStore."""

import uuid

import pytest

from jvagent.memory.conversation import Conversation
from jvagent.memory.task_store import TaskStore


def _unique_session_id():
    return f"test-sess-{uuid.uuid4().hex[:12]}"


@pytest.mark.asyncio
async def test_task_store_create_and_start_persists_task(test_db):
    """TaskStore.create + start persists a new active task on the conversation."""
    conv = await Conversation.create(
        session_id=_unique_session_id(),
        user_id="user1",
        channel="default",
    )
    try:
        store = TaskStore(conv)
        handle = await store.create(
            title="SignupInterviewInteractAction",
            description="SignupInterviewInteractAction",
            owner_action="SignupInterviewInteractAction",
        )
        await handle.start()
        assert len(conv.tasks) == 1
        t = conv.tasks[0]
        assert t["description"] == "SignupInterviewInteractAction"
        assert t["owner_action"] == "SignupInterviewInteractAction"
        assert t["status"] == "active"
        assert t["id"].startswith("task_")
        assert len(t["id"]) == 17
        assert "created_at" in t
        assert "updated_at" in t
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_cancel_transitions_task_and_preserves_history(test_db):
    """cancel transitions a task and keeps it in tasks for audit."""
    conv = await Conversation.create(
        session_id=_unique_session_id(),
        user_id="user1",
        channel="default",
    )
    try:
        store = TaskStore(conv)
        handle = await store.create(
            title="Signup interview",
            description="Signup interview",
            owner_action="SignupInterviewInteractAction",
        )
        await handle.start()
        assert conv.tasks[0]["status"] == "active"

        await handle.cancel()
        assert len(conv.tasks) == 1
        assert conv.tasks[0]["status"] == "cancelled"
        assert "updated_at" in conv.tasks[0]
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
        store = TaskStore(conv)
        handle = await store.create(
            title="Signup interview",
            description="Signup interview",
            owner_action="SignupInterviewInteractAction",
        )
        await handle.start()
        await handle.complete()
        assert conv.tasks[0]["status"] == "completed"
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_get_and_complete_distinguishes_tasks_by_id(test_db):
    """store.get(task_id) targets a single task for completion or cancellation."""
    conv = await Conversation.create(
        session_id=_unique_session_id(),
        user_id="user1",
        channel="default",
    )
    try:
        store = TaskStore(conv)
        first = await store.create(
            title="Task A",
            description="Task A",
            owner_action="Action1",
        )
        await first.start()
        second = await store.create(
            title="Task B",
            description="Task B",
            owner_action="Action2",
        )
        await second.start()
        assert len(conv.tasks) == 2

        handle = store.get(first.id)
        assert handle is not None
        await handle.cancel()
        assert conv.tasks[0]["status"] == "cancelled"
        assert conv.tasks[1]["status"] == "active"

        handle2 = store.get(second.id)
        assert handle2 is not None
        await handle2.complete()
        assert conv.tasks[1]["status"] == "completed"
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_get_returns_none_when_not_found(test_db):
    """store.get returns None when no matching task exists."""
    conv = await Conversation.create(
        session_id=_unique_session_id(),
        user_id="user1",
        channel="default",
    )
    try:
        store = TaskStore(conv)
        assert store.get("nonexistent") is None
    finally:
        await conv.delete(cascade=True)


def test_get_tasks_returns_all():
    """get_tasks returns all tasks when no filters provided."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.tasks = [
        {
            "id": "1",
            "title": "Task1",
            "description": "Task1",
            "owner_action": "Action1",
            "status": "active",
        },
        {
            "id": "2",
            "title": "Task2",
            "description": "Task2",
            "owner_action": "Action2",
            "status": "inactive",
        },
    ]
    result = conv.get_tasks()
    assert len(result) == 2


def test_get_tasks_filters_by_status():
    """get_tasks filters by status when provided."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.tasks = [
        {
            "id": "1",
            "title": "Task1",
            "description": "Task1",
            "owner_action": "Action1",
            "status": "active",
        },
        {
            "id": "2",
            "title": "Task2",
            "description": "Task2",
            "owner_action": "Action2",
            "status": "inactive",
        },
    ]
    result = conv.get_tasks(status="active")
    assert len(result) == 1
    assert result[0]["description"] == "Task1"


def test_get_tasks_filters_by_owner_action():
    """get_tasks filters by owner_action when provided."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.tasks = [
        {
            "id": "1",
            "title": "Task1",
            "description": "Task1",
            "owner_action": "Action1",
            "status": "active",
        },
        {
            "id": "2",
            "title": "Task2",
            "description": "Task2",
            "owner_action": "Action2",
            "status": "active",
        },
    ]
    result = conv.get_tasks(owner_action="Action2")
    assert len(result) == 1
    assert result[0]["owner_action"] == "Action2"


def test_get_task_by_description():
    """get_task returns matching task when filtering by description."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.tasks = [
        {"id": "1", "title": "Task1", "description": "Task1", "status": "active"},
        {"id": "2", "title": "Task2", "description": "Task2", "status": "active"},
    ]
    t = conv.get_task(description="Task2")
    assert t is not None
    assert t["description"] == "Task2"


def test_get_task_returns_none_when_description_not_found():
    """get_task returns None when no match for description."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.tasks = [
        {"id": "1", "title": "Task1", "description": "Task1", "status": "active"}
    ]
    t = conv.get_task(description="Task2")
    assert t is None


def test_get_task_by_owner_action():
    """get_task returns matching task when filtering by owner_action."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.tasks = [
        {
            "id": "1",
            "title": "Task1",
            "description": "Task1",
            "owner_action": "Action1",
            "status": "active",
        },
        {
            "id": "2",
            "title": "Task2",
            "description": "Task2",
            "owner_action": "Action2",
            "status": "active",
        },
    ]
    t = conv.get_task(owner_action="Action2")
    assert t is not None
    assert t["owner_action"] == "Action2"


def test_get_task_returns_none_when_owner_action_not_found():
    """get_task returns None when no match for owner_action."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.tasks = [
        {
            "id": "1",
            "title": "Task1",
            "description": "Task1",
            "owner_action": "Action1",
            "status": "active",
        }
    ]
    t = conv.get_task(owner_action="Action2")
    assert t is None


def test_get_active_tasks_for_context_returns_active_titles():
    """get_active_tasks_for_context returns only active task titles."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.tasks = [
        {"id": "1", "title": "Task1", "description": "Task1", "status": "active"},
        {"id": "2", "title": "Task2", "description": "Task2", "status": "inactive"},
    ]
    result = conv.get_active_tasks_for_context()
    assert result == ["Task1"]


def test_get_task_by_owner_action_and_status_returns_task():
    """get_task with owner_action and status returns task; use .get('owner_action') for name."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.tasks = [
        {
            "id": "1",
            "title": "SignupInterviewInteractAction",
            "description": "Guide user to complete SignupInterviewInteractAction",
            "owner_action": "SignupInterviewInteractAction",
            "status": "active",
        },
    ]
    t = conv.get_task(owner_action="SignupInterviewInteractAction", status="active")
    assert t is not None
    assert t.get("owner_action") == "SignupInterviewInteractAction"


def test_get_task_returns_none_when_no_matching_owner_action():
    """get_task returns None when no active task of given owner_action."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.tasks = [
        {
            "id": "1",
            "title": "Task1",
            "description": "Task1",
            "owner_action": "OtherAction",
            "status": "active",
        },
    ]
    t = conv.get_task(owner_action="SignupInterviewInteractAction", status="active")
    assert t is None


def test_get_task_returns_none_when_task_completed():
    """get_task with status=active returns None when matching task is completed."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.tasks = [
        {
            "id": "1",
            "title": "SignupInterviewInteractAction",
            "description": "Guide user to complete SignupInterviewInteractAction",
            "owner_action": "SignupInterviewInteractAction",
            "status": "completed",
        },
    ]
    t = conv.get_task(owner_action="SignupInterviewInteractAction", status="active")
    assert t is None
