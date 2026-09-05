"""Native tool-calling decision protocol (ADR-0044).

Under ``tool_protocol: native`` (the default) the provider's function-calling
API carries the decision: tools go up as JSON-Schema'd definitions, the model's
``tool_calls`` are the step, plain text is the reply, and this turn's steps
replay as assistant ``tool_calls`` + ``tool`` result messages. ``json`` keeps the
original structured-JSON-in-text contract byte for byte.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from jvagent.action.model.language.base import ModelActionResult
from jvagent.action.orchestrator import prompts as P
from jvagent.action.orchestrator.constants import (
    MODEL_ERROR_ACTION,
    MODEL_TRUNCATED_ACTION,
)
from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.orchestrator.tools import (
    HARNESS_NOTE_PREFIX,
    SkillTool,
    decisions_from_native_result,
    native_tool_definitions,
    native_tool_name,
    render_observation_messages,
)
from jvagent.action.orchestrator.turn_cache import bind_turn_cache, get_prompt_cache

_ORIGINAL_RUN_MODEL = OrchestratorInteractAction._run_model


async def _noop(args):
    return "ok"


def _tool(name, schema=None, description="does a thing"):
    return SkillTool(
        name=name,
        description=description,
        run=_noop,
        parameters_schema=schema or {"type": "object", "properties": {}},
    )


class _FakeModelAction:
    """Captures ``query_messages`` kwargs and returns scripted results."""

    def __init__(self, results: List[Any]):
        self.results = list(results)
        self.calls: List[Dict[str, Any]] = []

    async def query_messages(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _tool_call(name, args, call_id="call_1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def _bind(monkeypatch, ex, fake):
    async def _gear(self, gear):
        return fake, "fake-model", 0.2, 256, False

    monkeypatch.setattr(OrchestratorInteractAction, "_gear_model", _gear)
    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _ORIGINAL_RUN_MODEL)


# --- protocol selection ------------------------------------------------------


def test_default_protocol_is_native_and_unknown_values_fall_back():
    ex = OrchestratorInteractAction()
    assert ex._protocol() == "native"
    ex.tool_protocol = "json"
    assert ex._protocol() == "json"
    ex.tool_protocol = "something-else"
    assert ex._protocol() == "native"


# --- definitions -------------------------------------------------------------


def test_native_definitions_carry_schema_and_sanitise_names():
    schema = {
        "type": "object",
        "properties": {"q": {"type": "string"}},
        "required": ["q"],
    }
    defs, alias = native_tool_definitions(
        [_tool("find_tool", schema), _tool("mcp_fs__read/file"), _tool("x" * 80)]
    )
    by_name = {d["function"]["name"]: d for d in defs}
    assert by_name["find_tool"]["function"]["parameters"] == schema
    assert "mcp_fs__read_file" in by_name
    assert alias["mcp_fs__read_file"] == "mcp_fs__read/file"
    long_name = [n for n in by_name if n.startswith("xxxx")][0]
    assert len(long_name) == 64 and alias[long_name] == "x" * 80
    assert native_tool_name("reply") == "reply"


def test_native_definitions_disambiguate_alias_collisions_and_cap_descriptions():
    defs, alias = native_tool_definitions(
        [_tool("a/b", description="d" * 3000), _tool("a b")]
    )
    names = [d["function"]["name"] for d in defs]
    assert len(set(names)) == 2
    assert set(alias.values()) == {"a/b", "a b"}
    assert len(defs[0]["function"]["description"]) <= 1024


# --- result → decision -------------------------------------------------------


def test_tool_call_maps_to_tool_decision_with_meta_and_alias():
    decisions = decisions_from_native_result(
        [_tool_call("mcp_fs__read_file", {"path": "/x"})],
        "Reading the file.",
        alias_map={"mcp_fs__read_file": "mcp_fs__read/file"},
    )
    assert decisions == [
        {
            "action": "tool",
            "tool": "mcp_fs__read/file",
            "args": {"path": "/x"},
            "_call_id": "call_1",
            "_group_id": "call_1",
            "_assistant_text": "Reading the file.",
        }
    ]


def test_text_maps_to_reply_on_the_surface_and_to_final_when_finalizing():
    reply = decisions_from_native_result([], "Hello!", text_as_reply=True)
    assert reply[0]["action"] == "tool" and reply[0]["tool"] == "reply"
    assert reply[0]["args"] == {"text": "Hello!"}
    final = decisions_from_native_result([], "Hello!", text_as_reply=False)
    assert final == [
        {"action": "final", "answer": "Hello!", "_assistant_text": "Hello!"}
    ]
    assert decisions_from_native_result([], "") == []


def test_malformed_arguments_degrade_to_empty_args():
    decisions = decisions_from_native_result([_tool_call("x", {})], "")
    assert decisions[0]["args"] == {}
    broken = {
        "id": "c",
        "type": "function",
        "function": {"name": "x", "arguments": "{not json"},
    }
    assert decisions_from_native_result([broken], "")[0]["args"] == {}


# --- transcript replay -------------------------------------------------------


def test_observation_messages_pair_calls_with_results_and_merge_notes():
    observations = [
        {
            "tool": "(skill-session)",
            "args": {},
            "observation": "note A",
            "kind": "server_prep",
        },
        {"tool": "(guard)", "args": {}, "observation": "note B"},
        {
            "tool": "web_fetch__fetch",
            "args": {"url": "https://e.x"},
            "observation": "page text",
            "call_id": "c1",
            "group_id": "c1",
            "assistant_text": "Fetching.",
        },
        {
            "tool": "(guard)",
            "args": {},
            "observation": "(You already called that.)",
            "call_id": "c2",
            "group_id": "c2",
            "call_tool": "web_fetch__fetch",
            "call_args": {"url": "https://e.x"},
        },
    ]
    messages = render_observation_messages(observations)
    assert [m["role"] for m in messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
    ]
    assert (
        messages[0]["content"]
        == f"{HARNESS_NOTE_PREFIX}note A\n{HARNESS_NOTE_PREFIX}note B"
    )
    assert messages[1]["content"] == "Fetching."
    assert messages[1]["tool_calls"][0]["id"] == "c1"
    assert messages[2] == {
        "role": "tool",
        "tool_call_id": "c1",
        "name": "web_fetch__fetch",
        "content": "page text",
    }
    # A guard that stood in for the dispatch is the call's result, named as the
    # model made the call — not "(guard)".
    assert messages[3]["tool_calls"][0]["function"]["name"] == "web_fetch__fetch"
    assert json.loads(messages[3]["tool_calls"][0]["function"]["arguments"]) == {
        "url": "https://e.x"
    }
    assert messages[4]["tool_call_id"] == "c2"


def test_observation_messages_group_parallel_calls_and_bound_size():
    observations = [
        {
            "tool": "a",
            "args": {"p": "x" * 1000},
            "observation": "r" * 5000,
            "call_id": "c1",
            "group_id": "g",
        },
        {
            "tool": "b",
            "args": {},
            "observation": "rb",
            "call_id": "c2",
            "group_id": "g",
        },
    ]
    messages = render_observation_messages(
        observations,
        max_chars=100,
        stale_max_chars=50,
        full_recent=1,
        args_max_chars=40,
    )
    assert [m["role"] for m in messages] == ["assistant", "tool", "tool"]
    assert [tc["id"] for tc in messages[0]["tool_calls"]] == ["c1", "c2"]
    args = json.loads(messages[0]["tool_calls"][0]["function"]["arguments"])
    assert "elided" in args["p"] and len(args["p"]) < 100  # still valid JSON
    # Stale cap (older than full_recent): bounded far below the 5000-char body;
    # the elision marker itself is ~60 chars wide.
    assert len(messages[1]["content"]) <= 120 and "elided" in messages[1]["content"]
    assert messages[2]["content"] == "rb"


def test_observation_messages_replay_deflected_prose_as_assistant_text():
    observations = [
        {
            "tool": "(guard)",
            "args": {},
            "observation": "(not yet)",
            "assistant_text": "All done!",
        }
    ]
    messages = render_observation_messages(observations)
    assert messages[0] == {"role": "assistant", "content": "All done!"}
    assert messages[1]["role"] == "user" and "(not yet)" in messages[1]["content"]


def test_observation_messages_count_cap_notes_omission():
    observations = [
        {"tool": "t", "args": {}, "observation": str(i), "call_id": f"c{i}"}
        for i in range(5)
    ]
    messages = render_observation_messages(observations, max_observations=2)
    assert (
        messages[0]["role"] == "user"
        and "3 earlier tool results omitted" in messages[0]["content"]
    )
    assert [m["tool_call_id"] for m in messages if m["role"] == "tool"] == ["c3", "c4"]


# --- _run_model: native ------------------------------------------------------


@pytest.mark.asyncio
async def test_run_model_native_sends_definitions_and_replays_steps(
    make_visitor, monkeypatch
):
    ex = OrchestratorInteractAction()
    fake = _FakeModelAction(
        [
            ModelActionResult(
                response="", tool_calls=[_tool_call("get_current_datetime", {})]
            )
        ]
    )
    _bind(monkeypatch, ex, fake)
    v = make_visitor(utterance="what time is it?")
    observations = [
        {
            "tool": "find_tool",
            "args": {"query": "time"},
            "observation": "hit",
            "call_id": "c0",
        }
    ]
    tools = [_tool("get_current_datetime"), _tool("reply"), _tool("find_tool")]

    decision = await ex._run_model(v, "what time is it?", [], tools, observations)

    assert decision["action"] == "tool"
    assert decision["tool"] == "get_current_datetime"
    assert decision["_call_id"] == "call_1"
    kwargs = fake.calls[0]
    names = {d["function"]["name"] for d in kwargs["tools"]}
    assert names == {"get_current_datetime", "reply", "find_tool"}
    assert kwargs["tool_choice"] == "auto"
    assert kwargs["parallel_tool_calls"] is False
    assert "response_format" not in kwargs
    roles = [m["role"] for m in kwargs["messages"]]
    assert roles == ["system", "user", "assistant", "tool"]
    assert kwargs["messages"][3]["tool_call_id"] == "c0"
    system = kwargs["messages"][0]["content"]
    assert "Plain text with no tool call is delivered to the user" in system
    assert "Reply with a single JSON object" not in system
    user = kwargs["messages"][1]["content"]
    assert "Steps taken this turn" not in user
    assert "Return raw JSON only" not in user
    assert "OPERATING RULES" in user  # the behavioural reminder stays


@pytest.mark.asyncio
async def test_run_model_native_text_is_a_reply_and_finalize_offers_no_tools(
    make_visitor, monkeypatch
):
    ex = OrchestratorInteractAction()
    fake = _FakeModelAction(
        [
            ModelActionResult(response="Hi there!"),
            ModelActionResult(response="Best answer."),
        ]
    )
    _bind(monkeypatch, ex, fake)
    v = make_visitor()
    tools = [_tool("reply"), _tool("x")]

    decision = await ex._run_model(v, "hi", [], tools, [])
    assert decision["action"] == "tool" and decision["tool"] == "reply"
    assert decision["args"] == {"text": "Hi there!"}

    final = await ex._run_model(v, "hi", [], [], [], finalize=True)
    assert final == {
        "action": "final",
        "answer": "Best answer.",
        "_assistant_text": "Best answer.",
    }
    assert "tools" not in fake.calls[1] or fake.calls[1]["tools"] is None
    assert P.FINALIZE_PROMPT_NATIVE in fake.calls[1]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_run_model_native_queues_extra_parallel_calls(make_visitor, monkeypatch):
    ex = OrchestratorInteractAction()
    fake = _FakeModelAction(
        [
            ModelActionResult(
                response="",
                tool_calls=[_tool_call("a", {}, "c1"), _tool_call("b", {}, "c2")],
            )
        ]
    )
    _bind(monkeypatch, ex, fake)
    v = make_visitor()
    with bind_turn_cache():
        first = await ex._run_model(v, "go", [], [_tool("a"), _tool("b")], [])
        pending = get_prompt_cache().get("pending_decisions")
    assert first["tool"] == "a" and first["_group_id"] == "c1"
    assert pending and pending[0]["tool"] == "b" and pending[0]["_group_id"] == "c1"


@pytest.mark.asyncio
async def test_run_model_native_resolves_wire_aliases(make_visitor, monkeypatch):
    ex = OrchestratorInteractAction()
    fake = _FakeModelAction(
        [
            ModelActionResult(
                response="", tool_calls=[_tool_call("mcp_fs__read_file", {"p": 1})]
            )
        ]
    )
    _bind(monkeypatch, ex, fake)
    v = make_visitor()
    decision = await ex._run_model(v, "go", [], [_tool("mcp_fs__read/file")], [])
    assert decision["tool"] == "mcp_fs__read/file"


@pytest.mark.asyncio
async def test_run_model_surfaces_provider_failure_and_truncation(
    make_visitor, monkeypatch
):
    ex = OrchestratorInteractAction()
    fake = _FakeModelAction(
        [
            RuntimeError("503 upstream"),
            ModelActionResult(response="", finish_reason="length"),
            ModelActionResult(response=""),
        ]
    )
    _bind(monkeypatch, ex, fake)
    v = make_visitor()
    tools = [_tool("reply")]
    assert (await ex._run_model(v, "go", [], tools, []))["action"] == MODEL_ERROR_ACTION
    assert (await ex._run_model(v, "go", [], tools, []))[
        "action"
    ] == MODEL_TRUNCATED_ACTION
    assert await ex._run_model(v, "go", [], tools, []) is None


# --- _run_model: json --------------------------------------------------------


@pytest.mark.asyncio
async def test_run_model_json_protocol_is_unchanged(make_visitor, monkeypatch):
    ex = OrchestratorInteractAction()
    ex.tool_protocol = "json"
    fake = _FakeModelAction(
        [
            ModelActionResult(
                response='{"action":"tool","tool":"reply","args":{"text":"hi"}}'
            ),
            ModelActionResult(
                response='{"action":"tool","tool":', finish_reason="length"
            ),
        ]
    )
    _bind(monkeypatch, ex, fake)
    v = make_visitor()
    observations = [{"tool": "find_tool", "args": {"query": "t"}, "observation": "hit"}]

    decision = await ex._run_model(v, "hi", [], [_tool("reply")], observations)
    assert decision == {"action": "tool", "tool": "reply", "args": {"text": "hi"}}
    kwargs = fake.calls[0]
    assert kwargs["tools"] is None
    assert kwargs["response_format"] == {"type": "json_object"}
    assert "tool_choice" not in kwargs
    assert [m["role"] for m in kwargs["messages"]] == ["system", "user"]
    assert "Reply with a single JSON object" in kwargs["messages"][0]["content"]
    user = kwargs["messages"][1]["content"]
    assert "Steps taken this turn" in user and "TOOL find_tool" in user
    assert "Return raw JSON only" in user

    truncated = await ex._run_model(v, "hi", [], [_tool("reply")], observations)
    assert truncated["action"] == MODEL_TRUNCATED_ACTION


def test_json_protocol_system_prompt_is_byte_identical_to_the_legacy_text():
    """The measured A/B results (prompt-injection resistance, cache split) were
    taken on the pre-ADR-0044 text; the JSON protocol must still render it."""
    sections = dict(
        identity_section="You are Ada.\n\n",
        session_context_section="",
        tools_section="- reply: say",
        skills_section="- s: d",
        capabilities_section="(c)",
        parameters_section="(p)",
        loop_protocol_extra="\n\nEXTRA",
        extra_section="",
    )
    assert P.render_system_prompt(
        protocol="json", **sections
    ) == P.LEGACY_JSON_SYSTEM_PROMPT.format(**sections)


# --- persisted prompt overrides ---------------------------------------------


def _compose(ex):
    return ex._compose_system_prompt(
        identity_section="You are Ada.\n\n",
        tools_section="- reply: say something",
        skills_section="- research: investigate",
    )


def test_legacy_persisted_default_renders_the_native_protocol():
    """An agent registered before ADR-0044 persisted the JSON-era built-in as its
    ``system_prompt``; under the native protocol it must not instruct a
    tool-calling model to emit JSON."""
    ex = OrchestratorInteractAction()
    ex.system_prompt = P.LEGACY_JSON_SYSTEM_PROMPT
    out = _compose(ex)
    assert "Plain text with no tool call is delivered to the user" in out
    assert "Reply with a single JSON object" not in out
    assert "- reply: say something" in out


def test_operator_override_is_kept_verbatim_under_either_protocol():
    ex = OrchestratorInteractAction()
    ex.system_prompt = "{identity_section}CUSTOM {tools_section} // {skills_section}"
    assert _compose(ex).startswith("You are Ada.\n\nCUSTOM ")
    ex.tool_protocol = "json"
    assert _compose(ex).startswith("You are Ada.\n\nCUSTOM ")


def test_protocol_text_swaps_only_the_unchanged_default():
    ex = OrchestratorInteractAction()
    assert ex._protocol_text(
        P.FINALIZE_PROMPT, P.FINALIZE_PROMPT, P.FINALIZE_PROMPT_NATIVE
    ) == (P.FINALIZE_PROMPT_NATIVE)
    assert (
        ex._protocol_text("mine", P.FINALIZE_PROMPT, P.FINALIZE_PROMPT_NATIVE) == "mine"
    )
    ex.tool_protocol = "json"
    assert ex._protocol_text(
        P.FINALIZE_PROMPT, P.FINALIZE_PROMPT, P.FINALIZE_PROMPT_NATIVE
    ) == (P.FINALIZE_PROMPT)


# --- the loop end to end -----------------------------------------------------


@pytest.mark.asyncio
async def test_loop_replays_a_native_transcript_across_ticks(
    make_orchestrator, make_visitor, monkeypatch
):
    """Tick 1: the model calls a tool. Tick 2: it sees its own call and the
    result as assistant/tool messages, then replies in plain text."""
    from jvagent.action.reply.reply_action import ReplyAction

    ex = make_orchestrator(actions=[ReplyAction()])
    fake = _FakeModelAction(
        [
            ModelActionResult(
                response="",
                tool_calls=[_tool_call("get_current_datetime", {}, "call_dt")],
            ),
            ModelActionResult(response="It is now."),
        ]
    )
    _bind(monkeypatch, ex, fake)
    v = make_visitor(utterance="what time is it?")
    v.interaction.observability_metrics = []

    async def _save():
        return None

    v.interaction.save = _save
    await ex.execute(v)

    assert "It is now." in (v.interaction.response or "")
    second = fake.calls[1]["messages"]
    roles = [m["role"] for m in second]
    assert roles[:2] == ["system", "user"]
    assert "assistant" in roles and "tool" in roles
    assistant = next(m for m in second if m["role"] == "assistant")
    tool_msg = next(m for m in second if m["role"] == "tool")
    assert assistant["tool_calls"][0]["id"] == "call_dt"
    assert assistant["tool_calls"][0]["function"]["name"] == "get_current_datetime"
    assert tool_msg["tool_call_id"] == "call_dt"
    assert "ISO 8601" in tool_msg["content"]


@pytest.mark.asyncio
async def test_loop_strips_meta_before_dispatch_and_stamps_the_observation(
    make_orchestrator, make_visitor
):
    """Meta keys never reach tool args; the observation carries the call id."""
    from jvagent.action.reply.reply_action import ReplyAction

    seen: List[Dict[str, Any]] = []

    async def _spy(self, visitor, utterance, history, tools, observations, *a, **k):
        seen.append([dict(o) for o in observations])
        if len(seen) == 1:
            return {
                "action": "tool",
                "tool": "get_current_datetime",
                "args": {},
                "_call_id": "c1",
                "_group_id": "c1",
                "_assistant_text": "checking",
            }
        return {"action": "tool", "tool": "reply", "args": {"text": "done"}}

    ex = make_orchestrator(actions=[ReplyAction()])
    OrchestratorInteractAction._run_model = _spy  # type: ignore[method-assign]
    try:
        v = make_visitor()
        await ex.execute(v)
    finally:
        OrchestratorInteractAction._run_model = _ORIGINAL_RUN_MODEL  # type: ignore[method-assign]
    obs = next(o for o in seen[1] if o["tool"] == "get_current_datetime")
    assert obs["call_id"] == "c1" and obs["group_id"] == "c1"
    assert obs["call_tool"] == "get_current_datetime"
    assert obs["assistant_text"] == "checking"
    assert "_call_id" not in obs.get("args", {})
