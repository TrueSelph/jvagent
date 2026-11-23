"""A rejected declaration must not trigger destructive graph reconciliation."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from jvagent.core.agent_loader import AgentLoader
from jvagent.core.errors import ConfigError


@pytest.mark.asyncio
async def test_resolution_failure_precedes_action_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loader = AgentLoader(str(tmp_path))
    descriptor = SimpleNamespace(
        namespace="test", name="agent", actions=[{"action": "test/missing"}]
    )
    monkeypatch.setattr(
        loader.action_loader,
        "expected_import_paths_from_descriptor_actions",
        Mock(return_value=set()),
    )
    monkeypatch.setattr(loader, "_get_all_action_records", AsyncMock(return_value=[]))
    reconcile = AsyncMock()
    monkeypatch.setattr(loader, "_reconcile_actions", reconcile)
    monkeypatch.setattr(
        loader.action_loader,
        "load_actions_for_agent",
        Mock(side_effect=ConfigError("unavailable declaration")),
    )
    manager = SimpleNamespace(register_actions=AsyncMock())
    with pytest.raises(ConfigError):
        await loader._install_actions(
            SimpleNamespace(id="agent"), descriptor, manager, update_mode="source"
        )
    reconcile.assert_not_awaited()
    manager.register_actions.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_registration_cannot_report_installation_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loader = AgentLoader(str(tmp_path))
    descriptor = SimpleNamespace(
        namespace="test", name="agent", actions=[{"action": "test/policy"}]
    )
    action = SimpleNamespace(namespace="test", label="policy")
    monkeypatch.setattr(
        loader.action_loader, "load_actions_for_agent", Mock(return_value=[action])
    )
    manager = SimpleNamespace(
        register_actions=AsyncMock(return_value={"policy": False})
    )
    with pytest.raises(ConfigError, match="registration failed"):
        await loader._install_actions(SimpleNamespace(id="agent"), descriptor, manager)
