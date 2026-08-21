"""Always-register Meta webhook on startup/reload + Lambda await scheduling.

jvagent always POSTs jvconnect ``webhook/register``. Same ``callback_url`` is
a Meta no-op on jvconnect (returns existing secret). The lifecycle hook
**awaits** register on Lambda instead of ``asyncio.create_task``.
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


async def test_reload_always_registers():
    action = _action(secret="s3cret")
    register = AsyncMock(return_value={"status": "ok"})
    with (
        patch.object(WhatsAppAction, "is_configured", return_value=True),
        patch.object(WhatsAppAction, "is_meta_provider", return_value=True),
        patch.object(WhatsAppAction, "register_meta_webhook_subscription", register),
    ):
        await action.on_reload()
    register.assert_awaited_once()
    assert action._session_registered is True


async def test_reload_registers_without_secret():
    action = _action(secret="")
    register = AsyncMock(return_value={"status": "ok"})
    with (
        patch.object(WhatsAppAction, "is_configured", return_value=True),
        patch.object(WhatsAppAction, "is_meta_provider", return_value=True),
        patch.object(WhatsAppAction, "register_meta_webhook_subscription", register),
    ):
        await action.on_reload()
    register.assert_awaited_once()


def test_startup_webhook_timeout_serverless_default():
    with patch.object(WhatsAppAction, "_is_serverless_runtime", return_value=True):
        import os

        os.environ.pop("WHATSAPP_STARTUP_WEBHOOK_REGISTER_TIMEOUT_SECONDS", None)
        assert WhatsAppAction._startup_webhook_register_timeout_seconds() == 15.0


def test_startup_webhook_timeout_env_override():
    with patch.dict(
        "os.environ",
        {"WHATSAPP_STARTUP_WEBHOOK_REGISTER_TIMEOUT_SECONDS": "8"},
        clear=False,
    ):
        assert WhatsAppAction._startup_webhook_register_timeout_seconds() == 8.0


async def test_startup_always_registers_even_when_healthy_with_secret():
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
    register.assert_awaited_once()
    assert action._session_registered is True


async def test_startup_registers_without_secret():
    action = _action(secret="")
    register = AsyncMock(return_value={"status": "ok"})
    with patch.object(WhatsAppAction, "register_meta_webhook_subscription", register):
        await action._run_startup_meta_webhook_register()
    register.assert_awaited_once()


async def test_on_startup_always_schedules_register():
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
            WhatsAppAction, "_schedule_deferred_meta_webhook_register", schedule
        ),
        patch.object(
            WhatsAppAction, "get_webhook_url", AsyncMock(return_value="https://h")
        ),
    ):
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
