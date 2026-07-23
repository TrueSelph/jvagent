"""SSRF defense for webhook callbacks (AUDIT-core C-4).

Verifies that ``_resolve_and_validate`` rejects:
- Non-http(s) schemes.
- Private / loopback / link-local / multicast / reserved / unspecified IPs
  (resolved AND literal).
- IPv4-mapped IPv6 forms of the above.
- 0.0.0.0 and ::.
"""

import ipaddress
from unittest.mock import patch

import pytest

from jvagent.core.callback import _is_unsafe_ip, _resolve_and_validate


@pytest.mark.asyncio
async def test_literal_loopback_rejected():
    with pytest.raises(ValueError, match="blocked IP literal"):
        await _resolve_and_validate("http://127.0.0.1/x")


@pytest.mark.asyncio
async def test_literal_private_v4_rejected():
    with pytest.raises(ValueError, match="blocked IP literal"):
        await _resolve_and_validate("http://10.0.0.5/x")


@pytest.mark.asyncio
async def test_literal_link_local_aws_metadata_rejected():
    with pytest.raises(ValueError, match="blocked IP literal"):
        await _resolve_and_validate("http://169.254.169.254/latest/meta-data/")


@pytest.mark.asyncio
async def test_literal_unspecified_rejected():
    with pytest.raises(ValueError, match="blocked IP literal"):
        await _resolve_and_validate("http://0.0.0.0/x")


@pytest.mark.asyncio
async def test_literal_ipv6_loopback_rejected():
    with pytest.raises(ValueError, match="blocked IP literal"):
        await _resolve_and_validate("http://[::1]/x")


@pytest.mark.asyncio
async def test_literal_ipv4_mapped_ipv6_loopback_rejected():
    # ::ffff:127.0.0.1 — IPv4-mapped IPv6, easy SSRF bypass if not handled
    with pytest.raises(ValueError, match="blocked IP literal"):
        await _resolve_and_validate("http://[::ffff:127.0.0.1]/x")


@pytest.mark.asyncio
async def test_non_http_scheme_rejected():
    with pytest.raises(ValueError, match="scheme"):
        await _resolve_and_validate("file:///etc/passwd")
    with pytest.raises(ValueError, match="scheme"):
        await _resolve_and_validate("gopher://example.com/")
    with pytest.raises(ValueError, match="scheme"):
        await _resolve_and_validate("ftp://example.com/")


@pytest.mark.asyncio
async def test_resolved_private_ip_rejected():
    """Hostname resolving to a private IP must be rejected."""

    def _fake_getaddrinfo(host, *_args, **_kwargs):
        # Return one private IPv4 result tuple in the standard 5-tuple shape.
        return [(0, 0, 0, "", ("10.0.0.5", 0))]

    with patch("jvagent.core.callback.socket.getaddrinfo", _fake_getaddrinfo):
        with pytest.raises(ValueError, match="blocked address"):
            await _resolve_and_validate("http://attacker.example/x")


@pytest.mark.asyncio
async def test_resolved_public_ip_accepted():
    def _fake_getaddrinfo(host, *_args, **_kwargs):
        return [(0, 0, 0, "", ("93.184.216.34", 0))]  # example.com public IP

    with patch("jvagent.core.callback.socket.getaddrinfo", _fake_getaddrinfo):
        hostname, safe_ips = await _resolve_and_validate("http://example.com/x")
    assert hostname == "example.com"
    assert safe_ips == ["93.184.216.34"]


def test_is_unsafe_ip_classifications():
    # Loopback / private / link-local / multicast / reserved / unspecified.
    for ip_str in [
        "127.0.0.1",
        "10.0.0.1",
        "192.168.1.1",
        "172.16.0.1",
        "169.254.1.1",
        "0.0.0.0",
        "224.0.0.1",  # multicast
        "240.0.0.1",  # reserved
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "ff00::1",
    ]:
        assert _is_unsafe_ip(ipaddress.ip_address(ip_str)), ip_str
    # IPv4-mapped IPv6 of unsafe IPs.
    assert _is_unsafe_ip(ipaddress.ip_address("::ffff:127.0.0.1"))
    assert _is_unsafe_ip(ipaddress.ip_address("::ffff:10.0.0.1"))
    # Public IPs are safe.
    for ip_str in ["8.8.8.8", "1.1.1.1", "93.184.216.34"]:
        assert not _is_unsafe_ip(ipaddress.ip_address(ip_str)), ip_str
