"""Regression: the Deepgram STT MIME allowlist must ignore a codecs parameter.

Browsers label ``MediaRecorder`` output as e.g. ``audio/webm;codecs=opus``. The
allowlist stores the bare ``audio/webm`` type, so ``invoke_base64`` must strip the
``;codecs=...`` parameter before the membership check — otherwise every mic
recording from the embeddable messenger is silently rejected (empty transcript).
Deepgram sniffs the real codec from the bytes; ``audio_type`` only feeds this gate.
"""

import base64
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("deepgram")  # optional provider SDK; skip when absent (CI)

from jvagent.action.stt_action.deepgram.deepgram import DeepgramSTTAction

_AUDIO_B64 = base64.b64encode(b"\x00" * 128).decode()


def _make_action(monkeypatch, transcribe_mock: AsyncMock) -> DeepgramSTTAction:
    """A DeepgramSTTAction with its network I/O stubbed (no DB, no HTTP)."""
    action = DeepgramSTTAction.model_construct(
        model="nova-2", smart_format=True, timeout=30
    )

    class _Media:
        transcribe_file = transcribe_mock

    class _Client:
        listen = type("L", (), {"v1": type("V", (), {"media": _Media()})()})()

    monkeypatch.setattr(DeepgramSTTAction, "_env_api_key", staticmethod(lambda: "k"))
    monkeypatch.setattr(DeepgramSTTAction, "_get_client", lambda self: _Client())
    monkeypatch.setattr(
        DeepgramSTTAction, "_extract_transcript", lambda self, resp: "hello world"
    )
    return action


@pytest.mark.asyncio
async def test_webm_with_codecs_param_is_accepted(monkeypatch):
    transcribe = AsyncMock(return_value=object())
    action = _make_action(monkeypatch, transcribe)

    out = await action.invoke_base64(_AUDIO_B64, "audio/webm;codecs=opus")

    assert out == "hello world"
    transcribe.assert_awaited_once()


@pytest.mark.asyncio
async def test_bare_webm_still_accepted(monkeypatch):
    transcribe = AsyncMock(return_value=object())
    action = _make_action(monkeypatch, transcribe)

    out = await action.invoke_base64(_AUDIO_B64, "audio/webm")

    assert out == "hello world"
    transcribe.assert_awaited_once()


@pytest.mark.asyncio
async def test_disallowed_type_rejected_without_calling_api(monkeypatch):
    transcribe = AsyncMock(return_value=object())
    action = _make_action(monkeypatch, transcribe)

    out = await action.invoke_base64(_AUDIO_B64, "application/x-bogus")

    assert out is None
    transcribe.assert_not_awaited()
