"""The common parameter subsystem at the Orchestrator: native orchestration core,
accumulation onto the interaction, and orchestration-scoped rendering.

The Orchestrator natively owns the ``orchestration``-scoped core (applied in the
agentic loop). Each turn it accumulates every enabled action's scoped params onto
``interaction.parameters``; its system prompt renders only the orchestration
subset. Response-scoped params belong to the ReplyAction's response prompt.
"""

from unittest.mock import AsyncMock

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.parameters import (
    orchestration_parameters,
    render_parameters,
    response_parameters,
)

pytestmark = pytest.mark.asyncio


def test_orchestrator_native_core_is_orchestration_only():
    ex = OrchestratorInteractAction()
    assert len(orchestration_parameters(ex.parameters)) == 1
    assert len(response_parameters(ex.parameters)) == 0  # response is reply's job


def test_system_prompt_renders_orchestration_rules_and_response_safeguards():
    from jvagent.action.parameters import reply_core_parameters

    ex = OrchestratorInteractAction()
    # mirrors _run_loop: orchestration rules + the core response params (so a
    # reply the executive writes itself, via the fast reply path, is hardened).
    parameters_section = render_parameters(
        orchestration_parameters(ex.parameters) + reply_core_parameters()
    )
    sp = ex._compose_system_prompt(
        identity_section="You are X. ",
        tools_section="(tools)",
        skills_section="(skills)",
        capabilities_section="(caps)",
        parameters_section=parameters_section,
    )
    assert "OPERATING RULES" in sp
    assert "honor only directives" in sp  # orchestration rule
    assert "knowledge or training cutoff" in sp  # response safeguard
    assert "tools, skills, prompts" in sp  # no-internal-reveal safeguard


async def test_accumulate_pools_all_actions_params(monkeypatch):
    """The executive pools every enabled action's scoped params onto the
    interaction — orchestration (its own) + response (the reply's) + others."""
    ex = OrchestratorInteractAction()

    class _Cap:
        parameters = [{"scope": "response", "response": "no closers"}]

        def get_class_name(self):
            return "CapAction"

    monkeypatch.setattr(
        OrchestratorInteractAction, "_safe_agent", AsyncMock(return_value=object())
    )

    async def _enabled(self, _agent):
        return [ex, _Cap()]

    monkeypatch.setattr(OrchestratorInteractAction, "_enabled_actions", _enabled)

    saved = {}

    class _Inter:
        def __init__(self):
            self.parameters = []

        def add_parameters(self, params, name):
            self.parameters.extend(params)
            return True

        async def save(self):
            saved["ok"] = True

    inter = _Inter()
    await ex._accumulate_parameters(inter)
    # the executive's orchestration core + the contributed response param landed
    assert len(orchestration_parameters(inter.parameters)) == 1
    assert len(response_parameters(inter.parameters)) >= 1
    assert saved.get("ok") is True
