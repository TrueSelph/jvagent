"""Shared machinery for the wire tier.

Lives outside ``conftest.py`` so a subprocess can import it too — the
cross-process determinism check has to run in a *separate interpreter* with a
different ``PYTHONHASHSEED``, which is the only way to actually test that set
iteration order never reaches the model.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

APP_YAML = """
app: jvagent_wire_test
context:
  name: Wire Contract Test App
  description: boots a real graph so prompts can be asserted on the wire
config:
  database:
    type: json
    path: ./wire_jvdb
  logging:
    enabled: false

agents:
  - jvagent/wire_agent
"""

AGENT_YAML = """
agent: jvagent/wire_agent
version: 1.0.0
author: tests
jvagent: ~0.0.1

context:
  alias: Wire Agent
  role: a test agent used to assert what reaches the model
  description: wire-contract fixture agent
  enabled: true

actions:
  - action: jvagent/orchestrator
    context:
      enabled: true
  - action: jvagent/reply
    context:
      enabled: true
"""


def write_app(root: Path) -> str:
    """Materialise a minimal but real app on disk; return its root."""
    root = Path(root)
    (root / "app.yaml").write_text(textwrap.dedent(APP_YAML).strip(), "utf-8")
    agent_dir = root / "agents" / "jvagent" / "wire_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "agent.yaml").write_text(textwrap.dedent(AGENT_YAML).strip(), "utf-8")
    return str(root)


async def load_orchestrator(app_root: str) -> Any:
    """Bootstrap the graph and return the orchestrator **read back from it**."""
    from jvagent.cli.bootstrap import bootstrap_application_graph
    from jvagent.core.agents import Agents

    await bootstrap_application_graph(update_mode="source", app_root=app_root)
    agents = await (await Agents.get()).get_connected_agents()
    if not agents:
        raise AssertionError("wire fixture bootstrapped no agents")
    actions = await (await agents[0].get_actions_manager()).get_all_actions(
        enabled_only=True
    )
    orchestrator = next(
        (a for a in actions if type(a).__name__ == "OrchestratorInteractAction"), None
    )
    if orchestrator is None:
        raise AssertionError("wire fixture found no orchestrator on the graph")
    return orchestrator


@dataclass
class WireCapture:
    """Exactly what one tick would have sent."""

    system: str = ""
    user: str = ""
    messages: List[Dict[str, Any]] = field(default_factory=list)

    def __contains__(self, needle: str) -> bool:
        return needle in self.system or needle in self.user

    @property
    def whole(self) -> str:
        return f"{self.system}\n{self.user}"


class WireProbe:
    """Renders a real tick's prompts without calling a model."""

    def __init__(self, orchestrator: Any) -> None:
        self.orchestrator = orchestrator

    async def capture(
        self,
        utterance: str = "hello there",
        *,
        parameters: Optional[List[Any]] = None,
        tools: Optional[List[Any]] = None,
        **attrs: Any,
    ) -> WireCapture:
        """Render one tick; return the captured prompts.

        ``attrs`` sets orchestrator attributes for this capture, so a gate such
        as ``block_raw_tool_invocation`` is exercised as configuration rather
        than assumed.
        """
        from jvagent.action.orchestrator.orchestrator_interact_action import (
            OrchestratorInteractAction,
        )

        ex = self.orchestrator
        for key, value in attrs.items():
            setattr(ex, key, value)

        captured: Dict[str, Any] = {}

        async def _query_messages(**kwargs: Any) -> Any:
            captured["system"] = kwargs.get("system", "") or ""
            captured["messages"] = list(kwargs.get("messages") or [])
            return SimpleNamespace(response='{"action":"final"}')

        model = MagicMock()
        model.query_messages = _query_messages

        async def _get_model_action(self: Any, required: bool = False) -> Any:
            return model

        original = OrchestratorInteractAction.get_model_action
        OrchestratorInteractAction.get_model_action = _get_model_action  # type: ignore[assignment]
        try:
            visitor = MagicMock()
            visitor.interaction = SimpleNamespace(
                parameters=list(parameters or []), utterance=utterance, id="i-wire"
            )
            visitor.channel = "default"
            visitor.stream = False
            await ex._run_model(visitor, utterance, [], list(tools or []), [])
        finally:
            OrchestratorInteractAction.get_model_action = original  # type: ignore[assignment]

        user = ""
        for message in captured.get("messages", []):
            if message.get("role") == "user":
                user = message.get("content", "") or ""
        return WireCapture(
            system=captured.get("system", ""),
            user=user,
            messages=captured.get("messages", []),
        )
