"""SSRF-safe URL fetch helpers for PageIndex ingest paths."""

from __future__ import annotations

import re
import socket
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import unquote, urlparse

import httpx
from jvspatial.api.exceptions import ValidationError

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB


def ssrf_guard_url(raw: str) -> None:
    """Reject URLs that point at non-public targets."""
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise ValidationError("URL must be http or https")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValidationError("URL must include a hostname")
    if host in {"localhost", "ip6-localhost", "ip6-loopback"}:
        raise ValidationError("URL host is not allowed")
    try:
        import ipaddress

        addrs: List[str] = []
        try:
            addrs.append(str(ipaddress.ip_address(host)))
        except ValueError:
            try:
                infos = socket.getaddrinfo(host, None)
                addrs = [info[4][0] for info in infos]
            except socket.gaierror:
                raise ValidationError("URL host could not be resolved") from None
        for addr in addrs:
            ip = ipaddress.ip_address(addr)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
            ):
                raise ValidationError(
                    "URL resolves to a non-public address; refusing to fetch"
                )
    except ImportError:
        pass


def _filename_from_content_disposition(cd: Optional[str]) -> Optional[str]:
    if not cd:
        return None
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd, re.I)
    if m:
        return unquote(m.group(1).strip())
    return None


def _filename_from_url(url: str) -> str:
    tail = unquote(urlparse(url).path).split("/")[-1]
    return tail or "download"


async def fetch_url_bytes_capped(
    url: str,
    *,
    read_timeout: float = 120.0,
    max_bytes: int = MAX_UPLOAD_BYTES,
    user_agent: str = "jvagent-pageindex/1.0",
) -> Tuple[bytes, str, Optional[str]]:
    """Fetch *url* with SSRF guards and per-hop redirect validation."""
    raw = url.strip()
    ssrf_guard_url(raw)
    timeout = httpx.Timeout(read_timeout, connect=30.0)

    async def _validate_redirect(response: httpx.Response) -> None:
        if 300 <= response.status_code < 400:
            loc = response.headers.get("location")
            if loc:
                target = str(httpx.URL(response.url).join(loc))
                ssrf_guard_url(target)

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        event_hooks={"response": [_validate_redirect]},
    ) as client:
        async with client.stream(
            "GET", raw, headers={"User-Agent": user_agent}
        ) as resp:
            if resp.status_code != 200:
                raise ValidationError(f"Download failed: HTTP {resp.status_code}")
            ct_header = resp.headers.get("content-type")
            content_type: Optional[str] = None
            if ct_header:
                content_type = ct_header.split(";")[0].strip()
            cd = resp.headers.get("content-disposition")
            fname = _filename_from_content_disposition(cd) or _filename_from_url(raw)
            total = 0
            chunks: List[bytes] = []
            async for chunk in resp.aiter_bytes():
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise ValidationError(
                        f"Remote file exceeds maximum size "
                        f"({max_bytes // (1024 * 1024)} MB)"
                    )
                chunks.append(chunk)
            content = b"".join(chunks)
    if not content:
        raise ValidationError("Downloaded file is empty")
    return content, fname, content_type


def require_path_under_work_dir(path: str, work_dir: str) -> None:
    """Reject filesystem paths outside the PageIndex staging directory."""
    resolved = Path(path).resolve()
    base = Path(work_dir).resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError(
            f"document path must be under the PageIndex work directory; "
            f"refusing to ingest {path!r}"
        ) from exc
