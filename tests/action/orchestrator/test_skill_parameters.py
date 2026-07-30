"""A skill contributes behavioural parameters, like an Action does (ADR-0037).

An Action declares standing rules programmatically. A skill declares the rules
that hold while it is driving the turn, in its SKILL.md frontmatter. Both land in
the same interaction pool, in the same shape, so the loop prompt and the reply
compose read them through one path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from jvagent.action.parameters import (
    accumulate_skill_parameters,
    orchestration_parameters,
    render_parameters,
    response_parameters,
)
from jvagent.scaffold.skill_resolve import _normalize_parameters

# --- frontmatter parsing ----------------------------------------------------


def test_full_mapping_form():
    got = _normalize_parameters(
        [
            {
                "scope": "response",
                "condition": "the user asks about pricing",
                "response": "Quote the list price only.",
            }
        ],
        "SKILL.md",
        key="parameters",
    )
    assert got == [
        {
            "response": "Quote the list price only.",
            "condition": "the user asks about pricing",
            "scope": "response",
        }
    ]


def test_bare_string_is_an_unconditional_rule():
    """Hand-written SKILL.md files use the short form."""
    assert _normalize_parameters(["Always cite a source."], "SKILL.md", key="p") == [
        {"response": "Always cite a source."}
    ]


def test_entries_without_a_rule_are_dropped_not_silently_empty():
    got = _normalize_parameters(
        [{"condition": "x"}, "  ", 42, {"response": "Keep it short."}],
        "SKILL.md",
        key="p",
    )
    assert got == [{"response": "Keep it short."}]


def test_absent_parameters_is_empty():
    assert _normalize_parameters(None, "SKILL.md", key="p") == []


# --- pooling ----------------------------------------------------------------


def _interaction():
    pool = []
    interaction = MagicMock()
    interaction.parameters = pool

    def add_parameters(params, name):
        for p in params:
            entry = dict(p)
            entry["action_name"] = name
            pool.append(entry)
        return bool(params)

    interaction.add_parameters = add_parameters
    interaction.save = AsyncMock()
    return interaction


class _Doc:
    def __init__(self, name, parameters):
        self.name = name
        self.parameters = parameters


async def test_skill_parameters_reach_the_pool_attributed_to_the_skill():
    interaction = _interaction()
    doc = _Doc("research", [{"response": "Always cite sources.", "scope": "response"}])

    assert await accumulate_skill_parameters(interaction, [doc]) is True
    entry = interaction.parameters[0]
    assert entry["response"] == "Always cite sources."
    assert entry["action_name"] == "research"
    assert entry["scope"] == "response"


async def test_scope_defaults_to_response_like_an_action():
    interaction = _interaction()
    doc = _Doc("research", [{"response": "Be concise."}])
    await accumulate_skill_parameters(interaction, [doc])
    assert interaction.parameters[0]["scope"] == "response"


async def test_orchestration_scope_is_preserved():
    interaction = _interaction()
    doc = _Doc(
        "research", [{"scope": "orchestration", "response": "Search before answering."}]
    )
    await accumulate_skill_parameters(interaction, [doc])
    pooled = interaction.parameters
    assert orchestration_parameters(pooled)
    assert not response_parameters(pooled)


async def test_a_skill_with_no_parameters_changes_nothing():
    interaction = _interaction()
    assert await accumulate_skill_parameters(interaction, [_Doc("plain", [])]) is False
    assert interaction.parameters == []


async def test_pooled_skill_rules_render_like_action_rules():
    interaction = _interaction()
    doc = _Doc(
        "research",
        [
            {"response": "Always cite sources."},
            {"condition": "asked for an opinion", "response": "Say it is an opinion."},
        ],
    )
    await accumulate_skill_parameters(interaction, [doc])
    rendered = render_parameters(response_parameters(interaction.parameters))
    assert "- Always cite sources." in rendered
    assert "- When asked for an opinion: Say it is an opinion." in rendered


async def test_accumulation_is_defensive():
    """A broken interaction must not take the turn down — parameters are
    additive shaping, not a precondition for replying."""
    broken = MagicMock()
    broken.add_parameters = MagicMock(side_effect=RuntimeError("boom"))
    doc = _Doc("research", [{"response": "Be concise."}])
    assert await accumulate_skill_parameters(broken, [doc]) is False


# --- end to end through the loop --------------------------------------------


async def test_always_active_skill_rules_are_in_force_from_turn_start(
    make_orchestrator, make_visitor, monkeypatch
):
    from jvagent.action.orchestrator.orchestrator_interact_action import (
        OrchestratorInteractAction,
    )
    from jvagent.action.orchestrator.skills import SkillDoc

    doc = SkillDoc(
        name="house_rules",
        description="Always-on house rules.",
        body="Follow the rules.",
        always_active=True,
        parameters=({"scope": "response", "response": "Never quote a discount."},),
    )
    ex = make_orchestrator(actions=[], decisions=[{"action": "final", "answer": "ok"}])
    # after make_orchestrator: the fixture patches _discover_skills itself
    monkeypatch.setattr(
        OrchestratorInteractAction, "_discover_skills", lambda self, agent: [doc]
    )
    visitor = make_visitor(utterance="hello")

    pooled: list = []

    def add_parameters(params, name):
        for p in params:
            entry = dict(p)
            entry["action_name"] = name
            pooled.append(entry)
        visitor.interaction.parameters = pooled
        return bool(params)

    visitor.interaction.add_parameters = add_parameters
    visitor.interaction.save = AsyncMock()

    await ex.execute(visitor)

    rules = [p for p in pooled if p.get("action_name") == "house_rules"]
    assert rules, pooled
    assert rules[0]["response"] == "Never quote a discount."


async def test_a_merely_available_skill_contributes_nothing(
    make_orchestrator, make_visitor, monkeypatch
):
    """Otherwise every skill listed on the agent would shape every turn."""
    from jvagent.action.orchestrator.orchestrator_interact_action import (
        OrchestratorInteractAction,
    )
    from jvagent.action.orchestrator.skills import SkillDoc

    doc = SkillDoc(
        name="dormant",
        description="Not active.",
        body="…",
        parameters=({"response": "Should not apply."},),
    )
    ex = make_orchestrator(actions=[], decisions=[{"action": "final", "answer": "ok"}])
    # after make_orchestrator: the fixture patches _discover_skills itself
    monkeypatch.setattr(
        OrchestratorInteractAction, "_discover_skills", lambda self, agent: [doc]
    )
    visitor = make_visitor(utterance="hello")
    pooled: list = []
    visitor.interaction.add_parameters = lambda params, name: pooled.extend(
        dict(p, action_name=name) for p in params
    ) or bool(params)
    visitor.interaction.save = AsyncMock()

    await ex.execute(visitor)
    assert not [p for p in pooled if p.get("action_name") == "dormant"]
