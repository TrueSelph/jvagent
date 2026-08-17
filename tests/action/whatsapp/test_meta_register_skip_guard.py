"""Skip-if-healthy and serverless startup skip for Meta webhook register.

``on_reload`` and ``on_startup`` share the same secret rule: registration is
the only path that fetches ``jvconnect_webhook_secret``. A healthy forward
without a local secret must still POST register so inbound HMAC can verify.

Serverless skips startup registration only when the secret is already on
hand (later cold starts). First deploy (empty secret) still schedules
register. The lifecycle hook **awaits** that register on Lambda instead of
``asyncio.create_task``.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.whatsapp import whatsapp_action as whatsapp_module
from jvagent.action.whatsapp.whatsapp_action import WhatsAppAction


@pytest.fixture(autouse=True)
def _clear_scheduled_hooks():
    """The dedupe registry is module-global; keep tests independent."""
    whatsapp_module._meta_webhook_startup_hooks.clear()
    whatsapp_module._BACKGROUND_TASKS.clear()
    yield
    whatsapp_module._meta_webhook_startup_hooks.clear()
    whatsapp_module._BACKGROUND_TASKS.clear()


def _action(secret: str, action_id: str = "n.WhatsAppAction.test") -> WhatsAppAction:
    action = WhatsAppAction.model_construct(
        provider="meta",
        jvconnect_webhook_secret=secret,
        webhook_url="https://example.test/hook",
    )
    object.__setattr__(action, "id", action_id)
    return action


def _skip_decision(action: WhatsAppAction) -> tuple[bool, str]:
    return action._startup_meta_webhook_skip_decision()


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
    action = _action(secret="")
    with patch.dict(
        "os.environ",
        {"WHATSAPP_SKIP_STARTUP_WEBHOOK_REGISTRATION": "true"},
        clear=False,
    ):
        skip, reason = _skip_decision(action)
    assert skip is True
    assert reason == "WHATSAPP_SKIP_STARTUP_WEBHOOK_REGISTRATION=true"


def test_startup_skip_serverless_default_when_secret_present():
    import os

    action = _action(secret="s3cret")
    with (
        patch.object(WhatsAppAction, "_is_serverless_runtime", return_value=True),
        patch.object(
            WhatsAppAction,
            "_env_jvconnect_webhook_secret",
            lambda self: "s3cret",
        ),
    ):
        os.environ.pop("WHATSAPP_SKIP_STARTUP_WEBHOOK_REGISTRATION", None)
        skip, reason = _skip_decision(action)
    assert skip is True
    assert reason == "serverless_default"


def test_startup_skip_serverless_missing_secret_does_not_skip():
    """First Lambda deploy: credentials set, secret not persisted yet."""
    import os

    action = _action(secret="")
    with (
        patch.object(WhatsAppAction, "_is_serverless_runtime", return_value=True),
        patch.object(
            WhatsAppAction,
            "_env_jvconnect_webhook_secret",
            lambda self: "",
        ),
    ):
        os.environ.pop("WHATSAPP_SKIP_STARTUP_WEBHOOK_REGISTRATION", None)
        skip, reason = _skip_decision(action)
    assert skip is False
    assert reason == ""


def test_startup_skip_env_false_forces_register_on_serverless():
    action = _action(secret="s3cret")
    with (
        patch.dict(
            "os.environ",
            {"WHATSAPP_SKIP_STARTUP_WEBHOOK_REGISTRATION": "false"},
            clear=False,
        ),
        patch.object(WhatsAppAction, "_is_serverless_runtime", return_value=True),
    ):
        skip, reason = _skip_decision(action)
    assert skip is False
    assert reason == ""


def test_startup_no_skip_when_unset_non_serverless():
    import os

    action = _action(secret="")
    with patch.object(WhatsAppAction, "_is_serverless_runtime", return_value=False):
        os.environ.pop("WHATSAPP_SKIP_STARTUP_WEBHOOK_REGISTRATION", None)
        skip, reason = _skip_decision(action)
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


async def test_startup_healthy_without_secret_still_registers():
    """Cold start must POST register so jvconnect can return webhook_secret."""
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
    register.assert_awaited_once()


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
    action = _action(secret="s3cret")
    action.enabled = True
    action.tts_action = None
    action.request_timeout = 60
    schedule = AsyncMock()
    with (
        patch.object(WhatsAppAction, "is_configured", return_value=True),
        patch.object(WhatsAppAction, "is_meta_provider", return_value=True),
        patch.object(
            WhatsAppAction, "_warn_lambda_local_storage", AsyncMock(return_value=None)
        ),
        patch.object(WhatsAppAction, "get_agent", AsyncMock(return_value=object())),
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


async def test_on_startup_serverless_without_secret_schedules():
    """First Lambda deploy with empty secret must schedule register."""
    action = _action(secret="")
    action.enabled = True
    action.tts_action = None
    action.request_timeout = 60
    schedule = AsyncMock()
    with (
        patch.object(WhatsAppAction, "is_configured", return_value=True),
        patch.object(WhatsAppAction, "is_meta_provider", return_value=True),
        patch.object(
            WhatsAppAction, "_warn_lambda_local_storage", AsyncMock(return_value=None)
        ),
        patch.object(WhatsAppAction, "get_agent", AsyncMock(return_value=object())),
        patch("jvagent.action.whatsapp.whatsapp_action.WhatsAppFilter") as filter_cls,
        patch("jvagent.action.whatsapp.whatsapp_action.WhatsAppAdapter") as adapter_cls,
        patch.object(WhatsAppAction, "_is_serverless_runtime", return_value=True),
        patch.object(
            WhatsAppAction,
            "_env_jvconnect_webhook_secret",
            lambda self: "",
        ),
        patch.object(
            WhatsAppAction, "_schedule_deferred_meta_webhook_register", schedule
        ),
        patch.object(
            WhatsAppAction, "get_webhook_url", AsyncMock(return_value="https://h")
        ),
    ):
        import os

        os.environ.pop("WHATSAPP_SKIP_STARTUP_WEBHOOK_REGISTRATION", None)
        filter_inst = filter_cls.return_value
        filter_inst.initialize = AsyncMock(return_value=True)
        adapter_inst = adapter_cls.return_value
        adapter_inst.initialize = AsyncMock(return_value=True)
        await action.on_startup()
    schedule.assert_awaited_once()


_SERVER = "jvspatial.api.context.get_current_server"


async def test_lifecycle_hook_awaits_register_on_serverless():
    """Lambda must await register in the lifespan hook, not create_task."""
    action = _action(secret="", action_id="n.WhatsAppAction.await-lambda")
    hooks: list = []

    class _LM:
        def add_startup_hook(self, hook):
            hooks.append(hook)

    class _Server:
        lifecycle_manager = _LM()

    run = AsyncMock()
    with (
        patch(_SERVER, return_value=_Server()),
        patch.object(WhatsAppAction, "_is_serverless_runtime", return_value=True),
        patch.object(WhatsAppAction, "_run_startup_meta_webhook_register", run),
        patch.object(whatsapp_module.asyncio, "create_task") as create_task,
    ):
        await action._schedule_deferred_meta_webhook_register()
        assert len(hooks) == 1
        await hooks[0]()
    run.assert_awaited_once()
    create_task.assert_not_called()


async def test_lifecycle_hook_create_task_on_long_running():
    action = _action(secret="", action_id="n.WhatsAppAction.create-task")
    hooks: list = []

    class _LM:
        def add_startup_hook(self, hook):
            hooks.append(hook)

    class _Server:
        lifecycle_manager = _LM()

    task = MagicMock()
    with (
        patch(_SERVER, return_value=_Server()),
        patch.object(WhatsAppAction, "_is_serverless_runtime", return_value=False),
        patch.object(
            whatsapp_module.asyncio, "create_task", return_value=task
        ) as create_task,
    ):
        await action._schedule_deferred_meta_webhook_register()
        assert len(hooks) == 1
        await hooks[0]()
        create_task.call_args.args[0].close()
    create_task.assert_called_once()
    assert task in whatsapp_module._BACKGROUND_TASKS


async def test_fallback_awaits_on_serverless_without_lifecycle_manager():
    action = _action(secret="", action_id="n.WhatsAppAction.fallback-lambda")
    run = AsyncMock()
    with (
        patch(_SERVER, return_value=None),
        patch.object(WhatsAppAction, "_is_serverless_runtime", return_value=True),
        patch.object(WhatsAppAction, "_run_startup_meta_webhook_register", run),
        patch.object(whatsapp_module.asyncio, "create_task") as create_task,
    ):
        await action._schedule_deferred_meta_webhook_register()
    run.assert_awaited_once()
    create_task.assert_not_called()


async def test_fallback_create_task_without_lifecycle_manager_long_running():
    action = _action(secret="", action_id="n.WhatsAppAction.fallback-vm")
    task = MagicMock()
    with (
        patch(_SERVER, return_value=None),
        patch.object(WhatsAppAction, "_is_serverless_runtime", return_value=False),
        patch.object(
            whatsapp_module.asyncio, "create_task", return_value=task
        ) as create_task,
    ):
        await action._schedule_deferred_meta_webhook_register()
        create_task.call_args.args[0].close()
    create_task.assert_called_once()
    assert task in whatsapp_module._BACKGROUND_TASKS
    task.add_done_callback.assert_called_once()


async def test_second_schedule_for_same_action_does_not_re_register():
    action = _action(secret="", action_id="n.WhatsAppAction.dedupe")
    server = MagicMock()
    with patch(_SERVER, return_value=server):
        await action._schedule_deferred_meta_webhook_register()
        await action._schedule_deferred_meta_webhook_register()
    assert server.lifecycle_manager.add_startup_hook.call_count == 1
