"""SendGrid Mail Send API v3 action."""

import logging
import os
from typing import Any, Dict, List, Optional, Union

import httpx
from jvspatial.core.annotations import attribute
from jvspatial.api.exceptions import ValidationError

from jvagent.action.base import Action

logger = logging.getLogger(__name__)


def _deep_merge_dict(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge_dict(out[k], v)
        else:
            out[k] = v
    return out


def _merge_mail_overrides(
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


def _normalize_recipients(
    recipients: Union[str, List[Any], None],
) -> List[Dict[str, str]]:
    if recipients is None:
        return []
    if isinstance(recipients, str):
        s = recipients.strip()
        return [{"email": s}] if s else []
    out: List[Dict[str, str]] = []
    for item in recipients:
        if isinstance(item, str):
            s = item.strip()
            if s:
                out.append({"email": s})
        elif isinstance(item, dict):
            e = item.get("email") or item.get("Email")
            if not e:
                continue
            row: Dict[str, str] = {"email": str(e).strip()}
            name = item.get("name") or item.get("Name")
            if name is not None and str(name).strip():
                row["name"] = str(name).strip()
            out.append(row)
    return out


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


class SendGridAction(Action):
    """Send email via SendGrid HTTP API v3 (Mail Send)."""

    api_key: Optional[str] = attribute(
        default=None,
        description="SendGrid API key (Bearer token for /v3/mail/send)",
    )
    api_base_url: str = attribute(
        default="https://api.sendgrid.com/v3",
        description="SendGrid API base URL (no trailing slash required)",
    )
    timeout: int = attribute(
        default=30,
        description="HTTP client timeout in seconds",
        ge=1,
        le=120,
    )
    default_from_email: Optional[str] = attribute(
        default=None,
        description="Default From email when payload omits from",
    )
    default_from_name: Optional[str] = attribute(
        default=None,
        description="Default From display name when payload omits name",
    )

    def _apply_env_defaults(self) -> None:
        if not self.api_key or not str(self.api_key).strip():
            val = os.environ.get("SENDGRID_API_KEY", "").strip()
            if val:
                self.api_key = val
                logger.debug("Using SENDGRID_API_KEY from environment")

        base = os.environ.get("SENDGRID_API_BASE_URL", "").strip()
        if base and (
            not self.api_base_url
            or self.api_base_url == "https://api.sendgrid.com/v3"
        ):
            self.api_base_url = base.rstrip("/")
            logger.debug("Using SENDGRID_API_BASE_URL from environment")

        if not self.default_from_email or not str(self.default_from_email).strip():
            fe = os.environ.get("SENDGRID_FROM_EMAIL", "").strip()
            if fe:
                self.default_from_email = fe

        if not self.default_from_name or not str(self.default_from_name).strip():
            fn = os.environ.get("SENDGRID_FROM_NAME", "").strip()
            if fn:
                self.default_from_name = fn

    def _config_issues(self) -> List[str]:
        issues: List[str] = []
        if not self.api_key or not str(self.api_key).strip():
            issues.append(
                "api_key is not set (use context.api_key or SENDGRID_API_KEY)"
            )
        base = str(self.api_base_url or "").strip()
        if not base:
            issues.append("api_base_url is empty")
        elif not base.startswith(("http://", "https://")):
            issues.append("api_base_url must be an HTTP/HTTPS URL")
        return issues

    def is_configured(self) -> bool:
        self._apply_env_defaults()
        return len(self._config_issues()) == 0

    def get_capabilities(self) -> List[str]:
        return [
            "Send transactional or marketing email via SendGrid to any recipients, "
            "with plain text, HTML, dynamic templates, custom headers, and attachments "
            "(within SendGrid size limits, typically ~30MB total message)."
        ]

    def _auth_headers(self) -> Dict[str, str]:
        key = str(self.api_key).strip()
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        self._apply_env_defaults()
        if not self.is_configured():
            raise ValidationError(
                message="SendGrid action is not configured",
                details={"issues": self._config_issues()},
            )
        base = str(self.api_base_url).strip().rstrip("/")
        url = f"{base}/{path.lstrip('/')}"
        timeout = httpx.Timeout(self.timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.request(
                method,
                url,
                headers=self._auth_headers(),
                **kwargs,
            )

    def _raise_for_sendgrid_error(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        body: Any
        try:
            body = response.json()
        except Exception:
            body = {"errors": [{"message": response.text or response.reason_phrase}]}
        msg = self._format_error_message(body)
        raise ValidationError(
            message=msg or f"SendGrid request failed ({response.status_code})",
            details={
                "status_code": response.status_code,
                "body": body,
            },
        )

    @staticmethod
    def _format_error_message(body: Any) -> str:
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

    async def fetch_user_profile(self) -> Dict[str, Any]:
        """GET /user/profile — validate API key and return profile JSON."""
        response = await self._request("GET", "/user/profile")
        self._raise_for_sendgrid_error(response)
        data = response.json()
        return data if isinstance(data, dict) else {"profile": data}

    async def healthcheck(self) -> Union[bool, Dict[str, Any]]:
        self._apply_env_defaults()
        if not self.is_configured():
            return {"healthy": False, "issues": self._config_issues()}
        try:
            await self.fetch_user_profile()
            return True
        except ValidationError as e:
            return {"healthy": False, "error": str(e)}
        except httpx.HTTPError as e:
            logger.warning("SendGrid healthcheck HTTP error: %s", e)
            return {"healthy": False, "error": str(e)}

    def apply_default_from(self, mail: Dict[str, Any]) -> Dict[str, Any]:
        """Return a copy of mail with default from_* applied if missing."""
        self._apply_env_defaults()
        out = dict(mail)
        if "from" not in out or not out.get("from"):
            fe = self.default_from_email
            if fe and str(fe).strip():
                fn = self.default_from_name
                fobj: Dict[str, str] = {"email": str(fe).strip()}
                if fn and str(fn).strip():
                    fobj["name"] = str(fn).strip()
                out["from"] = fobj
        return out

    async def send_mail_v3(
        self,
        mail: Dict[str, Any],
        *,
        apply_defaults: bool = True,
    ) -> Dict[str, Any]:
        """POST a complete v3 Mail Send JSON body (after optional default ``from``)."""
        if not mail or not isinstance(mail, dict):
            raise ValidationError(
                message="mail must be a non-empty object",
                details={},
            )
        payload = self.apply_default_from(dict(mail)) if apply_defaults else dict(mail)
        response = await self._request("POST", "/mail/send", json=payload)
        self._raise_for_sendgrid_error(response)
        return self._send_response_summary(response)

    @staticmethod
    def _send_response_summary(response: httpx.Response) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "success": True,
            "status_code": response.status_code,
            "message_id": response.headers.get("X-Message-Id"),
        }
        if response.content:
            try:
                out["body"] = response.json()
            except Exception:
                out["body_text"] = response.text
        return out

    async def send_mail(
        self,
        *,
        to: Union[str, List[Any]],
        subject: Optional[str] = None,
        text: Optional[str] = None,
        html: Optional[str] = None,
        cc: Union[str, List[Any], None] = None,
        bcc: Union[str, List[Any], None] = None,
        mail_from: Optional[Dict[str, str]] = None,
        reply_to: Union[str, Dict[str, str], None] = None,
        headers: Optional[Dict[str, str]] = None,
        categories: Optional[List[str]] = None,
        template_id: Optional[str] = None,
        dynamic_template_data: Optional[Dict[str, Any]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        mail_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build a v3 payload from common fields, then merge ``mail_overrides``."""
        to_list = _normalize_recipients(to)
        if not to_list:
            raise ValidationError(
                message="At least one valid 'to' recipient is required",
                details={},
            )

        pers: Dict[str, Any] = {"to": to_list}
        cc_l = _normalize_recipients(cc)
        if cc_l:
            pers["cc"] = cc_l
        bcc_l = _normalize_recipients(bcc)
        if bcc_l:
            pers["bcc"] = bcc_l
        content: List[Dict[str, str]] = []
        if text is not None and str(text):
            content.append({"type": "text/plain", "value": str(text)})
        if html is not None and str(html):
            content.append({"type": "text/html", "value": str(html)})

        if dynamic_template_data:
            pers["dynamic_template_data"] = dict(dynamic_template_data)

        mail: Dict[str, Any] = {"personalizations": [pers]}
        if subject is not None and str(subject).strip():
            mail["subject"] = str(subject).strip()
        if content:
            mail["content"] = content

        if mail_from:
            mail["from"] = dict(mail_from)
        elif self.default_from_email and str(self.default_from_email).strip():
            f: Dict[str, str] = {"email": str(self.default_from_email).strip()}
            if self.default_from_name and str(self.default_from_name).strip():
                f["name"] = str(self.default_from_name).strip()
            mail["from"] = f

        rt = _normalize_reply_to(reply_to)
        if rt:
            mail["reply_to"] = rt
        if headers:
            mail["headers"] = dict(headers)
        if categories:
            mail["categories"] = list(categories)
        if template_id and str(template_id).strip():
            mail["template_id"] = str(template_id).strip()

        sg_attachments: List[Dict[str, Any]] = []
        for att in attachments or []:
            if not isinstance(att, dict):
                continue
            raw_content = att.get("content_base64") or att.get("content")
            fname = att.get("filename")
            if not raw_content or not fname:
                continue
            row: Dict[str, Any] = {
                "content": str(raw_content),
                "filename": str(fname),
                "type": str(att.get("type", "application/octet-stream")),
            }
            disp = att.get("disposition")
            if disp:
                row["disposition"] = str(disp)
            cid = att.get("content_id")
            if cid:
                row["content_id"] = str(cid)
            sg_attachments.append(row)
        if sg_attachments:
            mail["attachments"] = sg_attachments

        merged = _merge_mail_overrides(mail, mail_overrides)
        merged = self.apply_default_from(merged)
        return await self.send_mail_v3(merged, apply_defaults=False)
