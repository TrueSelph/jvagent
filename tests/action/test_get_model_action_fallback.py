"""get_model_action() base-class fallback must find any provider (AUDIT-actions HIGH).

The fallback previously called get_action(LanguageModelAction), which resolves
by exact class name via the type index — that index only holds concrete
provider names, so the base name never matched and any agent without the
specific model_action_type silently got None (losing identity compose)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from jvagent.action.actions import Actions
from jvagent.action.base import Action
from jvagent.action.model.language.base import LanguageModelAction
from jvagent.core.agent import Agent

pytestmark = pytest.mark.asyncio


class StubProviderAction(LanguageModelAction):
    """A concrete non-OpenAI provider stub (distinct class name)."""

    async def _query(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ):  # pragma: no cover - not invoked
        raise NotImplementedError

    async def _query_stream(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ):  # pragma: no cover - not invoked
        raise NotImplementedError


class PlainConsumerAction(Action):
    """An action that needs a model but declares no model_action_type."""


async def _agent_with_provider(provider_enabled=True):
    agent = await Agent.create(
        name="lm_agent", namespace="test", alias="LM", description="d"
    )
    manager = await Actions.create()
    await agent.connect(manager, direction="both")

    provider = await StubProviderAction.create(
        namespace="test", label="stub_provider", enabled=provider_enabled
    )
    object.__setattr__(provider, "agent_id", agent.id)
    await provider.save()
    await manager.connect(provider, direction="both")
    return agent, manager, provider


async def test_fallback_finds_non_openai_provider(test_db):
    agent, _, provider = await _agent_with_provider()

    consumer = PlainConsumerAction(agent_id=agent.id, namespace="test", label="c")

    found = await consumer.get_model_action()
    assert found is not None
    assert found.id == provider.id


async def test_required_raises_when_no_provider(test_db):
    agent = await Agent.create(
        name="empty_agent", namespace="test", alias="E", description="d"
    )
    manager = await Actions.create()
    await agent.connect(manager, direction="both")

    consumer = PlainConsumerAction(agent_id=agent.id, namespace="test", label="c")

    assert await consumer.get_model_action() is None
    with pytest.raises(RuntimeError):
        await consumer.get_model_action(required=True)


async def test_fallback_skips_disabled_provider(test_db):
    agent, _, _ = await _agent_with_provider(provider_enabled=False)

    consumer = PlainConsumerAction(agent_id=agent.id, namespace="test", label="c")

    # enabled_only is the default in get_action_by_base_class → disabled skipped.
    assert await consumer.get_model_action() is None
