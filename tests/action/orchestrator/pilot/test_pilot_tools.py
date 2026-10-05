"""Action tools remain the effect boundary when composed as capabilities."""

import asyncio
from types import SimpleNamespace

import pytest

from jvagent.action.orchestrator.pilot.contracts import PilotCaller, PilotRunContext
from jvagent.action.orchestrator.pilot.tools import (
    PilotToolConfigurationError,
    compose_skill_tools,
)
from jvagent.action.orchestrator.skills import SkillDoc
from jvagent.harness.contracts import IdempotencyClass
from jvagent.tooling.tool import Tool
from jvagent.tooling.tool_result import ToolResult


@pytest.mark.asyncio
async def test_composition_preserves_name_schema_and_rechecks_access_each_call(caplog):
    operations = []
    checks = []
    run_context = PilotRunContext(
        caller=PilotCaller(agent_id="a1", user_id="u1", session_id="s1"),
        task_id="task-1",
        run_id="run-1",
        skill_id="research",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
    )

    async def search(query: str) -> str:
        operations.append(query)
        return f"source-1: {query}"

    skill = SkillDoc(
        name="research",
        description="Evidence-first research",
        body="Search, then fetch.",
        requires_tools=("web_search__search",),
        requires_actions=("SearchAction",),
    )
    action_tool = Tool(
        name="web_search__search",
        description="Search the web.",
        parameters_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": True,
        },
        execute=search,
    )

    async def access_check(ctx, skill_name, tool_name, args):
        checks.append((ctx.run_id, skill_name, tool_name, dict(args)))
        return True

    (bound,) = await compose_skill_tools(
        skill,
        [("SearchAction", action_tool)],
        run_context=run_context,
        access_check=access_check,
    )
    assert bound.name == "web_search__search"
    assert bound.function_schema.json_schema == action_tool.parameters_schema

    with caplog.at_level("DEBUG", logger="jvagent.action.orchestrator.pilot.tools"):
        result = await bound.function(
            SimpleNamespace(deps=run_context, tool_call_id="model-call-1"),
            query="pilot architecture",
        )
    assert result == "source-1: pilot architecture"
    assert operations == ["pilot architecture"]
    assert checks == [
        (
            "run-1",
            "research",
            "web_search__search",
            {"query": "pilot architecture"},
        )
    ]
    dispatch_record = next(
        record for record in caplog.records if record.message == "pilot Action dispatch"
    )
    assert dispatch_record.pilot_correlation_id == "run-1"
    assert dispatch_record.pilot_task_id == "task-1"
    assert dispatch_record.pilot_skill_id == "research"
    assert dispatch_record.pilot_tool_call_id == "model-call-1"
    assert dispatch_record.pilot_tool_name == "web_search__search"

    for injected in (
        {"agent_id": "forged"},
        {"effect_key": "forged"},
        {"options": {"capability_token": "forged"}},
    ):
        with pytest.raises(ValueError, match="cannot supply caller"):
            bound.args_validator(None, query="q", **injected)
    assert operations == ["pilot architecture"]


@pytest.mark.asyncio
async def test_composition_fails_for_missing_duplicate_or_wrong_owner_tools():
    skill = SkillDoc(
        name="research",
        description="Research",
        body="",
        requires_tools=("web_search__search",),
        requires_actions=("SearchAction",),
    )
    tool = Tool(
        name="web_search__search",
        description="Search",
        parameters_schema={"type": "object", "properties": {}},
        execute=lambda: "ok",
    )

    def access(*_args):
        return True

    run_context = PilotRunContext(
        caller=PilotCaller(agent_id="a1", user_id="u1", session_id="s1"),
        task_id="task-1",
        run_id="run-1",
        skill_id="research",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
    )

    with pytest.raises(PilotToolConfigurationError, match="unavailable"):
        await compose_skill_tools(
            skill, [], run_context=run_context, access_check=access
        )
    with pytest.raises(PilotToolConfigurationError, match="duplicate"):
        await compose_skill_tools(
            skill,
            [("SearchAction", tool), ("SearchAction", tool)],
            run_context=run_context,
            access_check=access,
        )
    with pytest.raises(PilotToolConfigurationError, match="not owned"):
        await compose_skill_tools(
            skill,
            [("OtherAction", tool)],
            run_context=run_context,
            access_check=access,
        )


@pytest.mark.asyncio
async def test_access_check_denial_fails_before_action_call():
    calls = []
    run_context = PilotRunContext(
        caller=PilotCaller(agent_id="a1", user_id="u1", session_id="s1"),
        task_id="task-1",
        run_id="run-1",
        skill_id="research",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
    )
    skill = SkillDoc(
        name="research",
        description="Research",
        body="",
        requires_tools=("web_search__search",),
    )
    tool = Tool(
        name="web_search__search",
        description="Search",
        parameters_schema={"type": "object", "properties": {}},
        execute=lambda **_: calls.append("called"),
    )
    (bound,) = await compose_skill_tools(
        skill,
        [("SearchAction", tool)],
        run_context=run_context,
        access_check=lambda *_args: False,
    )
    with pytest.raises(PermissionError):
        await bound.function(SimpleNamespace(deps=run_context))
    assert calls == []


@pytest.mark.asyncio
async def test_tool_result_is_bounded_before_model_and_evidence_observer():
    observed = []
    run_context = PilotRunContext(
        caller=PilotCaller(agent_id="a1", user_id="u1", session_id="s1"),
        task_id="task-1",
        run_id="run-1",
        skill_id="research",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
        max_tool_result_chars=256,
    )
    skill = SkillDoc(
        name="research",
        description="Research",
        body="",
        requires_tools=("web_fetch__fetch",),
    )
    tool = Tool(
        name="web_fetch__fetch",
        description="Fetch",
        parameters_schema={"type": "object", "properties": {}},
        execute=lambda: "x" * 1000,
    )

    async def observe(_context, _tool_name, _arguments, content):
        observed.append(content)

    (bound,) = await compose_skill_tools(
        skill,
        [("FetchAction", tool)],
        run_context=run_context,
        access_check=lambda *_args: True,
        result_observer=observe,
    )

    result = await bound.function(SimpleNamespace(deps=run_context))

    assert len(result) == 256
    assert result.endswith("[tool output truncated by pilot]")
    assert observed == [result]


@pytest.mark.asyncio
async def test_effect_tool_requires_host_invoker_and_rechecks_authority_before_call():
    calls = []
    invocations = []
    access_decisions = [True, True, True, False]
    run_context = PilotRunContext(
        caller=PilotCaller(agent_id="a1", user_id="u1", session_id="s1"),
        task_id="task-effect",
        run_id="run-effect",
        skill_id="test_effect",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
    )
    skill = SkillDoc(
        name="test_effect",
        description="Test-only effect witness",
        body="Use the effect only after approval.",
        requires_tools=("fake_service__write",),
    )
    action_tool = Tool(
        name="fake_service__write",
        description="Write to the durable fake service.",
        parameters_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        execute=lambda **args: calls.append(args) or ToolResult("written"),
        idempotency_class=IdempotencyClass.NON_RETRYABLE,
    )

    async def access(*_args):
        return access_decisions.pop(0)

    with pytest.raises(PilotToolConfigurationError, match="host effect invoker"):
        await compose_skill_tools(
            skill,
            [("FakeServiceAction", action_tool)],
            run_context=run_context,
            access_check=access,
        )

    async def approved_effect(context, name, payload, recheck, operation):
        invocations.append((context.task_id, name, dict(payload)))
        if not await recheck():
            raise PermissionError("authority changed while awaiting approval")
        return await operation()

    (bound,) = await compose_skill_tools(
        skill,
        [("FakeServiceAction", action_tool)],
        run_context=run_context,
        access_check=access,
        effect_invoker=approved_effect,
    )
    result = await bound.function(SimpleNamespace(deps=run_context), value="first")
    assert result == "written"
    assert invocations == [("task-effect", "fake_service__write", {"value": "first"})]
    assert calls == [{"value": "first"}]

    with pytest.raises(PermissionError, match="authority changed"):
        await bound.function(SimpleNamespace(deps=run_context), value="revoked")
    assert calls == [{"value": "first"}]


@pytest.mark.asyncio
async def test_action_error_result_is_not_returned_or_observed_as_success():
    observations = []
    run_context = PilotRunContext(
        caller=PilotCaller(agent_id="a1", user_id="u1", session_id="s1"),
        task_id="task-1",
        run_id="run-1",
        skill_id="research",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
    )
    skill = SkillDoc(
        name="research",
        description="Research",
        body="Search and cite evidence.",
        requires_tools=("web_search__search",),
        requires_actions=("SearchAction",),
    )

    async def failed_search(query: str) -> ToolResult:
        return ToolResult.error(f"provider failed for {query}")

    async def access(*_args):
        return True

    async def observe(*args):
        observations.append(args)

    (bound,) = await compose_skill_tools(
        skill,
        [
            (
                "SearchAction",
                Tool(
                    name="web_search__search",
                    description="Search",
                    parameters_schema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                    execute=failed_search,
                ),
            )
        ],
        run_context=run_context,
        access_check=access,
        result_observer=observe,
    )

    with pytest.raises(RuntimeError, match="returned an error"):
        await bound.function(SimpleNamespace(deps=run_context), query="private query")
    assert observations == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("action failed"), "cancel"])
async def test_action_exception_or_cancellation_does_not_create_evidence(failure):
    observations = []
    run_context = PilotRunContext(
        caller=PilotCaller(agent_id="a1", user_id="u1", session_id="s1"),
        task_id="task-1",
        run_id="run-1",
        skill_id="research",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
    )
    skill = SkillDoc(
        name="research",
        description="Research",
        body="",
        requires_tools=("web_search__search",),
    )

    async def execute(**_kwargs):
        if failure == "cancel":
            raise asyncio.CancelledError()
        raise failure

    async def observe(*args):
        observations.append(args)

    (bound,) = await compose_skill_tools(
        skill,
        [
            (
                "SearchAction",
                Tool(
                    name="web_search__search",
                    description="Search",
                    parameters_schema={"type": "object", "properties": {}},
                    execute=execute,
                ),
            )
        ],
        run_context=run_context,
        access_check=lambda *_args: True,
        result_observer=observe,
    )

    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await bound.function(SimpleNamespace(deps=run_context))
    else:
        with pytest.raises(RuntimeError, match="action failed"):
            await bound.function(SimpleNamespace(deps=run_context))
    assert observations == []
