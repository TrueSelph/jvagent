"""Inbound email authentication gates (SPF/DKIM for webhook providers).

SendGrid Inbound Parse posts ``SPF`` and ``dkim`` form fields. Gmail/Outlook
poll paths fetch mail via an authenticated API (OAuth), so SMTP-style From
forgery is not applicable there — those providers skip the SPF/DKIM gate.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

# SendGrid SPF: "pass" | "fail" | "none" | …
_SPF_PASS = frozenset({"pass"})

# SendGrid dkim examples: "{@domain.com : pass}" | "none" | "fail"
_DKIM_PASS_RE = re.compile(r":\s*pass\b", re.IGNORECASE)


def spf_passes(spf_raw: Optional[str]) -> bool:
    """True when SendGrid ``SPF`` field is an explicit pass."""
    if spf_raw is None:
        return False
    return str(spf_raw).strip().lower() in _SPF_PASS


def dkim_passes(dkim_raw: Optional[str]) -> bool:
    """True when SendGrid ``dkim`` field reports at least one domain pass."""
    if dkim_raw is None:
        return False
    s = str(dkim_raw).strip()
    if not s or s.lower() == "none":
        return False
    if s.lower() == "pass":
        return True
    return bool(_DKIM_PASS_RE.search(s))


def assert_inbound_auth(
    provider: str,
    *,
    spf: Optional[str] = None,
    dkim: Optional[str] = None,
    headers: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Return True when inbound identity may be trusted for ``provider``.

    - ``sendgrid``: require SPF **and** DKIM pass (form fields or headers).
    - ``gmail`` / ``outlook``: authenticated API fetch — always True.
    - unknown: False (fail closed).
    """
    prov = (provider or "").strip().lower()
    if prov in ("gmail", "outlook"):
        return True
    if prov != "sendgrid":
        logger.debug("inbound auth: unknown provider %r — reject", provider)
        return False

    spf_val = spf
    dkim_val = dkim
    if headers:
        lower = {str(k).lower(): v for k, v in headers.items()}
        if spf_val is None:
            spf_val = lower.get("spf")  # type: ignore[assignment]
        if dkim_val is None:
            dkim_val = lower.get("dkim")  # type: ignore[assignment]

    ok_spf = spf_passes(spf_val if isinstance(spf_val, str) else None)
    ok_dkim = dkim_passes(dkim_val if isinstance(dkim_val, str) else None)
    if not ok_spf or not ok_dkim:
        logger.info(
            "SendGrid inbound rejected: SPF=%r (pass=%s) dkim=%r (pass=%s)",
            spf_val,
            ok_spf,
            dkim_val,
            ok_dkim,
        )
        return False
    return True


__all__ = [
    "assert_inbound_auth",
    "dkim_passes",
    "spf_passes",
]
