"""Deferred Messenger webhook registration on startup.

``on_startup`` runs inside ``asyncio.run(pre_startup_bootstrap)``, before
uvicorn is listening. Registering there means Meta's verification GET can hit a
socket that is not accepting yet, so registration is deferred onto the server
lifecycle instead — with a ``create_task`` fallback for hosts that expose no
lifecycle manager.

Both paths are easy to break silently: a double-registration re-POSTs to Meta on
every reload, and a broken fallback means the webhook is never registered at all
on a host without a lifecycle manager. Neither surfaces in a normal test run.
"""

from unittest.mock import MagicMock, patch

import pytest

from jvagent.action.facebook_action.facebook_action import (
    FacebookAction,
    _messenger_webhook_startup_hooks,
)


@pytest.fixture(autouse=True)
def _clear_scheduled_hooks():
    """The dedupe registry is module-global; keep tests independent."""
    _messenger_webhook_startup_hooks.clear()
    yield
    _messenger_webhook_startup_hooks.clear()


def _action(action_id: str = "n.FacebookAction.test") -> FacebookAction:
    action = object.__new__(FacebookAction)
    object.__setattr__(action, "id", action_id)
    object.__setattr__(action, "base_url", "https://example.test")
    object.__setattr__(action, "webhook_url", "https://example.test/hook?api_key=x")
    return action


class TestStartupTimeout:
    def test_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(
            "FACEBOOK_STARTUP_WEBHOOK_REGISTER_TIMEOUT_SECONDS", raising=False
        )
        assert FacebookAction._startup_webhook_register_timeout_seconds() == 60.0

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACEBOOK_STARTUP_WEBHOOK_REGISTER_TIMEOUT_SECONDS", "12.5")
        assert FacebookAction._startup_webhook_register_timeout_seconds() == 12.5

    def test_garbage_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A typo must not take startup down; fall through to the default."""
        monkeypatch.setenv(
            "FACEBOOK_STARTUP_WEBHOOK_REGISTER_TIMEOUT_SECONDS", "not-a-number"
        )
        assert FacebookAction._startup_webhook_register_timeout_seconds() == 60.0

    def test_floor_is_one_second(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A zero/negative timeout would cancel registration immediately."""
        monkeypatch.setenv("FACEBOOK_STARTUP_WEBHOOK_REGISTER_TIMEOUT_SECONDS", "0")
        assert FacebookAction._startup_webhook_register_timeout_seconds() == 1.0


class TestDeferredScheduling:
    _SERVER = "jvspatial.api.context.get_current_server"

    def test_registers_a_startup_hook(self) -> None:
        server = MagicMock()
        with patch(self._SERVER, return_value=server):
            _action()._schedule_deferred_messenger_webhook_register()
        server.lifecycle_manager.add_startup_hook.assert_called_once()

    def test_second_call_for_same_action_does_not_re_register(self) -> None:
        """A reload must not queue a second Meta subscribe for the same action."""
        server = MagicMock()
        action = _action()
        with patch(self._SERVER, return_value=server):
            action._schedule_deferred_messenger_webhook_register()
            action._schedule_deferred_messenger_webhook_register()
        assert server.lifecycle_manager.add_startup_hook.call_count == 1

    def test_distinct_actions_each_register(self) -> None:
        server = MagicMock()
        with patch(self._SERVER, return_value=server):
            _action(
                "n.FacebookAction.a"
            )._schedule_deferred_messenger_webhook_register()
            _action(
                "n.FacebookAction.b"
            )._schedule_deferred_messenger_webhook_register()
        assert server.lifecycle_manager.add_startup_hook.call_count == 2

    def test_falls_back_to_create_task_without_lifecycle_manager(self) -> None:
        """No lifecycle manager must still register, not silently do nothing."""
        with (
            patch(self._SERVER, return_value=None),
            patch(
                "jvagent.action.facebook_action.facebook_action.asyncio.create_task"
            ) as create_task,
        ):
            _action()._schedule_deferred_messenger_webhook_register()
        create_task.assert_called_once()
        # Close the coroutine the mock never scheduled, so it does not surface
        # as an un-awaited-coroutine warning at collection.
        create_task.call_args.args[0].close()

    def test_fallback_task_is_strongly_referenced(self) -> None:
        """asyncio keeps only a weak reference; a bare task can be GC'd mid-flight."""
        from jvagent.action.facebook_action import facebook_action as module

        task = MagicMock()
        with (
            patch(self._SERVER, return_value=None),
            patch.object(module.asyncio, "create_task", return_value=task),
        ):
            _action()._schedule_deferred_messenger_webhook_register()
            module.asyncio.create_task.call_args.args[0].close()
        assert task in module._BACKGROUND_TASKS
        task.add_done_callback.assert_called_once()
