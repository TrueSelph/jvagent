"""HTTP endpoints for EmailAction (Gmail/Outlook inbox webhook + SendGrid inbound).

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
from jvagent.action.utils.endpoint_helpers import require_typed_action
from jvagent.core.agent import Agent

from .canonical_send_builder import build_canonical_send_message
from .email_action import EmailAction
from .email_webhook_helpers import (
    inbound_email_access_denied_action,
    process_email_interaction_async,
)
from .gmail_inbox import fetch_next_gmail_inbox_message
from .inbound.sendgrid import parse_sendgrid_inbound
from .modules.sendgrid import SendGridEmailProvider
from .outlook_inbox import fetch_next_outlook_inbox_message

logger = logging.getLogger(__name__)


async def _agent_and_email_action_for_webhook(
    agent_id: str,
) -> tuple[Agent, EmailAction]:
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
        "**SendGrid:** multipart or urlencoded Inbound Parse (**from**, **subject**, "
        "**text**, **html**, **headers**, attachments). **Gmail** and **Outlook:** POST with "
        "**api_key** triggers **one** inbox fetch (same as admin fetch-once routes); request body "
        "is ignored. Authenticate with **api_key** query or header."
    ),
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="received"),
            "response": ResponseField(
                field_type=Optional[str], example=None, default=None
            ),
            "gmail_inbound": ResponseField(
                field_type=Optional[Dict[str, Any]],
                description="When provider is gmail: fetch outcome (scanned, processed, …)",
                default=None,
            ),
            "outlook_inbound": ResponseField(
                field_type=Optional[Dict[str, Any]],
                description="When provider is outlook: fetch outcome (scanned, processed, …)",
                default=None,
            ),
        }
    ),
)
async def email_interact_webhook(request: Request, agent_id: str) -> Any:
    """Handle POST: SendGrid inbound form or Gmail/Outlook one-shot inbox fetch."""
    agent, email_action = await _agent_and_email_action_for_webhook(agent_id)
    email_action._apply_env_defaults()

    provider = (email_action.provider or "gmail").strip().lower()
    ct = (request.headers.get("content-type") or "").lower()

    if provider == "gmail":
        if not email_action.is_configured():
            raise HTTPException(
                status_code=400,
                detail="EmailAction is not configured for Gmail inbound",
            )
        result = await fetch_next_gmail_inbox_message(email_action, agent=agent)
        if result.get("processed"):
            return {
                "status": "received",
                "response": None,
                "gmail_inbound": result,
                "outlook_inbound": None,
            }
        return {
            "status": "ignored",
            "response": None,
            "gmail_inbound": result,
            "outlook_inbound": None,
        }

    if provider == "outlook":
        if not email_action.is_configured():
            raise HTTPException(
                status_code=400,
                detail="EmailAction is not configured for Outlook inbound",
            )
        result = await fetch_next_outlook_inbox_message(email_action, agent=agent)
        if result.get("processed"):
            return {
                "status": "received",
                "response": None,
                "gmail_inbound": None,
                "outlook_inbound": result,
            }
        return {
            "status": "ignored",
            "response": None,
            "gmail_inbound": None,
            "outlook_inbound": result,
        }

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
        return {
            "status": "ignored",
            "response": None,
            "gmail_inbound": None,
            "outlook_inbound": None,
        }

    access_control_action = await agent.get_access_control_action()

    async def handle_one(
        user_id: str, _utterance: str, data_dict: Dict[str, Any]
    ) -> None:
        denied = await inbound_email_access_denied_action(
            access_control_action, user_id
        )
        if denied:
            log_access_denied(
                agent_id=agent_id,
                user_id=user_id,
                channel="email",
                action_label=denied,
                stage="email_inbound",
            )
            return

        inbound = data_dict.get("email_inbound") or {}
        sender_name = inbound.get("FromName") if isinstance(inbound, dict) else None

        task = await create_task(
            process_email_interaction_async(
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
                user_id,
                agent_id,
                agent,
                data_dict,
                sender_name=sender_name,
            )

    for user_id, utterance, data_dict in tuples:
        await handle_one(user_id, utterance, data_dict)

    return {
        "status": "received",
        "response": None,
        "gmail_inbound": None,
        "outlook_inbound": None,
    }


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
    action = await require_typed_action(
        action_id,
        EmailAction,
        not_found_message=f"Email action not found: {action_id}",
        wrong_type_message=f"Action is not an EmailAction: {action_id}",
    )
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
        "**users.getProfile**. For **outlook**, checks **MicrosoftOutlookMailAction** and "
        "**GET /me**. Admin only."
    ),
)
async def email_health(action_id: str) -> Dict[str, Any]:
    action = await require_typed_action(
        action_id,
        EmailAction,
        not_found_message=f"Email action not found: {action_id}",
        wrong_type_message=f"Action is not an EmailAction: {action_id}",
    )
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
        "optional **EMAIL_DEFAULT_SENDER** (else mailbox profile address). **Outlook:** "
        "**MICROSOFT_CLIENT_ID** and **MicrosoftOutlookMailAction** OAuth; optional "
        "**EMAIL_DEFAULT_SENDER** (else mailbox profile). **SendGrid:** "
        "**SENDGRID_API_KEY** and **EMAIL_DEFAULT_SENDER**.\n\n"
        "**Body:** **to** (email), **subject** (optional), **html_content** / "
        "**htmlContent** and/or **text_content** / **textContent**, optional "
        "**cc** / **ccRecipients** (list of emails or `{email,name}`), **to_name**, "
        "**sender_email**, **sender_name**, **reply_to**, **headers**, "
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
    action = await require_typed_action(
        action_id,
        EmailAction,
        not_found_message=f"Email action not found: {action_id}",
        wrong_type_message=f"Action is not an EmailAction: {action_id}",
    )
    if not action.is_configured():
        raise ValidationError(
            message="EmailAction is not configured",
            details={"issues": action._config_issues()},
        )

    prov = (action.provider or "gmail").strip().lower()
    to_preview = (
        data.get("to")
        or data.get("to_email")
        or (data.get("personalizations") or [{}])[0].get("to")
        if isinstance(data.get("personalizations"), list)
        else None
    )
    logger.info(
        "email_send: action_id=%s provider=%s to_field=%r keys=%s",
        action_id,
        prov,
        to_preview,
        sorted(data.keys()),
    )

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

    canonical = await build_canonical_send_message(
        data,
        action_id=action_id,
        resolve_sender=action.resolve_outbound_sender,
        effective_sender_name=lambda: EmailAction._effective_sender_name(action),
    )

    logger.info(
        "email_send: canonical action_id=%s provider=%s from=%r to=%r subject=%r "
        "cc=%s",
        action_id,
        prov,
        canonical.sender_email,
        canonical.to_email,
        canonical.subject,
        [r.email for r in (canonical.cc or [])],
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
    "/actions/{action_id}/email/gmail/fetch-inbox-once",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Email Action"],
    summary="Fetch Gmail inbox once (EmailAction provider=gmail)",
    description=(
        "Lists messages with the action’s **gmail_list_query**, then processes the first "
        "that passes access control (same rules as inbound webhooks). That message is "
        "marked read before the agent runs. Admin only."
    ),
    response=success_response(
        data={
            "result": ResponseField(
                field_type=Dict[str, Any],
                description="Fetch outcome (scanned, processed, errors, etc.)",
                example={"scanned": 1, "processed": True},
            ),
            "success": ResponseField(field_type=bool, example=True),
        }
    ),
)
async def email_gmail_fetch_inbox_once(action_id: str) -> Dict[str, Any]:
    action = await require_typed_action(
        action_id,
        EmailAction,
        not_found_message=f"Email action not found: {action_id}",
        wrong_type_message=f"Action is not an EmailAction: {action_id}",
    )
    if not action.is_configured():
        raise ValidationError(
            message="EmailAction is not configured",
            details={"issues": action._config_issues()},
        )
    result = await fetch_next_gmail_inbox_message(action)
    return {"success": True, "result": result}


@endpoint(
    "/actions/{action_id}/email/outlook/fetch-inbox-once",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Email Action"],
    summary="Fetch Outlook inbox once (EmailAction provider=outlook)",
    description=(
        "Lists Inbox messages with the action’s **outlook_mail_filter**, then processes the first "
        "that passes access control (same rules as inbound webhooks). That message is "
        "marked read before the agent runs. Admin only."
    ),
    response=success_response(
        data={
            "result": ResponseField(
                field_type=Dict[str, Any],
                description="Fetch outcome (scanned, processed, errors, etc.)",
                example={"scanned": 1, "processed": True},
            ),
            "success": ResponseField(field_type=bool, example=True),
        }
    ),
)
async def email_outlook_fetch_inbox_once(action_id: str) -> Dict[str, Any]:
    action = await require_typed_action(
        action_id,
        EmailAction,
        not_found_message=f"Email action not found: {action_id}",
        wrong_type_message=f"Action is not an EmailAction: {action_id}",
    )
    if not action.is_configured():
        raise ValidationError(
            message="EmailAction is not configured",
            details={"issues": action._config_issues()},
        )
    result = await fetch_next_outlook_inbox_message(action)
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
        "**Gmail** and **Outlook** inbound are triggered by POSTing to the shared email webhook "
        "URL (with **api_key**); there is no provider API to register here. Prefer configuring "
        "SendGrid Inbound Parse in the Twilio/SendGrid console. Admin only."
    ),
)
async def email_register_inbound_webhook(
    action_id: str,
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Provider-specific inbound registration (mostly a no-op for current providers)."""
    action = await require_typed_action(
        action_id,
        EmailAction,
        not_found_message=f"Email action not found: {action_id}",
        wrong_type_message=f"Action is not an EmailAction: {action_id}",
    )
    prov = (action.provider or "gmail").strip().lower()
    if prov in ("gmail", "outlook"):
        raise ValidationError(
            message=(
                "Gmail and Outlook inbound are triggered via the shared webhook URL "
                "(POST with api_key); there is no provider API registration step"
            ),
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
