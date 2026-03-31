"""jvagent.core.public_url — canonical public origin."""

from jvagent.core.public_url import get_public_base_url


def test_get_public_base_url_strips(monkeypatch):
    monkeypatch.setenv("JVAGENT_PUBLIC_BASE_URL", "  https://app.example.com/  ")
    assert get_public_base_url() == "https://app.example.com/"


def test_get_public_base_url_empty(monkeypatch):
    monkeypatch.delenv("JVAGENT_PUBLIC_BASE_URL", raising=False)
    assert get_public_base_url() == ""
