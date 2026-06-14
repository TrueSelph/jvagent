"""SendGrid Mail Send API v3 provider (canonical payload)."""

import logging
from typing import Any, Dict, List, Optional, Union

import httpx

from jvagent.action.email_action.email_payload import (
    CanonicalSendMessage,
    EmailAttachment,
)

from .base import default_inbound_webhook_unsupported

logger = logging.getLogger(__name__)

SENDGRID_DEFAULT_BASE = "https://api.sendgrid.com/v3"


def _deep_merge_dict(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge_dict(out[k], v)
        else:
            out[k] = v
    return out


def merge_mail_overrides(
    base: Dict[str, Any], overrides: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    if not overrides:
        return base
    merged = dict(base)
    for key, val in overrides.items():
        if key == "personalizations" and isinstance(val, list) and val:
            pers = list(merged.get("personalizations") or [])
            if not pers:
                pers = [{}]
            first = pers[0] if isinstance(pers[0], dict) else {}
            o0 = val[0] if isinstance(val[0], dict) else {}
            pers[0] = _deep_merge_dict(first, o0)
            merged["personalizations"] = pers
        elif isinstance(val, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged[key], val)
        else:
            merged[key] = val
    return merged


def _normalize_reply_to(
    reply_to: Union[str, Dict[str, str], None],
) -> Optional[Dict[str, str]]:
    if reply_to is None:
        return None
    if isinstance(reply_to, str):
        s = reply_to.strip()
        return {"email": s} if s else None
    if isinstance(reply_to, dict):
        e = reply_to.get("email") or reply_to.get("Email")
        if not e:
            return None
        out: Dict[str, str] = {"email": str(e).strip()}
        name = reply_to.get("name") or reply_to.get("Name")
        if name is not None and str(name).strip():
            out["name"] = str(name).strip()
        return out
    return None


def _sg_attachments_from_canonical(
    attachments: List[EmailAttachment],
) -> List[Dict[str, Any]]:
    sg_attachments: List[Dict[str, Any]] = []
    for att in attachments:
        row: Dict[str, Any] = {
            "content": att.content_base64,
            "filename": att.filename,
            "type": att.content_type,
        }
        if att.disposition:
            row["disposition"] = att.disposition
        if att.content_id:
            row["content_id"] = att.content_id
        sg_attachments.append(row)
    return sg_attachments


def _format_sendgrid_error_body(body: Any) -> str:
    if not isinstance(body, dict):
        return str(body)
    errs = body.get("errors")
    if isinstance(errs, list) and errs:
        parts = []
        for e in errs:
            if isinstance(e, dict):
                m = e.get("message") or e.get("field")
                if m:
                    parts.append(str(m))
            elif e:
                parts.append(str(e))
        if parts:
            return "; ".join(parts)
    return str(body.get("message", ""))


class SendGridEmailProvider:
    """Mail Send v3; inbound webhook registration via API is not supported."""

    def __init__(
        self,
        *,
        api_key: str,
        api_base: str = SENDGRID_DEFAULT_BASE,
        timeout: float = 30.0,
        default_from_email: str = "",
        default_from_name: Optional[str] = None,
    ) -> None:
        self._api_key = (api_key or "").strip()
        self.api_base = (api_base or SENDGRID_DEFAULT_BASE).rstrip("/")
        self.timeout = timeout
        self._default_from_email = (default_from_email or "").strip()
        self._default_from_name = (
            (default_from_name or "").strip() or None if default_from_name else None
        )

    def _auth_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _canonical_to_mail(self, msg: CanonicalSendMessage) -> Dict[str, Any]:
        html_content = msg.html_content
        text_content = msg.text_content
        if not html_content and not text_content:
            raise ValueError("html_content or text_content required")

        pers: Dict[str, Any] = {"to": [{"email": msg.to_email}]}
        if msg.to_name:
            pers["to"][0]["name"] = msg.to_name

        to_lower = (msg.to_email or "").strip().lower()
        cc_sg: List[Dict[str, str]] = []
        for r in msg.cc or []:
            addr = (r.email or "").strip()
            if not addr or "@" not in addr or addr.lower() == to_lower:
                continue
            row: Dict[str, str] = {"email": addr}
            if r.name and str(r.name).strip():
                row["name"] = str(r.name).strip()
            cc_sg.append(row)
        if cc_sg:
            pers["cc"] = cc_sg

        content: List[Dict[str, str]] = []
        if text_content:
            content.append({"type": "text/plain", "value": str(text_content)})
        if html_content:
            content.append({"type": "text/html", "value": str(html_content)})

        from_obj: Dict[str, str] = {"email": msg.sender_email}
        if msg.sender_name:
            from_obj["name"] = msg.sender_name
        mail: Dict[str, Any] = {
            "personalizations": [pers],
            "subject": msg.subject,
            "content": content,
            "from": from_obj,
        }
        rt = _normalize_reply_to(msg.reply_to)
        if rt:
            mail["reply_to"] = rt
        if msg.headers:
            mail["headers"] = dict(msg.headers)
        if msg.attachments:
            mail["attachments"] = _sg_attachments_from_canonical(msg.attachments)
        return mail

    async def send_canonical(self, msg: CanonicalSendMessage) -> Dict[str, Any]:
        try:
            mail = self._canonical_to_mail(msg)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        url = f"{self.api_base}/mail/send"
        try:
            logger.info(
                "SendGrid send_canonical: from=%r to=%r subject=%r",
                msg.sender_email,
                msg.to_email,
                msg.subject,
            )
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=mail, headers=self._auth_headers())
        except httpx.HTTPError as e:
            logger.error("SendGrid send HTTP error: %s", e)
            return {"ok": False, "error": str(e)}

        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = {"errors": [{"message": resp.text or resp.reason_phrase}]}
            err_msg = _format_sendgrid_error_body(body)
            return {
                "ok": False,
                "error": err_msg or f"HTTP {resp.status_code}",
                "status_code": resp.status_code,
                "response": body,
            }

        out: Dict[str, Any] = {
            "ok": True,
            "message_id": resp.headers.get("X-Message-Id"),
            "status_code": resp.status_code,
        }
        if resp.content:
            try:
                out["response"] = resp.json()
            except Exception:
                out["response_text"] = resp.text
        return out

    async def send_mail_v3(
        self,
        mail: Dict[str, Any],
        *,
        mail_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """POST raw v3 body (after default from merge). For advanced API use."""
        merged = merge_mail_overrides(dict(mail), mail_overrides)
        merged = self._apply_default_from(merged)
        url = f"{self.api_base}/mail/send"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=merged, headers=self._auth_headers())
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = {"errors": [{"message": resp.text or resp.reason_phrase}]}
            return {
                "ok": False,
                "error": _format_sendgrid_error_body(body)
                or f"HTTP {resp.status_code}",
                "status_code": resp.status_code,
                "response": body,
            }
        out: Dict[str, Any] = {
            "ok": True,
            "message_id": resp.headers.get("X-Message-Id"),
            "status_code": resp.status_code,
        }
        if resp.content:
            try:
                out["response"] = resp.json()
            except Exception:
                out["response_text"] = resp.text
        return out

    def _apply_default_from(self, mail: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(mail)
        if (not out.get("from")) and self._default_from_email:
            fobj: Dict[str, str] = {"email": self._default_from_email}
            if self._default_from_name:
                fobj["name"] = self._default_from_name
            out["from"] = fobj
        return out

    async def fetch_user_profile(self) -> Dict[str, Any]:
        url = f"{self.api_base}/user/profile"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self._auth_headers())
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = {}
            raise RuntimeError(
                _format_sendgrid_error_body(body) or f"HTTP {resp.status_code}"
            )
        data = resp.json()
        return data if isinstance(data, dict) else {"profile": data}

    async def create_inbound_webhook(
        self,
        *,
        url: str,
        domain: str,
        description: str = "",
    ) -> Dict[str, Any]:
        _ = (url, domain, description)
        return default_inbound_webhook_unsupported()
