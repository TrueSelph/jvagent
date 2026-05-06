from typing import Any, ClassVar, Dict, List

from jvagent.action.email_action.canonical_send_builder import (
    build_canonical_send_message,
    resolve_outbound_sender_for_standalone_mailbox,
    standalone_mailbox_effective_sender_name,
)
from jvagent.action.email_action.modules.gmail import GmailEmailProvider

from ..google_action import GoogleAction


class GoogleGmailAction(GoogleAction):
    """Action for Google Gmail operations using OAuth2 (user-delegated credentials)."""

    API_SERVICE_NAME: ClassVar[str] = "gmail"
    API_VERSION: ClassVar[str] = "v1"
    SCOPES: ClassVar[List[str]] = [
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.modify",
    ]

    async def send_email(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Send mail using the same canonical payload as EmailAction / HTTP ``/send``.

        ``data`` matches the inner object of ``{"data": { ... }}`` (``to``, optional
        ``subject``, ``html_content`` / ``text_content``, attachments, etc.).

        Raises:
            ValidationError: From :func:`~jvagent.action.email_action.canonical_send_builder.build_canonical_send_message` on invalid input.
        """
        canonical = await build_canonical_send_message(
            data,
            action_id=self.id,
            resolve_sender=lambda: resolve_outbound_sender_for_standalone_mailbox(self),
            effective_sender_name=standalone_mailbox_effective_sender_name,
        )
        provider = GmailEmailProvider(gmail_action=self)
        return await provider.send_canonical(canonical)

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

    async def get_tools(self) -> List[Any]:
        from jvagent.tooling.tool import Tool

        action = self

        async def _send(to: str, subject: str, body: str = "") -> str:
            import json

            data: Dict[str, Any] = {"to": to, "subject": subject}
            if body:
                data["html_content"] = body
            result = await action.send_email(data)
            return json.dumps(result, indent=2)

        async def _list(query: str, limit: int = 10) -> str:
            import json

            results = await action.list_messages(query, max_results=limit)
            return json.dumps(results, indent=2)

        return [
            Tool(
                name="gmail__send",
                description="Send an email via Gmail.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "to": {
                            "type": "string",
                            "description": "Recipient email address.",
                        },
                        "subject": {"type": "string", "description": "Email subject."},
                        "body": {
                            "type": "string",
                            "description": "HTML body of the email.",
                        },
                    },
                    "required": ["to", "subject"],
                },
                execute=_send,
            ),
            Tool(
                name="gmail__search",
                description="Search Gmail messages matching a query.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Gmail search query.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results (default 10).",
                            "default": 10,
                        },
                    },
                    "required": ["query"],
                },
                execute=_list,
            ),
        ]
