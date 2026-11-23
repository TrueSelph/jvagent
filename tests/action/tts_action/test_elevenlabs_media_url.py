"""Tests for ElevenLabs TTS media URL generation."""

import logging
from unittest.mock import AsyncMock, patch

import pytest

from jvagent.action.tts_action.elevenlabs.elevenlabs import ElevenLabsTTSAction


@pytest.mark.asyncio
async def test_get_audio_as_url_uses_signed_proxy_url():
    action = ElevenLabsTTSAction()
    with (
        patch.object(
            ElevenLabsTTSAction, "save_file", new=AsyncMock(return_value=True)
        ) as save,
        patch.object(
            ElevenLabsTTSAction,
            "get_short_file_url",
            new=AsyncMock(return_value="/p/abc123"),
        ) as proxy_url,
        patch.object(
            ElevenLabsTTSAction, "get_file_url", new=AsyncMock()
        ) as direct_url,
    ):
        result = await action.get_audio_as(b"audio-bytes", as_url=True)

    assert result == "/p/abc123"
    save.assert_awaited_once()
    storage_path, content = save.await_args.args
    assert storage_path.startswith("tts_audio/tts_audio_")
    assert storage_path.endswith(".mp3")
    assert content == b"audio-bytes"
    proxy_url.assert_awaited_once_with(storage_path, expires_in=3600)
    direct_url.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_audio_as_url_warns_when_proxy_url_disabled(caplog):
    action = ElevenLabsTTSAction()
    direct = "/api/files/actions/agent/elevenlabs_tts/tts_audio/voice.mp3"
    with (
        patch.object(
            ElevenLabsTTSAction, "save_file", new=AsyncMock(return_value=True)
        ),
        patch.object(
            ElevenLabsTTSAction,
            "get_short_file_url",
            new=AsyncMock(return_value=direct),
        ),
    ):
        with caplog.at_level(
            logging.WARNING,
            logger="jvagent.action.tts_action.elevenlabs.elevenlabs",
        ):
            result = await action.get_audio_as(b"audio-bytes", as_url=True)

    assert result == direct
    assert "JVSPATIAL_PROXY_ENABLED=true" in caplog.text


@pytest.mark.asyncio
async def test_get_audio_as_base64_does_not_touch_url_helpers():
    action = ElevenLabsTTSAction()
    with (
        patch.object(ElevenLabsTTSAction, "save_file", new=AsyncMock()) as save,
        patch.object(
            ElevenLabsTTSAction, "get_short_file_url", new=AsyncMock()
        ) as proxy_url,
        patch.object(
            ElevenLabsTTSAction, "get_file_url", new=AsyncMock()
        ) as direct_url,
    ):
        result = await action.get_audio_as(b"audio-bytes", as_base64=True)

    assert result == "YXVkaW8tYnl0ZXM="
    save.assert_not_awaited()
    proxy_url.assert_not_awaited()
    direct_url.assert_not_awaited()
