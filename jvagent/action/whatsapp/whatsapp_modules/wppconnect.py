"""Async API module for interacting with the WPPConnect HTTP API."""

import base64
import logging
import mimetypes
import os
from typing import Dict, List, Optional

import aiohttp
from dotenv import load_dotenv

load_dotenv()


class WPPConnectAPI:
    """Async class for interacting with the WPPConnect API."""

    logger = logging.getLogger(__name__)

    def __init__(
        self,
        api_url: str,
        session: str,
        token: str,
        secret_key: Optional[str] = None,
        timeout: float = 10.0,
    ) -> None:
        """
        Initializes the WPPConnectAPI object with base URL, instance, and credentials.

        :param api_url: API base URL.
        :param session: WPPConnect instance ID.
        :param token: API authentication key.
        :param secret_key: Master key for instance creation (if any).
        """
        self.api_url = api_url.rstrip("/")
        self.session = session
        self.token = token
        self.secret_key = secret_key or os.environ.get("WPP_SECRET_KEY", "")
        self.timeout = timeout

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
        if headers is None:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            }

        url = endpoint if use_full_url else f"{self.api_url}/{self.session}/{endpoint.lstrip('/')}"

        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                kwargs = {"headers": headers, "params": params}

                if json_body and data:
                    kwargs["json"] = data
                elif data:
                    kwargs["data"] = data

                async with session.request(method, url, **kwargs) as response:
                    response.raise_for_status()

                    if response.content_length and response.content_length > 0:
                        try:
                            return await response.json()
                        except Exception:
                            raw_content = await response.read()
                            return {"ok": True, "raw": raw_content}
                    return {"ok": True, "no_content": True}

        except aiohttp.ClientError as e:
            self.logger.error(f"WPPConnect request error: {str(e)}")
            return {"ok": False, "error": str(e)}

    # Utility
    async def parse_inbound_message(self, request: dict) -> dict:
        """Parses an inbound message request payload and returns extracted values."""
        payload = {}

        try:
            event = request.get("event")
            if event not in ["onmessage", "onpollresponse", "onack"]:
                return {}

            payload = {
                "message_id": request.get("id", ""),
                "event_type": request.get("dataType", event),
                "message_type": request.get("type", "unknown"),
                "author": str(request.get("author", "").replace("@c.us", "")),
                "sender": str(request.get("from", "").replace("@c.us", "")),
                "receiver": str(request.get("to", "").replace("@c.us", "")),
                "caption": request.get("caption", ""),
                "location": request.get("location", {}),
                "fromMe": request.get("fromMe", False),
                "isGroup": request.get("isGroupMsg", False),
                "isForwarded": request.get("isForwarded", False),
                "sender_name": request.get("notifyName", ""),
            }

            if isinstance(payload["fromMe"], dict):
                payload["fromMe"] = payload["fromMe"].get("fromMe", False)

            if isinstance(payload["message_id"], dict):
                payload["fromMe"] = payload["message_id"].get("fromMe", False)
                payload["message_id"] = payload["message_id"].get("id", "")

            if "quotedMsg" in request:
                payload["quoted_message"] = request["quotedMsg"]

            if payload["author"] and payload["sender"] and payload["author"] != payload["sender"]:
                payload["isGroup"] = True

            if payload["message_type"] == "chat":
                payload["body"] = request.get("content", request.get("body", ""))
            elif payload["message_type"] in ["image", "video", "document"]:
                payload["media"] = request.get("body", "")
                payload["filename"] = request.get("filename", "")
                payload["mime_type"] = request.get("mimetype", "")
            elif payload["message_type"] == "location":
                payload["location"] = {
                    "latitude": request.get("lat", ""),
                    "longitude": request.get("lng", ""),
                }
            elif payload["message_type"] in ["audio", "ptt", "sticker"]:
                payload["media"] = request.get("body", "")
            elif payload["message_type"] in ["contacts", "vcard"]:
                payload["contact"] = request.get("body", {})
            elif payload["event_type"] == "onpollresponse":
                payload["poll_id"] = request.get("msgId", {}).get("_serialized", "")
                payload["selectedOptions"] = request.get("selectedOptions", "")
                payload["sender"] = str(request.get("chatId", "").replace("@c.us", ""))
                payload["message_type"] = "poll"

            return payload

        except Exception as e:
            WPPConnectAPI.logger.error("Error parsing inbound message: %s", str(e))
            return {}

    @staticmethod
    async def get_file_type(
        file_path: Optional[str] = None,
        url: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> dict:
        """
        Determines the MIME type of a file or URL and categorizes it into common file types
        (image, document, audio, video, unknown).
        """

        detected_mime_type = None

        if file_path:
            detected_mime_type, _ = mimetypes.guess_type(file_path)
        elif url:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.head(url, allow_redirects=True) as response:
                        detected_mime_type = response.headers.get("Content-Type")
            except aiohttp.ClientError as e:
                WPPConnectAPI.logger.error(f"Error making HEAD request: {e}")
        else:
            detected_mime_type = mime_type

        mime_categories = {
            "image": [
                "image/jpeg",
                "image/png",
                "image/gif",
                "image/bmp",
                "image/webp",
                "image/tiff",
                "image/svg+xml",
                "image/x-icon",
                "image/heic",
                "image/heif",
                "image/x-raw",
            ],
            "document": [
                "application/pdf",
                "application/msword",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/vnd.ms-excel",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/vnd.ms-powerpoint",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "text/plain",
                "text/csv",
                "text/html",
                "application/rtf",
                "application/x-tex",
                "application/vnd.oasis.opendocument.text",
                "application/vnd.oasis.opendocument.spreadsheet",
                "application/epub+zip",
                "application/x-mobipocket-ebook",
                "application/x-fictionbook+xml",
                "application/x-abiword",
                "application/vnd.apple.pages",
                "application/vnd.google-apps.document",
            ],
            "audio": [
                "audio/mpeg",
                "audio/wav",
                "audio/ogg",
                "audio/flac",
                "audio/aac",
                "audio/mp3",
                "audio/webm",
                "audio/amr",
                "audio/midi",
                "audio/x-m4a",
                "audio/x-realaudio",
                "audio/x-aiff",
                "audio/x-wav",
                "audio/x-matroska",
            ],
            "video": [
                "video/mp4",
                "video/mpeg",
                "video/ogg",
                "video/webm",
                "video/quicktime",
                "video/x-msvideo",
                "video/x-matroska",
                "video/x-flv",
                "video/x-ms-wmv",
                "video/3gpp",
                "video/3gpp2",
                "video/h264",
                "video/h265",
                "video/x-f4v",
                "video/avi",
            ],
            "poll": [
                "application/poll",
                "application/vnd.jivas.poll",
                "poll/message",
                "application/x-poll-data",
                "application/jivas-poll+json",
                "jivas/poll",
            ],
        }

        if not detected_mime_type or detected_mime_type == "binary/octet-stream":
            file_extension = ""
            if file_path:
                _, file_extension = os.path.splitext(file_path)
            elif url:
                _, file_extension = os.path.splitext(url)

            detected_mime_type = mimetypes.types_map.get(file_extension.lower(), "unknown/unknown")

        for category, mime_list in mime_categories.items():
            if detected_mime_type in mime_list:
                return {"file_type": category, "mime": detected_mime_type}

        return {"file_type": "unknown", "mime": detected_mime_type}

    async def register_session(
        self,
        webhook_url: str = "",
        wait_qr_code: bool = True,
        auto_register: bool = True,
    ) -> dict:
        """
        Initializes the WPPConnect session:
        1. Checks session status.
        2. If not active, creates a token (if required), starts the session, and fetches QR code.
        3. If active, returns number/session info.
        Returns a dict with status, and either QR code (for scan) or bound device info.
        """
        # 1. Get session status
        status_resp = self.status()
        status = status_resp.get("status", "").upper()

        if "Unauthorized" in str(status_resp.get("error", "")) or status == "":
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

        if status == "CONNECTED":
            start_res = await self.start_session(webhook=webhook_url, wait_qr_code=wait_qr_code)

            if start_res.get("status") == "CONNECTED":
                device_info = await self.get_host_device()
                return {
                    "status": "CONNECTED",
                    "message": "Session is already active and connected.",
                    "device": device_info,
                    "session": self.session,
                    "token": self.token,
                }
            return start_res

        elif status in {"QRCODE", "DISCONNECTED", "CLOSED", ""} and auto_register:
            start_res = await self.start_session(webhook=webhook_url, wait_qr_code=wait_qr_code)

            if start_res.get("qrcode"):
                qrcode_b64 = start_res["qrcode"]
            else:
                qr_resp = await self.qrcode()
                qrcode_b64 = qr_resp.get("qrcode")

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

    # 1. Instance/session related

    async def status(self) -> dict:
        """GET /status-session"""
        return await self.send_rest_request("status-session", method="GET")

    async def show_all_sessions(self) -> dict:
        """
        GET /api/{secretkey}/show-all-sessions
        Retrieves all sessions using the secret key.

        Returns:
            dict: Response from the server.
        """
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
        if result.get("status"):
            return result
        else:
            result = await self.send_rest_request("start-session", data=data)
            return result

    async def close_session(self) -> dict:
        """POST /close-session"""
        return await self.send_rest_request("close-session")

    async def logout_session(self) -> None:
        """POST /logout-session"""
        await self.send_rest_request("logout-session")

    async def qrcode(self) -> dict:
        """GET /qrcode-session (base64 encoded image returned)"""
        url = f"{self.api_url}/{self.session}/qrcode-session"
        headers = {"Authorization": f"Bearer {self.token}"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.ok:
                        content = await response.read()
                        return {"qrcode_base64": base64.b64encode(content).decode("ascii")}
                    else:
                        text = await response.text()
                        return {"ok": False, "error": text}
        except aiohttp.ClientError as e:
            return {"ok": False, "error": str(e)}

    async def get_host_device(self) -> dict:
        """GET /host-device"""
        return await self.send_rest_request("host-device", method="GET")

    async def profile_exists(self) -> dict:
        """GET /profile-exists"""
        return await self.send_rest_request("profile-exists", method="GET")

    async def create_session(self) -> dict:
        """POST /{session}/{secretKey}/generate-token"""
        if not self.secret_key:
            return {"ok": False, "error": "secret_key required"}
        url = f"{self.api_url}/{self.session}/{self.secret_key}/generate-token"
        return await self.send_rest_request(url, method="POST", use_full_url=True)

    # 2. Messaging

    async def send_message(
        self,
        phone: str,
        message: str,
        is_group: bool = False,
        is_newsletter: bool = False,
        message_id: str = "",
        options: Optional[dict] = None,
    ) -> dict:
        """POST /send-message"""
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

    async def send_reply(
        self, phone: str, message: str, message_id: str, is_group: bool = False
    ) -> dict:
        """POST /send-reply"""
        data = {
            "phone": phone,
            "message": message,
            "isGroup": is_group,
            "messageId": message_id,
        }
        return await self.send_rest_request("reply-message", data=data)

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

    async def send_contact(self, phone: str, contactid: str, is_group: bool = False) -> dict:
        """POST /send-contact"""
        data = {"phone": phone, "contactid": contactid, "isGroup": is_group}
        return await self.send_rest_request("send-contact", data=data)

    async def send_image(
        self,
        phone: str,
        is_group: bool = False,
        is_newsletter: bool = False,
        is_lid: bool = False,
        filename: str = "",
        caption: str = "",
        file_url: str = "",
    ) -> dict:
        """POST /send-image"""
        base64_data = await self.file_url_to_base64(file_url)
        data = {
            "phone": phone,
            "isGroup": is_group,
            "isNewsletter": is_newsletter,
            "isLid": is_lid,
            "filename": filename,
            "caption": caption,
            "base64": base64_data,
        }
        return await self.send_rest_request("send-image", data=data)

    async def send_file(
        self,
        phone: str,
        is_group: bool = False,
        is_newsletter: bool = False,
        is_lid: bool = False,
        filename: str = "",
        caption: str = "",
        file_url: str = "",
    ) -> dict:
        """POST /send-file"""
        base64_data = await self.file_url_to_base64(file_url)
        data = {
            "phone": phone,
            "isGroup": is_group,
            "isNewsletter": is_newsletter,
            "isLid": is_lid,
            "filename": filename,
            "caption": caption,
            "base64": base64_data,
        }
        return await self.send_rest_request("send-file", data=data)

    async def send_file_base64(
        self,
        phone: str,
        base64: str,
        filename: str = "",
        caption: str = "",
        is_group: bool = False,
        is_newsletter: bool = False,
        is_lid: bool = False,
    ) -> dict:
        """POST /send-file-base64"""
        data = {
            "phone": phone,
            "base64": base64,
            "filename": filename,
            "caption": caption,
            "isGroup": is_group,
            "isNewsletter": is_newsletter,
            "isLid": is_lid,
        }
        return await self.send_rest_request("send-file-base64", data=data)

    async def send_voice(
        self,
        phone: str,
        file_url: str,
        is_group: bool = False,
        quoted_message_id: str = "",
    ) -> dict:
        """POST /api/{session}/send-voice"""
        data = {
            "phone": phone,
            "isGroup": is_group,
            "path": file_url,
            "quotedMessageId": quoted_message_id,
        }
        return await self.send_rest_request("send-voice", data=data)

    async def send_voice_base64(self, phone: str, base64_ptt: str, is_group: bool = False) -> dict:
        """POST /send-voice-base64"""
        data = {"phone": phone, "isGroup": is_group, "base64Ptt": base64_ptt}
        return await self.send_rest_request("send-voice-base64", data=data)

    async def send_poll_message(
        self,
        phone: str,
        name: str,
        choices: list,
        options: Optional[dict] = None,
        is_group: bool = False,
    ) -> dict:
        """POST /api/{session}/send-poll-message"""
        data = {
            "phone": phone,
            "isGroup": is_group,
            "name": name,
            "choices": choices,
        }
        if options:
            data["options"] = options

        return await self.send_rest_request("send-poll-message", data=data)

    async def send_status_message(
        self, phone: str, message: str, is_group: bool, message_id: Optional[str] = None
    ) -> dict:
        """POST /api/{session}/send-status"""
        data = {"phone": phone, "isGroup": is_group, "message": message}
        if message_id:
            data["messageId"] = message_id

        return await self.send_rest_request("send-status", method="POST", data=data)

    async def send_link_preview(
        self, phone: str, url: str, caption: str, is_group: bool = False
    ) -> dict:
        """POST /api/{session}/send-link-preview"""
        data = {"phone": phone, "isGroup": is_group, "url": url, "caption": caption}
        return await self.send_rest_request("send-link-preview", method="POST", data=data)

    async def send_mentioned_message(
        self, phone: str, message: str, mentioned: List[str], is_group: bool = True
    ) -> dict:
        """POST /api/{session}/send-mentioned"""
        data = {
            "phone": phone,
            "isGroup": is_group,
            "message": message,
            "mentioned": mentioned,
        }
        return await self.send_rest_request("send-mentioned", method="POST", data=data)

    async def send_buttons_message(
        self, phone: str, text: str, buttons: List[dict], is_group: bool = False
    ) -> dict:
        """POST /api/{session}/send-buttons"""
        data = {"phone": phone, "isGroup": is_group, "text": text, "buttons": buttons}
        return await self.send_rest_request("send-buttons", method="POST", data=data)

    async def send_list_message(
        self,
        phone: str,
        description: str,
        button_text: str,
        sections: List[dict],
        is_group: bool = False,
    ) -> dict:
        """POST /api/{session}/send-list-message"""
        data = {
            "phone": phone,
            "isGroup": is_group,
            "description": description,
            "buttonText": button_text,
            "sections": sections,
        }
        return await self.send_rest_request("send-list-message", method="POST", data=data)

    async def send_order_message(
        self,
        phone: str,
        items: List[dict],
        is_group: bool = False,
        options: Optional[dict] = None,
    ) -> dict:
        """POST /api/{session}/send-order-message"""
        data = {"phone": phone, "isGroup": is_group, "items": items}
        if options:
            data["options"] = options
        return await self.send_rest_request("send-order-message", method="POST", data=data)

    # 3. Groups

    async def create_group(self, name: str, participants: List[str]) -> dict:
        """POST /create-group"""
        data = {"name": name, "participants": participants}
        return await self.send_rest_request("create-group", data=data)

    async def group_members(self, group_id: str) -> dict:
        """GET /group-members/{group_id}"""
        if not group_id:
            return {}
        return await self.send_rest_request(f"group-members/{group_id}", method="GET")

    async def leave_group(self, group_id: str) -> dict:
        """POST /leave-group"""
        data = {"groupId": group_id}
        return await self.send_rest_request("leave-group", data=data)

    async def add_group_participant(self, group_id: str, phone: str) -> dict:
        """POST /add-participant-group"""
        data = {"groupId": group_id, "phone": phone}
        return await self.send_rest_request("add-participant-group", data=data)

    async def remove_group_participant(self, group_id: str, phone: str) -> dict:
        """POST /remove-participant-group"""
        data = {"groupId": group_id, "phone": phone}
        return await self.send_rest_request("remove-participant-group", data=data)

    async def promote_group_admin(self, group_id: str, phone: str) -> dict:
        """POST /promote-participant-group"""
        data = {"groupId": group_id, "phone": phone}
        return await self.send_rest_request("promote-participant-group", data=data)

    async def demote_group_admin(self, group_id: str, phone: str) -> dict:
        """POST /demote-participant-group"""
        data = {"groupId": group_id, "phone": phone}
        return await self.send_rest_request("demote-participant-group", data=data)

    async def set_group_subject(self, group_id: str, title: str) -> dict:
        """POST /group-subject"""
        data = {"groupId": group_id, "title": title}
        return await self.send_rest_request("group-subject", data=data)

    async def set_group_description(self, group_id: str, description: str) -> dict:
        """POST /group-description"""
        data = {"groupId": group_id, "description": description}
        return await self.send_rest_request("group-description", data=data)

    # 4. Contacts

    async def get_contacts(self) -> dict:
        """GET /all-contacts"""
        return await self.send_rest_request("all-contacts", method="GET")

    async def get_contact(self, phone: str) -> dict:
        """GET /contact/{phone}"""
        return await self.send_rest_request(f"contact/{phone}", method="GET")

    async def block_contact(self, phone: str, is_group: bool = False) -> dict:
        """POST /block-contact"""
        data = {"phone": phone, "isGroup": is_group}
        return await self.send_rest_request("block-contact", data=data)

    async def unblock_contact(self, phone: str, is_group: bool = False) -> dict:
        """POST /unblock-contact"""
        data = {"phone": phone, "isGroup": is_group}
        return await self.send_rest_request("unblock-contact", data=data)

    async def get_blocklist(self) -> dict:
        """GET /blocklist"""
        return await self.send_rest_request("blocklist", method="GET")

    # 5. Chats

    async def list_chats(self, options: Optional[dict] = None) -> dict:
        """POST /api/{session}/list-chats"""
        return await self.send_rest_request("list-chats", method="POST", data=options or {})

    async def get_chat_by_id(self, phone: str) -> dict:
        """GET /chat-by-id/{phone}"""
        return await self.send_rest_request(f"chat-by-id/{phone}", method="GET")

    async def clear_chat(self, phone: str, is_group: bool = False) -> dict:
        """POST /clear-chat"""
        data = {"phone": phone, "isGroup": is_group}
        return await self.send_rest_request("clear-chat", data=data)

    async def archive_chat(self, phone: str, is_group: bool = False) -> dict:
        """POST /archive-chat"""
        data = {"phone": phone, "isGroup": is_group, "value": True}
        return await self.send_rest_request("archive-chat", data=data)

    async def unarchive_chat(self, phone: str, is_group: bool = False) -> dict:
        """POST /unarchive-chat"""
        data = {"phone": phone, "isGroup": is_group, "value": False}
        return await self.send_rest_request("archive-chat", data=data)

    async def set_typing_status(
        self, phone: str, is_group: bool = False, value: bool = True
    ) -> dict:
        """POST /api/{session}/typing"""
        data = {"phone": phone, "isGroup": is_group, "value": value}
        return await self.send_rest_request("typing", method="POST", data=data)

    async def set_recording_status(
        self, phone: str, is_group: bool = False, duration: int = 5, value: bool = True
    ) -> dict:
        """POST /api/{session}/recording"""
        data = {
            "phone": phone,
            "isGroup": is_group,
            "duration": duration,
            "value": value,
        }
        return await self.send_rest_request("recording", method="POST", data=data)

    # 6. Media
    @staticmethod
    async def file_url_to_base64(file_url: str, force_prefix: bool = True) -> Optional[str]:
        """
        Downloads a file from a URL and returns its base64-encoded content with MIME type.

        Args:
            file_url (str): URL of the file to download.
            force_prefix (bool): If True, prepends 'data:{mime};base64,' to the result.

        Returns:
            Optional[str]: Base64 string with or without MIME prefix, or None if download fails.
        """
        try:
            # Use aiohttp for async HTTP requests
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.get(file_url, timeout=15) as response:
                    response.raise_for_status()
                    content = await response.read()

            # Use filetype to guess MIME type from content
            import filetype

            kind = filetype.guess(content)
            content_type = kind.mime if kind else "application/octet-stream"

            # Base64 encode the file content
            import base64

            encoded = base64.b64encode(content).decode("utf-8")

            if force_prefix:
                return f"data:{content_type};base64,{encoded}"
            return encoded

        except Exception as e:
            WPPConnectAPI.logger.error(f"[ERROR] Failed to fetch or encode file: {e}")
            return None

    # 7. Utility & info

    async def device_battery(self) -> dict:
        """GET /battery-level"""
        return await self.send_rest_request("battery-level", method="GET")

    async def mark_unread(self, chatid: str) -> dict:
        """POST /mark-unread"""
        data = {"chatId": chatid}
        return await self.send_rest_request("mark-unread", data=data)

    async def read_chat(self, chatid: str) -> dict:
        """POST /send-seen"""
        data = {"chatId": chatid}
        return await self.send_rest_request("send-seen", data=data)

    async def get_profile_picture(self, phone: str) -> dict:
        """GET /profile-pic"""
        return await self.send_rest_request("profile-pic", method="GET", params={"phone": phone})

    async def get_message_by_id(self, message_id: str) -> dict:
        """GET /message-by-id"""
        return await self.send_rest_request(
            "message-by-id", method="GET", params={"messageId": message_id}
        )

    async def forward_messages(self, phone: str, message_ids: list, is_group: bool = False) -> dict:
        """POST /forward-messages"""
        data = {"phone": phone, "messageIds": message_ids, "isGroup": is_group}
        return await self.send_rest_request("forward-messages", data=data)

    async def delete_message(
        self,
        phone: str,
        message_id: str,
        is_group: bool = False,
        only_local: bool = False,
        delete_media_in_device: bool = False,
    ) -> dict:
        """POST /delete-message"""
        data = {
            "phone": phone,
            "messageId": message_id,
            "isGroup": is_group,
            "onlyLocal": only_local,
            "deleteMediaInDevice": delete_media_in_device,
        }
        return await self.send_rest_request("delete-message", data=data)

    # Profile

    async def change_username(self, name: str) -> dict:
        """POST /change-username"""
        data = {"name": name}
        return await self.send_rest_request("change-username", data=data)

    async def set_profile_status(self, status: str) -> dict:
        """POST /profile-status"""
        data = {"status": status}
        return await self.send_rest_request("profile-status", data=data)

    async def set_profile_pic(self, file_data: bytes) -> dict:
        """POST /set-profile-pic"""
        url = f"{self.api_url}/{self.session}/set-profile-pic"
        headers = {
            "Authorization": f"Bearer {self.token}",
        }
        # Use aiohttp for async file upload
        import aiohttp
        import io

        data = aiohttp.FormData()
        data.add_field(
            "file", io.BytesIO(file_data), filename="profile.jpg", content_type="image/jpeg"
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=data, headers=headers) as response:
                return await response.json()

    # Catalog & Business

    async def add_product(self, product_data: Dict[str, str]) -> dict:
        """POST /add-product"""
        return await self.send_rest_request("add-product", data=product_data)

    async def edit_product(self, product_id: str, options: dict) -> dict:
        """POST /edit-product"""
        data = {"id": product_id, "options": options}
        return await self.send_rest_request("edit-product", data=data)

    async def delete_product(self, product_id: str) -> dict:
        """POST /del-products"""
        data = {"id": product_id}
        return await self.send_rest_request("del-products", data=data)

    async def change_product_image(self, product_id: str, base64_image: str) -> dict:
        """POST /change-product-image"""
        data = {"id": product_id, "base64": base64_image}
        return await self.send_rest_request("change-product-image", data=data)

    async def get_products(self, phone: Optional[str] = None, qnt: Optional[int] = None) -> dict:
        """GET /get-products"""
        params = {"phone": phone, "qnt": qnt} if phone or qnt else None
        return await self.send_rest_request("get-products", method="GET", params=params)

    # Misc

    async def health_check(self) -> dict:
        """GET /healthz"""
        return await self.send_rest_request("/healthz", method="GET")

    async def get_metrics(self) -> dict:
        """GET /metrics"""
        return await self.send_rest_request("/metrics", method="GET")

    @staticmethod
    async def list_files_in_folder(directory: str, within_seconds: int = 0) -> List[str]:
        """
        Returns filenames created within the last X seconds.

        Args:
            directory: Path to scan
            within_seconds: Files created within this time window (seconds)

        Returns:
            List of filenames created recently
        """
        # Import needed modules
        import asyncio
        from pathlib import Path
        import os
        import time

        dir_path = Path(directory)

        # Create the directory if it doesn't exist
        dir_path.mkdir(parents=True, exist_ok=True)

        if not dir_path.is_dir():
            raise ValueError(f"Directory not found: {directory}")

        current_time = time.time()
        recent_files = []

        # Use asyncio to run the file scanning in a thread pool
        loop = asyncio.get_event_loop()

        def scan_files():
            files = []
            for file in dir_path.iterdir():
                if file.is_file():
                    if within_seconds > 0:
                        # Get creation time
                        if os.name == "nt":  # Windows
                            created = os.path.getctime(file)
                        else:  # Mac/Linux
                            stat = file.stat()
                            created = getattr(stat, "st_birthtime", stat.st_ctime)

                        # Check if created within time window
                        if (current_time - created) <= within_seconds:
                            files.append(file.name)
                    else:
                        files.append(file.name)
            return files

        # Run the blocking file operations in a thread pool
        recent_files = await loop.run_in_executor(None, scan_files)
        return recent_files
