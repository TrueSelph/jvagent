"""Live provider smoke: the real Orchestrator loop against a real model.

Builds an in-memory Orchestrator (no database, no agent graph) whose model slot
is a real ``LanguageModelAction`` for one provider, then drives a small set of
CUCS scenarios through :class:`~jvagent.testing.live_runner.LiveScenarioRunner`.
The scenarios exercise what the conformance suite cannot prove offline: that
the native tool protocol, the transcript replay and the reply path work against
the provider's *current* API behaviour.

**This costs money** (a few cents per provider) and needs the provider's API
key in the environment. It runs from ``scripts/live_smoke.py`` and the nightly
``live-providers`` workflow — never from the normal test suite.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

logger = logging.getLogger(__name__)

# provider → (module path, class name, key env var, default model)
PROVIDER_ACTIONS: Dict[str, tuple] = {
    "openai": (
        "jvagent.action.model.language.openai.openai",
        "OpenAILanguageModelAction",
        "OPENAI_API_KEY",
        "gpt-4o-mini",
    ),
    "anthropic": (
        "jvagent.action.model.language.anthropic.anthropic",
        "AnthropicLanguageModelAction",
        "ANTHROPIC_API_KEY",
        "claude-3-5-haiku-latest",
    ),
    "groq": (
        "jvagent.action.model.language.groq.groq",
        "GroqLanguageModelAction",
        "GROQ_API_KEY",
        "llama-3.3-70b-versatile",
    ),
    "openrouter": (
        "jvagent.action.model.language.openrouter.openrouter",
        "OpenRouterLanguageModelAction",
        "OPENROUTER_API_KEY",
        "openai/gpt-4o-mini",
    ),
    "ollama": (
        "jvagent.action.model.language.ollama.ollama",
        "OllamaLanguageModelAction",
        None,
        "llama3.1",
    ),
    "litellm": (
        "jvagent.action.model.language.litellm.litellm_lm",
        "LiteLLMLanguageModelAction",
        "OPENAI_API_KEY",
        "openai/gpt-4o-mini",
    ),
}

# A three-step SOP over in-memory tools, sized so the schema result exceeds the
# default stale-observation cap (2500 chars) — the shape that killed a reasoning
# model on the JSON contract in issue #203: it re-derived its plan every tick,
# re-issued the schema call, and the repeat guard ended the turn. The scenario
# asserts the loop finishes with every step run once and no repeat guard.
_SOP_SCHEMA = json.dumps(
    {
        "track": "Identity",
        "version": 7,
        "type_key": "role",
        "fields": [
            {
                "name": f"field_{i:02d}",
                "type": ["string", "integer", "boolean", "date"][i % 4],
                "required": i % 3 == 0,
                "description": (
                    f"Field {i} of the Identity track; populated from the intake "
                    "form and validated against the role taxonomy before write."
                ),
            }
            for i in range(28)
        ],
    },
    indent=2,
)


class _SopToolsAction:
    """Three tools that only make sense in order: schema → transform → write."""

    binds_tools_to_visitor = False
    enabled = True

    def __init__(self) -> None:
        self.calls: List[str] = []

    def get_class_name(self) -> str:
        return "SopToolsAction"

    async def get_tools(self) -> List[Any]:
        from jvagent.tooling.tool import Tool
        from jvagent.tooling.tool_result import ToolResult

        async def get_schema(**kwargs: Any) -> Any:
            self.calls.append("sop_get_track_schema")
            return ToolResult(content=_SOP_SCHEMA)

        async def transform(**kwargs: Any) -> Any:
            self.calls.append("sop_transform_record")
            if not kwargs.get("type_key"):
                return ToolResult(
                    content="(error: type_key is required — read it from the schema)"
                )
            return ToolResult(
                content=json.dumps(
                    {"record_id": "rec-001", "role": "member", "fields_mapped": 28}
                )
            )

        async def write(**kwargs: Any) -> Any:
            self.calls.append("sop_write_record")
            if not kwargs.get("record"):
                return ToolResult(content="(error: record is required)")
            return ToolResult(content='{"written": true, "id": "rec-001"}')

        return [
            Tool(
                name="sop_get_track_schema",
                description=(
                    "Step 1 of the onboarding SOP: fetch the schema of a track. "
                    "Args: track_id (string)."
                ),
                execute=get_schema,
            ),
            Tool(
                name="sop_transform_record",
                description=(
                    "Step 2: transform the sample intake record to a track schema. "
                    "Args: type_key (string, from the schema's type_key), "
                    "track_id (string)."
                ),
                execute=transform,
            ),
            Tool(
                name="sop_write_record",
                description=(
                    "Step 3: write a transformed record. Args: record (object, the "
                    "output of step 2)."
                ),
                execute=write,
            ),
        ]


MULTISTEP_SCENARIO: Dict[str, Any] = {
    "schema": "jvagent.use-case/v1",
    "id": "live.multistep_sop",
    "title": "runs a three-step SOP without losing its thread",
    "given": {"channel": "web"},
    "turns": [
        {
            "id": "sop",
            "when": {
                "user": (
                    "Run the onboarding SOP for the Identity track: fetch the "
                    "track schema, transform the sample intake record to it, "
                    "write the result, then confirm what was written."
                )
            },
            "then": {
                "loop": {
                    "must_reply": True,
                    "tools_include": [
                        "sop_get_track_schema",
                        "sop_transform_record",
                        "sop_write_record",
                    ],
                    "guards_exclude": ["repeat"],
                    "max_ticks": 8,
                }
            },
        }
    ],
}

# The smallest scenario set that proves the loop end to end against a provider:
# a plain reply, a real tool call followed by a reply, the "act, don't announce"
# rule, and the multi-step SOP above.
SMOKE_SCENARIOS: List[Dict[str, Any]] = [
    {
        "schema": "jvagent.use-case/v1",
        "id": "live.greeting",
        "title": "greets and stops",
        "given": {"channel": "web"},
        "turns": [
            {
                "id": "greet",
                "when": {"user": "Hi there! Just saying hello."},
                "then": {"loop": {"must_reply": True, "max_ticks": 3}},
            }
        ],
    },
    {
        "schema": "jvagent.use-case/v1",
        "id": "live.datetime_tool",
        "title": "uses the datetime tool, then replies",
        "given": {"channel": "web"},
        "turns": [
            {
                "id": "ask",
                "when": {
                    "user": (
                        "Please call your datetime tool to get the exact current "
                        "time, then tell me the result."
                    )
                },
                "then": {
                    "loop": {
                        "must_reply": True,
                        "tools_include": ["get_current_datetime"],
                        "max_ticks": 4,
                    }
                },
            }
        ],
    },
    {
        "schema": "jvagent.use-case/v1",
        "id": "live.act_dont_announce",
        "title": "acts instead of announcing",
        "given": {"channel": "web"},
        "turns": [
            {
                "id": "ask",
                "when": {"user": "What time is it right now?"},
                "then": {"loop": {"must_reply": True, "must_not_announce": True}},
            }
        ],
    },
    MULTISTEP_SCENARIO,
]


def build_model_action(provider: str, model: Optional[str] = None, **attrs: Any) -> Any:
    """Instantiate the provider's language-model action (in memory)."""
    import importlib

    module_path, class_name, _key_env, default_model = PROVIDER_ACTIONS[provider]
    cls = getattr(importlib.import_module(module_path), class_name)
    action = cls()
    action.model = model or default_model
    for key, value in attrs.items():
        setattr(action, key, value)
    return action


def build_live_orchestrator(model_action: Any, **attrs: Any) -> Any:
    """An Orchestrator wired to ``model_action`` and a ReplyAction, no graph.

    Mirrors the unit-test fixture (``tests/action/orchestrator/conftest.py``)
    except the model call is real: the enabled-action surface, agent lookup and
    skill discovery are stubbed; ``publish`` writes to the interaction.
    """
    from jvagent.action.orchestrator.orchestrator_interact_action import (
        OrchestratorInteractAction,
    )
    from jvagent.action.reply.reply_action import ReplyAction

    class LiveOrchestrator(OrchestratorInteractAction):  # type: ignore[misc]
        async def get_agent(self):  # noqa: D401 - stub
            agent = MagicMock()
            agent.get_access_control_action = AsyncMock(return_value=None)
            agent.alias = "Smoke"
            agent.role = "a terse test assistant"
            return agent

        async def get_action(self, name):  # type: ignore[override]
            key = name if isinstance(name, str) else getattr(name, "__name__", "")
            if key == model_action.get_class_name():
                return model_action
            if key == "ReplyAction":
                return reply
            if key == "SopToolsAction":
                return sop_tools
            return None

        async def get_model_action(self, required: bool = False):  # type: ignore[override]
            return model_action

        async def _enabled_actions(self, _agent):  # type: ignore[override]
            return [reply, model_action, sop_tools]

        async def _enabled_interact_actions(self, _agent):  # type: ignore[override]
            return []

        def _discover_skills(self, _agent):  # type: ignore[override]
            return []

        async def publish(self, *, visitor, content, **kwargs):  # type: ignore[override]
            interaction = getattr(visitor, "interaction", None)
            if interaction is not None:
                interaction.response = (interaction.response or "") + content
            return None

    reply = ReplyAction()
    sop_tools = _SopToolsAction()
    orchestrator = LiveOrchestrator()
    orchestrator.model_action_type = model_action.get_class_name()
    orchestrator.model = model_action.model
    for key, value in attrs.items():
        setattr(orchestrator, key, value)
    return orchestrator


async def run_smoke(
    provider: str,
    model: Optional[str] = None,
    *,
    scenarios: Optional[List[Dict[str, Any]]] = None,
    transport: Optional[str] = None,
    **orchestrator_attrs: Any,
) -> List[Any]:
    """Run the smoke scenarios against ``provider``; returns ``ScenarioResult``s."""
    from jvagent.testing.live_runner import LiveScenarioRunner

    action_attrs: Dict[str, Any] = {}
    if transport:
        action_attrs["transport"] = transport
    model_action = build_model_action(provider, model, **action_attrs)
    orchestrator = build_live_orchestrator(model_action, **orchestrator_attrs)
    runner = LiveScenarioRunner(orchestrator)
    results = []
    for scenario in scenarios or SMOKE_SCENARIOS:
        results.append(await runner.run(scenario))
    return results


def summarise(results: List[Any]) -> Dict[str, Any]:
    """JSON-friendly summary of a smoke run."""
    return {
        "passed": all(r.passed for r in results),
        "scenarios": [
            {
                "id": r.scenario_id,
                "passed": r.passed,
                "failures": list(r.failures),
                "turns": [
                    {
                        "id": t.turn_id,
                        "ended_via": t.ended_via,
                        "ticks": t.tick_count,
                        "tools": list(t.tools_invoked),
                        "reply": (t.reply or "")[:200],
                        "error": t.error,
                    }
                    for t in r.turns
                ],
            }
            for r in results
        ],
    }


__all__ = [
    "MULTISTEP_SCENARIO",
    "PROVIDER_ACTIONS",
    "SMOKE_SCENARIOS",
    "build_model_action",
    "build_live_orchestrator",
    "run_smoke",
    "summarise",
]
