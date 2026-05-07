"""Regression: cockpit config knobs reach the model action / task tools.

Previously these attributes were declared on ``CockpitInteractAction`` and
``CockpitConfig`` and plumbed through ``_build_cockpit_config`` but had
zero read sites in cockpit code, so flipping them in ``agent.yaml`` did
nothing. This file pins the wiring so the regression doesn't return.
"""

from __future__ import annotations

import pytest

from jvagent.action.cockpit.engine import CockpitEngine
from jvagent.action.cockpit.tools.task import _build_task_tools

from .conftest import ScriptedModelAction, make_lm_result


@pytest.mark.asyncio
async def test_engine_forwards_model_temperature_max_tokens(
    cockpit_ctx, patch_assemble_cockpit_tools
):
    cockpit_ctx.config.model = "claude-sonnet-test"
    cockpit_ctx.config.model_temperature = 0.42
    cockpit_ctx.config.model_max_tokens = 1234
    cockpit_ctx.model_action = ScriptedModelAction([make_lm_result(response="ok")])
    engine = CockpitEngine(cockpit_ctx)
    await engine.initialize()
    await engine.step()

    forwarded = cockpit_ctx.model_action.calls[0]["kwargs"]
    assert forwarded["model"] == "claude-sonnet-test"
    assert forwarded["temperature"] == 0.42
    assert forwarded["max_tokens"] == 1234


@pytest.mark.asyncio
async def test_engine_omits_model_when_unset(cockpit_ctx, patch_assemble_cockpit_tools):
    cockpit_ctx.config.model = ""
    cockpit_ctx.model_action = ScriptedModelAction([make_lm_result(response="ok")])
    engine = CockpitEngine(cockpit_ctx)
    await engine.initialize()
    await engine.step()

    forwarded = cockpit_ctx.model_action.calls[0]["kwargs"]
    assert "model" not in forwarded


@pytest.mark.asyncio
async def test_engine_translates_reasoning_config(
    cockpit_ctx, patch_assemble_cockpit_tools
):
    """Reasoning fields are translated by the provider and forwarded."""

    captured: dict = {}

    class ProviderWithReasoning(ScriptedModelAction):
        def translate_reasoning_config(self, cfg):
            captured["reasoning_effort"] = cfg.reasoning_effort
            captured["reasoning_budget_tokens"] = cfg.reasoning_budget_tokens
            captured["reasoning_enabled"] = cfg.reasoning_enabled
            return {"reasoning_effort": "high", "thinking": {"budget_tokens": 8000}}

    cockpit_ctx.config.reasoning_effort = "high"
    cockpit_ctx.config.reasoning_budget_tokens = 8000
    cockpit_ctx.config.reasoning_enabled = True
    cockpit_ctx.model_action = ProviderWithReasoning([make_lm_result(response="ok")])
    engine = CockpitEngine(cockpit_ctx)
    await engine.initialize()
    await engine.step()

    assert captured == {
        "reasoning_effort": "high",
        "reasoning_budget_tokens": 8000,
        "reasoning_enabled": True,
    }
    forwarded = cockpit_ctx.model_action.calls[0]["kwargs"]
    assert forwarded["reasoning_effort"] == "high"
    assert forwarded["thinking"] == {"budget_tokens": 8000}


@pytest.mark.asyncio
async def test_task_create_plan_caps_steps_at_max_task_plan_steps(cockpit_ctx):
    cockpit_ctx.config.max_task_plan_steps = 3
    tools = _build_task_tools(cockpit_ctx)
    create_plan = next(t for t in tools if t.name == "task_create_plan")

    too_many = ["s1", "s2", "s3", "s4"]
    result = await create_plan.call(title="big", steps=too_many)
    text = (
        result if isinstance(result, str) else getattr(result, "content", str(result))
    )
    assert "max_task_plan_steps=3" in text
    assert "Reduce" in text


@pytest.mark.asyncio
async def test_task_create_plan_accepts_within_cap(cockpit_ctx):
    cockpit_ctx.config.max_task_plan_steps = 3
    tools = _build_task_tools(cockpit_ctx)
    create_plan = next(t for t in tools if t.name == "task_create_plan")

    result = await create_plan.call(title="ok", steps=["a", "b", "c"])
    text = (
        result if isinstance(result, str) else getattr(result, "content", str(result))
    )
    # Either created successfully or surfaced an internal error — but never
    # rejected on the cap.
    assert "max_task_plan_steps" not in text
