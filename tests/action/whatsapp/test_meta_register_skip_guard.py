"""Skip-if-healthy must not skip past the secret fetch.

``register_meta_webhook_subscription`` is the only code path that fetches and
persists ``jvconnect_webhook_secret``, which every inbound webhook needs for
signature verification. The #80008-avoidance optimisation ("forward already
healthy → don't re-register") therefore has a hidden precondition: the secret
must already be on hand. On a fresh DB against an already-registered forward
the old guard skipped anyway — the secret was never fetched, every inbound
message 500'd, and nothing ever re-registered. A permanent outage born from a
health check.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.whatsapp.whatsapp_action import WhatsAppAction

pytestmark = pytest.mark.asyncio


def _action(secret: str) -> WhatsAppAction:
    action = WhatsAppAction.model_construct(
        provider="meta",
        jvconnect_webhook_secret=secret,
        webhook_url="https://example.test/hook",
    )
    return action


async def _run_reload_branch(action: WhatsAppAction) -> AsyncMock:
    """Drive on_reload's meta branch with a healthy forward; return the
    register mock so callers can assert whether registration ran."""
    register = AsyncMock(return_value={"status": "ok"})
    with (
        patch.object(WhatsAppAction, "is_configured", return_value=True),
        patch.object(WhatsAppAction, "is_meta_provider", return_value=True),
        patch.object(
            WhatsAppAction,
            "_meta_webhook_forward_is_healthy",
            AsyncMock(return_value=True),
        ),
        patch.object(WhatsAppAction, "register_meta_webhook_subscription", register),
        patch.dict("os.environ", {}, clear=False),
    ):
        # Ensure no env fallback leaks in from the developer's shell.
        with patch.object(
            WhatsAppAction,
            "_env_jvconnect_webhook_secret",
            lambda self: (self.jvconnect_webhook_secret or "").strip(),
        ):
            await action.on_reload()
    return register


async def test_healthy_forward_with_secret_skips_registration():
    """The optimisation still works when it is safe: secret in hand → no
    re-register, no Meta #80008 exposure."""
    action = _action(secret="s3cret")
    register = await _run_reload_branch(action)
    register.assert_not_awaited()


async def test_healthy_forward_without_secret_still_registers():
    """The outage case: fresh DB, forward healthy, no secret anywhere.
    Registration must run — it is the only way the secret ever arrives."""
    action = _action(secret="")
    register = await _run_reload_branch(action)
    register.assert_awaited_once()


async def test_unhealthy_forward_registers_regardless_of_secret():
    action = _action(secret="s3cret")
    register = AsyncMock(return_value={"status": "ok"})
    with (
        patch.object(WhatsAppAction, "is_configured", return_value=True),
        patch.object(WhatsAppAction, "is_meta_provider", return_value=True),
        patch.object(
            WhatsAppAction,
            "_meta_webhook_forward_is_healthy",
            AsyncMock(return_value=False),
        ),
        patch.object(WhatsAppAction, "register_meta_webhook_subscription", register),
    ):
        await action.on_reload()
    register.assert_awaited_once()
