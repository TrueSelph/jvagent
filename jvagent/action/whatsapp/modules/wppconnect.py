"""Async API module for interacting with the WPPConnect HTTP API."""

import base64
from typing import Dict, List, Optional

import aiohttp

from .base import BaseWhatsAppAPI
import logging

logger = logging.getLogger(__name__)

class WPPConnectAPI(BaseWhatsAppAPI):
    """Async class for interacting with the WPPConnect API."""

    def _build_url(self, endpoint: str, use_full_url: bool = False) -> str:
        """Build the full URL for an endpoint."""
        if use_full_url:
            return endpoint
        return f"{self.api_url}/{self.session}/{endpoint.lstrip('/')}"

    def _build_headers(self, headers: Optional[dict] = None) -> dict:
        """Build request headers with authentication."""
        if headers is None:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            }
        return headers

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
        """Generic async HTTP request to WPPConnect API."""
        url = self._build_url(endpoint, use_full_url)
        headers = self._build_headers(headers)
        return await self._make_request(url, method, headers, data, params, json_body)

    # ========================================================================
    # SESSION MANAGEMENT
    # ========================================================================

    async def register_session(
        self,
        webhook_url: str = "",
        wait_qr_code: bool = True,
        auto_register: bool = True,
    ) -> dict:
        """Initialize the WPPConnect session with optional webhook."""
        status_resp = await self.status()
        status = status_resp.get("status", "").upper()

        # Create session if unauthorized
        if "Unauthorized" in str(status_resp.get("error", "")) or not status:
            create_res = await self.create_session()
            if not create_res.get("token"):
                return {
                    "status": "ERROR",
                    "message": "Could not create instance or get token.",
                    "details": create_res,
                }
            self.token = create_res["token"]
            status_resp = await self.status()
            status = status_resp.get("status", "").upper()

        # Handle connected state - update webhook for existing session
        if status == "CONNECTED":
            # Update webhook URL for the existing session
            if webhook_url:
                try:
                    await self.close_session()
                    start_res = await self.start_session(webhook=webhook_url, wait_qr_code=wait_qr_code)
                    if start_res.get("status") == "CONNECTED":
                        logger.debug(
                            f"Updated webhook URL for existing session '{self.session}'"
                        )
                    elif start_res.get("error") or not start_res.get("ok", True):
                        logger.debug(
                            f"Could not update webhook for existing session '{self.session}': "
                            f"{start_res.get('error', 'Unknown error')}"
                        )
                except Exception as e:
                    logger.debug(
                        f"Error updating webhook for existing session '{self.session}': {e}"
                    )
            
            # Return success regardless - session is connected
            device_info = await self.get_host_device()
            return {
                "status": "CONNECTED",
                "message": "Session is already active and connected.",
                "device": device_info,
                "session": self.session,
                "token": self.token,
            }

        # Handle disconnected states
        if status in {"QRCODE", "DISCONNECTED", "CLOSED", ""} and auto_register:
            start_res = await self.start_session(webhook=webhook_url, wait_qr_code=wait_qr_code)
            qrcode_b64 = start_res.get("qrcode") or (await self.qrcode()).get("qrcode")
            
            return {
                "status": "AWAITING_QR_SCAN",
                "message": "Session created or started. Awaiting QR Code scan.",
                "qrcode": qrcode_b64,
                "session": self.session,
                "token": self.token,
            }

        return {
            "status": status,
            "message": f"Session status: {status}",
            "details": status_resp,
            "qrcode": status_resp.get("qrcode"),
        }

    async def status(self) -> dict:
        """GET /status-session"""
        return await self.send_rest_request("status-session", method="GET")

    async def show_all_sessions(self) -> dict:
        """GET /api/{secretkey}/show-all-sessions"""
        if not self.secret_key:
            return {"ok": False, "error": "secret_key required"}
        url = f"{self.api_url}/{self.secret_key}/show-all-sessions"
        return await self.send_rest_request(url, method="GET", use_full_url=True)

    async def check_connection(self) -> dict:
        """GET /api/{session}/check-connection-session"""
        return await self.send_rest_request("check-connection-session", method="GET")

    async def start_session(self, webhook: str = "", wait_qr_code: bool = False) -> dict:
        """POST /start-session"""
        data = {"webhook": webhook, "waitQrCode": wait_qr_code}
        result = await self.send_rest_request("start-session", data=data)
        return result if result.get("status") else await self.send_rest_request("start-session", data=data)

    async def close_session(self) -> dict:
        """POST /close-session"""
        return await self.send_rest_request("close-session")

    async def logout_session(self) -> None:
        """POST /logout-session"""
        await self.send_rest_request("logout-session")

    async def qrcode(self) -> dict:
        """GET /qrcode-session (base64 encoded image returned)
        
        Uses connection pooling for efficient HTTP requests.
        """
        from .base import get_connection_pool
        
        url = f"{self.api_url}/{self.session}/qrcode-session"
        headers = {"Authorization": f"Bearer {self.token}"}
        
        try:
            pool = await get_connection_pool()
            session = await pool.get_session(self.api_url, self.timeout)
            async with session.get(url, headers=headers) as response:
                if response.ok:
                    content = await response.read()
                    return {"qrcode_base64": base64.b64encode(content).decode("ascii")}
                return {"ok": False, "error": await response.text()}
        except aiohttp.ClientError as e:
            return {"ok": False, "error": str(e)}

    async def get_host_device(self) -> dict:
        """GET /host-device"""
        return await self.send_rest_request("host-device", method="GET")

    async def create_session(self) -> dict:
        """POST /{session}/{secretKey}/generate-token"""
        if not self.secret_key:
            return {"ok": False, "error": "secret_key required"}
        url = f"{self.api_url}/{self.session}/{self.secret_key}/generate-token"
        return await self.send_rest_request(url, method="POST", use_full_url=True)

    async def set_typing_status(self, phone: str, value: bool = True, is_group: bool = False) -> dict:
        """
        POST /api/{session}/typing
        Sets the typing status for a chat.

        Args:
            phone (str): The phone number or group ID to set the typing status for.
            is_group (bool): Whether the chat is a group. Default is False.
            value (bool): Typing status value. True for typing, False for not typing. Default is True.

        Returns:
            dict: Response from the server.
        """
        data = {"phone": phone, "isGroup": is_group, "value": value}
        return await self.send_rest_request("typing", method="POST", data=data)

    async def set_recording_status(
        self, phone: str, is_group: bool = False, duration: int = 5, value: bool = True
    ) -> dict:
        """
        POST /api/{session}/recording
        Sets the recording status for a chat.

        Args:
            phone (str): The phone number or group ID to set the recording status for.
            is_group (bool): Whether the chat is a group. Default is False.
            duration (int): Duration of the recording status in seconds. Default is 5.
            value (bool): Recording status value. True for recording, False for not recording. Default is True.

        Returns:
            dict: Response from the server.
        """
        data = {
            "phone": phone,
            "isGroup": is_group,
            "duration": duration,
            "value": value,
        }
        return await self.send_rest_request("recording", method="POST", data=data)
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
        """POST /send-message or /send-reply"""
        data = {
            "phone": phone,
            "isGroup": is_group,
            "isNewsletter": is_newsletter,
            "message": message,
        }
        
        if options:
            data["options"] = options
        
        if message_id:
            data["messageId"] = message_id
            return await self.send_rest_request("send-reply", data=data)
        
        return await self.send_rest_request("send-message", data=data)


    async def send_image(self, phone: str, file_url: str = "", **kwargs) -> dict:
        """POST /send-image"""
        return await self.send_media(phone, "send-image", file_url=file_url, **kwargs)

    async def send_file(self, phone: str, file_url: str = "", **kwargs) -> dict:
        """POST /send-file"""
        return await self.send_media(phone, "send-file", file_url=file_url, **kwargs)

    async def send_media(
        self, 
        phone: str, 
        endpoint: str, 
        file_url: str = "", 
        caption: str = "",
        filename: str = "",
        is_group: bool = False,
        **kwargs
    ) -> dict:
        """Generic media sending method."""
        data = {
            "phone": phone,
            "isGroup": is_group,
            "path": file_url,
            "caption": caption,
            "filename": filename,
        }
        # Add any additional kwargs
        data.update(kwargs)
        return await self.send_rest_request(endpoint, data=data)

    async def send_voice(
        self,
        phone: str,
        file_url: str,
        is_group: bool = False,
        quoted_message_id: str = "",
    ) -> dict:
        """POST /send-voice"""
        data = {
            "phone": phone,
            "isGroup": is_group,
            "path": file_url,
            "quotedMessageId": quoted_message_id,
        }
        return await self.send_rest_request("send-voice", data=data)

    async def send_location(
        self,
        phone: str,
        latitude: float,
        longitude: float,
        title: str = "",
        is_group: bool = False,
    ) -> dict:
        """POST /send-location"""
        data = {
            "phone": phone,
            "latitude": latitude,
            "longitude": longitude,
            "title": title,
            "isGroup": is_group,
        }
        return await self.send_rest_request("send-location", data=data)

    # ========================================================================
    # GROUPS
    # ========================================================================

    async def create_group(self, name: str, participants: List[str]) -> dict:
        """POST /create-group"""
        return await self.send_rest_request("create-group", data={"name": name, "participants": participants})

    async def group_members(self, group_id: str) -> dict:
        """GET /group-members/{group_id}"""
        return await self.send_rest_request(f"group-members/{group_id}", method="GET") if group_id else {}

    async def manage_group_participant(
        self, action: str, group_id: str, phone: str
    ) -> dict:
        """Helper for group participant management."""
        data = {"groupId": group_id, "phone": phone}
        return await self.send_rest_request(f"{action}-participant-group", data=data)

    async def add_group_participant(self, group_id: str, phone: str) -> dict:
        """POST /add-participant-group"""
        return await self.manage_group_participant("add", group_id, phone)

    async def remove_group_participant(self, group_id: str, phone: str) -> dict:
        """POST /remove-participant-group"""
        return await self.manage_group_participant("remove", group_id, phone)

    async def promote_group_admin(self, group_id: str, phone: str) -> dict:
        """POST /promote-participant-group"""
        return await self.manage_group_participant("promote", group_id, phone)

    async def demote_group_admin(self, group_id: str, phone: str) -> dict:
        """POST /demote-participant-group"""
        return await self.manage_group_participant("demote", group_id, phone)

    # ========================================================================
    # CONTACTS & CHATS
    # ========================================================================

    async def get_contacts(self) -> dict:
        """GET /all-contacts"""
        return await self.send_rest_request("all-contacts", method="GET")

    async def get_contact(self, phone: str) -> dict:
        """GET /contact/{phone}"""
        return await self.send_rest_request(f"contact/{phone}", method="GET")

    async def list_chats(self, options: Optional[dict] = None) -> dict:
        """POST /list-chats"""
        return await self.send_rest_request("list-chats", method="POST", data=options or {})

    async def get_chat_by_id(self, phone: str) -> dict:
        """GET /chat-by-id/{phone}"""
        return await self.send_rest_request(f"chat-by-id/{phone}", method="GET")

    async def read_chat(self, chatid: str) -> dict:
        """POST /send-seen"""
        return await self.send_rest_request("send-seen", data={"chatId": chatid})

    # ========================================================================
    # UTILITIES
    # ========================================================================

    async def device_battery(self) -> dict:
        """GET /battery-level"""
        return await self.send_rest_request("battery-level", method="GET")

    async def get_profile_picture(self, phone: str) -> dict:
        """GET /profile-pic"""
        return await self.send_rest_request("profile-pic", method="GET", params={"phone": phone})

    async def health_check(self) -> dict:
        """GET /healthz"""
        return await self.send_rest_request("/healthz", method="GET")