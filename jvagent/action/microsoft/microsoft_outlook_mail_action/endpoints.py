"""API endpoints for Microsoft Outlook mail action."""

import logging
from typing import Any, Dict, List

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ValidationError

from jvagent.action.utils.endpoint_helpers import require_typed_action

from .microsoft_outlook_mail_action import MicrosoftOutlookMailAction

logger = logging.getLogger(__name__)


@endpoint(
    "/actions/{action_id}/send",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Microsoft Outlook Action"],
    summary="Send an email via Microsoft Graph (canonical payload)",
    description=(
        "Same JSON body as **EmailAction** ``/email/send`` (without SendGrid-only **mail**). "
        "**to** (email), optional **subject**, **html_content** / **htmlContent** and/or "
        "**text_content** / **textContent**, optional **to_name**, **sender_email**, "
        "**sender_name**, **reply_to**, **headers**, **attachments**. "
        "Sends as the signed-in user (**/me/sendMail**). "
        "Do not name query fields **user_id** — use **mailbox_user_id** on list/profile if needed."
    ),
    response=success_response(
        data={
            "result": ResponseField(
                field_type=Dict[str, Any],
                description="Send outcome; Graph sendMail returns 204/202 with no message body (ok flag)",
                example={"ok": True},
            ),
            "success": ResponseField(
                field_type=bool,
                description="Whether the email was sent successfully",
                example=True,
            ),
        }
    ),
)
async def send_outlook_mail(action_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Send email via Microsoft Graph using the canonical message shape."""
    action = await require_typed_action(
        action_id,
        MicrosoftOutlookMailAction,
        not_found_message=f"Microsoft Outlook mail action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a MicrosoftOutlookMailAction",
    )

    try:
        result = await action.send_email(data)
        if not result.get("ok"):
            raise ValidationError(
                message=str(result.get("error") or "send failed"),
                details={"result": result, "action_id": action_id},
            )
        return {"success": True, "result": result}
    except ValidationError:
        raise
    except Exception as e:
        logger.error("Failed to send Outlook mail message: %s", e, exc_info=True)
        raise ValidationError(
            message=f"Failed to send email: {e}",
            details={"action_id": action_id},
        )


@endpoint(
    "/actions/{action_id}/list",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Microsoft Outlook Action"],
    summary="List Outlook mail messages",
    response=success_response(
        data={
            "messages": ResponseField(
                field_type=List[Dict[str, Any]],
                description="List of message stubs from the mailbox",
                example=[
                    {"id": "AQMkADAw...", "threadId": "AAQkADAw..."}  # pragma: allowlist secret
                ],
            ),
            "success": ResponseField(
                field_type=bool,
                description="Whether the retrieval was successful",
                example=True,
            ),
        }
    ),
)
async def list_outlook_mail_messages(
    action_id: str,
    query: str = "",
    max_results: int = 10,
    mailbox_user_id: str = "me",
) -> Dict[str, Any]:
    """List messages in the mailbox (Microsoft Graph).

    **mailbox_user_id** is reserved for API parity; the client always uses **/me**.
    """
    action = await require_typed_action(
        action_id,
        MicrosoftOutlookMailAction,
        not_found_message=f"Microsoft Outlook mail action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a MicrosoftOutlookMailAction",
    )

    messages = await action.list_messages(
        query=query, max_results=max_results, user_id=mailbox_user_id
    )
    return {"success": True, "messages": messages}


@endpoint(
    "/actions/{action_id}/profile",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Microsoft Outlook Action"],
    summary="Get Outlook / Microsoft 365 mailbox profile",
    response=success_response(
        data={
            "profile": ResponseField(
                field_type=Dict[str, Any],
                description="User and mailbox profile from Microsoft Graph",
                example={
                    "mail": "user@contoso.com",
                    "displayName": "Example User",
                },
            ),
            "success": ResponseField(
                field_type=bool,
                description="Whether the retrieval was successful",
                example=True,
            ),
        }
    ),
)
async def get_outlook_mail_profile(
    action_id: str, mailbox_user_id: str = "me"
) -> Dict[str, Any]:
    """Get mailbox profile for the authenticated user (**/me**)."""
    action = await require_typed_action(
        action_id,
        MicrosoftOutlookMailAction,
        not_found_message=f"Microsoft Outlook mail action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a MicrosoftOutlookMailAction",
    )

    profile = await action.get_profile(user_id=mailbox_user_id)
    return {"success": True, "profile": profile}
