"""Tests for PersonaAction parameter admin endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jvspatial.api.exceptions import ValidationError


@pytest.mark.asyncio
async def test_import_parameters_reports_skipped_rows(monkeypatch):
    class Node:
        def __init__(self):
            self.parameters = []

        async def save(self):
            pass

    node = Node()

    async def get_pa(_action_id: str):
        return node

    monkeypatch.setattr(
        "jvagent.action.persona.endpoints._get_persona_action",
        get_pa,
    )
    from jvagent.action.persona import endpoints as ep

    out = await ep.import_parameters_endpoint(
        "action-1",
        [
            {"condition": "ok", "response": "yes"},
            {"condition": " ", "response": "c"},
            {"condition": "bad", "response": 123},
            "not-a-dict",
        ],
    )
    assert out["imported"] == 1
    assert out["skipped"] == 3
    assert len(out["skipped_details"]) == 3
    assert node.parameters[0]["condition"] == "ok"
    assert node.parameters[0]["response"] == "yes"


@pytest.mark.asyncio
async def test_update_parameter_rejects_empty_condition(monkeypatch):
    node = MagicMock()
    node.parameters = [{"condition": "x", "response": "y", "enabled": True}]
    node.save = AsyncMock()
    monkeypatch.setattr(
        "jvagent.action.persona.endpoints._get_persona_action",
        AsyncMock(return_value=node),
    )
    from jvagent.action.persona import endpoints as ep

    with pytest.raises(ValidationError, match="condition cannot be empty"):
        await ep.update_parameter_endpoint("aid", "param_0", condition="   ")


@pytest.mark.asyncio
async def test_update_parameter_rejects_empty_response(monkeypatch):
    node = MagicMock()
    node.parameters = [{"condition": "x", "response": "y", "enabled": True}]
    node.save = AsyncMock()
    monkeypatch.setattr(
        "jvagent.action.persona.endpoints._get_persona_action",
        AsyncMock(return_value=node),
    )
    from jvagent.action.persona import endpoints as ep

    with pytest.raises(ValidationError, match="response cannot be empty"):
        await ep.update_parameter_endpoint("aid", "param_0", response="\t")


@pytest.mark.asyncio
async def test_persona_respond_validation_error_when_nothing_to_apply():
    from jvagent.action.persona.persona_action import PersonaAction

    persona = PersonaAction()
    persona.parameters = []

    interaction = MagicMock()
    interaction.response = None
    interaction.add_parameters = MagicMock(return_value=False)
    interaction.get_unexecuted_directives = MagicMock(return_value=[])
    interaction.get_unexecuted_parameters = MagicMock(return_value=[])

    with patch.object(PersonaAction, "get_model_action", new_callable=AsyncMock) as gm:
        gm.return_value = MagicMock()
        with pytest.raises(ValidationError, match="No persona directives"):
            await persona.respond(interaction)
