"""Tests for ElevenLabs TTS voice-list TTL cache."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from jvagent.action.tts_action.elevenlabs import elevenlabs as el_mod
from jvagent.action.tts_action.elevenlabs.elevenlabs import ElevenLabsTTSAction


@pytest.fixture(autouse=True)
def _clear_cache():
    el_mod._clear_voices_cache_for_tests()
    yield
    el_mod._clear_voices_cache_for_tests()


@pytest.mark.asyncio
async def test_fetch_voices_uses_ttl_cache(monkeypatch):
    monkeypatch.setattr(
        ElevenLabsTTSAction, "_env_api_key", staticmethod(lambda: "test-key")
    )
    voice = SimpleNamespace(name="Sarah", voice_id="v1", category="premade")
    calls: list[int] = []

    async def _fake_to_thread(fn, *args, **kwargs):
        calls.append(1)
        return SimpleNamespace(voices=[voice])

    mock_client = MagicMock()
    action = ElevenLabsTTSAction()
    with patch("elevenlabs.client.ElevenLabs", return_value=mock_client):
        with patch.object(el_mod.asyncio, "to_thread", side_effect=_fake_to_thread):
            first = await action.get_voices()
            second = await action.get_voices()

    assert first == [{"name": "Sarah", "voice_id": "v1", "category": "premade"}]
    assert second == first
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_get_voice_by_name_hits_cache(monkeypatch):
    monkeypatch.setattr(
        ElevenLabsTTSAction, "_env_api_key", staticmethod(lambda: "test-key")
    )
    voice = SimpleNamespace(name="Sarah", voice_id="v1", category="premade")
    calls: list[int] = []

    async def _fake_to_thread(fn, *args, **kwargs):
        calls.append(1)
        return SimpleNamespace(voices=[voice])

    mock_client = MagicMock()
    action = ElevenLabsTTSAction()
    with patch("elevenlabs.client.ElevenLabs", return_value=mock_client):
        with patch.object(el_mod.asyncio, "to_thread", side_effect=_fake_to_thread):
            a = await action.get_voice_by_name("Sarah")
            b = await action.get_voice_by_name("sarah")
    assert a == "v1"
    assert b == "v1"
    assert len(calls) == 1
