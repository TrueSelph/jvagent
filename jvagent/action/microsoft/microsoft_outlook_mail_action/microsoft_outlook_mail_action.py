from typing import Any, ClassVar, Dict, List, Optional
from urllib.parse import quote

from jvagent.action.email_action.canonical_send_builder import (
    build_canonical_send_message,
    resolve_outbound_sender_for_standalone_mailbox,
    standalone_mailbox_effective_sender_name,
)
from jvagent.action.email_action.modules.outlook import OutlookEmailProvider

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
            params["$search"] = f'"{query}"'
            headers = {"ConsistencyLevel": "eventual"}
        resp = await self.graph_request(
            "GET",
            "/me/messages",
            params=params,
            headers=headers,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Graph list messages failed: {resp.status_code} {resp.text[:400]}")
        data = resp.json()
        raw = data.get("value") or []
        out: List[Dict[str, Any]] = []
        for m in raw[: max_results]:
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
