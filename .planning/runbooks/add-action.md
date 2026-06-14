# Runbook — Add a New Action

> End-to-end: choose base → create files → wire endpoints → write tests → enable in `agent.yaml`. Worked example uses a hypothetical Slack action under `contrib/slack`.

Cross-link: [`action-authoring.md`](../reference/action-authoring.md) for the full contract.

---

## 1. Decide

| Question | If yes... |
|---|---|
| Will it participate in `/interact`? | Subclass `InteractAction` |
| Will it call an LLM provider? | Subclass `LanguageModelAction` |
| Will it expose a tool to the Orchestrator tool surface (model-callable)? | Override `get_tools()` |
| Is it a one-per-agent capability? | Set `singleton: true` in `info.yaml` |
| Should it run AFTER response is sent? | Set `run_in_background: true` |

For Slack: it's a channel adapter — subclass `Action`, register with the `ResponseBus`, define `get_tools()` for model-driven sending via the Orchestrator tool surface.

---

## 2. Create the directory

```bash
mkdir -p jvagent/action/contrib/slack
touch jvagent/action/contrib/slack/{__init__.py,slack_action.py,endpoints.py,info.yaml}
```

(For an app-local action, use `<app_root>/actions/custom/slack/` instead.)

---

## 3. Write `info.yaml`

```yaml
package:
  name: contrib/slack
  author: your_name
  archetype: SlackAction
  version: 0.1.0

  meta:
    title: Slack Channel Adapter
    description: Sends agent responses to Slack channels and receives messages via Events API.
    group: contrib
    type: action

  config:
    singleton: false      # multiple Slack workspaces possible

  dependencies:
    jvagent: ~0.0.1
    actions:
      - jvagent/persona
    pip:
      - slack-sdk>=3.27
```

---

## 4. Write `slack_action.py`

```python
"""Slack channel adapter."""
import logging
from typing import Any, Dict, List, Optional

from jvspatial.core.annotations import attribute
from jvagent.action.base import Action

logger = logging.getLogger(__name__)


class SlackAction(Action):
    """Sends/receives messages via Slack."""

    bot_token: str = attribute(
        default="",
        description="Slack bot token. Prefer ${SLACK_BOT_TOKEN}.",
    )
    signing_secret: str = attribute(
        default="",
        description="Slack signing secret for webhook verification.",
    )
    default_channel: str = attribute(
        default="",
        description="Channel to post into when none specified.",
    )

    # Runtime instance (private, not persisted)
    _client: Any = attribute(private=True, default=None)

    async def on_register(self) -> None:
        # First-time setup: validate token shape
        if not self.bot_token:
            logger.warning("SlackAction registered without bot_token")

    async def on_enable(self) -> None:
        from slack_sdk.web.async_client import AsyncWebClient
        self._client = AsyncWebClient(token=self.bot_token)

    async def on_disable(self) -> None:
        self._client = None

    async def healthcheck(self) -> Dict[str, Any]:
        if not self._client:
            return {"healthy": False, "details": "client not initialized"}
        try:
            await self._client.auth_test()
            return {"healthy": True}
        except Exception as e:
            return {"healthy": False, "details": str(e)}

    def get_capabilities(self) -> List[str]:
        return ["Send messages to Slack channels and threads."]

    async def get_tools(self) -> List[Any]:
        from jvagent.tooling.tool import Tool
        return [
            Tool(
                name="action__slack__send",
                description="Post a message to a Slack channel.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "channel": {"type": "string"},
                        "text": {"type": "string"},
                    },
                    "required": ["channel", "text"],
                },
                handler=self._tool_send,
            ),
        ]

    async def _tool_send(self, channel: str, text: str) -> Dict[str, Any]:
        if not self._client:
            raise RuntimeError("Slack client not initialized; is the action enabled?")
        resp = await self._client.chat_postMessage(channel=channel, text=text)
        return {"ok": resp.get("ok", False), "ts": resp.get("ts")}
```

---

## 5. Write `endpoints.py`

```python
"""HTTP endpoints for SlackAction."""
from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError

from jvagent.action.base import Action
from .slack_action import SlackAction


@endpoint(
    "/actions/{action_id}/slack/events",
    methods=["POST"],
    auth=False,    # public webhook — verify Slack signing secret inside
    description="Slack Events API webhook.",
)
async def slack_events(action_id: str, payload: dict = ResponseField(...)) -> dict:
    action = await Action.get(action_id)
    if not action or not isinstance(action, SlackAction):
        raise ResourceNotFoundError(f"SlackAction not found: {action_id}")

    # 1. Verify Slack signing secret (omitted for brevity).
    # 2. Handle url_verification challenge.
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    # 3. Process event — convert to InteractWalker payload, spawn on agent.
    # ... (see whatsapp/endpoints.py for the pattern)

    return success_response({"received": True})
```

---

## 6. Write `__init__.py`

```python
"""Slack action package."""
from .slack_action import SlackAction  # noqa: F401
from . import endpoints  # noqa: F401   # side-effect: register @endpoint decorators
```

---

## 7. Write tests

```bash
mkdir -p tests/action/contrib_slack
touch tests/action/contrib_slack/{__init__.py,test_slack.py}
```

`tests/action/contrib_slack/test_slack.py`:

```python
import pytest

from jvagent.action.contrib.slack.slack_action import SlackAction


@pytest.mark.asyncio
async def test_slack_action_disabled_by_default_without_token():
    action = SlackAction(label="slack-test")
    assert action.bot_token == ""
    health = await action.healthcheck()
    assert health["healthy"] is False


@pytest.mark.asyncio
async def test_slack_get_tools_returns_send():
    action = SlackAction(label="slack-test")
    tools = await action.get_tools()
    assert any(t.name == "action__slack__send" for t in tools)
```

Run:

```bash
pytest tests/action/contrib_slack/ -v
```

---

## 8. Enable in `agent.yaml`

```yaml
actions:
  - action: contrib/slack
    context:
      bot_token: ${SLACK_BOT_TOKEN}
      signing_secret: ${SLACK_SIGNING_SECRET}
      default_channel: "#general"
```

Then:

```bash
jvagent /path/to/app --update --debug
# Confirm in logs that SlackAction registers + enables
```

---

## 9. Verify

```bash
# Get the action ID
curl -s -H "Authorization: Bearer $JV" \
  "http://localhost:8000/api/agents/$AGENT_ID/actions" | jq

# Send a model request that should invoke the slack tool — verify in Slack
curl -s -X POST -H "Authorization: Bearer $JV" \
  -H "Content-Type: application/json" \
  "http://localhost:8000/agents/$AGENT_ID/interact" \
  -d '{"utterance":"Send a hello to #general on Slack","user_id":"u","session_id":"s"}'
```

---

## 10. Update the catalog

Edit [`.planning/reference/actions-catalog.md`](../reference/actions-catalog.md) — add a row under §1.4 (Messaging / broadcast).

---

## 11. Common mistakes

| Mistake | Symptom |
|---|---|
| Forgot `from . import endpoints` in `__init__.py` | Webhook URL returns 404 |
| Class name doesn't match `archetype: SlackAction` in `info.yaml` | Loader silently skips package |
| Endpoint path not under `/actions/{action_id}/` | Deregister leaks the route |
| Heavy import (e.g., `slack_sdk`) at module top level | Slow startup; defer to `on_enable` if optional |
| Token in `info.yaml` instead of `agent.yaml` context | Token leaks into committed config; use `${SLACK_BOT_TOKEN}` env indirection |

---

## 12. Checklist

- [ ] `info.yaml` with correct `name`, `archetype`, `dependencies`
- [ ] `slack_action.py` subclasses correct base
- [ ] `attribute(...)` for every persisted field
- [ ] Lifecycle hooks (`on_register`, `on_enable`, `on_disable`) implemented
- [ ] `healthcheck()` returns useful status
- [ ] `get_capabilities()` returns 1-line summaries
- [ ] `get_tools()` exposes model-callable tools to the Orchestrator tool surface (if applicable)
- [ ] `endpoints.py` under `/actions/{action_id}/...`
- [ ] `__init__.py` re-exports class + imports endpoints
- [ ] `tests/action/<name>/` with at least 1 unit + 1 integration test
- [ ] `agent.yaml` entry with env-var-indirected secrets
- [ ] Catalog updated in [`actions-catalog.md`](../reference/actions-catalog.md)
- [ ] `pre-commit run --all-files` clean
- [ ] `pytest tests/action/<name>/` clean
