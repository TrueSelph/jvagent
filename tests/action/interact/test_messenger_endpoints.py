"""Unit tests for the public widget endpoints (voice STT/TTS + uploads).

Handler logic is isolated by patching the shared gate
(``require_messenger_session``) and the resolved provider actions; a separate case
exercises the real gate's missing-token rejection.
"""

import pytest
from jvspatial.api.exceptions import AuthenticationError, ValidationError

from jvagent.action.interact import public_gate, upload_endpoints, voice_endpoints


class _Headers:
    """Case-insensitive header .get, mimicking starlette Headers."""

    def __init__(self, data=None):
        self._d = {k.lower(): v for k, v in (data or {}).items()}

    def get(self, key, default=None):
        return self._d.get(key.lower(), default)


class _Client:
    host = "203.0.113.7"


class FakeRequest:
    def __init__(self, *, headers=None, json_body=None, form=None):
        self.headers = _Headers(headers)
        self.client = _Client()
        self._json = json_body
        self._form = form or {}

    async def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    async def form(self):
        return self._form


class FakeUpload:
    def __init__(self, content=b"data", content_type="image/png", filename="a.png"):
        self._content = content
        self.content_type = content_type
        self.filename = filename

    async def read(self):
        return self._content


class FakeSTT:
    async def invoke_base64(self, audio_base64, audio_type="audio/mp3"):
        return "hello world"


class FakeTTS:
    output_mime_type = "audio/mpeg"

    async def invoke(self, text, as_base64=False, as_url=False):
        return "QUJD"  # base64 for "ABC"


def _patch_gate(monkeypatch, module, agent=object(), claims=None):
    async def _gate(request, agent_id):
        return agent, (claims or {"session_id": "sess-1"})

    monkeypatch.setattr(module, "require_messenger_session", _gate)


# --------------------------------------------------------------------------- #
# STT
# --------------------------------------------------------------------------- #
async def test_stt_happy_path(monkeypatch):
    _patch_gate(monkeypatch, voice_endpoints)

    async def _resolve(agent, base):
        return FakeSTT()

    monkeypatch.setattr(voice_endpoints, "resolve_agent_action", _resolve)
    req = FakeRequest(json_body={"audio_base64": "QUJD", "audio_type": "audio/webm"})
    out = await voice_endpoints.voice_stt_endpoint(req, "agent-1")
    assert out == {"transcript": "hello world"}


async def test_stt_missing_audio_400(monkeypatch):
    _patch_gate(monkeypatch, voice_endpoints)
    req = FakeRequest(json_body={"audio_type": "audio/webm"})
    with pytest.raises(ValidationError):
        await voice_endpoints.voice_stt_endpoint(req, "agent-1")


async def test_stt_no_provider_400(monkeypatch):
    _patch_gate(monkeypatch, voice_endpoints)

    async def _resolve(agent, base):
        return None

    monkeypatch.setattr(voice_endpoints, "resolve_agent_action", _resolve)
    req = FakeRequest(json_body={"audio_base64": "QUJD"})
    with pytest.raises(ValidationError):
        await voice_endpoints.voice_stt_endpoint(req, "agent-1")


# --------------------------------------------------------------------------- #
# TTS
# --------------------------------------------------------------------------- #
async def test_tts_happy_path(monkeypatch):
    _patch_gate(monkeypatch, voice_endpoints)

    async def _resolve(agent, base):
        return FakeTTS()

    monkeypatch.setattr(voice_endpoints, "resolve_agent_action", _resolve)
    req = FakeRequest(json_body={"text": "Hello there"})
    out = await voice_endpoints.voice_tts_endpoint(req, "agent-1")
    assert out == {"audio_base64": "QUJD", "mime_type": "audio/mpeg"}


async def test_tts_missing_text_400(monkeypatch):
    _patch_gate(monkeypatch, voice_endpoints)
    req = FakeRequest(json_body={"text": "   "})
    with pytest.raises(ValidationError):
        await voice_endpoints.voice_tts_endpoint(req, "agent-1")


# --------------------------------------------------------------------------- #
# Uploads
# --------------------------------------------------------------------------- #
async def test_upload_happy_path(monkeypatch):
    _patch_gate(monkeypatch, upload_endpoints, claims={"session_id": "sess-1"})

    class FakeApp:
        async def save_file(self, path, content, metadata=None):
            return True

        async def get_file_url(self, path):
            return "/files/" + path

    async def _get():
        return FakeApp()

    from jvagent.core.app import App

    monkeypatch.setattr(App, "get", staticmethod(_get))
    req = FakeRequest(form={"file": FakeUpload(content=b"pixels")})
    out = await upload_endpoints.upload_endpoint(req, "agent-1")
    assert out["mime_type"] == "image/png"
    assert out["filename"] == "a.png"
    assert out["size"] == len(b"pixels")
    assert out["url"].startswith("/files/messenger_uploads/agent-1/sess-1/")


async def test_upload_rejects_bad_mime(monkeypatch):
    _patch_gate(monkeypatch, upload_endpoints)
    req = FakeRequest(
        form={"file": FakeUpload(content_type="application/x-msdownload")}
    )
    with pytest.raises(ValidationError):
        await upload_endpoints.upload_endpoint(req, "agent-1")


async def test_upload_rejects_oversize(monkeypatch):
    _patch_gate(monkeypatch, upload_endpoints)
    big = b"x" * (upload_endpoints.DEFAULT_MAX_UPLOAD_ITEM_BYTES + 1)
    req = FakeRequest(form={"file": FakeUpload(content=big, content_type="image/png")})
    with pytest.raises(ValidationError):
        await upload_endpoints.upload_endpoint(req, "agent-1")


async def test_upload_requires_file_part(monkeypatch):
    _patch_gate(monkeypatch, upload_endpoints)
    req = FakeRequest(form={})
    with pytest.raises(ValidationError):
        await upload_endpoint_call(req)


async def upload_endpoint_call(req):
    return await upload_endpoints.upload_endpoint(req, "agent-1")


# --------------------------------------------------------------------------- #
# Gate: missing token is always rejected (independent of PUBLIC_AUTH mode)
# --------------------------------------------------------------------------- #
async def test_gate_missing_token_rejected(monkeypatch):
    # Rate limiter is a process global; a fresh call is under the limit.
    req = FakeRequest(headers={})
    with pytest.raises(AuthenticationError):
        await public_gate.require_messenger_session(req, "agent-1")


# --------------------------------------------------------------------------- #
# Profile (public, no token): avatar + name + description
# --------------------------------------------------------------------------- #
class FakeAvatarAction:
    def get_avatar(self, with_prefix=True):
        return "data:image/png;base64,AAA" if with_prefix else "AAA"


class FakeAgent:
    alias = "Iris"
    description = "Your friendly virtual assistant."


async def test_profile_returns_avatar_name_description(monkeypatch):
    import jvagent.core.cache as cache
    from jvagent.action.interact import avatar_endpoints

    async def _resolve(agent, name):
        return FakeAvatarAction()

    async def _agent(aid):
        return FakeAgent()

    monkeypatch.setattr(avatar_endpoints, "resolve_agent_action", _resolve)
    monkeypatch.setattr(cache, "get_cached_agent", _agent)
    out = await avatar_endpoints.agent_profile_endpoint(FakeRequest(), "agent-1")
    assert out == {
        "avatar": "data:image/png;base64,AAA",
        "name": "Iris",
        "description": "Your friendly virtual assistant.",
    }


async def test_profile_nulls_when_no_action_or_agent(monkeypatch):
    import jvagent.core.cache as cache
    from jvagent.action.interact import avatar_endpoints

    async def _resolve(agent, name):
        return None

    async def _agent(aid):
        return None

    monkeypatch.setattr(avatar_endpoints, "resolve_agent_action", _resolve)
    monkeypatch.setattr(cache, "get_cached_agent", _agent)
    out = await avatar_endpoints.agent_profile_endpoint(FakeRequest(), "agent-1")
    assert out == {"avatar": None, "name": None, "description": None}
