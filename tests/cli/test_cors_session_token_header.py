"""The interact/messenger client sends X-Session-Token; jvagent must always
allow-list it as a CORS request header, or the browser preflight fails on the
first token-carrying (resume/voice/upload) turn."""

from jvagent.cli.server_config import (
    _DEFAULT_CORS_HEADERS,
    _SESSION_TOKEN_HEADER,
    _ensure_session_token_header,
)


def test_none_yields_defaults_plus_token():
    out = _ensure_session_token_header(None)
    assert _SESSION_TOKEN_HEADER in out
    # Standard headers preserved.
    for h in _DEFAULT_CORS_HEADERS:
        assert h in out


def test_custom_list_gets_token_appended():
    out = _ensure_session_token_header(["Content-Type", "Authorization"])
    assert out == ["Content-Type", "Authorization", _SESSION_TOKEN_HEADER]


def test_token_not_duplicated_case_insensitive():
    out = _ensure_session_token_header(["Content-Type", "x-session-token"])
    assert sum(h.lower() == _SESSION_TOKEN_HEADER.lower() for h in out) == 1
