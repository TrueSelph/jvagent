"""Approval and receipt contract tests for a durable fake Action service."""

import hashlib
import json
import sqlite3
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from jvagent.action.orchestrator.pilot.contracts import (
    PilotCaller,
    PilotInvocation,
    PilotRunContext,
    PilotSnapshot,
)
from jvagent.action.orchestrator.pilot.tools import compose_skill_tools
from jvagent.action.orchestrator.skills import SkillDoc
from jvagent.harness.contracts import IdempotencyClass
from jvagent.tooling.tool import Tool
from jvagent.tooling.tool_result import ToolResult
from tests.action.orchestrator.pilot.effect_state_fixture import PilotEffectTestStore


class DurableEffectService:
    """Test-only approval + receipt store; SQLite is authoritative for effects."""

    def __init__(self, path: Path):
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS approvals "
                "(invocation_id TEXT PRIMARY KEY, caller TEXT, digest TEXT, expires TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS effects "
                "(invocation_id TEXT PRIMARY KEY, state TEXT, result TEXT)"
            )

    @staticmethod
    def _caller(context):
        return json.dumps(context.caller.model_dump(mode="json"), sort_keys=True)

    @staticmethod
    def _digest(payload):
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()

    @classmethod
    def _invocation_id(cls, context, name, payload) -> str:
        source = f"{context.run_id}:{name}:{cls._digest(payload)}"
        return hashlib.sha256(source.encode()).hexdigest()

    def approve(self, context, name, payload, *, expires_at=None):
        invocation_id = self._invocation_id(context, name, payload)
        expiry = expires_at or datetime.now(timezone.utc) + timedelta(minutes=5)
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR REPLACE INTO approvals VALUES (?, ?, ?, ?)",
                (
                    invocation_id,
                    self._caller(context),
                    self._digest(payload),
                    expiry.isoformat(),
                ),
            )
        return invocation_id

    async def invoke(self, context, name, payload, recheck, operation):
        invocation_id = self._invocation_id(context, name, payload)
        caller = self._caller(context)
        digest = self._digest(payload)
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT caller, digest, expires FROM approvals WHERE invocation_id=?",
                (invocation_id,),
            ).fetchone()
            effect = db.execute(
                "SELECT state, result FROM effects WHERE invocation_id=?",
                (invocation_id,),
            ).fetchone()
            if effect and effect[0] == "complete":
                return ToolResult(effect[1])
            if effect and effect[0] == "pending":
                raise RuntimeError("effect outcome requires reconciliation")
            if row is None or row[0] != caller or row[1] != digest:
                raise PermissionError("effect approval is missing or does not match")
            if datetime.now(timezone.utc) >= datetime.fromisoformat(row[2]):
                raise PermissionError("effect approval has expired")
            db.execute(
                "INSERT INTO effects VALUES (?, 'pending', '')", (invocation_id,)
            )
        if not await recheck():
            with sqlite3.connect(self.path) as db:
                db.execute(
                    "DELETE FROM effects WHERE invocation_id=? AND state='pending'",
                    (invocation_id,),
                )
            raise PermissionError("caller authority was revoked")
        result = await operation()
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE effects SET state='complete', result=? WHERE invocation_id=?",
                (result.content, invocation_id),
            )
        return result


@pytest.fixture
def effect_binding(tmp_path):
    caller = PilotCaller(agent_id="agent-1", user_id="user-1", session_id="session-1")
    context = PilotRunContext(
        caller=caller,
        task_id="effect-task",
        run_id="effect-run",
        skill_id="test_effect",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
    )
    skill = SkillDoc(
        name="test_effect",
        description="Test-only effect",
        body="Request approval before writing.",
        requires_tools=("fake_service__write",),
    )
    calls = []

    async def write(value):
        calls.append(value)
        return ToolResult(f"stored:{value}")

    tool = Tool(
        name="fake_service__write",
        description="Persist one fake value.",
        parameters_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        execute=write,
        idempotency_class=IdempotencyClass.NON_RETRYABLE,
    )
    service = DurableEffectService(tmp_path / "effects.sqlite")
    allowed = [True]

    async def access(*_args):
        return allowed[-1]

    async def invoke_effect(ctx, name, payload, recheck, operation):
        return await service.invoke(ctx, name, payload, recheck, operation)

    return context, caller, skill, tool, calls, service, allowed, access, invoke_effect


@pytest.mark.asyncio
async def test_effect_denial_approval_and_repeated_delivery_are_durable(effect_binding):
    context, _, skill, tool, calls, service, _, access, invoke_effect = effect_binding
    (bound,) = await compose_skill_tools(
        skill,
        [("FakeServiceAction", tool)],
        run_context=context,
        access_check=access,
        effect_invoker=invoke_effect,
    )
    args = {"value": "alpha"}
    with pytest.raises(PermissionError, match="approval"):
        await bound.function(SimpleNamespace(deps=context), **args)
    assert calls == []

    invocation_id = service.approve(context, tool.name, args)
    assert await bound.function(SimpleNamespace(deps=context), **args) == "stored:alpha"
    assert await bound.function(SimpleNamespace(deps=context), **args) == "stored:alpha"
    assert calls == ["alpha"]
    with sqlite3.connect(service.path) as db:
        row = db.execute(
            "SELECT state, result FROM effects WHERE invocation_id=?", (invocation_id,)
        ).fetchone()
    assert row == ("complete", "stored:alpha")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation, message",
    [
        ("payload", "approval"),
        ("caller", "approval"),
        ("expiry", "expired"),
    ],
)
async def test_effect_approval_is_bound_to_payload_caller_and_expiry(
    effect_binding, mutation, message
):
    context, _, skill, tool, calls, service, _, access, invoke_effect = effect_binding
    args = {"value": "approved"}
    expires = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
        if mutation == "expiry"
        else None
    )
    service.approve(context, tool.name, args, expires_at=expires)
    (bound,) = await compose_skill_tools(
        skill,
        [("FakeServiceAction", tool)],
        run_context=context,
        access_check=access,
        effect_invoker=invoke_effect,
    )
    if mutation == "payload":
        args = {"value": "changed"}
    elif mutation == "caller":
        context = context.model_copy(
            update={
                "caller": PilotCaller(
                    agent_id="agent-1", user_id="other-user", session_id="session-1"
                )
            }
        )
    with pytest.raises(PermissionError, match=message):
        await bound.function(SimpleNamespace(deps=context), **args)
    assert calls == []


@pytest.mark.asyncio
async def test_effect_rechecks_revoked_authority_and_does_not_write(effect_binding):
    context, _, skill, tool, calls, service, allowed, access, invoke_effect = (
        effect_binding
    )
    args = {"value": "revoked"}
    service.approve(context, tool.name, args)
    access_results = [True, False]

    async def revoke_after_approval(*_args):
        return access_results.pop(0)

    (bound,) = await compose_skill_tools(
        skill,
        [("FakeServiceAction", tool)],
        run_context=context,
        access_check=revoke_after_approval,
        effect_invoker=invoke_effect,
    )
    with pytest.raises(PermissionError, match="revoked"):
        await bound.function(SimpleNamespace(deps=context), **args)
    assert calls == []


@pytest.mark.asyncio
async def test_uncertain_effect_is_not_replayed_automatically(effect_binding):
    context, _, skill, tool, calls, service, _, access, invoke_effect = effect_binding
    args = {"value": "possibly-written"}
    invocation_id = service.approve(context, tool.name, args)
    with sqlite3.connect(service.path) as db:
        db.execute("INSERT INTO effects VALUES (?, 'pending', '')", (invocation_id,))
    (bound,) = await compose_skill_tools(
        skill,
        [("FakeServiceAction", tool)],
        run_context=context,
        access_check=access,
        effect_invoker=invoke_effect,
    )
    with pytest.raises(RuntimeError, match="reconciliation"):
        await bound.function(SimpleNamespace(deps=context), **args)
    assert calls == []


@pytest.mark.asyncio
async def test_approved_composed_effect_records_task_intent_and_settled_receipt(
    tmp_path,
):
    caller = PilotCaller(agent_id="a1", user_id="u1", session_id="s1")
    durable = []

    class Conversation:
        def __init__(self, tasks=None):
            self.tasks = deepcopy(tasks or [])

        async def save(self):
            durable[:] = deepcopy(self.tasks)
            return None

        async def flush(self):
            durable[:] = deepcopy(self.tasks)
            return None

    conversation = Conversation()
    state = PilotEffectTestStore(conversation)
    snapshot = PilotSnapshot(
        caller=caller,
        skill_id="test_effect",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
        question="Run the approved fake write.",
    )
    handle = await state.create(
        snapshot, title="fake effect", description="Persist one test value"
    )
    context = PilotRunContext(
        caller=caller,
        task_id=handle.id,
        run_id="run-task-effect",
        skill_id="test_effect",
        skill_digest=snapshot.skill_digest,
        config_digest=snapshot.config_digest,
    )
    skill = SkillDoc(
        name="test_effect",
        description="Test-only effect witness",
        body="Use the approved fake write.",
        requires_tools=("fake_service__write",),
    )
    calls = []

    async def write(value):
        calls.append(value)
        return ToolResult(f"stored:{value}")

    tool = Tool(
        name="fake_service__write",
        description="Write to the durable fake service.",
        parameters_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        execute=write,
        idempotency_class=IdempotencyClass.NON_RETRYABLE,
    )
    service = DurableEffectService(tmp_path / "task-effects.sqlite")
    args = {"value": "approved-value"}
    invocation_id = service._invocation_id(context, tool.name, args)
    payload_digest = service._digest(args)
    approval_id = service.approve(context, tool.name, args)
    invocation = PilotInvocation(
        invocation_id=invocation_id,
        tool_name=tool.name,
        payload_digest=payload_digest,
    )
    snapshot = await state.record_invocation(handle, snapshot, invocation)
    waiting = await state.wait_for_approval(
        handle,
        snapshot,
        invocation_id=invocation_id,
        approval_id=approval_id,
        payload_digest=payload_digest,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    resumed = await state.resume(
        handle,
        caller=caller,
        skill_id="test_effect",
        skill_digest=snapshot.skill_digest,
        config_digest=snapshot.config_digest,
        approval_id=approval_id,
        approval_payload_digest=payload_digest,
    )
    assert waiting.status == "parked"
    assert resumed.status == "running"

    async def access(*_args):
        return True

    async def invoke_effect(ctx, name, payload, recheck, operation):
        current = PilotSnapshot.model_validate(handle.snapshot)
        recorded = next(
            item for item in current.invocations if item.invocation_id == invocation_id
        )
        if recorded.status == "settled":
            return ToolResult(recorded.result or "")
        if recorded.status == "started":
            await state.require_reconciliation(
                handle, current, "effect outcome needs reconciliation"
            )
            raise RuntimeError("effect outcome requires reconciliation")
        started = await state.mark_invocation_started(handle, current, invocation_id)
        result = await service.invoke(ctx, name, payload, recheck, operation)
        await state.settle_invocation(handle, started, invocation_id, result.content)
        return result

    (bound,) = await compose_skill_tools(
        skill,
        [("FakeServiceAction", tool)],
        run_context=context,
        access_check=access,
        effect_invoker=invoke_effect,
    )
    target = SimpleNamespace(deps=context)
    assert await bound.function(target, **args) == "stored:approved-value"
    assert await bound.function(target, **args) == "stored:approved-value"
    reloaded = PilotEffectTestStore(Conversation(durable))
    _, persisted = reloaded.load(
        handle.id,
        caller=caller,
        skill_id="test_effect",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
    )
    assert persisted.invocations[0].status == "settled"
    assert persisted.invocations[0].result == "stored:approved-value"
    assert calls == ["approved-value"]
