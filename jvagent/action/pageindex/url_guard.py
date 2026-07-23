"""SSRF-safe URL fetch helpers for PageIndex ingest paths."""

from __future__ import annotations

import re
import socket
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import unquote, urlparse, urlunparse

import httpx
from jvspatial.api.exceptions import ValidationError

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB


def _jvforge_base_origin() -> Optional[str]:
    """Normalized ``JVAGENT_JVFORGE_BASE_URL`` origin (scheme://host[:port]), or None."""
    from jvagent.env import get_jvagent_jvforge_base_url

    base = (get_jvagent_jvforge_base_url() or "").strip().rstrip("/")
    if not base:
        return None
    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    netloc = parsed.netloc or parsed.hostname
    return f"{parsed.scheme}://{netloc}".rstrip("/")


def is_trusted_jvforge_url(url: str) -> bool:
    """True when *url* host/port matches configured ``JVAGENT_JVFORGE_BASE_URL``."""
    origin = _jvforge_base_origin()
    if not origin:
        return False
    raw = (url or "").strip()
    if not raw:
        return False
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    url_origin = f"{parsed.scheme}://{parsed.netloc or parsed.hostname}".rstrip("/")
    return url_origin.lower() == origin.lower()


def rewrite_process_document_url_to_jvforge_base(url: str) -> str:
    """Rewrite ``/v1/artifacts/...`` (and ``/v1/jobs/...``) URLs onto the forge base.

    jvforge may advertise artifacts under ``JVFORGE_PUBLIC_BASE_URL`` (e.g. a
    tunnel hostname). jvagent should fetch via ``JVAGENT_JVFORGE_BASE_URL``,
    which is the origin it already uses to submit jobs.
    """
    raw = (url or "").strip()
    if not raw:
        return raw
    origin = _jvforge_base_origin()
    if not origin:
        return raw
    parsed = urlparse(raw)
    path = parsed.path or ""
    if not (path.startswith("/v1/artifacts/") or path.startswith("/v1/jobs/")):
        return raw
    # Already on the configured origin — no rewrite needed.
    if is_trusted_jvforge_url(raw):
        return raw
    rewritten = urlunparse(
        (
            urlparse(origin).scheme,
            urlparse(origin).netloc,
            path,
            "",
            parsed.query,
            "",
        )
    )
    return rewritten


def ssrf_guard_url(
    raw: str,
    *,
    allow_private_for_trusted_jvforge: bool = False,
) -> None:
    """Reject URLs that point at non-public targets.

    When ``allow_private_for_trusted_jvforge`` is True and *raw* matches the
    configured ``JVAGENT_JVFORGE_BASE_URL`` origin, private/loopback addresses
    and DNS resolution failures are allowed (local-dev forge on 127.0.0.1).
    """
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise ValidationError("URL must be http or https")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValidationError("URL must include a hostname")

    trusted = allow_private_for_trusted_jvforge and is_trusted_jvforge_url(raw)

    if host in {"localhost", "ip6-localhost", "ip6-loopback"}:
        if trusted:
            return
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
                if trusted:
                    return
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
                if trusted:
                    return
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
    trusted_jvforge: bool = False,
) -> Tuple[bytes, str, Optional[str]]:
    """Fetch *url* with SSRF guards and per-hop redirect validation.

    When ``trusted_jvforge`` is True, the configured jvforge origin (and
    redirects that stay on that origin) may resolve to private/loopback hosts.
    """
    raw = url.strip()
    ssrf_guard_url(raw, allow_private_for_trusted_jvforge=trusted_jvforge)
    timeout = httpx.Timeout(read_timeout, connect=30.0)

    async def _validate_redirect(response: httpx.Response) -> None:
        if 300 <= response.status_code < 400:
            loc = response.headers.get("location")
            if loc:
                target = str(httpx.URL(response.url).join(loc))
                ssrf_guard_url(
                    target,
                    allow_private_for_trusted_jvforge=(
                        trusted_jvforge and is_trusted_jvforge_url(target)
                    ),
                )

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
