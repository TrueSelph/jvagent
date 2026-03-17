"""API endpoints for Google Gmail action."""

import logging
from typing import Any, Dict, List, Optional

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError

from .google_gmail_action import GoogleGmailAction

logger = logging.getLogger(__name__)


async def _get_gmail_action(action_id: str) -> Optional[GoogleGmailAction]:
    """Resolve action by ID; validate it is a GoogleGmailAction.

    **Args:**

    - action_id: ID of the action to retrieve

    **Returns:**

    GoogleGmailAction instance if found and valid, else None
    """
    action = await GoogleGmailAction.get(action_id)
    if action and isinstance(action, GoogleGmailAction):
        return action
    return None


@endpoint(
    "/actions/{action_id}/send",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Google Gmail Action"],
    summary="Send an email via Gmail",
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
async def send_gmail(
    action_id: str,
    to: str,
    subject: str,
    body: str,
    user_id: str = "me",
) -> Dict[str, Any]:
    """Send an email via the Gmail API.

    **Overview:**

    Composes and sends an email from the authenticated Gmail account to the
    specified recipient.

    **Args:**

    - action_id: ID of the Google Gmail action
    - to: Recipient email address (e.g., \"recipient@example.com\")
    - subject: Subject line of the email
    - body: Plain-text body content of the email
    - user_id: Gmail user ID to send from. Use \"me\" for the authenticated user. default=\"me\"

    **Returns:**

    Dictionary containing:
    - **result**: The Gmail API response including the message id, threadId, and labelIds
    - **success**: Always True if the email is sent

    **Raises:**

    - ResourceNotFoundError: If the Google Gmail action is not found
    - ValidationError: If the send operation fails
    """
    action = await _get_gmail_action(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Google Gmail action {action_id} not found",
            details={"action_id": action_id},
        )

    try:
        result = await action.send_email(
            to=to,
            subject=subject,
            body=body,
            user_id=user_id,
        )
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Failed to send Gmail message: {e}", exc_info=True)
        raise ValidationError(
            message=f"Failed to send email: {str(e)}",
            details={"action_id": action_id, "to": to, "subject": subject},
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
    user_id: str = "me",
) -> Dict[str, Any]:
    """List messages in a Gmail inbox.

    **Overview:**

    Retrieves a list of message stubs (id and threadId) from the authenticated
    Gmail inbox. Use the `query` parameter to filter messages using Gmail search syntax.

    **Args:**

    - action_id: ID of the Google Gmail action
    - query: Gmail search query string (e.g., \"is:unread from:boss@example.com\"). default=\"\" (all messages)
    - max_results: Maximum number of messages to return. default=10
    - user_id: Gmail user ID to query. Use \"me\" for the authenticated user. default=\"me\"

    **Returns:**

    Dictionary containing:
    - **messages**: List of message stubs with id and threadId fields
    - **success**: Always True if retrieval completes

    **Raises:**

    - ResourceNotFoundError: If the Google Gmail action is not found
    """
    action = await _get_gmail_action(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Google Gmail action {action_id} not found",
            details={"action_id": action_id},
        )

    messages = await action.list_messages(
        query=query, max_results=max_results, user_id=user_id
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
async def get_gmail_profile(action_id: str, user_id: str = "me") -> Dict[str, Any]:
    """Get the Gmail profile for an authenticated user.

    **Overview:**

    Retrieves profile information for the Gmail account associated with this action,
    including email address and mailbox statistics.

    **Args:**

    - action_id: ID of the Google Gmail action
    - user_id: Gmail user ID to retrieve profile for. Use \"me\" for the authenticated user. default=\"me\"

    **Returns:**

    Dictionary containing:
    - **profile**: Gmail profile object with emailAddress, messagesTotal, threadsTotal, and historyId
    - **success**: Always True if retrieval completes

    **Raises:**

    - ResourceNotFoundError: If the Google Gmail action is not found
    """
    action = await _get_gmail_action(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Google Gmail action {action_id} not found",
            details={"action_id": action_id},
        )

    profile = await action.get_profile(user_id=user_id)
    return {"success": True, "profile": profile}
