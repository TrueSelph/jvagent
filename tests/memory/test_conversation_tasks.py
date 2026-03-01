"""Tests for Conversation task tracker (active_tasks)."""

import uuid

import pytest

from jvagent.memory.conversation import Conversation


def _unique_session_id():
    return f"test-sess-{uuid.uuid4().hex[:12]}"


@pytest.mark.asyncio
async def test_add_active_task_adds_new_task(test_db):
    """add_active_task adds a new task when none exists."""
    conv = await Conversation.create(
        session_id=_unique_session_id(),
        user_id="user1",
        channel="default",
    )
    try:
        await conv.add_active_task(
            "SignupInterviewInteractAction",
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
async def test_add_active_task_upserts_existing(test_db):
    """add_active_task updates existing task when description or action_name matches."""
    conv = await Conversation.create(
        session_id=_unique_session_id(),
        user_id="user1",
        channel="default",
    )
    try:
        await conv.add_active_task(
            "SignupInterviewInteractAction",
            action_name="SignupInterviewInteractAction",
        )
        await conv.add_active_task(
            "SignupInterviewInteractAction",
            metadata={"state": "REVIEW"},
            action_name="SignupInterviewInteractAction",
        )
        assert len(conv.active_tasks) == 1
        t = conv.active_tasks[0]
        assert t["metadata"] == {"state": "REVIEW"}
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_update_task_updates_status_preserves_task(test_db):
    """update_task updates status and preserves task for audit log."""
    conv = await Conversation.create(
        session_id=_unique_session_id(),
        user_id="user1",
        channel="default",
    )
    try:
        await conv.add_active_task(
            "Signup interview",
            action_name="SignupInterviewInteractAction",
        )
        assert len(conv.active_tasks) == 1
        assert conv.active_tasks[0]["status"] == "active"

        updated = await conv.update_task(
            status="cancelled",
            action_name="SignupInterviewInteractAction",
        )
        assert updated is True
        assert len(conv.active_tasks) == 1
        assert conv.active_tasks[0]["status"] == "cancelled"
        assert conv.active_tasks[0]["action_name"] == "SignupInterviewInteractAction"
        assert "updated_at" in conv.active_tasks[0]
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_remove_active_task_transitions_to_completed(test_db):
    """remove_active_task transitions task to completed (preserves for audit log)."""
    conv = await Conversation.create(
        session_id=_unique_session_id(),
        user_id="user1",
        channel="default",
    )
    try:
        await conv.add_active_task(
            "Signup interview",
            action_name="SignupInterviewInteractAction",
        )
        assert len(conv.active_tasks) == 1
        updated = await conv.remove_active_task(
            action_name="SignupInterviewInteractAction",
        )
        assert updated is True
        assert len(conv.active_tasks) == 1
        assert conv.active_tasks[0]["status"] == "completed"
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_remove_active_task_by_description_transitions(test_db):
    """remove_active_task by description transitions to completed."""
    conv = await Conversation.create(
        session_id=_unique_session_id(),
        user_id="user1",
        channel="default",
    )
    try:
        await conv.add_active_task("SignupInterviewInteractAction")
        assert len(conv.active_tasks) == 1
        updated = await conv.remove_active_task(
            description="SignupInterviewInteractAction",
        )
        assert updated is True
        assert len(conv.active_tasks) == 1
        assert conv.active_tasks[0]["status"] == "completed"
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_update_task_by_task_id_and_description_distinguishes_tasks(test_db):
    """update_task with task_id or description updates only the matching task."""
    conv = await Conversation.create(
        session_id=_unique_session_id(),
        user_id="user1",
        channel="default",
    )
    try:
        await conv.add_active_task(
            "Task A",
            action_name="Action1",
        )
        await conv.add_active_task(
            "Task B",
            action_name="Action2",
        )
        assert len(conv.active_tasks) == 2
        task_a_id = conv.active_tasks[0]["task_id"]

        updated = await conv.update_task(
            status="cancelled",
            task_id=task_a_id,
        )
        assert updated is True
        assert conv.active_tasks[0]["status"] == "cancelled"
        assert conv.active_tasks[1]["status"] == "active"

        updated = await conv.update_task(
            status="completed",
            description="Task B",
        )
        assert updated is True
        assert conv.active_tasks[1]["status"] == "completed"
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_remove_active_task_returns_false_when_not_found(test_db):
    """remove_active_task returns False when no matching task."""
    conv = await Conversation.create(
        session_id=_unique_session_id(),
        user_id="user1",
        channel="default",
    )
    try:
        removed = await conv.remove_active_task(action_name="NonExistentTask")
        assert removed is False
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
    """get_active_task_by_description returns matching task."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.active_tasks = [
        {"task_id": "1", "description": "Task1", "status": "active"},
        {"task_id": "2", "description": "Task2", "status": "active"},
    ]
    t = conv.get_active_task_by_description("Task2")
    assert t is not None
    assert t["description"] == "Task2"


def test_get_active_task_by_description_returns_none_when_not_found():
    """get_active_task_by_description returns None when no match."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.active_tasks = [{"task_id": "1", "description": "Task1", "status": "active"}]
    t = conv.get_active_task_by_description("Task2")
    assert t is None


def test_get_active_task_by_action():
    """get_active_task_by_action returns matching task by action_name."""
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
    t = conv.get_active_task_by_action("Action2")
    assert t is not None
    assert t["action_name"] == "Action2"


def test_get_active_task_by_action_returns_none_when_not_found():
    """get_active_task_by_action returns None when no match."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.active_tasks = [
        {
            "task_id": "1",
            "description": "Task1",
            "action_name": "Action1",
            "status": "active",
        }
    ]
    t = conv.get_active_task_by_action("Action2")
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


def test_get_active_interview_action_name_returns_action_when_active():
    """get_active_interview_action_name returns action_name when active interview exists."""
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
    result = conv.get_active_interview_action_name()
    assert result == "SignupInterviewInteractAction"


def test_get_active_interview_action_name_returns_none_when_no_interview():
    """get_active_interview_action_name returns None when no active interview task."""
    conv = Conversation(session_id="", user_id="", channel="default")
    conv.active_tasks = [
        {
            "task_id": "1",
            "description": "Task1",
            "action_name": "OtherAction",
            "status": "active",
        },
    ]
    result = conv.get_active_interview_action_name()
    assert result is None


def test_get_active_interview_action_name_returns_none_when_interview_completed():
    """get_active_interview_action_name returns None when interview task is completed."""
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
    result = conv.get_active_interview_action_name()
    assert result is None


@pytest.mark.asyncio
async def test_add_active_task_with_task_type_stores_top_level(test_db):
    """add_active_task with task_type stores it as top-level property."""
    conv = await Conversation.create(
        session_id=_unique_session_id(),
        user_id="user1",
        channel="default",
    )
    try:
        await conv.add_active_task(
            "Guide user to complete SignupInterviewInteractAction",
            action_name="SignupInterviewInteractAction",
            task_type="INTERVIEW",
        )
        assert len(conv.active_tasks) == 1
        t = conv.active_tasks[0]
        assert t["task_type"] == "INTERVIEW"
        assert (
            conv.get_active_interview_action_name() == "SignupInterviewInteractAction"
        )
    finally:
        await conv.delete(cascade=True)
