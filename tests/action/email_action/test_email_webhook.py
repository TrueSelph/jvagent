"""Tests for email inbound parsing and EmailAction helpers."""

import base64
import json
import pytest
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.datastructures import FormData

from jvagent.action.email_action.email_action import EmailAction
from jvagent.action.email_action.email_adapter import EmailAdapter, _subject_and_thread_headers
from jvagent.action.email_action.email_filter import EmailFilter, plain_text_to_email_html
from jvagent.action.email_action.email_payload import CanonicalSendMessage
from jvagent.action.email_action.email_webhook_helpers import parse_inbound_payload
from jvagent.action.email_action.inbound.gmail import gmail_raw_message_to_tuple
from jvagent.action.email_action.inbound.sendgrid import parse_sendgrid_inbound
from jvagent.action.email_action.modules.sendgrid import SendGridEmailProvider
from jvagent.action.response.message import ResponseMessage


def _gmail_raw_resource(
    *,
    from_addr: str = "alice@example.com",
    subject: str = "Hello",
    body: str = "Hi there",
    content_type: str = "plain",
) -> dict:
    if content_type == "html":
        msg = MIMEText(f"<p>{body}</p>", "html", "utf-8")
    else:
        msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = "support@example.com"
    msg["Message-ID"] = "<id@mail>"
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return {"id": "gmail-msg-1", "threadId": "t1", "raw": raw}


def test_gmail_raw_message_extracts_user_and_body():
    resource = _gmail_raw_resource()
    out = gmail_raw_message_to_tuple(resource)
    assert out is not None
    uid, utt, data = out
    assert uid == "alice@example.com"
    assert utt == "Hello"
    assert data["email_provider"] == "gmail"
    assert data["email_inbound"]["Subject"] == "Hello"
    assert data["email_inbound"]["BodyPlain"] == "Hi there"
    assert data["email_inbound"]["GmailMessageId"] == "gmail-msg-1"


def test_gmail_raw_html_falls_back_to_text():
    resource = _gmail_raw_resource(content_type="html", body="Yo")
    out = gmail_raw_message_to_tuple(resource)
    assert out is not None
    assert out[1] == "Hello"
    assert "Yo" in (out[2]["email_inbound"].get("BodyPlain") or "")
    assert "Yo" in (out[2]["email_inbound"].get("BodyHtml") or "")


def test_gmail_multipart_prefers_plain():
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText("plain part", "plain", "utf-8"))
    alt.attach(MIMEText("<b>html</b>", "html", "utf-8"))
    alt["Subject"] = "S"
    alt["From"] = "mix@example.com"
    alt["To"] = "x@y.com"
    raw = base64.urlsafe_b64encode(alt.as_bytes()).decode()
    out = gmail_raw_message_to_tuple({"id": "m2", "raw": raw})
    assert out is not None
    assert out[0] == "mix@example.com"
    assert out[1] == "S"
    assert out[2]["email_inbound"]["BodyPlain"] == "plain part"
    assert "<b>html</b>" in (out[2]["email_inbound"].get("BodyHtml") or "")


def test_gmail_subject_only_still_processes():
    resource = _gmail_raw_resource(body="")
    out = gmail_raw_message_to_tuple(resource)
    assert out is not None
    assert out[1] == "Hello"
    assert "BodyPlain" not in out[2]["email_inbound"]


def test_gmail_no_subject_uses_placeholder():
    msg = MIMEText("body only", "plain", "utf-8")
    msg["From"] = "bob@example.com"
    msg["To"] = "x@y.com"
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    out = gmail_raw_message_to_tuple({"id": "m3", "raw": raw})
    assert out is not None
    assert out[1] == "(no subject)"
    assert out[2]["email_inbound"]["BodyPlain"] == "body only"


def test_gmail_inline_image_adds_image_urls():
    from email.mime.image import MIMEImage

    tiny_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    mixed = MIMEMultipart("mixed")
    mixed["Subject"] = "pic"
    mixed["From"] = "cam@example.com"
    mixed["To"] = "x@y.com"
    mixed.attach(MIMEText("see image", "plain", "utf-8"))
    mixed.attach(MIMEImage(tiny_png, _subtype="png"))
    raw = base64.urlsafe_b64encode(mixed.as_bytes()).decode()
    out = gmail_raw_message_to_tuple({"id": "m4", "raw": raw})
    assert out is not None
    assert out[1] == "pic"
    urls = out[2].get("image_urls") or []
    assert len(urls) == 1
    assert urls[0].startswith("data:image/png;base64,")


def test_parse_inbound_payload_legacy_empty():
    assert parse_inbound_payload("brevo", {"items": []}) == []
    assert parse_inbound_payload("other", {"items": []}) == []


@pytest.mark.asyncio
async def test_parse_sendgrid_inbound_form():
    headers = {"Message-ID": "<sg@example.com>", "In-Reply-To": "<prior@example.com>"}
    fd = FormData(
        [
            ("from", "Carol <carol@example.com>"),
            ("to", "inbox@example.com"),
            ("subject", "SG subject"),
            ("text", "SG body"),
            ("headers", json.dumps(headers)),
        ]
    )
    rows = await parse_sendgrid_inbound(fd)
    assert len(rows) == 1
    uid, utt, data = rows[0]
    assert uid == "carol@example.com"
    assert utt == "SG body"
    assert data["email_provider"] == "sendgrid"
    assert data["email_inbound"]["Subject"] == "SG subject"
    assert data["email_inbound"]["BodyPlain"] == "SG body"
    assert data["email_inbound"]["MessageId"] == "<sg@example.com>"
    assert data["email_inbound"]["InReplyTo"] == "<prior@example.com>"
    assert data["email_inbound"]["FromName"] == "Carol"


@pytest.mark.asyncio
async def test_parse_sendgrid_inbound_html_sets_body_fields():
    fd = FormData(
        [
            ("from", "dan@example.com"),
            ("subject", "H"),
            ("html", "<p>Hi HTML</p>"),
        ]
    )
    rows = await parse_sendgrid_inbound(fd)
    assert len(rows) == 1
    _uid, _utt, data = rows[0]
    assert "<p>Hi HTML</p>" in (data["email_inbound"].get("BodyHtml") or "")
    assert "Hi HTML" in (data["email_inbound"].get("BodyPlain") or "")


def test_email_action_gmail_config_issues(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_SECRETS_JSON", raising=False)
    a = EmailAction()
    a.provider = "gmail"
    issues = a._config_issues()
    assert any("GOOGLE_CLIENT_SECRETS_JSON" in i for i in issues)


def test_email_action_sendgrid_config_issues(monkeypatch):
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    monkeypatch.delenv("EMAIL_DEFAULT_SENDER", raising=False)
    a = EmailAction()
    a.provider = "sendgrid"
    issues = a._config_issues()
    assert any("SENDGRID_API_KEY" in i for i in issues)
    assert any("EMAIL_DEFAULT_SENDER" in i for i in issues)


def test_email_action_invalid_provider():
    a = EmailAction()
    # bypass pattern validation as DB might have bad data
    object.__setattr__(a, "provider", "unknown")
    issues = a._config_issues()
    assert any("Unsupported provider" in i for i in issues)


def test_get_capabilities_respects_enabled():
    a = EmailAction()
    object.__setattr__(a, "enabled", False)
    assert a.get_capabilities() == []
    object.__setattr__(a, "enabled", True)
    caps = a.get_capabilities()
    assert len(caps) >= 1
    assert any("email" in c.lower() for c in caps)


@pytest.mark.asyncio
async def test_healthcheck_unconfigured(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_SECRETS_JSON", raising=False)
    a = EmailAction()
    h = await a.healthcheck()
    assert isinstance(h, dict)
    assert h.get("configured") is False
    assert "issues" in h


@pytest.mark.asyncio
async def test_healthcheck_sendgrid_configured_shape(monkeypatch):
    monkeypatch.setenv("SENDGRID_API_KEY", "k" * 20)
    monkeypatch.setenv("EMAIL_DEFAULT_SENDER", "sender@example.com")
    a = EmailAction()
    object.__setattr__(a, "provider", "sendgrid")
    object.__setattr__(a, "enabled", True)
    with patch.object(
        SendGridEmailProvider, "fetch_user_profile", new_callable=AsyncMock
    ) as _prof:
        _prof.return_value = {"email": "sender@example.com"}
        h = await a.healthcheck()
    assert h.get("configured") is True
    assert h.get("provider") == "sendgrid"
    assert h.get("api_key_configured") is True


@pytest.mark.asyncio
async def test_email_adapter_calls_provider():
    mock_provider = MagicMock()
    mock_provider.send_canonical = AsyncMock(
        return_value={"ok": True, "messageId": "mid1"}
    )

    class _StubEmailAction:
        provider = "gmail"

        def is_configured(self) -> bool:
            return True

        def _apply_env_defaults(self) -> None:
            pass

        async def resolve_outbound_sender(self):
            return "bot@example.com", None

        async def api(self):
            return mock_provider

    stub = _StubEmailAction()
    adapter = EmailAdapter(action=stub)
    msg = ResponseMessage(
        user_id="user@example.com",
        content="<p>hi</p>",
        channel="email",
        metadata={"subject": "S", "email_wrapped_html": True},
    )
    ok = await adapter.send(msg)
    assert ok is True
    mock_provider.send_canonical.assert_awaited_once()
    canon: CanonicalSendMessage = mock_provider.send_canonical.await_args.args[0]
    assert canon.to_email == "user@example.com"
    assert canon.subject == "S"
    assert canon.headers is None


def test_subject_and_thread_headers_re_prefix():
    subj, hdrs = _subject_and_thread_headers(
        {
            "email_inbound": {
                "Subject": "Hello",
                "MessageId": "<m1@x>",
                "InReplyTo": "<parent@x>",
            }
        }
    )
    assert subj == "Re: Hello"
    assert hdrs == {"In-Reply-To": "<m1@x>", "References": "<parent@x> <m1@x>"}


def test_subject_explicit_overrides_inbound():
    subj, hdrs = _subject_and_thread_headers(
        {"subject": "Custom", "email_inbound": {"Subject": "Orig", "MessageId": "<id>"}}
    )
    assert subj == "Custom"
    assert hdrs["In-Reply-To"] == "<id>"


@pytest.mark.asyncio
async def test_email_adapter_passes_thread_headers():
    mock_provider = MagicMock()
    mock_provider.send_canonical = AsyncMock(
        return_value={"ok": True, "messageId": "mid1"}
    )

    class _StubEmailAction:
        provider = "gmail"

        def is_configured(self) -> bool:
            return True

        def _apply_env_defaults(self) -> None:
            pass

        async def resolve_outbound_sender(self):
            return "bot@example.com", None

        async def api(self):
            return mock_provider

    stub = _StubEmailAction()
    adapter = EmailAdapter(action=stub)
    msg = ResponseMessage(
        user_id="user@example.com",
        content="Hi",
        channel="email",
        metadata={
            "email_wrapped_html": True,
            "email_inbound": {"Subject": "Q", "MessageId": "<mid@a>"},
        },
    )
    ok = await adapter.send(msg)
    assert ok is True
    canon: CanonicalSendMessage = mock_provider.send_canonical.await_args.args[0]
    assert canon.subject == "Re: Q"
    assert canon.headers is not None
    assert canon.headers["In-Reply-To"] == "<mid@a>"
    assert canon.headers["References"] == "<mid@a>"


def test_plain_text_to_email_html_bold_and_list():
    html_out = plain_text_to_email_html("*Hi*\n\n- one\n- two")
    assert "<strong>Hi</strong>" in html_out
    assert "<ul>" in html_out and "<li>" in html_out


@pytest.mark.asyncio
async def test_email_filter_produces_html_fragment():
    flt = EmailFilter()
    msg = ResponseMessage(
        user_id="u@example.com",
        content="*B*\n\n- a",
        channel="email",
        metadata={},
    )
    await flt.filter(msg)
    assert "<strong>B</strong>" in msg.content
    assert msg.metadata.get("email_wrapped_html") is True
