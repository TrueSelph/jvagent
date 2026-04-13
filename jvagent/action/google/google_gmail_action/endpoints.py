"""API endpoints for Google Gmail action."""

import logging
from typing import Any, Dict, List

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ValidationError

from jvagent.action.utils.endpoint_helpers import require_typed_action

from .google_gmail_action import GoogleGmailAction

logger = logging.getLogger(__name__)


@endpoint(
    "/actions/{action_id}/send",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Google Gmail Action"],
    summary="Send an email via Gmail (canonical payload)",
    description=(
        "Same JSON body as **EmailAction** ``/email/send`` (without SendGrid-only **mail**). "
        "**to** (email), optional **subject**, **html_content** / **htmlContent** and/or "
        "**text_content** / **textContent**, optional **to_name**, **sender_email**, "
        "**sender_name**, **reply_to**, **headers**, **attachments**. "
        "Uses Gmail API with **userId=me** (OAuth mailbox). "
        "Do not name query/body fields **user_id** — use **mailbox_user_id** only on list/profile."
    ),
    response=success_response(
        data={
            "result": ResponseField(
                field_type=Dict[str, Any],
                description="The sent message metadata returned by the Gmail API",
                example={
                    "id": "18e2f3a4b5c6d7e8",  # pragma: allowlist secret
                    "threadId": "18e2f3a4b5c6d7e8",  # pragma: allowlist secret
                    "labelIds": ["SENT"],
                },
            ),
            "success": ResponseField(
                field_type=bool,
                description="Whether the email was sent successfully",
                example=True,
            ),
        }
    ),
)
async def send_gmail(action_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Send email via Gmail using the canonical message shape (see EmailAction email/send)."""
    action = await require_typed_action(
        action_id,
        GoogleGmailAction,
        not_found_message=f"Google Gmail action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a GoogleGmailAction",
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
        logger.error(f"Failed to send Gmail message: {e}", exc_info=True)
        raise ValidationError(
            message=f"Failed to send email: {str(e)}",
            details={"action_id": action_id},
        )


@endpoint(
    "/actions/{action_id}/list",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Google Gmail Action"],
    summary="List Gmail messages",
    response=success_response(
        data={
            "messages": ResponseField(
                field_type=List[Dict[str, Any]],
                description="List of message stubs matching the query",
                example=[
                    {
                        "id": "18e2f3a4b5c6d7e8",  # pragma: allowlist secret
                        "threadId": "18e2f3a4b5c6d7e8",  # pragma: allowlist secret
                    },
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
async def list_gmail_messages(
    action_id: str,
    query: str = "",
    max_results: int = 10,
    mailbox_user_id: str = "me",
) -> Dict[str, Any]:
    """List messages in a Gmail inbox.

    **Args:**

    - mailbox_user_id: Gmail API **userId** (default **me**). Not the spatial platform user id.

    """
    action = await require_typed_action(
        action_id,
        GoogleGmailAction,
        not_found_message=f"Google Gmail action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a GoogleGmailAction",
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
    tags=["Google Gmail Action"],
    summary="Get Gmail profile info",
    response=success_response(
        data={
            "profile": ResponseField(
                field_type=Dict[str, Any],
                description="Gmail profile information for the authenticated user",
                example={
                    "emailAddress": "user@example.com",
                    "messagesTotal": 1234,
                    "threadsTotal": 456,
                    "historyId": "7890",
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
async def get_gmail_profile(
    action_id: str, mailbox_user_id: str = "me"
) -> Dict[str, Any]:
    """Get the Gmail profile for an authenticated mailbox userId (default me)."""
    action = await require_typed_action(
        action_id,
        GoogleGmailAction,
        not_found_message=f"Google Gmail action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a GoogleGmailAction",
    )

    profile = await action.get_profile(user_id=mailbox_user_id)
    return {"success": True, "profile": profile}
