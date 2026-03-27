"""HTTP endpoints for SendGrid Mail Send action."""

import logging
from typing import Any, Dict, Optional

from fastapi import Body
from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ValidationError

from jvagent.action.utils.endpoint_helpers import require_typed_action

from .sendgrid_action import SendGridAction, _merge_mail_overrides

logger = logging.getLogger(__name__)


async def _require_sendgrid_action(action_id: str) -> SendGridAction:
    return await require_typed_action(
        action_id,
        SendGridAction,
        not_found_message=f"SendGrid action with ID '{action_id}' not found",
        wrong_type_message=f"Action '{action_id}' is not a SendGridAction",
    )


@endpoint(
    "/actions/{action_id}/sendgrid/health",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["SendGrid Action"],
    summary="SendGrid action health check",
)
async def sendgrid_health(action_id: str) -> Dict[str, Any]:
    """Check API key against SendGrid ``/user/profile``."""
    action = await _require_sendgrid_action(action_id)
    health = await action.healthcheck()
    if health is True:
        return {"healthy": True, "details": None}
    if isinstance(health, dict):
        return {
            "healthy": health.get("healthy", False),
            "details": health,
        }
    return {"healthy": bool(health), "details": None}


@endpoint(
    "/actions/{action_id}/sendgrid/send",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["SendGrid Action"],
    summary="Send email via SendGrid v3 (raw mail or convenience fields)",
    response=success_response(
        data={
            "result": ResponseField(
                field_type=Dict[str, Any],
                description="Send result: status_code, X-Message-Id when present",
                example={
                    "success": True,
                    "status_code": 202,
                    "message_id": "abc123",
                },
            ),
            "success": ResponseField(
                field_type=bool,
                description="True when SendGrid accepts the message",
                example=True,
            ),
        }
    ),
)
async def sendgrid_send(
    action_id: str,
    body: Dict[str, Any] = Body(
        ...,
        description=(
            "Either provide ``mail`` (full v3 JSON) or convenience fields: "
            "``to`` (required), ``subject``, ``text``, ``html``, ``cc``, ``bcc``, "
            "``from`` (object), ``reply_to``, ``headers``, ``categories``, "
            "``template_id``, ``dynamic_template_data``, ``attachments``. "
            "Optional ``mail_overrides`` deep-merges into the built or raw payload. "
            "Successful sends usually return HTTP 202 with an empty body; "
            "``X-Message-Id`` is returned when present. Total message size is limited "
            "by SendGrid (on the order of tens of MB including attachments)."
        ),
    ),
) -> Dict[str, Any]:
    """Send one message via SendGrid Mail Send API v3."""
    action = await _require_sendgrid_action(action_id)

    overrides: Optional[Dict[str, Any]] = None
    if "mail_overrides" in body and body["mail_overrides"] is not None:
        if not isinstance(body["mail_overrides"], dict):
            raise ValidationError(
                message="mail_overrides must be an object",
                details={"action_id": action_id},
            )
        overrides = body["mail_overrides"]

    if "mail" in body and body["mail"] is not None:
        if not isinstance(body["mail"], dict):
            raise ValidationError(
                message="mail must be an object",
                details={"action_id": action_id},
            )
        merged = _merge_mail_overrides(dict(body["mail"]), overrides)
        try:
            result = await action.send_mail_v3(merged)
        except Exception as e:
            logger.error("SendGrid send_mail_v3 failed: %s", e, exc_info=True)
            raise ValidationError(
                message=f"Failed to send email: {e}",
                details={"action_id": action_id},
            ) from e
        return {"success": True, "result": result}

    to = body.get("to")
    if to is None:
        raise ValidationError(
            message="Request must include ``mail`` (v3 payload) or ``to`` recipients",
            details={"action_id": action_id},
        )

    mail_from = body.get("from")
    if mail_from is not None and not isinstance(mail_from, dict):
        raise ValidationError(
            message="from must be an object with email (and optional name)",
            details={"action_id": action_id},
        )

    try:
        result = await action.send_mail(
            to=to,
            subject=body.get("subject"),
            text=body.get("text"),
            html=body.get("html"),
            cc=body.get("cc"),
            bcc=body.get("bcc"),
            mail_from=mail_from,
            reply_to=body.get("reply_to"),
            headers=body.get("headers"),
            categories=body.get("categories"),
            template_id=body.get("template_id"),
            dynamic_template_data=body.get("dynamic_template_data"),
            attachments=body.get("attachments"),
            mail_overrides=overrides,
        )
    except ValidationError:
        raise
    except Exception as e:
        logger.error("SendGrid send_mail failed: %s", e, exc_info=True)
        raise ValidationError(
            message=f"Failed to send email: {e}",
            details={"action_id": action_id},
        ) from e
    return {"success": True, "result": result}
