"""Action tools remain the effect boundary when composed as capabilities."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from jvagent.action.orchestrator.pilot.contracts import PilotCaller, PilotRunContext
from jvagent.action.orchestrator.pilot.tools import (
    PilotToolConfigurationError,
    PilotToolOutcome,
    _bound_tool_result,
    compose_skill_tools,
    validate_skill_action_tools,
)
from jvagent.action.orchestrator.skills import SkillDoc
from jvagent.harness.contracts import IdempotencyClass
from jvagent.tooling.tool import Tool
from jvagent.tooling.tool_result import ToolResult, ToolResultText


@pytest.mark.asyncio
async def test_skill_action_admission_composes_declared_read_only_extensions():
    skill = SkillDoc(
        name="research",
        description="Evidence-first research",
        body="Use the inventory lookup when relevant, then cite fetched sources.",
        requires_tools=(
            "web_search__search",
            "web_fetch__fetch",
            "inventory__lookup",
        ),
        requires_actions=(
            "SerperWebSearchAction",
            "WebFetchAction",
            "InventoryAction",
        ),
    )
    reads = []

    async def lookup(record_id: str) -> str:
        reads.append(record_id)
        return f"record {record_id}"

    action_tools = [
        (
            "SerperWebSearchAction",
            Tool("web_search__search", "Search", execute=lambda: ""),
        ),
        (
            "WebFetchAction",
            Tool("web_fetch__fetch", "Fetch", execute=lambda: ""),
        ),
        (
            "InventoryAction",
            Tool(
                "inventory__lookup",
                "Read inventory records",
                parameters_schema={
                    "type": "object",
                    "properties": {"record_id": {"type": "string"}},
                    "required": ["record_id"],
                    "additionalProperties": False,
                },
                execute=lookup,
                effect_class="read",
            ),
        ),
    ]

    selected = validate_skill_action_tools(skill, action_tools)
    assert selected == tuple(action_tools)
    context = PilotRunContext(
        caller=PilotCaller(agent_id="a1", user_id="u1", session_id="s1"),
        task_id="task-1",
        run_id="run-1",
        skill_id="research",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
    )
    bindings = await compose_skill_tools(
        skill,
        selected,
        run_context=context,
        access_check=lambda *_args: True,
    )
    assert {binding.name for binding in bindings} == {
        "web_search__search",
        "web_fetch__fetch",
        "inventory__lookup",
    }
    composed = await compose_skill_tools(
        SkillDoc(
            name="research",
            description="Research",
            body="",
            requires_tools=("inventory__lookup",),
            requires_actions=("InventoryAction",),
        ),
        selected,
        run_context=context,
        access_check=lambda *_args: True,
    )
    assert len(composed) == 1
    assert (
        await composed[0].function(
            SimpleNamespace(deps=context, tool_call_id="inventory-call"),
            record_id="item-7",
        )
        == "record item-7"
    )
    assert reads == ["item-7"]


@pytest.mark.parametrize("effect_class", [None, "write", "external"])
def test_skill_action_admission_rejects_unclassified_or_effectful_extensions(
    effect_class,
):
    skill = SkillDoc(
        name="research",
        description="Research",
        body="",
        requires_tools=(
            "web_search__search",
            "web_fetch__fetch",
            "inventory__lookup",
        ),
        requires_actions=("SerperWebSearchAction", "WebFetchAction", "InventoryAction"),
    )
    action_tools = [
        (
            "SerperWebSearchAction",
            Tool("web_search__search", "Search", execute=lambda: ""),
        ),
        (
            "WebFetchAction",
            Tool("web_fetch__fetch", "Fetch", execute=lambda: ""),
        ),
        (
            "InventoryAction",
            Tool(
                "inventory__lookup",
                "Inventory operation",
                execute=lambda: "",
                effect_class=effect_class,
            ),
        ),
    ]

    with pytest.raises(PilotToolConfigurationError, match="effect_class='read'"):
        validate_skill_action_tools(skill, action_tools)


def test_large_search_json_is_bounded_without_losing_source_ids():
    content = json.dumps(
        {
            "organic": [
                {
                    "title": f"Source {index}",
                    "link": f"https://example.test/{index}",
                    "pilot_source_id": f"source-{index}",
                    "snippet": "detail " * 200,
                }
                for index in range(8)
            ]
        }
    )

    bounded = _bound_tool_result(content, 2400)
    decoded = json.loads(bounded)

    assert len(bounded) <= 2400
    assert decoded["pilot_truncated"] is True
    assert decoded["organic"]
    assert all(row["pilot_source_id"] for row in decoded["organic"])
    assert all(len(row["snippet"]) < len("detail " * 200) for row in decoded["organic"])


def test_large_scalar_json_is_valid_and_bounded_after_escaping():
    content = json.dumps({"message": 'quote " slash \\ newline\n' * 200})

    bounded = _bound_tool_result(content, 256)
    decoded = json.loads(bounded)

    assert len(bounded) <= 256
    assert decoded["pilot_truncated"] is True
    assert decoded["preview"]


def test_extremely_large_action_result_is_rejected_without_json_parsing():
    result = _bound_tool_result('"' * 256_001, 4000)

    assert len(result) <= 4000
    assert json.loads(result) == {
        "pilot_truncated": True,
        "notice": "tool output exceeded the pilot inspection limit",
    }


@pytest.mark.asyncio
async def test_composition_preserves_name_schema_and_rechecks_access_each_call(caplog):
    operations = []
    checks = []
    events = []
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

    async def observe_event(phase, ctx, tool_name, call_id, args, result):
        events.append((phase, ctx.run_id, tool_name, call_id, dict(args), result))

    (bound,) = await compose_skill_tools(
        skill,
        [("SearchAction", action_tool)],
        run_context=run_context,
        access_check=access_check,
        tool_event_observer=observe_event,
    )
    assert bound.name == "web_search__search"
    assert bound.function_schema.json_schema == action_tool.parameters_schema

    with caplog.at_level("DEBUG", logger="jvagent.action.orchestrator.pilot.tools"):
        result = await bound.function(
            SimpleNamespace(deps=run_context, tool_call_id="model-call-1"),
            query="pilot architecture",
        )
    assert result == "source-1: pilot architecture"
    assert events == [
        (
            "tool_call",
            "run-1",
            "web_search__search",
            "model-call-1",
            {"query": "pilot architecture"},
            None,
        ),
        (
            "tool_result",
            "run-1",
            "web_search__search",
            "model-call-1",
            {"query": "pilot architecture"},
            "source-1: pilot architecture",
        ),
    ]
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
        with pytest.raises(ValueError, match="reserved authority or binding fields"):
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
async def test_access_check_resolution_error_fails_before_action_call():
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
        parameters_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        execute=lambda **_: calls.append("called"),
    )

    async def access_check(*_args):
        raise RuntimeError("policy resolution unavailable")

    (bound,) = await compose_skill_tools(
        skill,
        [("SearchAction", tool)],
        run_context=run_context,
        access_check=access_check,
    )

    with pytest.raises(RuntimeError, match="policy resolution unavailable"):
        await bound.function(SimpleNamespace(deps=run_context), query="q")
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
    assert observed == ["x" * 1000]


@pytest.mark.asyncio
async def test_typed_tool_metadata_reaches_evidence_observer():
    observed = []
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
        requires_tools=("web_fetch__fetch",),
    )
    receipt = {"web_fetch_result": {"outcome": "success", "http_status": 200}}
    action_tool = Tool(
        name="web_fetch__fetch",
        description="Fetch",
        parameters_schema={"type": "object", "properties": {}},
        execute=lambda: ToolResultText("bounded page text", receipt),
    )

    async def observe(_context, _tool_name, _arguments, content):
        observed.append((content, getattr(content, "tool_result_metadata", {})))

    (bound,) = await compose_skill_tools(
        skill,
        [("WebFetchAction", action_tool)],
        run_context=run_context,
        access_check=lambda *_args: True,
        result_observer=observe,
    )
    result = await bound.function(SimpleNamespace(deps=run_context))

    assert result == "bounded page text"
    assert observed == [("bounded page text", receipt)]


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
async def test_allowlisted_read_tool_error_is_recoverable_and_explicitly_typed():
    observations = []
    events = []
    run_context = PilotRunContext(
        caller=PilotCaller(agent_id="a1", user_id="u1", session_id="s1"),
        task_id="task-read-error",
        run_id="run-read-error",
        skill_id="research",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
    )
    skill = SkillDoc(
        name="research",
        description="Research",
        body="Search and cite evidence.",
        requires_tools=("web_search__search",),
    )
    tool = Tool(
        name="web_search__search",
        description="Search",
        parameters_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        execute=lambda **_kwargs: ToolResult.error("sensitive provider detail"),
    )

    async def observe(*args):
        observations.append(args)

    async def observe_event(*args):
        events.append(args)

    (bound,) = await compose_skill_tools(
        skill,
        [("SearchAction", tool)],
        run_context=run_context,
        access_check=lambda *_args: True,
        result_observer=observe,
        tool_event_observer=observe_event,
        recoverable_tool_errors=frozenset({"web_search__search"}),
    )

    result = await bound.function(
        SimpleNamespace(deps=run_context, tool_call_id="call-read-error"),
        query="query",
    )

    assert "returned an error" in result
    assert "No evidence was recorded" in result
    assert "sensitive provider detail" not in result
    assert observations == []
    assert events[-1][0] == "tool_result"
    assert isinstance(events[-1][-1], PilotToolOutcome)
    assert events[-1][-1].status == "failed"


@pytest.mark.asyncio
async def test_read_tool_exception_returns_recoverable_timeout_without_leaking_error():
    events = []
    run_context = PilotRunContext(
        caller=PilotCaller(agent_id="a1", user_id="u1", session_id="s1"),
        task_id="task-read-timeout",
        run_id="run-read-timeout",
        skill_id="research",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
    )
    skill = SkillDoc(
        name="research",
        description="Research",
        body="",
        requires_tools=("web_fetch__fetch",),
    )

    async def fetch(**_kwargs):
        raise asyncio.TimeoutError("secret URL and token")

    async def observe_event(*args):
        events.append(args)

    (bound,) = await compose_skill_tools(
        skill,
        [
            (
                "FetchAction",
                Tool(
                    name="web_fetch__fetch",
                    description="Fetch",
                    parameters_schema={"type": "object", "properties": {}},
                    execute=fetch,
                ),
            )
        ],
        run_context=run_context,
        access_check=lambda *_args: True,
        tool_event_observer=observe_event,
        recoverable_tool_errors=frozenset({"web_fetch__fetch"}),
    )

    result = await bound.function(SimpleNamespace(deps=run_context))

    assert "timed out" in result
    assert "secret URL" not in result
    assert isinstance(events[-1][-1], PilotToolOutcome)
    assert events[-1][-1].status == "timed_out"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("action failed"), "cancel"])
async def test_action_exception_or_cancellation_does_not_create_evidence(failure):
    observations = []
    tool_events = []
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

    async def observe_tool_event(phase, _context, _name, _call_id, _args, result):
        tool_events.append((phase, result))

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
        tool_event_observer=observe_tool_event,
    )

    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await bound.function(SimpleNamespace(deps=run_context))
    else:
        with pytest.raises(RuntimeError, match="action failed"):
            await bound.function(SimpleNamespace(deps=run_context))
    assert observations == []
    assert [phase for phase, _ in tool_events] == ["tool_call", "tool_result"]
    if failure == "cancel":
        assert isinstance(tool_events[-1][1], PilotToolOutcome)
        assert tool_events[-1][1].status == "cancelled"
    else:
        assert isinstance(tool_events[-1][1], PilotToolOutcome)
        assert tool_events[-1][1].status == "failed"
        assert "action failed" not in tool_events[-1][1].content


@pytest.mark.asyncio
async def test_model_arguments_cannot_override_bound_tool_or_bypass_schema():
    calls = []
    checks = []
    context = PilotRunContext(
        caller=PilotCaller(agent_id="a", user_id="u", session_id="s"),
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

    async def search(query: str) -> str:
        calls.append(query)
        return "ok"

    async def access(_ctx, _skill, tool_name, _args):
        checks.append(tool_name)
        return tool_name == "web_search__search"

    tool = Tool(
        name="web_search__search",
        description="Search",
        parameters_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            # The wrapper must protect its internal binding names even when
            # the underlying Action deliberately accepts extension fields.
            "additionalProperties": True,
        },
        execute=search,
    )
    (bound,) = await compose_skill_tools(
        skill, [("SearchAction", tool)], run_context=context, access_check=access
    )
    run_context = SimpleNamespace(deps=context, tool_call_id="call-1")

    for injected in (
        {"_tool_name": "web_fetch__fetch"},
        {"_original": "forged-action"},
    ):
        with pytest.raises(ValueError, match="reserved authority or binding fields"):
            await bound.function(run_context, query="safe", **injected)
    with pytest.raises(ValueError, match="invalid arguments"):
        await bound.function(run_context, query=123)
    with pytest.raises(ValueError, match="invalid arguments"):
        await bound.function(run_context)

    assert checks == []
    assert calls == []
