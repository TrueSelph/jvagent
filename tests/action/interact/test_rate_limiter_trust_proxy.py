"""Rate-limiter client-IP extraction trust policy (AUDIT-interact HIGH).

The default MUST ignore client-supplied proxy headers — otherwise an attacker
sends a random ``X-Forwarded-For`` per request, lands each in a distinct per-IP
bucket, and the rate limit never trips."""

from __future__ import annotations

from types import SimpleNamespace

from jvagent.action.interact.rate_limiter import extract_client_ip


def _request(headers: dict, client_host: str = "10.0.0.1") -> SimpleNamespace:
    return SimpleNamespace(
        headers={k.lower(): v for k, v in headers.items()},
        client=SimpleNamespace(host=client_host),
    )


def test_default_ignores_forwarded_for(monkeypatch):
    monkeypatch.delenv("JVAGENT_TRUST_PROXY_HEADERS", raising=False)
    req = _request({"x-forwarded-for": "1.2.3.4"}, client_host="10.0.0.1")
    # Spoofed header ignored; falls back to the real socket peer.
    assert extract_client_ip(req) == "10.0.0.1"


def test_explicit_false_ignores_forwarded_for(monkeypatch):
    monkeypatch.setenv("JVAGENT_TRUST_PROXY_HEADERS", "false")
    req = _request({"x-forwarded-for": "1.2.3.4"}, client_host="10.0.0.1")
    assert extract_client_ip(req) == "10.0.0.1"


def test_spoofed_headers_share_one_bucket_by_default(monkeypatch):
    monkeypatch.delenv("JVAGENT_TRUST_PROXY_HEADERS", raising=False)
    ips = {
        extract_client_ip(
            _request({"x-forwarded-for": f"9.9.9.{i}"}, client_host="10.0.0.1")
        )
        for i in range(5)
    }
    # All spoof attempts collapse to the single real peer address.
    assert ips == {"10.0.0.1"}


def test_explicit_true_honors_forwarded_for(monkeypatch):
    monkeypatch.setenv("JVAGENT_TRUST_PROXY_HEADERS", "true")
    req = _request({"x-forwarded-for": "1.2.3.4, 5.6.7.8"}, client_host="10.0.0.1")
    assert extract_client_ip(req) == "1.2.3.4"


def test_explicit_true_falls_back_to_peer(monkeypatch):
    monkeypatch.setenv("JVAGENT_TRUST_PROXY_HEADERS", "true")
    req = _request({}, client_host="10.0.0.1")
    assert extract_client_ip(req) == "10.0.0.1"
