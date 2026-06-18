from typing import Annotated, Any, ClassVar, Dict, List, Optional
from urllib.parse import quote

from jvagent.action.email_action.canonical_send_builder import (
    build_canonical_send_message,
    resolve_outbound_sender_for_standalone_mailbox,
    standalone_mailbox_effective_sender_name,
)
from jvagent.action.email_action.modules.outlook import OutlookEmailProvider
from jvagent.tooling.tool_decorator import tool

from ..microsoft_action import MicrosoftAction


class MicrosoftOutlookMailAction(MicrosoftAction):
    """Send and read mail via Microsoft Graph (Outlook / Exchange Online)."""

    SCOPES: ClassVar[List[str]] = [
        "offline_access",
        "User.Read",
        "Mail.Read",
        "Mail.ReadWrite",
        "Mail.Send",
    ]

    _MESSAGE_SELECT_FIELDS = (
        "subject,body,from,toRecipients,conversationId,internetMessageId,"
        "internetMessageHeaders,hasAttachments,id"
    )

    async def send_email(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Send mail using the same canonical payload as EmailAction / HTTP ``/send``.

        ``data`` matches the inner object of ``{"data": { ... }}``.

        Raises:
            ValidationError: From :func:`~jvagent.action.email_action.canonical_send_builder.build_canonical_send_message` on invalid input.
        """
        canonical = await build_canonical_send_message(
            data,
            action_id=self.id,
            resolve_sender=lambda: resolve_outbound_sender_for_standalone_mailbox(self),
            effective_sender_name=standalone_mailbox_effective_sender_name,
        )
        provider = OutlookEmailProvider(outlook_action=self)
        return await provider.send_canonical(canonical)

    async def list_messages(
        self,
        query: str = "",
        max_results: int = 10,
        user_id: str = "me",
    ) -> List[Dict[str, Any]]:
        _ = user_id
        params: Dict[str, Any] = {"$top": max(1, min(max_results, 999))}
        headers: Optional[Dict[str, str]] = None
        if query:
            escaped = query.replace("\\", "\\\\").replace('"', '\\"')
            params["$search"] = f'"{escaped}"'
            headers = {"ConsistencyLevel": "eventual"}
        resp = await self.graph_request(
            "GET",
            "/me/messages",
            params=params,
            headers=headers,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Graph list messages failed: {resp.status_code} {resp.text[:400]}"
            )
        data = resp.json()
        raw = data.get("value") or []
        out: List[Dict[str, Any]] = []
        for m in raw[:max_results]:
            out.append(
                {
                    "id": m.get("id"),
                    "threadId": m.get("conversationId"),
                }
            )
        return out

    async def list_inbox_messages(
        self,
        *,
        odata_filter: str = "isRead eq false",
        max_results: int = 25,
        user_id: str = "me",
    ) -> List[Dict[str, Any]]:
        """List messages in the Inbox folder (OData $filter, e.g. unread)."""
        _ = user_id
        top = max(1, min(int(max_results), 100))
        params: Dict[str, Any] = {
            "$top": top,
            "$orderby": "receivedDateTime desc",
            "$select": "id,conversationId",
        }
        if odata_filter and str(odata_filter).strip():
            params["$filter"] = str(odata_filter).strip()
        resp = await self.graph_request(
            "GET",
            "/me/mailFolders/inbox/messages",
            params=params,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Graph list inbox messages failed: {resp.status_code} {resp.text[:400]}"
            )
        data = resp.json()
        raw = data.get("value") or []
        out: List[Dict[str, Any]] = []
        for m in raw[:top]:
            out.append(
                {
                    "id": m.get("id"),
                    "threadId": m.get("conversationId"),
                }
            )
        return out

    async def get_message(
        self,
        message_id: str,
        *,
        user_id: str = "me",
    ) -> Dict[str, Any]:
        """Fetch one message with full body and headers for inbound processing."""
        _ = user_id
        mid = (message_id or "").strip()
        if not mid:
            raise ValueError("message_id is required")
        path = f"/me/messages/{quote(mid, safe='')}"
        result = await self.graph_json(
            "GET",
            path,
            params={"$select": self._MESSAGE_SELECT_FIELDS},
        )
        if not isinstance(result, dict):
            return {}
        return result

    async def mark_read(self, message_id: str, user_id: str = "me") -> None:
        """Mark a message as read (PATCH isRead)."""
        _ = user_id
        mid = (message_id or "").strip()
        if not mid:
            raise ValueError("message_id is required")
        path = f"/me/messages/{quote(mid, safe='')}"
        await self.graph_json("PATCH", path, json_body={"isRead": True}, ok=(200, 204))

    async def get_profile(self, user_id: str = "me") -> Dict[str, Any]:
        _ = user_id
        me = await self.graph_json("GET", "/me")
        return {
            "emailAddress": me.get("mail") or me.get("userPrincipalName"),
            "displayName": me.get("displayName"),
        }

    @tool(name="outlook__send_email")
    async def _t_send_email(
        self,
        to: Annotated[str, "Recipient email address."],
        subject: Annotated[str, "Email subject line."],
        body: Annotated[Optional[str], "HTML body of the email."] = None,
    ) -> str:
        """Send an email via Outlook."""
        import json

        body = body if body is not None else ""
        data: Dict[str, Any] = {"to": to, "subject": subject}
        if body:
            data["html_content"] = body
        return json.dumps(await self.send_email(data), indent=2)

    @tool(name="outlook__list_messages")
    async def _t_list_messages(
        self,
        query: Annotated[Optional[str], "Search query (default: '')."] = None,
        max_results: Annotated[
            int, "Maximum number of messages to return (default: 10)."
        ] = 10,
        user_id: Annotated[Optional[str], "User identifier (default: 'me')."] = None,
    ) -> str:
        """List Outlook mail messages matching a query."""
        import json

        query = query if query is not None else ""
        user_id = user_id if user_id is not None else "me"
        return json.dumps(
            await self.list_messages(query, max_results=max_results, user_id=user_id),
            indent=2,
        )

    @tool(name="outlook__list_inbox_messages")
    async def _t_list_inbox_messages(
        self,
        odata_filter: Annotated[
            Optional[str], "OData filter expression (default: 'isRead eq false')."
        ] = None,
        max_results: Annotated[
            int, "Maximum number of messages to return (default: 25)."
        ] = 25,
        user_id: Annotated[Optional[str], "User identifier (default: 'me')."] = None,
    ) -> str:
        """List Outlook inbox messages with OData filtering."""
        import json

        odata_filter = odata_filter if odata_filter is not None else "isRead eq false"
        user_id = user_id if user_id is not None else "me"
        return json.dumps(
            await self.list_inbox_messages(
                odata_filter=odata_filter,
                max_results=max_results,
                user_id=user_id,
            ),
            indent=2,
        )

    @tool(name="outlook__get_message")
    async def _t_get_message(
        self,
        message_id: Annotated[str, "The ID of the message to retrieve."],
        user_id: Annotated[Optional[str], "User identifier (default: 'me')."] = None,
    ) -> str:
        """Get a specific Outlook mail message by ID."""
        import json

        user_id = user_id if user_id is not None else "me"
        return json.dumps(await self.get_message(message_id, user_id=user_id), indent=2)

    @tool(name="outlook__mark_read")
    async def _t_mark_read(
        self,
        message_id: Annotated[str, "The ID of the message to mark as read."],
        user_id: Annotated[Optional[str], "User identifier (default: 'me')."] = None,
    ) -> str:
        """Mark an Outlook mail message as read."""
        import json

        user_id = user_id if user_id is not None else "me"
        await self.mark_read(message_id, user_id=user_id)
        return json.dumps({"success": True}, indent=2)

    @tool(name="outlook__get_profile")
    async def _t_get_profile(
        self,
        user_id: Annotated[Optional[str], "User identifier (default: 'me')."] = None,
    ) -> str:
        """Get the authenticated user's Outlook mail profile."""
        import json

        user_id = user_id if user_id is not None else "me"
        return json.dumps(await self.get_profile(user_id=user_id), indent=2)
