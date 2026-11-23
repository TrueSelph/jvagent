"""Host policy credentials are private across all response transport modes."""

from typing import Any

import pytest

from jvagent.action.response.response_bus import ResponseBus


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("native", [False, True])
async def test_host_envelope_never_reaches_public_transport(
    stream: bool,
    native: bool,
) -> None:
    bus = ResponseBus()
    bus.native_execution = native
    metadata = {
        "host_system_context": {
            "body": "private host policy",
            "signature": "fixture-signature",
        },
        "run_id": "host-request",
        "native_run_id": "corr-server",
        "routing": {"recipient": "fixture"},
    }
    observed: list[dict[str, Any]] = []

    def receive(message: Any) -> None:
        observed.append(dict(message.metadata or {}))

    session = f"host-metadata-{native}-{stream}"
    await bus.subscribe(session, receive, receive_chunks=True)
    try:
        message = await bus.publish(
            session_id=session,
            content="Public reply",
            channel="default",
            stream=stream,
            interaction_id=session,
            metadata=metadata,
        )
        queued = await bus.get_messages(session)
        assert observed and queued
        for item in [message, *queued]:
            public = item.metadata or {}
            assert "host_system_context" not in public
            assert "run_id" not in public
            assert public["native_run_id"] == "corr-server"
            assert public["routing"] == {"recipient": "fixture"}
        assert all(
            "host_system_context" not in item and "run_id" not in item
            for item in observed
        )
        assert metadata["host_system_context"]["body"] == "private host policy"
        assert metadata["run_id"] == "host-request"  # caller input was not mutated
    finally:
        await bus.unsubscribe(session, receive)


@pytest.mark.asyncio
async def test_unrelated_application_run_id_is_preserved() -> None:
    bus = ResponseBus()
    bus.native_execution = True
    result = await bus.publish(
        session_id="application-metadata",
        content="Public reply",
        channel="default",
        metadata={"run_id": "application-workflow"},
    )
    assert result.metadata == {"run_id": "application-workflow"}
