"""Real HTTPX request construction preserves pinned transport and safe logs."""

import asyncio
import importlib
import json
import logging
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from jvagent.core import callback


@pytest.fixture(autouse=True)
def capture_httpx_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Establish capture independently of earlier server logging configuration."""
    http_logger = logging.getLogger("httpx")
    caplog.set_level(logging.INFO, logger="httpx")
    monkeypatch.setattr(http_logger, "disabled", False)
    monkeypatch.setattr(http_logger, "propagate", True)


def test_callback_logging_accepts_an_existing_callable_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http_logger = logging.getLogger("httpx")

    def existing(record: logging.LogRecord) -> bool:
        return True

    monkeypatch.setattr(http_logger, "filters", [existing])
    importlib.reload(callback)
    assert existing in http_logger.filters
    assert any(
        getattr(f, "name", None) == "jv-callback-http" for f in http_logger.filters
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize(
    "address", ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"]
)
async def test_real_httpx_pins_ip_preserves_host_and_sets_request_sni(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    scheme: str,
    address: str,
) -> None:
    lookups = []
    requests = []
    original = httpx.AsyncClient
    caplog.set_level("INFO")

    def resolve(host: str, *args: Any) -> list[Any]:
        lookups.append(host)
        return [(0, 0, 0, "", (address, 0))]

    async def receive(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.url.host == address
        assert request.url.port == 8443
        assert request.headers["host"] == "receiver.example:8443"
        assert request.url.path == "/events"
        assert request.url.query == b"token=fixture-query"
        assert json.loads(request.content) == {"event": "probe"}
        assert request.extensions["timeout"]["connect"] == 3.0
        assert request.extensions.get("sni_hostname") == (
            "receiver.example" if scheme == "https" else None
        )
        return httpx.Response(200, json={"accepted": True})

    monkeypatch.setattr(callback.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(
        callback.httpx,
        "AsyncClient",
        lambda **settings: original(transport=httpx.MockTransport(receive), **settings),
    )
    response = await callback._post_webhook_pinned_async(
        scheme + "://receiver.example:8443/events?token=fixture-query",
        {"event": "probe"},
        timeout=3.0,
    )
    assert response.status_code == 200 and response.json() == {"accepted": True}
    assert lookups == ["receiver.example"] and len(requests) == 1
    assert "fixture-query" not in caplog.text
    assert "Callback HTTP transport event" in caplog.text


@pytest.mark.asyncio
async def test_redirect_does_not_escape_validated_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = []
    original = httpx.AsyncClient

    async def receive(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    monkeypatch.setattr(
        callback.socket,
        "getaddrinfo",
        lambda *args: [(0, 0, 0, "", ("93.184.216.34", 0))],
    )
    monkeypatch.setattr(
        callback.httpx,
        "AsyncClient",
        lambda **settings: original(
            transport=httpx.MockTransport(receive), follow_redirects=True, **settings
        ),
    )
    response = await callback._post_webhook_pinned_async(
        "https://receiver.example/events", {"event": "probe"}
    )
    assert response.status_code == 302 and len(sent) == 1


@pytest.mark.asyncio
async def test_unsafe_dns_refuses_before_client_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        callback.socket,
        "getaddrinfo",
        lambda *args: [(0, 0, 0, "", ("127.0.0.1", 0))],
    )

    def client(**settings: Any) -> Any:
        pytest.fail("Unsafe callback created an HTTP client")

    monkeypatch.setattr(callback.httpx, "AsyncClient", client)
    with pytest.raises(ValueError, match="blocked address"):
        await callback._post_webhook_pinned_async("https://receiver.example/", {})


@pytest.mark.parametrize(
    "url,expected",
    [
        (
            "https://fixture-user:fixture-pass@receiver.example:8443/private?token=fixture-query",
            "https://receiver.example:8443",
        ),
        (
            "https://[2606:4700:4700::1111]:8443/private",
            "https://[2606:4700:4700::1111]:8443",
        ),
        ("https://receiver.example:invalid/private", "[invalid-webhook-url]"),
    ],
)
def test_target_logging_excludes_userinfo_path_and_query(
    url: str, expected: str
) -> None:
    assert callback._safe_webhook_target(url) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event", ["created", "updated", "completed", "failed", "cancelled"]
)
async def test_background_errors_never_log_credentials_or_response_body(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, event: str
) -> None:
    private_url = (
        "https://fixture-user:fixture-pass@receiver.example/private?token=fixture-query"
    )
    monkeypatch.setenv(f"JVAGENT_TASK_{event.upper()}_WEBHOOK_URL", private_url)
    agent = SimpleNamespace(id="agent", get_action_by_type=AsyncMock(return_value=None))
    conversation = SimpleNamespace(
        session_id="session", get_agent=AsyncMock(return_value=agent)
    )

    async def fail(*args: Any, **kwargs: Any) -> Any:
        raise ValueError(private_url + " private-response-body")

    async def run(coro: Any, **kwargs: Any) -> None:
        await coro

    monkeypatch.setattr(callback, "_post_webhook_pinned_async", fail)
    monkeypatch.setattr(callback, "create_task", run)
    caplog.set_level("INFO")
    trigger = getattr(callback, f"trigger_task_{event}_callback")
    await trigger(conversation, {"task_id": "task", "status": event})
    assert "ValueError" in caplog.text
    for secret in [
        "fixture-user",
        "fixture-pass",
        "private-response-body",
        "fixture-query",
        "/private",
    ]:
        assert secret not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["none", "timeout", "cancel"])
async def test_callback_log_scope_resets_on_success_error_and_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    original = httpx.AsyncClient

    async def receive(request: httpx.Request) -> httpx.Response:
        if request.url.host == "93.184.216.34":
            if failure == "timeout":
                raise httpx.ReadTimeout("private transport detail")
            if failure == "cancel":
                raise asyncio.CancelledError()
        return httpx.Response(200)

    monkeypatch.setattr(
        callback.socket,
        "getaddrinfo",
        lambda *args: [(0, 0, 0, "", ("93.184.216.34", 0))],
    )
    monkeypatch.setattr(
        callback.httpx,
        "AsyncClient",
        lambda **settings: original(transport=httpx.MockTransport(receive), **settings),
    )
    caplog.set_level("INFO")
    if failure == "none":
        await callback._post_webhook_pinned_async(
            "https://receiver.example/private", {}
        )
    else:
        with pytest.raises(
            httpx.ReadTimeout if failure == "timeout" else asyncio.CancelledError
        ):
            await callback._post_webhook_pinned_async(
                "https://receiver.example/private", {}
            )
    async with original(transport=httpx.MockTransport(receive)) as client:
        await client.get("https://ordinary.example/visible-after")
    assert "https://ordinary.example/visible-after" in caplog.text
    assert "receiver.example/private" not in caplog.text


@pytest.mark.asyncio
async def test_callback_log_scope_does_not_hide_other_tasks(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    original = httpx.AsyncClient

    async def receive(request: httpx.Request) -> httpx.Response:
        if request.url.host == "93.184.216.34":
            entered.set()
            await release.wait()
        return httpx.Response(200)

    async def ordinary() -> None:
        await entered.wait()
        try:
            async with original(transport=httpx.MockTransport(receive)) as client:
                await client.get("https://ordinary.example/visible-sibling")
        finally:
            release.set()

    monkeypatch.setattr(
        callback.socket,
        "getaddrinfo",
        lambda *args: [(0, 0, 0, "", ("93.184.216.34", 0))],
    )
    monkeypatch.setattr(
        callback.httpx,
        "AsyncClient",
        lambda **settings: original(transport=httpx.MockTransport(receive), **settings),
    )
    caplog.set_level("INFO")
    await asyncio.wait_for(
        asyncio.gather(
            callback._post_webhook_pinned_async(
                "https://receiver.example/private?token=fixture-query", {}
            ),
            ordinary(),
        ),
        timeout=2,
    )
    assert "https://ordinary.example/visible-sibling" in caplog.text
    assert "fixture-query" not in caplog.text


@pytest.mark.asyncio
async def test_callback_dns_does_not_block_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    observed = []
    original = httpx.AsyncClient

    def resolve(*args: Any) -> list[Any]:
        observed.append(release.wait(timeout=0.5))
        return [(0, 0, 0, "", ("93.184.216.34", 0))]

    async def receive(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    monkeypatch.setattr(callback.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(
        callback.httpx,
        "AsyncClient",
        lambda **settings: original(transport=httpx.MockTransport(receive), **settings),
    )
    timer = asyncio.get_running_loop().call_later(0.02, release.set)
    try:
        response = await callback._post_webhook_pinned_async(
            "https://receiver.example/", {}
        )
        assert response.status_code == 200
        assert observed == [
            True
        ], "DNS held the event loop past its scheduled heartbeat"
    finally:
        timer.cancel()
        release.set()
