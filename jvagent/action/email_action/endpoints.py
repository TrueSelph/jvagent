"""HTTP endpoints for EmailAction (Gmail poll + SendGrid webhook).

Public webhook routes authenticate via jvspatial API keys (``webhook:email``).
Authenticated admin routes require a logged-in user; some require the ``admin`` role.
"""

import logging
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request
from jvspatial import create_task
from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError
from jvspatial.exceptions import ValidationError as SpatialValidationError

from jvagent.action.access_control.access_control_action import log_access_denied
from jvagent.core.agent import Agent

from .email_action import EmailAction
from .email_payload import CanonicalSendMessage, normalize_attachments_from_body
from .email_webhook_helpers import (
    DEFAULT_EMAIL_UTTERANCE_MAX,
    process_email_interaction_async,
)
from .gmail_poll import poll_gmail_inbox_once
from .inbound.sendgrid import parse_sendgrid_inbound
from .modules.sendgrid import SendGridEmailProvider
from .utils.endpoint_helpers import get_email_action

logger = logging.getLogger(__name__)


async def _agent_and_email_action_for_webhook(agent_id: str) -> tuple[Agent, EmailAction]:
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id},
        )
    email_action = await agent.get_action_by_type("EmailAction")
    if not email_action:
        raise ResourceNotFoundError(
            message="Action with label 'EmailAction' not found",
            details={"agent_id": agent_id},
        )
    return agent, email_action


@endpoint(
    "/email/interact/webhook/{agent_id}",
    methods=["POST"],
    webhook=True,
    auth=False,
    webhook_auth="api_key",
    tags=["Email Action"],
    summary="Receive inbound parsed email webhooks",
    description=(
        "Used when **EmailAction.provider** is **sendgrid**. **SendGrid Inbound Parse:** "
        "multipart or urlencoded form (**from**, **subject**, **text**, **html**, "
        "**headers**, attachments). **Gmail** uses server-side polling instead of this "
        "URL. Authenticate with **api_key** query or header."
    ),
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="received"),
            "response": ResponseField(
                field_type=Optional[str], example=None, default=None
            ),
        }
    ),
)
async def email_interact_webhook(request: Request, agent_id: str) -> Any:
    """Handle POST from the email provider; enqueue one interaction per parsed item."""
    agent, email_action = await _agent_and_email_action_for_webhook(agent_id)
    email_action._apply_env_defaults()
    utterance_max = int(
        getattr(email_action, "utterance_max_length", None)
        or DEFAULT_EMAIL_UTTERANCE_MAX
    )

    provider = (email_action.provider or "gmail").strip().lower()
    ct = (request.headers.get("content-type") or "").lower()

    if provider == "gmail":
        raise HTTPException(
            status_code=400,
            detail=(
                "EmailAction provider is gmail: inbound uses Gmail API polling, "
                "not HTTP webhooks. Use POST .../email/gmail/poll-once or enable "
                "gmail_poll_interval_seconds on the action."
            ),
        )

    if provider == "sendgrid":
        if (
            "multipart/form-data" not in ct
            and "application/x-www-form-urlencoded" not in ct
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "SendGrid inbound expects multipart/form-data or "
                    "application/x-www-form-urlencoded "
                    f"(got {request.headers.get('content-type')!r})"
                ),
            )
        form = await request.form()
        tuples = await parse_sendgrid_inbound(form)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported EmailAction.provider for webhook: {provider!r}",
        )

    if not tuples:
        return {"status": "ignored", "response": None}

    access_control_action = await agent.get_access_control_action()

    async def handle_one(
        user_id: str, utterance: str, data_dict: Dict[str, Any]
    ) -> None:
        if len(utterance) > utterance_max:
            logger.warning(
                "Email utterance too long from %s (%s chars; max %s)",
                user_id,
                len(utterance),
                utterance_max,
            )
            return
        has_access = True
        if access_control_action:
            has_access = await access_control_action.has_action_access(
                user_id=user_id,
                action_label="EmailAction",
                channel="email",
            )
        if not has_access:
            log_access_denied(
                agent_id=agent_id,
                user_id=user_id,
                channel="email",
                action_label="EmailAction",
                stage="email_inbound",
            )
            return

        inbound = data_dict.get("email_inbound") or {}
        sender_name = inbound.get("FromName") if isinstance(inbound, dict) else None

        task = await create_task(
            process_email_interaction_async(
                utterance,
                user_id,
                agent_id,
                agent,
                data_dict,
                sender_name=sender_name,
            ),
            name=f"email_interaction_{user_id}",
        )
        if task is None:
            await process_email_interaction_async(
                utterance,
                user_id,
                agent_id,
                agent,
                data_dict,
                sender_name=sender_name,
            )

    for user_id, utterance, data_dict in tuples:
        await handle_one(user_id, utterance, data_dict)

    return {"status": "received", "response": None}


@endpoint(
    "/actions/{action_id}/email/webhook-url",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Email Action"],
    summary="Get inbound webhook callback URL (with API key)",
    description=(
        "Returns the full HTTPS URL to configure in your provider’s inbound webhook "
        "(SendGrid Inbound Parse). The URL includes an **api_key** query "
        "parameter for jvspatial webhook authentication.\n\n"
        "**Path:** `action_id` — persisted **EmailAction** node id.\n\n"
        "**Query:** `regenerate` — if true, revoke the previous webhook key and "
        "issue a new URL (use when rotating credentials).\n\n"
        "Requires **JVAGENT_PUBLIC_BASE_URL** (or action **base_url**) so the URL "
        "points at this deployment. Admin role only."
    ),
)
async def email_get_webhook_url(
    action_id: str, regenerate: bool = False
) -> Dict[str, Any]:
    """Return ``{ success, webhook_url }`` for admin to paste into the provider."""
    action = await get_email_action(action_id)
    try:
        url = await action.get_webhook_url(regenerate=regenerate)
    except SpatialValidationError as e:
        raise ValidationError(
            message=str(e),
            details={"action_id": action_id},
        ) from e
    return {"success": True, "webhook_url": url}


@endpoint(
    "/actions/{action_id}/email/health",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Email Action"],
    summary="EmailAction health (provider API check for SendGrid)",
    description=(
        "Runs **EmailAction.healthcheck()**. For **sendgrid**, validates the API key "
        "via **GET /user/profile**. For **gmail**, checks **GoogleGmailAction** and "
        "**users.getProfile**. Admin only."
    ),
)
async def email_health(action_id: str) -> Dict[str, Any]:
    action = await get_email_action(action_id)
    h = await action.healthcheck()
    if h is True:
        return {"healthy": True, "details": None}
    if isinstance(h, dict):
        return {
            "healthy": h.get("healthy", False),
            "details": h,
        }
    return {"healthy": bool(h), "details": None}


@endpoint(
    "/actions/{action_id}/email/send",
    methods=["POST"],
    auth=True,
    tags=["Email Action"],
    summary="Send a transactional email (canonical payload)",
    description=(
        "Sends through **EmailAction**’s configured provider using one canonical JSON "
        "shape. **Gmail:** **GOOGLE_CLIENT_SECRETS_JSON** and **GoogleGmailAction** OAuth; "
        "optional **EMAIL_DEFAULT_SENDER** (else mailbox profile address). **SendGrid:** "
        "**SENDGRID_API_KEY** and **EMAIL_DEFAULT_SENDER**.\n\n"
        "**Body:** **to** (email), **subject** (optional), **html_content** / "
        "**htmlContent** and/or **text_content** / **textContent**, optional "
        "**to_name**, **sender_email**, **sender_name**, **reply_to**, **headers**, "
        "**attachments** (list of **filename**, **content_base64**, optional **type**). "
        "**SendGrid only:** optional raw **mail** (v3) plus **mail_overrides** — "
        "if **mail** is set, other fields are ignored unless merged via overrides."
    ),
)
async def email_send(
    action_id: str,
    data: Dict[str, Any],
) -> Dict[str, Any]:
    """Authenticated send using the canonical message or SendGrid raw mail."""
    action = await get_email_action(action_id)
    if not action.is_configured():
        raise ValidationError(
            message="EmailAction is not configured",
            details={"issues": action._config_issues()},
        )

    prov = (action.provider or "gmail").strip().lower()

    if prov == "sendgrid" and isinstance(data.get("mail"), dict):
        client = await action.api()
        if not isinstance(client, SendGridEmailProvider):
            raise ValidationError(
                message="Expected SendGridEmailProvider",
                details={"action_id": action_id},
            )
        overrides = data.get("mail_overrides")
        if overrides is not None and not isinstance(overrides, dict):
            raise ValidationError(
                message="mail_overrides must be an object",
                details={"action_id": action_id},
            )
        result = await client.send_mail_v3(
            dict(data["mail"]),
            mail_overrides=overrides if isinstance(overrides, dict) else None,
        )
        if not result.get("ok"):
            raise ValidationError(
                message=str(result.get("error") or "send failed"),
                details={"result": result},
            )
        return {"success": True, "result": result}

    to = (data.get("to") or "").strip()
    if not to or "@" not in to:
        raise ValidationError(
            message="Field 'to' must be a valid email address",
            details={"action_id": action_id},
        )
    subject = (data.get("subject") or "").strip() or "Message"
    html_content = data.get("htmlContent") or data.get("html_content")
    text_content = data.get("textContent") or data.get("text_content")
    if html_content:
        html_content = str(html_content)
    if text_content:
        text_content = str(text_content)
    if not html_content and not text_content:
        raise ValidationError(
            message="Provide htmlContent or textContent (or mail for SendGrid)",
            details={"action_id": action_id},
        )

    sender_email = (data.get("sender_email") or "").strip()
    sender_name = data.get("sender_name")
    if not sender_email:
        resolved_email, resolved_name = await action.resolve_outbound_sender()
        sender_email = resolved_email
        if sender_name is None:
            sender_name = resolved_name
    if not sender_email:
        raise ValidationError(
            message=(
                "sender_email, EMAIL_DEFAULT_SENDER, or Gmail OAuth profile address is required"
            ),
            details={"action_id": action_id},
        )

    to_name = data.get("to_name")
    if to_name is not None:
        to_name = str(to_name).strip() or None
    reply_to = data.get("reply_to")
    if reply_to is not None:
        reply_to = str(reply_to).strip() or None

    if sender_name is None:
        sender_name = EmailAction._effective_sender_name(action)
    elif isinstance(sender_name, str):
        sender_name = sender_name.strip() or None

    headers = data.get("headers")
    if headers is not None:
        if not isinstance(headers, dict):
            raise ValidationError(
                message="headers must be an object with string values",
                details={"action_id": action_id},
            )
        headers = {str(k): str(v) for k, v in headers.items()}
    else:
        headers = None

    attachments = normalize_attachments_from_body(data.get("attachments"))

    canonical = CanonicalSendMessage(
        to_email=to,
        to_name=to_name,
        subject=subject,
        html_content=html_content,
        text_content=text_content,
        sender_email=sender_email,
        sender_name=sender_name,
        reply_to=reply_to,
        headers=headers,
        attachments=attachments,
    )

    provider = await action.api()
    result = await provider.send_canonical(canonical)
    if not result.get("ok"):
        raise ValidationError(
            message=str(result.get("error") or "send failed"),
            details={"result": result},
        )
    return {"success": True, "result": result}


@endpoint(
    "/actions/{action_id}/email/gmail/poll-once",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Email Action"],
    summary="Poll Gmail inbox once (EmailAction provider=gmail)",
    description=(
        "Lists messages with the action’s **gmail_list_query**, then processes the first "
        "that passes access control (same rules as inbound webhooks). That message is "
        "marked read before the agent runs. Admin only."
    ),
    response=success_response(
        data={
            "result": ResponseField(
                field_type=Dict[str, Any],
                description="Poll outcome (scanned, processed, errors, etc.)",
                example={"scanned": 1, "processed": True},
            ),
            "success": ResponseField(field_type=bool, example=True),
        }
    ),
)
async def email_gmail_poll_once(action_id: str) -> Dict[str, Any]:
    action = await get_email_action(action_id)
    if not action.is_configured():
        raise ValidationError(
            message="EmailAction is not configured",
            details={"issues": action._config_issues()},
        )
    result = await poll_gmail_inbox_once(action)
    return {"success": True, "result": result}


@endpoint(
    "/actions/{action_id}/email/inbound-webhook/register",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Email Action"],
    summary="Register inbound-parse webhook via provider API (SendGrid: not supported)",
    description=(
        "Attempts **create_inbound_webhook** on the configured provider. "
        "**SendGrid** does not support API registration (returns not supported). "
        "**Gmail** uses polling, not webhooks. Prefer configuring SendGrid Inbound Parse "
        "in the Twilio/SendGrid console. Admin only."
    ),
)
async def email_register_inbound_webhook(
    action_id: str,
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Provider-specific inbound registration (mostly a no-op for current providers)."""
    action = await get_email_action(action_id)
    prov = (action.provider or "gmail").strip().lower()
    if prov == "gmail":
        raise ValidationError(
            message="Gmail inbound uses polling, not webhook registration",
            details={"action_id": action_id},
        )
    if not action.is_configured():
        raise ValidationError(
            message="EmailAction is not configured",
            details={"issues": action._config_issues()},
        )

    payload = data if isinstance(data, dict) else {}
    domain = (payload.get("domain") or "").strip() or "inbound"

    try:
        url = await action.get_webhook_url(regenerate=bool(payload.get("regenerate")))
    except SpatialValidationError as e:
        raise ValidationError(
            message=str(e),
            details={"action_id": action_id},
        ) from e

    description = (payload.get("description") or "").strip() or "jvagent inbound email"

    provider_client = await action.api()
    reg = await provider_client.create_inbound_webhook(
        url=url,
        domain=domain,
        description=description,
    )
    if not reg.get("ok"):
        raise ValidationError(
            message=str(reg.get("error") or "registration failed"),
            details={"reg": reg},
        )
    return {"success": True, "webhook_url_used": url, "registration": reg}
