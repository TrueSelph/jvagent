"""Skip-if-healthy and serverless startup skip for Meta webhook register.

``on_reload`` still requires a local secret before skipping: registration is
the only path that fetches ``jvconnect_webhook_secret``, and skipping without
it on an intentional reload would strand inbound HMAC forever.

``on_startup`` / cold start is different: never Meta-re-POST just to fetch a
secret when the forward is already healthy (Lambda GB-second / #80008). Ops
must persist ``JVCONNECT_WEBHOOK_SECRET`` from a one-shot admin register.
Serverless defaults to skipping startup registration entirely.
"""

from unittest.mock import AsyncMock, patch

from jvagent.action.whatsapp.whatsapp_action import WhatsAppAction


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
        # Opt in: default is skip on_reload; tests exercise the subscribe path.
        patch.dict(
            "os.environ",
            {"WHATSAPP_RELOAD_WEBHOOK_SUBSCRIBE": "true"},
            clear=False,
        ),
        patch.object(
            WhatsAppAction,
            "_env_jvconnect_webhook_secret",
            lambda self: (self.jvconnect_webhook_secret or "").strip(),
        ),
    ):
        await action.on_reload()
    return register


async def test_reload_skips_meta_subscribe_by_default():
    """CTO guidance: do not re-register Meta webhook on every on_reload."""
    action = _action(secret="s3cret")
    register = AsyncMock(return_value={"status": "ok"})
    with (
        patch.object(WhatsAppAction, "is_configured", return_value=True),
        patch.object(WhatsAppAction, "is_meta_provider", return_value=True),
        patch.object(WhatsAppAction, "register_meta_webhook_subscription", register),
        patch.dict(
            "os.environ", {"WHATSAPP_RELOAD_WEBHOOK_SUBSCRIBE": ""}, clear=False
        ),
    ):
        import os

        os.environ.pop("WHATSAPP_RELOAD_WEBHOOK_SUBSCRIBE", None)
        await action.on_reload()
    register.assert_not_awaited()


async def test_healthy_forward_with_secret_skips_registration():
    """The optimisation still works when it is safe: secret in hand → no
    re-register, no Meta #80008 exposure."""
    action = _action(secret="s3cret")
    register = await _run_reload_branch(action)
    register.assert_not_awaited()


async def test_healthy_forward_without_secret_still_registers():
    """Reload outage case: fresh DB, forward healthy, no secret anywhere.
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
        patch.dict(
            "os.environ",
            {"WHATSAPP_RELOAD_WEBHOOK_SUBSCRIBE": "true"},
            clear=False,
        ),
    ):
        await action.on_reload()
    register.assert_awaited_once()


def test_startup_skip_env_true():
    with patch.dict(
        "os.environ",
        {"WHATSAPP_SKIP_STARTUP_WEBHOOK_REGISTRATION": "true"},
        clear=False,
    ):
        skip, reason = WhatsAppAction._startup_meta_webhook_skip_decision()
    assert skip is True
    assert reason == "WHATSAPP_SKIP_STARTUP_WEBHOOK_REGISTRATION=true"


def test_startup_skip_serverless_default():
    import os

    with patch.object(WhatsAppAction, "_is_serverless_runtime", return_value=True):
        os.environ.pop("WHATSAPP_SKIP_STARTUP_WEBHOOK_REGISTRATION", None)
        skip, reason = WhatsAppAction._startup_meta_webhook_skip_decision()
    assert skip is True
    assert reason == "serverless_default"


def test_startup_skip_env_false_forces_register_on_serverless():
    with (
        patch.dict(
            "os.environ",
            {"WHATSAPP_SKIP_STARTUP_WEBHOOK_REGISTRATION": "false"},
            clear=False,
        ),
        patch.object(WhatsAppAction, "_is_serverless_runtime", return_value=True),
    ):
        skip, reason = WhatsAppAction._startup_meta_webhook_skip_decision()
    assert skip is False
    assert reason == ""


def test_startup_no_skip_when_unset_non_serverless():
    import os

    with patch.object(WhatsAppAction, "_is_serverless_runtime", return_value=False):
        os.environ.pop("WHATSAPP_SKIP_STARTUP_WEBHOOK_REGISTRATION", None)
        skip, reason = WhatsAppAction._startup_meta_webhook_skip_decision()
    assert skip is False
    assert reason == ""


def test_startup_webhook_timeout_serverless_default():
    with (patch.object(WhatsAppAction, "_is_serverless_runtime", return_value=True),):
        import os

        os.environ.pop("WHATSAPP_STARTUP_WEBHOOK_REGISTER_TIMEOUT_SECONDS", None)
        assert WhatsAppAction._startup_webhook_register_timeout_seconds() == 5.0


def test_startup_webhook_timeout_env_override():
    with patch.dict(
        "os.environ",
        {"WHATSAPP_STARTUP_WEBHOOK_REGISTER_TIMEOUT_SECONDS": "8"},
        clear=False,
    ):
        assert WhatsAppAction._startup_webhook_register_timeout_seconds() == 8.0


async def test_startup_healthy_without_secret_skips_register():
    """Cold start must not Meta-re-POST when forward is healthy but secret missing."""
    action = _action(secret="")
    register = AsyncMock(return_value={"status": "ok"})
    with (
        patch.object(
            WhatsAppAction,
            "_meta_webhook_forward_is_healthy",
            AsyncMock(return_value=True),
        ),
        patch.object(WhatsAppAction, "register_meta_webhook_subscription", register),
        patch.object(
            WhatsAppAction,
            "_env_jvconnect_webhook_secret",
            lambda self: "",
        ),
    ):
        await action._run_startup_meta_webhook_register()
    register.assert_not_awaited()
    assert action._session_registered is True


async def test_startup_healthy_with_secret_skips_register():
    action = _action(secret="s3cret")
    register = AsyncMock(return_value={"status": "ok"})
    with (
        patch.object(
            WhatsAppAction,
            "_meta_webhook_forward_is_healthy",
            AsyncMock(return_value=True),
        ),
        patch.object(WhatsAppAction, "register_meta_webhook_subscription", register),
        patch.object(
            WhatsAppAction,
            "_env_jvconnect_webhook_secret",
            lambda self: "s3cret",
        ),
    ):
        await action._run_startup_meta_webhook_register()
    register.assert_not_awaited()


async def test_startup_unhealthy_registers():
    action = _action(secret="")
    register = AsyncMock(return_value={"status": "ok"})
    with (
        patch.object(
            WhatsAppAction,
            "_meta_webhook_forward_is_healthy",
            AsyncMock(return_value=False),
        ),
        patch.object(WhatsAppAction, "register_meta_webhook_subscription", register),
    ):
        await action._run_startup_meta_webhook_register()
    register.assert_awaited_once()


async def test_on_startup_serverless_skips_schedule():
    action = _action(secret="")
    action.enabled = True
    action.tts_action = None
    action.request_timeout = 60
    schedule = AsyncMock()
    agent = object()
    with (
        patch.object(WhatsAppAction, "is_configured", return_value=True),
        patch.object(WhatsAppAction, "is_meta_provider", return_value=True),
        patch.object(
            WhatsAppAction, "_warn_lambda_local_storage", AsyncMock(return_value=None)
        ),
        patch.object(WhatsAppAction, "get_agent", AsyncMock(return_value=agent)),
        patch("jvagent.action.whatsapp.whatsapp_action.WhatsAppFilter") as filter_cls,
        patch("jvagent.action.whatsapp.whatsapp_action.WhatsAppAdapter") as adapter_cls,
        patch.object(
            WhatsAppAction,
            "_startup_meta_webhook_skip_decision",
            return_value=(True, "serverless_default"),
        ),
        patch.object(
            WhatsAppAction, "_schedule_deferred_meta_webhook_register", schedule
        ),
    ):
        filter_inst = filter_cls.return_value
        filter_inst.initialize = AsyncMock(return_value=True)
        adapter_inst = adapter_cls.return_value
        adapter_inst.initialize = AsyncMock(return_value=True)
        await action.on_startup()
    schedule.assert_not_called()
