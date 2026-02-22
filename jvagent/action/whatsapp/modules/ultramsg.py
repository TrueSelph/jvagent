"""UltraMsg API Wrapper with WPPConnect-compatible interface (Async Version)."""

import base64
from typing import Dict, List, Optional

from .base import BaseWhatsAppAPI, MessagePayload


class UltraMsgAPI(BaseWhatsAppAPI):
    """UltraMsg API wrapper with WPPConnect-compatible interface (Async)."""

    def __init__(
        self,
        api_url: str,
        session: str,  # instance_id in UltraMsg
        token: str,
        secret_key: Optional[str] = None,
        timeout: float = 10.0,
    ):
        """
        Initialize UltraMsg API.

        Args:
            api_url: Base URL (e.g., "https://api.ultramsg.com")
            session: Instance ID from UltraMsg
            token: API token from UltraMsg
            secret_key: Not used in UltraMsg, kept for compatibility
            timeout: Request timeout in seconds
        """
        super().__init__(api_url, session, token, secret_key, timeout)

    # ========================================================================
    # INTERNAL HELPERS
    # ========================================================================

    def _build_url(self, endpoint: str) -> str:
        """Build the full URL for an endpoint."""
        # UltraMsg format: https://api.ultramsg.com/{instance_id}/{endpoint}
        return f"{self.api_url}/{self.session}/{endpoint.lstrip('/')}"

    def _build_params(self, extra_params: Optional[dict] = None) -> dict:
        """Build request parameters with authentication."""
        params = {"token": self.token}
        if extra_params:
            params.update(extra_params)
        return params

    async def send_rest_request(
        self,
        endpoint: str,
        method: str = "POST",
        data: Optional[dict] = None,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        json_body: bool = True,
        use_full_url: bool = False,
    ) -> dict:
        """Generic HTTP request to UltraMsg API."""
        url = endpoint if use_full_url else self._build_url(endpoint)

        # UltraMsg uses query params for auth, not headers
        params = self._build_params(params)

        if headers is None:
            headers = {}
        if json_body and "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"

        result = await self._make_request(url, method, headers, data, params, json_body)

        # Normalize UltraMsg response format
        # UltraMsg typically returns {"sent": true/false, "message": "..."}
        if "sent" in result:
            result["ok"] = result["sent"]
        elif "error" not in result:
            result["ok"] = True

        return result

    # ========================================================================
    # MESSAGE FORMAT TRANSLATION
    # ========================================================================

    async def parse_inbound_message(self, request: dict) -> Optional[MessagePayload]:
        """
        Parses UltraMsg webhook format and converts to standard format.

        UltraMsg webhook structure:
        {
            "event_type": "message",
            "data": {
                "id": "...",
                "from": "1234567890",
                "to": "0987654321",
                "body": "message text",
                "type": "chat",
                "fromMe": false,
                "timestamp": 1234567890,
                ...
            }
        }
        """
        try:
            event_type = request.get("event_type", "message")
            data = request.get("data", request)  # Support both formats

            # Build standard format
            payload = MessagePayload(
                message_id=str(data.get("id", "")),
                event_type=event_type,
                message_type=data.get("type", "chat"),
                author=self._clean_phone_number(
                    data.get("author", data.get("from", ""))
                ),
                sender=self._clean_phone_number(data.get("from", "")),
                receiver=self._clean_phone_number(data.get("to", "")),
                caption=data.get("caption", ""),
                fromMe=data.get("fromMe", False),
                isGroup=data.get("isGroup", False)
                or data.get("from", "").endswith("@g.us"),
                isForwarded=data.get("isForwarded", False),
                sender_name=data.get("pushname", data.get("notifyName", "")),
            )

            # Parse content by type
            if payload.message_type == "chat":
                payload.body = data.get("body", "")
            elif payload.message_type in ["image", "video", "document"]:
                payload.media = data.get("media", data.get("body", ""))
                payload.filename = data.get("filename", "")
                payload.mime_type = data.get("mimetype", "")
            elif payload.message_type == "location":
                payload.location = {
                    "latitude": data.get("latitude", ""),
                    "longitude": data.get("longitude", ""),
                }
            elif payload.message_type in ["audio", "ptt", "voice"]:
                payload.media = data.get("media", data.get("body", ""))

            return payload

        except Exception as e:
            self.logger.error(f"Error parsing UltraMsg message: {e}")
            return None

    # ========================================================================
    # SESSION MANAGEMENT
    # ========================================================================

    async def register_session(
        self,
        webhook_url: str = "",
        wait_qr_code: bool = True,
        auto_register: bool = True,
    ) -> dict:
        """
        Initialize the UltraMsg session.
        Note: UltraMsg manages sessions differently - instances are pre-configured.
        """
        status_resp = await self.status()

        if status_resp.get("ok"):
            account_status = status_resp.get("account_status", "")

            if account_status == "authenticated":
                return {
                    "status": "CONNECTED",
                    "message": "Session is already authenticated.",
                    "session": self.session,
                    "token": self.token,
                    "details": status_resp,
                }
            elif account_status in ["got qr code", "loading"]:
                qr_resp = await self.qrcode() if wait_qr_code else {}
                return {
                    "status": "AWAITING_QR_SCAN",
                    "message": "Awaiting QR Code scan.",
                    "qrcode": qr_resp.get("qrcode"),
                    "session": self.session,
                    "token": self.token,
                }
            else:
                return {
                    "status": account_status.upper(),
                    "message": f"Session status: {account_status}",
                    "details": status_resp,
                }

        return {
            "status": "ERROR",
            "message": "Could not retrieve session status.",
            "details": status_resp,
        }

    async def status(self) -> dict:
        """GET /instance/status"""
        result = await self.send_rest_request("instance/status", method="GET")

        # Add normalized status field
        if result.get("ok") and "account_status" in result:
            status_map = {
                "authenticated": "CONNECTED",
                "got qr code": "QRCODE",
                "loading": "LOADING",
                "logout": "DISCONNECTED",
            }
            result["status"] = status_map.get(
                result["account_status"], result["account_status"].upper()
            )

        return result

    async def qrcode(self) -> dict:
        """GET /instance/qr"""
        result = await self.send_rest_request("instance/qr", method="GET")

        # UltraMsg returns QR as base64 or URL
        if result.get("ok") and "qrCode" in result:
            result["qrcode"] = result["qrCode"]
            result["qrcode_base64"] = result["qrCode"]

        return result

    async def logout_session(self) -> dict:
        """GET /instance/logout"""
        return await self.send_rest_request("instance/logout", method="GET")

    async def restart_session(self) -> dict:
        """GET /instance/restart"""
        return await self.send_rest_request("instance/restart", method="GET")

    # ========================================================================
    # MESSAGING
    # ========================================================================

    async def send_message(
        self,
        phone: str,
        message: str,
        is_group: bool = False,
        is_newsletter: bool = False,
        message_id: str = "",
        options: Optional[dict] = None,
    ) -> dict:
        """POST /messages/chat"""
        data = {
            "to": phone,
            "body": message,
        }

        # UltraMsg uses specific fields for options
        if options:
            if "priority" in options:
                data["priority"] = options["priority"]
            if "referenceId" in options:
                data["referenceId"] = options["referenceId"]

        return await self.send_rest_request("messages/chat", data=data)

    async def send_image(
        self,
        phone: str,
        file_url: str = "",
        caption: str = "",
        is_group: bool = False,
        **kwargs,
    ) -> dict:
        """POST /messages/image"""
        data = {
            "to": phone,
            "image": file_url,
            "caption": caption,
        }

        if kwargs.get("referenceId"):
            data["referenceId"] = kwargs["referenceId"]

        return await self.send_rest_request("messages/image", data=data)

    async def send_file(
        self,
        phone: str,
        file_url: str = "",
        filename: str = "",
        caption: str = "",
        is_group: bool = False,
        **kwargs,
    ) -> dict:
        """POST /messages/document"""
        data = {
            "to": phone,
            "document": file_url,
            "filename": filename,
            "caption": caption,
        }

        if kwargs.get("referenceId"):
            data["referenceId"] = kwargs["referenceId"]

        return await self.send_rest_request("messages/document", data=data)

    async def send_video(
        self,
        phone: str,
        file_url: str = "",
        caption: str = "",
        is_group: bool = False,
        **kwargs,
    ) -> dict:
        """POST /messages/video"""
        data = {
            "to": phone,
            "video": file_url,
            "caption": caption,
        }

        if kwargs.get("referenceId"):
            data["referenceId"] = kwargs["referenceId"]

        return await self.send_rest_request("messages/video", data=data)

    async def send_audio(
        self, phone: str, file_url: str = "", is_group: bool = False, **kwargs
    ) -> dict:
        """POST /messages/audio"""
        data = {
            "to": phone,
            "audio": file_url,
        }

        if kwargs.get("referenceId"):
            data["referenceId"] = kwargs["referenceId"]

        return await self.send_rest_request("messages/audio", data=data)

    async def send_voice(
        self,
        phone: str,
        file_url: str,
        is_group: bool = False,
        quoted_message_id: str = "",
    ) -> dict:
        """POST /messages/voice"""
        data = {
            "to": phone,
            "audio": file_url,
        }

        return await self.send_rest_request("messages/voice", data=data)

    async def send_location(
        self,
        phone: str,
        latitude: float,
        longitude: float,
        title: str = "",
        is_group: bool = False,
    ) -> dict:
        """POST /messages/location"""
        data = {
            "to": phone,
            "address": title,
            "lat": str(latitude),
            "lng": str(longitude),
        }

        return await self.send_rest_request("messages/location", data=data)

    async def send_contact(
        self,
        phone: str,
        contact_id: str,
        is_group: bool = False,
    ) -> dict:
        """POST /messages/contact"""
        data = {
            "to": phone,
            "contact": contact_id,
        }

        return await self.send_rest_request("messages/contact", data=data)

    async def send_link(
        self,
        phone: str,
        url: str,
        caption: str = "",
        is_group: bool = False,
    ) -> dict:
        """POST /messages/link"""
        data = {
            "to": phone,
            "link": url,
            "caption": caption,
        }

        return await self.send_rest_request("messages/link", data=data)

    async def send_vcard(
        self,
        phone: str,
        vcard: str,
        is_group: bool = False,
    ) -> dict:
        """POST /messages/vcard"""
        data = {
            "to": phone,
            "vcard": vcard,
        }

        return await self.send_rest_request("messages/vcard", data=data)

    async def send_sticker(
        self,
        phone: str,
        sticker_url: str,
        is_group: bool = False,
    ) -> dict:
        """POST /messages/sticker"""
        data = {
            "to": phone,
            "sticker": sticker_url,
        }

        return await self.send_rest_request("messages/sticker", data=data)

    # ========================================================================
    # GROUPS
    # ========================================================================

    async def create_group(
        self,
        name: str,
        participants: List[str],
    ) -> dict:
        """POST /chats/createGroup"""
        data = {
            "groupName": name,
            "groupParticipants": ",".join(participants),
        }

        return await self.send_rest_request("chats/createGroup", data=data)

    async def leave_group(self, group_id: str) -> dict:
        """POST /chats/leaveGroup"""
        data = {"chatId": group_id}
        return await self.send_rest_request("chats/leaveGroup", data=data)

    async def get_group_info(self, group_id: str) -> dict:
        """GET /chats/getGroupInfo"""
        params = {"chatId": group_id}
        return await self.send_rest_request(
            "chats/getGroupInfo", method="GET", params=params
        )

    # ========================================================================
    # CONTACTS & CHATS
    # ========================================================================

    async def get_contacts(self) -> dict:
        """GET /contacts"""
        return await self.send_rest_request("contacts", method="GET")

    async def get_contact(self, phone: str) -> dict:
        """GET /contacts/{phone}"""
        return await self.send_rest_request(f"contacts/{phone}", method="GET")

    async def check_phone(self, phone: str) -> dict:
        """GET /contacts/check"""
        params = {"chatId": phone}
        return await self.send_rest_request(
            "contacts/check", method="GET", params=params
        )

    async def list_chats(self) -> dict:
        """GET /chats"""
        return await self.send_rest_request("chats", method="GET")

    async def get_chat_messages(
        self,
        chat_id: str,
        limit: int = 100,
        last: bool = True,
    ) -> dict:
        """GET /chats/messages"""
        params = {
            "chatId": chat_id,
            "limit": limit,
            "last": "true" if last else "false",
        }
        return await self.send_rest_request(
            "chats/messages", method="GET", params=params
        )

    async def read_chat(self, chatid: str) -> dict:
        """POST /chats/readMessages"""
        data = {"chatId": chatid}
        return await self.send_rest_request("chats/readMessages", data=data)

    async def archive_chat(self, chat_id: str) -> dict:
        """POST /chats/archive"""
        data = {"chatId": chat_id}
        return await self.send_rest_request("chats/archive", data=data)

    async def unarchive_chat(self, chat_id: str) -> dict:
        """POST /chats/unarchive"""
        data = {"chatId": chat_id}
        return await self.send_rest_request("chats/unarchive", data=data)

    async def clear_chat(self, chat_id: str) -> dict:
        """POST /chats/clear"""
        data = {"chatId": chat_id}
        return await self.send_rest_request("chats/clear", data=data)

    async def delete_chat(self, chat_id: str) -> dict:
        """POST /chats/delete"""
        data = {"chatId": chat_id}
        return await self.send_rest_request("chats/delete", data=data)

    # ========================================================================
    # WEBHOOKS
    # ========================================================================

    async def set_webhook(
        self, webhook_url: str, events: Optional[List[str]] = None
    ) -> dict:
        """POST /instance/webhook/set"""
        data = {"webhookUrl": webhook_url}

        if events:
            # UltraMsg supports: message, ack, status, etc.
            data["events"] = ",".join(events)

        return await self.send_rest_request("instance/webhook/set", data=data)

    async def get_webhook(self) -> dict:
        """GET /instance/webhook"""
        return await self.send_rest_request("instance/webhook", method="GET")

    async def delete_webhook(self) -> dict:
        """POST /instance/webhook/delete"""
        return await self.send_rest_request("instance/webhook/delete", data={})

    # ========================================================================
    # UTILITIES
    # ========================================================================

    async def get_me(self) -> dict:
        """GET /instance/me"""
        return await self.send_rest_request("instance/me", method="GET")

    async def get_profile_picture(self, phone: str) -> dict:
        """GET /contacts/profilePic"""
        params = {"chatId": phone}
        return await self.send_rest_request(
            "contacts/profilePic", method="GET", params=params
        )

    async def block_contact(self, phone: str, is_group: bool = False) -> dict:
        """POST /contacts/block"""
        data = {"chatId": phone}
        return await self.send_rest_request("contacts/block", data=data)

    async def unblock_contact(self, phone: str, is_group: bool = False) -> dict:
        """POST /contacts/unblock"""
        data = {"chatId": phone}
        return await self.send_rest_request("contacts/unblock", data=data)

    async def health_check(self) -> dict:
        """GET /instance/status - Alias for status check"""
        return await self.status()

    # ========================================================================
    # STATISTICS & ANALYTICS
    # ========================================================================

    async def get_statistics(self) -> dict:
        """GET /instance/statistic"""
        return await self.send_rest_request("instance/statistic", method="GET")

    async def get_message_statistics(
        self,
        start_date: str,
        end_date: str,
    ) -> dict:
        """GET /instance/messageStatistic"""
        params = {
            "start": start_date,  # Format: YYYY-MM-DD
            "end": end_date,  # Format: YYYY-MM-DD
        }
        return await self.send_rest_request(
            "instance/messageStatistic", method="GET", params=params
        )
