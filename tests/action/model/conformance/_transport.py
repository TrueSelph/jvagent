"""Replay / record HTTP transports for the provider conformance suite.

Every adapter is driven through the same logical exchanges (``authored.py``)
and must produce the same normalised :class:`ModelResponse`. Provider responses
are served from fixtures:

- **recorded** — ``fixtures/<provider>/<scenario>.json``, captured against the
  real endpoint with ``JVAGENT_CONFORMANCE_RECORD=1`` (and the provider's API
  key in the environment). Takes precedence when present.
- **authored** — hand-written wire bodies in ``authored.py`` following the
  provider's documented format; the fallback until a recording exists.

A fixture is ``{"source", "provider", "scenario", "responses": [{"status",
"headers", "body"}], "expect": {...}}``. ``responses`` is a sequence so a retry
scenario can answer 429 then 200.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

FIXTURES_DIR = Path(__file__).parent / "fixtures"
RECORD_ENV = "JVAGENT_CONFORMANCE_RECORD"


def recording_enabled() -> bool:
    return os.environ.get(RECORD_ENV, "").strip().lower() in ("1", "true", "yes")


def fixture_path(provider: str, scenario: str) -> Path:
    return FIXTURES_DIR / provider / f"{scenario}.json"


def load_recorded(provider: str, scenario: str) -> Optional[Dict[str, Any]]:
    path = fixture_path(provider, scenario)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_recorded(provider: str, scenario: str, fixture: Dict[str, Any]) -> Path:
    path = fixture_path(provider, scenario)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(fixture, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path


class ReplayTransport(httpx.AsyncBaseTransport):
    """Serve the fixture's responses in order; remember every request sent."""

    def __init__(self, responses: List[Dict[str, Any]]):
        self._responses = list(responses)
        self.requests: List[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("conformance: more requests than fixture responses")
        spec = self._responses.pop(0)
        body = spec.get("body", "")
        content = body.encode("utf-8") if isinstance(body, str) else bytes(body or b"")
        return httpx.Response(
            status_code=int(spec.get("status", 200)),
            headers=dict(spec.get("headers") or {}),
            content=content,
            request=request,
        )

    def request_json(self, index: int = -1) -> Dict[str, Any]:
        body = self.requests[index].content
        return json.loads(body.decode("utf-8")) if body else {}


class RecordingTransport(httpx.AsyncBaseTransport):
    """Forward to the real endpoint and capture status/headers/body verbatim."""

    _KEEP_HEADERS = ("content-type", "retry-after")

    def __init__(self) -> None:
        self._inner = httpx.AsyncHTTPTransport()
        self.requests: List[httpx.Request] = []
        self.captured: List[Dict[str, Any]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = await self._inner.handle_async_request(request)
        body = await response.aread()
        await response.aclose()
        self.captured.append(
            {
                "status": response.status_code,
                "headers": {
                    k: v
                    for k, v in response.headers.items()
                    if k.lower() in self._KEEP_HEADERS
                },
                "body": body.decode("utf-8", errors="replace"),
            }
        )
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            content=body,
            request=request,
        )

    async def aclose(self) -> None:
        await self._inner.aclose()

    def request_json(self, index: int = -1) -> Dict[str, Any]:
        body = self.requests[index].content
        return json.loads(body.decode("utf-8")) if body else {}
