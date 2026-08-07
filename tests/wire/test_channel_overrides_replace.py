"""``channel_overrides`` semantics, asserted from YAML through a real graph.

The orchestrator suites set ``ex.channel_overrides = {...}`` in Python. That
pins the *resolver*; it never exercises the trip a real deployment takes —
agent.yaml → bootstrap → persisted attribute → resolution. A loader that dropped
or reshaped the nested block would leave every one of those tests green while
the feature did nothing in production.

Two properties are pinned here because both present as "channel overrides are
broken" and neither raises:

- list knobs (``pinned_tools`` / ``denied_tools`` / ``skill_only_tools``)
  **REPLACE** the action-level list on that channel rather than merging, so a
  config that worked with the block commented out can stop working when it is
  uncommented
- override keys match ``visitor.channel`` **exactly** — ``whatsapp`` does not
  cover ``whatsapp_call``

See docs/ORCHESTRATOR.md "Per-channel overrides".
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

APP_YAML = """
app: channel_override_wire
context:
  name: Channel Override Wire Test
  description: boots a real graph to assert per-channel resolution
config:
  database:
    type: json
    path: ./chan_jvdb
  logging:
    enabled: false

agents:
  - jvagent/chan_agent
"""

AGENT_YAML = """
agent: jvagent/chan_agent
version: 1.0.0
author: tests
jvagent: ~0.0.1

context:
  alias: Channel Agent
  role: a test agent for per-channel override resolution
  description: channel-override fixture agent
  enabled: true

actions:
  - action: jvagent/orchestrator
    context:
      enabled: true
      skill_only_tools:
        - "pay__*"
      pinned_tools:
        - "kb__search"
      channel_overrides:
        whatsapp:
          skill_only_tools:
            - "wa__*"
          pinned_tools:
            - "whatsapp__send_flow"
        whatsapp_call:
          skill_only_tools: []
        web:
          history_limit: 4
  - action: jvagent/reply
    context:
      enabled: true
"""


@pytest.fixture
async def orchestrator(tmp_path, monkeypatch) -> Any:
    """The orchestrator for the app above, read back out of the graph."""
    from jvagent.cli.bootstrap import bootstrap_application_graph
    from jvagent.core.agents import Agents
    from jvagent.core.app_context import clear_app_root, set_app_root

    monkeypatch.setenv("JVSPATIAL_ENABLE_DEFERRED_SAVES", "false")
    root = Path(tmp_path)
    (root / "app.yaml").write_text(textwrap.dedent(APP_YAML).strip(), "utf-8")
    agent_dir = root / "agents" / "jvagent" / "chan_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "agent.yaml").write_text(textwrap.dedent(AGENT_YAML).strip(), "utf-8")

    set_app_root(str(root))
    try:
        await bootstrap_application_graph(update_mode="source", app_root=str(root))
        agents = await (await Agents.get()).get_connected_agents()
        assert agents, "fixture bootstrapped no agents"
        actions = await (await agents[0].get_actions_manager()).get_all_actions(
            enabled_only=True
        )
        found = next(
            (a for a in actions if type(a).__name__ == "OrchestratorInteractAction"),
            None,
        )
        assert found is not None, "fixture found no orchestrator on the graph"
        yield found
    finally:
        clear_app_root()


def _resolve(orchestrator: Any, channel: str, key: str, current: Any) -> Any:
    return orchestrator._channel_cfg(SimpleNamespace(channel=channel), key, current)


async def test_nested_block_survives_yaml_bootstrap(orchestrator) -> None:
    """The whole nested mapping must land on the attribute, not a flattened
    or emptied version of it — everything below depends on this."""
    overrides = orchestrator.channel_overrides
    assert set(overrides) == {"whatsapp", "whatsapp_call", "web"}
    assert overrides["whatsapp"]["skill_only_tools"] == ["wa__*"]
    assert overrides["whatsapp_call"]["skill_only_tools"] == []
    assert overrides["web"]["history_limit"] == 4
    assert orchestrator.skill_only_tools == ["pay__*"]


async def test_list_knobs_replace_rather_than_merge(orchestrator) -> None:
    """The trap: the action-level entries are GONE on an overridden channel.

    An operator reading this as "adds to" ships a channel where the global
    gate silently does not apply.
    """
    resolved = _resolve(
        orchestrator, "whatsapp", "skill_only_tools", orchestrator.skill_only_tools
    )
    assert resolved == ["wa__*"]
    assert "pay__*" not in resolved

    pins = _resolve(orchestrator, "whatsapp", "pinned_tools", orchestrator.pinned_tools)
    assert pins == ["whatsapp__send_flow"]
    assert "kb__search" not in pins


async def test_explicit_empty_means_none_here_not_fall_back(orchestrator) -> None:
    """``[]`` gates nothing on that channel; a truthiness check gets this wrong."""
    assert (
        _resolve(
            orchestrator,
            "whatsapp_call",
            "skill_only_tools",
            orchestrator.skill_only_tools,
        )
        == []
    )


async def test_channel_without_that_key_falls_back(orchestrator) -> None:
    """``web`` overrides only history_limit, so the gate stays action-level."""
    assert _resolve(
        orchestrator, "web", "skill_only_tools", orchestrator.skill_only_tools
    ) == ["pay__*"]
    assert _resolve(orchestrator, "web", "history_limit", 12) == 4


async def test_keys_match_the_channel_string_exactly(orchestrator) -> None:
    """No prefix matching: a sibling channel does not inherit the block.

    ``whatsapp`` vs ``whatsapp_call`` is the pairing that actually ships, and a
    mis-keyed block no-ops silently rather than erroring.
    """
    whatsapp = _resolve(
        orchestrator, "whatsapp", "pinned_tools", orchestrator.pinned_tools
    )
    assert whatsapp == ["whatsapp__send_flow"]

    # A channel that shares the 'whatsapp' prefix but has NO block of its own.
    # This is the case that actually detects prefix matching: a sibling with its
    # own block resolves by exact hit, so the fallback path never runs and the
    # assertion proves nothing.
    unblocked_sibling = _resolve(
        orchestrator, "whatsapp_media", "pinned_tools", orchestrator.pinned_tools
    )
    assert unblocked_sibling == [
        "kb__search"
    ], "whatsapp_media must fall back to action-level, not inherit whatsapp's block"

    # And one with a block of its own keeps that block, not the prefix's.
    assert (
        _resolve(
            orchestrator,
            "whatsapp_call",
            "skill_only_tools",
            orchestrator.skill_only_tools,
        )
        == []
    )

    unknown = _resolve(
        orchestrator, "telegram", "skill_only_tools", orchestrator.skill_only_tools
    )
    assert unknown == ["pay__*"]
