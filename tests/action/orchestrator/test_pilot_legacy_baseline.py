"""Deterministic legacy research-path baseline for the capability pilot."""

import json
from copy import deepcopy

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.orchestrator.skills import SkillDoc
from jvagent.tooling.tool import Tool
from jvagent.tooling.tool_result import ToolResult


@pytest.mark.asyncio
async def test_legacy_research_path_baseline(
    make_orchestrator, make_visitor, monkeypatch, publish_log, record_property
):
    """Capture activation, exact Action calls, egress, state, and prompt size."""

    calls = []

    async def search(*, query, **_kwargs):
        calls.append(("web_search__search", {"query": query}))
        return ToolResult(
            '[{"title":"Fixture","link":"https://example.test/source",'
            '"snippet":"The fixture reports a 2026 baseline of 42."}]'
        )

    async def fetch(*, url, **_kwargs):
        calls.append(("web_fetch__fetch", {"url": url}))
        return ToolResult("Fixture source confirms a 2026 baseline of 42.")

    class ResearchActions:
        def get_class_name(self):
            return "ResearchActions"

        async def get_tools(self):
            return [
                Tool(
                    name="web_search__search",
                    description="Search public sources.",
                    parameters_schema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                    execute=search,
                ),
                Tool(
                    name="web_fetch__fetch",
                    description="Fetch a source URL.",
                    parameters_schema={
                        "type": "object",
                        "properties": {"url": {"type": "string"}},
                        "required": ["url"],
                    },
                    execute=fetch,
                ),
            ]

    decisions = [
        {"action": "tool", "tool": "use_skill", "args": {"name": "research"}},
        {
            "action": "tool",
            "tool": "web_search__search",
            "args": {"query": "2026 fixture baseline"},
        },
        {
            "action": "tool",
            "tool": "web_fetch__fetch",
            "args": {"url": "https://example.test/source"},
        },
        {
            "action": "final",
            "answer": "The 2026 fixture baseline is 42. [source](https://example.test/source)",
        },
    ]
    ex = make_orchestrator(actions=[ResearchActions()], decisions=[])
    monkeypatch.setattr(
        OrchestratorInteractAction,
        "_discover_skills",
        lambda _self, _agent: [
            SkillDoc(
                name="research",
                description="Research with retrieved evidence.",
                body="Search, fetch, and cite sources.",
                requires_tools=("web_search__search", "web_fetch__fetch"),
            )
        ],
    )
    model_turns = []

    async def run_model(
        _self,
        _visitor,
        utterance,
        history,
        tools,
        observations,
        flow_note="",
        skills_section="",
        **_kwargs,
    ):
        tool_names = [getattr(tool, "name", None) for tool in tools]
        serialized_tools = [
            (
                tool.to_serialized()
                if callable(getattr(tool, "to_serialized", None))
                else {
                    "name": getattr(tool, "name", ""),
                    "description": getattr(tool, "description", ""),
                    "parameters_schema": getattr(tool, "parameters_schema", {}),
                }
            )
            for tool in tools
        ]
        model_turns.append(
            {
                "tool_names": tool_names,
                "utterance_chars": len(utterance),
                "history_chars": len(json.dumps(history, sort_keys=True, default=str)),
                "observations_chars": len(
                    json.dumps(observations, sort_keys=True, default=str)
                ),
                "skills_section_chars": len(skills_section),
                "serialized_tools_chars": len(
                    json.dumps(serialized_tools, sort_keys=True, default=str)
                ),
            }
        )
        return deepcopy(decisions.pop(0))

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", run_model)
    visitor = make_visitor(
        utterance="Research the 2026 fixture baseline and cite the source."
    )
    visitor.proactive_task = None
    visitor.interaction.observability_metrics = []
    await ex.execute(visitor)

    activation = next(
        event
        for event in visitor.interaction.observability_metrics
        if event.get("event_type") == "orchestrator_activation"
    )
    summary = {
        "driver": "legacy",
        "model_turns": len(model_turns),
        "tool_names_by_turn": [turn["tool_names"] for turn in model_turns],
        "calls": calls,
        "activated_skills": activation["data"]["skills_used"],
        "tools_invoked": activation["data"]["tools_invoked"],
        "egress_count": len(publish_log),
        "published": [item["content"] for item in publish_log],
        "task_state": deepcopy(visitor.conversation.tasks),
        "prompt_size_chars_by_turn": [
            sum(
                turn[key]
                for key in (
                    "utterance_chars",
                    "history_chars",
                    "observations_chars",
                    "skills_section_chars",
                    "serialized_tools_chars",
                )
            )
            for turn in model_turns
        ],
    }
    assert calls == [
        ("web_search__search", {"query": "2026 fixture baseline"}),
        ("web_fetch__fetch", {"url": "https://example.test/source"}),
    ]
    assert summary["activated_skills"] == ["research"]
    assert summary["egress_count"] == 1
    assert summary["task_state"] == []
    assert len(model_turns) == 4
    record_property("legacy_research_baseline", json.dumps(summary, sort_keys=True))
    print(f"LEGACY_RESEARCH_BASELINE={json.dumps(summary, sort_keys=True)}")
