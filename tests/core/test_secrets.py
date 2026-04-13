from jvspatial.env import env


def test_env_prefers_first_non_empty(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    assert env("OPENAI_API_KEY") == "openai-key"


def test_env_returns_default_when_missing(monkeypatch):
    monkeypatch.delenv("DOES_NOT_EXIST", raising=False)
    assert (env("DOES_NOT_EXIST") or "fallback") == "fallback"


def test_env_strip_toggle(monkeypatch):
    monkeypatch.setenv("SPACED_KEY", "  hello  ")
    assert env("SPACED_KEY") == "hello"
    assert env("SPACED_KEY", strip=False) == "  hello  "
