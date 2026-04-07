import base64
import logging
from email.mime.text import MIMEText
from typing import Any, ClassVar, Dict, List, Optional

from ..google_action import GoogleAction

logger = logging.getLogger(__name__)


class GoogleGmailAction(GoogleAction):
    """Action for Google Gmail operations using OAuth2 (user-delegated credentials)."""

    API_SERVICE_NAME: ClassVar[str] = "gmail"
    API_VERSION: ClassVar[str] = "v1"
    SCOPES: ClassVar[List[str]] = [
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.modify",
    ]

    async def send_email(
        self, to: str, subject: str, body: str, user_id: str = "me"
    ) -> Dict[str, Any]:
        """Send an email via Gmail API."""
        service = await self.get_service()

        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        return (
            service.users().messages().send(userId=user_id, body={"raw": raw}).execute()
        )

    async def list_messages(
        self, query: str = "", max_results: int = 10, user_id: str = "me"
    ) -> List[Dict[str, Any]]:
        """List messages in Gmail inbox."""
        service = await self.get_service()
        results = (
            service.users()
            .messages()
            .list(userId=user_id, q=query, maxResults=max_results)
            .execute()
        )
        return results.get("messages", [])

    async def get_profile(self, user_id: str = "me") -> Dict[str, Any]:
        """Get user Gmail profile."""
        service = await self.get_service()
        return service.users().getProfile(userId=user_id).execute()

    async def get_message(
        self,
        message_id: str,
        *,
        user_id: str = "me",
        fmt: str = "full",
    ) -> Dict[str, Any]:
        """Fetch one message by id (format ``full`` or ``raw``)."""
        service = await self.get_service()
        return (
            service.users()
            .messages()
            .get(userId=user_id, id=message_id, format=fmt)
            .execute()
        )

    async def mark_read(self, message_id: str, user_id: str = "me") -> Dict[str, Any]:
        """Remove UNREAD label (mark as read)."""
        service = await self.get_service()
        return (
            service.users()
            .messages()
            .modify(
                userId=user_id,
                id=message_id,
                body={"removeLabelIds": ["UNREAD"]},
            )
            .execute()
        )
