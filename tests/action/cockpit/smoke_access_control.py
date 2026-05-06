"""Real-LM smoke for cockpit access-control integration (Milestone F).

Boots the example app, attaches an AccessControlAction to ``cockpit_agent``
on the fly with a deny rule for ``skill:web_search`` against a specific
user_id, then sends a web-search-style utterance and confirms cockpit
respects the deny.

Run::

    .venv/bin/python tests/action/cockpit/smoke_access_control.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = REPO_ROOT / "examples" / "jvagent_app"


async def _attach_access_control(agent: Any, *, denied_user: str) -> Any:
    """Attach (or update) AccessControlAction on the agent with a deny rule."""
    from jvagent.action.access_control.access_control_action import AccessControlAction

    actions_mgr = await agent.get_actions_manager()
    ac = await agent.get_access_control_action()
    if ac is None:
        # Real apps register AccessControlAction via app.yaml; the smoke test
        # creates one in-memory and connects it for a single run.
        ac = AccessControlAction(
            agent_id=agent.id,
            namespace="jvagent",
            label="access_control_action",
            enabled=True,
            enforce=True,
            allow_anonymous=True,
        )
        await actions_mgr.register_action(ac)

    ac.enforce = True
    ac.allow_anonymous = True
    ac.permissions = {
        "default": {
            "any": {"deny": [], "allow": [{"group": "all", "enabled": True}]},
            "skill:web_search": {
                "deny": [{"user": denied_user, "enabled": True}],
                "allow": [{"group": "all", "enabled": True}],
            },
            "HandoffInteractAction": {
                "deny": [{"user": denied_user, "enabled": True}],
                "allow": [{"group": "all", "enabled": True}],
            },
            "tool:web_search__search": {
                "deny": [{"user": denied_user, "enabled": True}],
                "allow": [{"group": "all", "enabled": True}],
            },
        }
    }
    await ac.save()
    return ac


async def _run(agent: Any, *, utterance: str, user_id: str) -> dict:
    from jvspatial import flush_deferred_entities

    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.model.context import set_interaction

    walker = InteractWalker(
        agent_id=agent.id,
        utterance=utterance,
        channel="default",
        data={},
        session_id=None,
        user_id=user_id,
        stream=False,
    )
    await walker.spawn(agent)
    interaction = walker.interaction
    if interaction is not None:
        try:
            interaction.streamed = False
            await interaction.close_interaction()
            await flush_deferred_entities(
                interaction, walker.conversation, strict=False
            )
        except Exception:
            pass
        set_interaction(None)

    return {
        "utterance": utterance,
        "user_id": user_id,
        "response": (interaction.response if interaction else "") or "",
        "actions": (
            list(getattr(interaction, "actions", []) or []) if interaction else []
        ),
    }


async def _main() -> int:
    from dotenv import load_dotenv

    load_dotenv(APP_ROOT / ".env", override=True)
    logging.basicConfig(
        level=logging.WARNING, format="%(levelname)s %(name)s | %(message)s"
    )
    logging.getLogger("jvagent.action.cockpit.access").setLevel(logging.INFO)

    from jvagent.cli.bootstrap import bootstrap_application_graph
    from jvagent.cli.server_config import _set_db_env_from_config
    from jvagent.core.agent import Agent
    from jvagent.core.index_bootstrap import run_index_migration

    _set_db_env_from_config(str(APP_ROOT))
    await run_index_migration()
    await bootstrap_application_graph(update_mode=None, app_root=str(APP_ROOT))

    agents = await Agent.find({"context.name": "cockpit_agent"})
    if not agents:
        print("cockpit_agent not found", file=sys.stderr)
        return 1
    agent = agents[0]

    denied_user = "denied_user_smoke"
    allowed_user = "allowed_user_smoke"
    await _attach_access_control(agent, denied_user=denied_user)

    print("\n=== access control deny scenarios ===")
    deny_skill = await _run(
        agent,
        utterance="Search the web for the most recent Python release.",
        user_id=denied_user,
    )
    print(
        f"DENIED USER (web_search skill blocked): response_chars={len(deny_skill['response'])}"
    )
    print(f"  actions executed: {deny_skill['actions']}")
    print(f"  response preview: {deny_skill['response'][:160]}")

    deny_ia = await _run(
        agent,
        utterance="I'd like to speak to a human please",
        user_id=denied_user,
    )
    print(
        f"\nDENIED USER (HandoffInteractAction blocked): response_chars={len(deny_ia['response'])}"
    )
    print(f"  actions executed: {deny_ia['actions']}")
    print(f"  response preview: {deny_ia['response'][:160]}")

    print("\n=== allow scenario (control) ===")
    allow_run = await _run(
        agent,
        utterance="Search the web for the most recent Python release.",
        user_id=allowed_user,
    )
    print(f"ALLOWED USER: response_chars={len(allow_run['response'])}")
    print(f"  actions executed: {allow_run['actions']}")
    print(f"  response preview: {allow_run['response'][:160]}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
