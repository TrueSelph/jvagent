"""Clock reads remain an app service and a permissioned ordinary SDK tool."""

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from jvagent.action.agent_utils import AgentUtils


def bind_app(monkeypatch: Any, app: Any) -> None:
    async def get_app(self: Any) -> Any:
        return app

    monkeypatch.setattr(AgentUtils, "get_app", get_app)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "configured,now,expected",
    [
        (None, datetime(2026, 11, 6, 12), None),
        ("", datetime(2026, 11, 6, 12), None),
        (
            "America/New_York",
            datetime(2026, 7, 1, 12, tzinfo=ZoneInfo("UTC")),
            "2026-07-01T08:00:00-04:00",
        ),
        (
            "America/New_York",
            datetime(2026, 12, 1, 12, tzinfo=ZoneInfo("UTC")),
            "2026-12-01T07:00:00-05:00",
        ),
    ],
)
async def test_clock_normalizes_local_time_and_dst(
    configured: str | None, now: datetime, expected: str | None, monkeypatch: Any
) -> None:
    bind_app(
        monkeypatch,
        SimpleNamespace(timezone=configured, now=AsyncMock(return_value=now)),
    )
    result = await AgentUtils().get_current_datetime()
    stamp = result.splitlines()[0].removeprefix("ISO 8601: ")
    assert datetime.fromisoformat(stamp).tzinfo is not None
    if expected:
        assert stamp == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [False, 0, [], {}])
async def test_unset_timezone_does_not_accept_other_falsy_types(
    configured: Any, monkeypatch: Any
) -> None:
    clock = AsyncMock(return_value=datetime(2026, 11, 6, 12))
    bind_app(monkeypatch, SimpleNamespace(timezone=configured, now=clock))
    with pytest.raises(ValueError, match="timezone is invalid"):
        await AgentUtils().get_current_datetime()
    clock.assert_not_awaited()


@pytest.mark.asyncio
async def test_clock_keeps_name_schema_timezone_and_freshness(monkeypatch: Any) -> None:
    action = AgentUtils()
    first = datetime(2026, 11, 6, 11, 30, tzinfo=ZoneInfo("America/Guyana"))
    app = SimpleNamespace(
        timezone="America/Guyana",
        now=AsyncMock(side_effect=[first, first + timedelta(minutes=1)]),
    )
    bind_app(monkeypatch, app)
    clock = next(
        t for t in await action.get_tools() if t.name == "get_current_datetime"
    )
    assert not clock.parameters_schema.get("properties")
    assert clock.effect_class == "read"
    one = (await clock.call()).content
    two = (await clock.call()).content
    assert "2026-11-06T11:30:00-04:00" in one and "America/Guyana" in one
    assert "11:31:00" in two and two != one


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["missing-app", "invalid-zone", "naive-configured", "invalid-time"]
)
async def test_invalid_clock_does_not_invent_a_date(
    monkeypatch: Any, failure: str
) -> None:
    app = SimpleNamespace(
        timezone="America/Guyana",
        now=AsyncMock(return_value=datetime.now(ZoneInfo("America/Guyana"))),
    )
    if failure == "missing-app":
        app = None
    elif failure == "invalid-zone":
        app.timezone = "private-invalid-zone"
    elif failure == "naive-configured":
        app.now.return_value = datetime(2026, 11, 6)
    else:
        app.now.return_value = "not a timestamp"
    bind_app(monkeypatch, app)
    with pytest.raises((ValueError, RuntimeError)) as caught:
        await AgentUtils().get_current_datetime()
    assert "private-invalid-zone" not in str(caught.value)


@pytest.mark.asyncio
async def test_native_clock_obeys_host_access_and_rejects_extra_arguments(
    tmp_path: Path, monkeypatch: Any
) -> None:
    pytest.importorskip("pydantic_ai_harness")
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    from jvagent.harness.native import build_native_agent

    directory = tmp_path / "clock"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: clock\ndescription: Read the app clock\nallowed-tools: [get_current_datetime]\nrequires-actions: [AgentUtils]\nrequires-tools: [get_current_datetime]\n---\nRead time from the tool.\n"
    )
    clock_app = SimpleNamespace(
        timezone="UTC",
        now=AsyncMock(return_value=datetime(2026, 11, 6, tzinfo=ZoneInfo("UTC"))),
    )
    bind_app(monkeypatch, clock_app)
    for allowed, arguments in [(False, {}), (True, {"timezone": "private-model-zone"})]:
        calls = []

        async def access(name: str) -> bool:
            calls.append(name)
            return allowed

        step = 0

        def respond(messages: Any, info: Any) -> ModelResponse:
            nonlocal step
            step += 1
            return (
                ModelResponse(parts=[ToolCallPart("get_current_datetime", arguments)])
                if step == 1
                else ModelResponse(parts=[TextPart("unavailable")])
            )

        agent = build_native_agent(
            FunctionModel(respond),
            skill_directory=directory,
            tools=await AgentUtils().get_tools(),
            available_actions=["AgentUtils"],
            access_check=access,
            instructions="Use only actual clock readings.",
        )
        assert (await agent.run("Read clock")).output == "unavailable"
        assert calls == (["get_current_datetime"] if not allowed else [])
    clock_app.now.assert_not_awaited()
