"""Tests for the log query endpoint's filter/time parsing and service wiring."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jvspatial.api.exceptions import ValidationError

from jvagent.logging.endpoints import get_logs_by_agent

pytestmark = pytest.mark.asyncio


def _service(result=None):
    svc = MagicMock()
    svc.get_error_logs = AsyncMock(
        return_value=result or {"errors": [], "pagination": {"page": 1}}
    )
    return svc


async def test_returns_service_result_with_defaults():
    svc = _service({"errors": [{"m": "boom"}], "pagination": {"page": 1}})
    with patch("jvagent.logging.endpoints.get_logging_service", return_value=svc):
        out = await get_logs_by_agent("agent_1")
    assert out["errors"] == [{"m": "boom"}]
    kwargs = svc.get_error_logs.await_args.kwargs
    assert kwargs["agent_id"] == "agent_1"
    assert kwargs["start_time"] is None and kwargs["end_time"] is None
    assert kwargs["page"] == 1 and kwargs["page_size"] == 50
    assert kwargs["filter_query"] is None


async def test_uses_separate_logs_database():
    """Contract: queries must go to the dedicated 'logs' DB, not the main one."""
    svc = _service()
    with patch(
        "jvagent.logging.endpoints.get_logging_service", return_value=svc
    ) as get_svc:
        await get_logs_by_agent("agent_1")
    get_svc.assert_called_once_with(database_name="logs")


async def test_parses_iso_times_with_z_suffix():
    svc = _service()
    with patch("jvagent.logging.endpoints.get_logging_service", return_value=svc):
        await get_logs_by_agent(
            "agent_1",
            start_time="2025-01-01T00:00:00Z",
            end_time="2025-01-31T23:59:59Z",
        )
    kwargs = svc.get_error_logs.await_args.kwargs
    assert kwargs["start_time"].year == 2025
    assert kwargs["end_time"].month == 1


async def test_invalid_start_time_raises_validation_error():
    with pytest.raises(ValidationError):
        await get_logs_by_agent("agent_1", start_time="not-a-date")


async def test_invalid_filter_json_raises_validation_error():
    with pytest.raises(ValidationError):
        await get_logs_by_agent("agent_1", filter="{not json")


async def test_non_object_filter_raises_validation_error():
    with pytest.raises(ValidationError):
        await get_logs_by_agent("agent_1", filter='["a", "b"]')


async def test_valid_filter_passes_through_validator():
    svc = _service()
    with patch("jvagent.logging.endpoints.get_logging_service", return_value=svc):
        await get_logs_by_agent("agent_1", filter='{"context.log_data.user_id": "123"}')
    kwargs = svc.get_error_logs.await_args.kwargs
    assert kwargs["filter_query"] == {"context.log_data.user_id": "123"}
