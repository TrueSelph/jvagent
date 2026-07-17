"""Tests for ElevenLabs TTS voice-list TTL cache."""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from jvagent.action.tts_action.elevenlabs import elevenlabs as el_mod
from jvagent.action.tts_action.elevenlabs.elevenlabs import ElevenLabsTTSAction


@pytest.fixture(autouse=True)
def _clear_cache():
    el_mod._clear_voices_cache_for_tests()
    yield
    el_mod._clear_voices_cache_for_tests()


@pytest.fixture
def stub_elevenlabs(monkeypatch):
    """Install a fake elevenlabs SDK so tests run without the optional dep."""
    mock_client_cls = MagicMock(name="ElevenLabs")
    client_mod = ModuleType("elevenlabs.client")
    client_mod.ElevenLabs = mock_client_cls
    pkg = ModuleType("elevenlabs")
    pkg.client = client_mod
    monkeypatch.setitem(sys.modules, "elevenlabs", pkg)
    monkeypatch.setitem(sys.modules, "elevenlabs.client", client_mod)
    return mock_client_cls


@pytest.mark.asyncio
async def test_fetch_voices_uses_ttl_cache(monkeypatch, stub_elevenlabs):
    monkeypatch.setattr(
        ElevenLabsTTSAction, "_env_api_key", staticmethod(lambda: "test-key")
    )
    voice = SimpleNamespace(name="Sarah", voice_id="v1", category="premade")
    calls: list[int] = []

    async def _fake_to_thread(fn, *args, **kwargs):
        calls.append(1)
        return SimpleNamespace(voices=[voice])

    action = ElevenLabsTTSAction()
    with patch.object(el_mod.asyncio, "to_thread", side_effect=_fake_to_thread):
        first = await action.get_voices()
        second = await action.get_voices()

    assert first == [{"name": "Sarah", "voice_id": "v1", "category": "premade"}]
    assert second == first
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_get_voice_by_name_hits_cache(monkeypatch, stub_elevenlabs):
    monkeypatch.setattr(
        ElevenLabsTTSAction, "_env_api_key", staticmethod(lambda: "test-key")
    )
    voice = SimpleNamespace(name="Sarah", voice_id="v1", category="premade")
    calls: list[int] = []

    async def _fake_to_thread(fn, *args, **kwargs):
        calls.append(1)
        return SimpleNamespace(voices=[voice])

    action = ElevenLabsTTSAction()
    with patch.object(el_mod.asyncio, "to_thread", side_effect=_fake_to_thread):
        a = await action.get_voice_by_name("Sarah")
        b = await action.get_voice_by_name("sarah")
    assert a == "v1"
    assert b == "v1"
    assert len(calls) == 1
