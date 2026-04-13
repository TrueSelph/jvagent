"""Regression: jvspatial create_task Shape B awaits coroutine when serverless."""

import pytest
from jvspatial.runtime.serverless import reset_serverless_mode_cache


@pytest.fixture(autouse=True)
def _reset_serverless_cache():
    reset_serverless_mode_cache()
    yield
    reset_serverless_mode_cache()


@pytest.mark.asyncio
async def test_create_task_coroutine_awaited_when_serverless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jvspatial.async_utils import create_task

    monkeypatch.setattr(
        "jvspatial.async_utils.is_serverless_mode",
        lambda: True,
    )

    steps: list[str] = []

    async def worker() -> None:
        steps.append("ran")

    await create_task(worker(), name="long_memory_bg_smoke")
    assert steps == ["ran"]


@pytest.mark.asyncio
async def test_create_task_coroutine_scheduled_when_not_serverless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jvspatial.async_utils import create_task

    monkeypatch.setattr(
        "jvspatial.async_utils.is_serverless_mode",
        lambda: False,
    )

    steps: list[str] = []

    async def worker() -> None:
        steps.append("ran")

    t = await create_task(worker(), name="long_memory_bg_smoke")
    assert t is not None
    await t
    assert steps == ["ran"]
