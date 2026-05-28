import pytest

from jvagent.action.loader import ActionLoader


@pytest.mark.asyncio
async def test_loader_ignores_unknown_secret_context_keys(temp_dir, test_db, caplog):
    agent_dir = temp_dir / "agents" / "test_namespace" / "test_agent"
    action_dir = agent_dir / "actions" / "test_namespace" / "secret_action"
    action_dir.mkdir(parents=True)

    (action_dir / "secret_action.py").write_text(
        """from jvspatial.core.annotations import attribute
from jvagent.action.base import Action

class SecretAction(Action):
    timeout: int = attribute(default=30, description="Timeout")
"""
    )
    (action_dir / "info.yaml").write_text(
        """package:
  name: test_namespace/secret_action
  archetype: SecretAction
  version: 1.0.0
  meta:
    title: Secret Action
    description: test
"""
    )

    loader = ActionLoader(str(temp_dir))
    actions = loader.load_actions_for_agent(
        "test_namespace",
        "test_agent",
        "agent-id-1",
        action_configs=[
            {
                "action": "test_namespace/secret_action",
                "context": {
                    "api_key": "${OPENAI_API_KEY}",
                    "token": "literal-secret",
                    "enabled": True,
                },
            }
        ],
    )

    assert len(actions) == 1
    action = actions[0]
    assert action.timeout == 30
    assert "unknown context key 'api_key' for action" in caplog.text
    assert "unknown context key 'token' for action" in caplog.text
