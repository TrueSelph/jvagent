"""API module for interacting with the WPPConnect HTTP API."""

import base64
import logging
import mimetypes
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

import filetype
import requests
from dotenv import load_dotenv

load_dotenv()


class WPPConnectAPI:
    """Class for interacting with the WPPConnect API."""

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

    def send_rest_request(
        self,
        endpoint: str,
        method: str = "POST",
        data: Optional[dict] = None,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        json_body: bool = True,
        use_full_url: bool = False,
    ) -> dict:
        """Generic HTTP request to WPPConnect API. Handles GET, POST, PUT, DELETE with timeout."""
        if headers is None:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            }

        url = (
            endpoint
            if use_full_url
            else f"{self.api_url}/{self.session}/{endpoint.lstrip('/')}"
        )
        json_payload = data if json_body else None
        body = None if json_body else data

        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                json=json_payload,
                data=body,
                params=params,
                timeout=self.timeout,  # request timeout
            )
            response.raise_for_status()
            if response.content:
                try:
                    return response.json()
                except Exception:
                    return {"ok": True, "raw": response.content}
            return {"ok": True, "no_content": True}
        except requests.Timeout:
            self.logger.error(
                f"WPPConnect request timed out after {self.timeout} seconds."
            )
            return {"ok": False, "error": f"Timeout after {self.timeout} seconds"}
        except requests.RequestException as e:
            self.logger.error(f"WPPConnect request error: {str(e)}")
            return {"ok": False, "error": str(e)}

    # Utility

    @staticmethod
    def parse_inbound_message(request: dict) -> dict:
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

            # fromMe correction if misplaced
            if isinstance(payload["fromMe"], dict):
                payload["fromMe"] = payload["fromMe"].get("fromMe", False)

            # message id extraction in the event of weird nested struct
            if isinstance(payload["message_id"], dict):
                payload["fromMe"] = payload["message_id"].get("fromMe", False)
                payload["message_id"] = payload["message_id"].get("id", "")

            # quotedMsg/parent message
            if "quotedMsg" in request:
                payload["quoted_message"] = request["quotedMsg"]

            # Group detection
            if (
                payload["author"]
                and payload["sender"]
                and payload["author"] != payload["sender"]
            ):
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
    def get_file_type(
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
            # Use mimetypes to guess MIME type based on file extension
            detected_mime_type, _ = mimetypes.guess_type(file_path)
        elif url:
            # Make a HEAD request to get the Content-Type header
            try:
                response = requests.head(url, allow_redirects=True)
                detected_mime_type = response.headers.get("Content-Type")
            except requests.RequestException as e:
                WPPConnectAPI.logger.error(f"Error making HEAD request: {e}")
        else:
            # Fallback to initial MIME type if provided
            detected_mime_type = mime_type

        # MIME type categories
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
                "application/poll",  # Generic and clean
                "application/vnd.jivas.poll",  # Vendor-specific to your framework
                "poll/message",  # Custom subtype under a new "poll" type
                "application/x-poll-data",  # Legacy-style custom type
                "application/jivas-poll+json",  # Jivas framework + structured data format
                "jivas/poll",  # Jivas framework + poll
            ],
        }

        # Handle cases where MIME type cannot be detected
        if not detected_mime_type or detected_mime_type == "binary/octet-stream":
            file_extension = ""
            if file_path:
                _, file_extension = os.path.splitext(file_path)
            elif url:
                _, file_extension = os.path.splitext(url)

            detected_mime_type = mimetypes.types_map.get(
                file_extension.lower(), "unknown/unknown"
            )

        # Categorize MIME type
        for category, mime_list in mime_categories.items():
            if detected_mime_type in mime_list:
                return {"file_type": category, "mime": detected_mime_type}

        # Default to "unknown" if no category matches
        return {"file_type": "unknown", "mime": detected_mime_type}

    def register_session(
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
        # Optionally generate instance/token if needed
        if "Unauthorized" in str(status_resp.get("error", "")) or status == "":
            # This corresponds to a missing/invalid token, so attempt to create instance/token
            create_res = self.create_session()
            if not create_res.get("token"):
                return {
                    "status": "ERROR",
                    "message": "Could not create instance or get token.",
                    "details": create_res,
                }
            self.token = create_res["token"]

            status_resp = self.status()
            status = status_resp.get("status", "").upper()

        # These status keys may vary depending on your WPPConnect version
        # Active/connected session
        if status == "CONNECTED":

            # start session with new webhook
            start_res = self.start_session(
                webhook=webhook_url, wait_qr_code=wait_qr_code
            )

            if start_res.get("status") == "CONNECTED":
                # Get the host device info/number
                device_info = self.get_host_device()
                return {
                    "status": "CONNECTED",
                    "message": "Session is already active and connected.",
                    "device": device_info,
                    "session": self.session,
                    "token": self.token,
                }
            return start_res
        # Some deployments may say "QRCODE" or "DISCONNECTED" or "CLOSED"
        elif status in {"QRCODE", "DISCONNECTED", "CLOSED", ""} and auto_register:
            # Start the session, register webhook, and request QR code
            start_res = self.start_session(
                webhook=webhook_url, wait_qr_code=wait_qr_code
            )

            if start_res.get("qrcode"):
                # Some deployments return QR code directly
                qrcode_b64 = start_res["qrcode"]
            else:
                # Otherwise, get it from /qrcode-session
                qr_resp = self.qrcode()
                qrcode_b64 = qr_resp.get("qrcode")

            return {
                "status": "AWAITING_QR_SCAN",
                "message": "Session created or started. Awaiting QR Code scan.",
                "qrcode": qrcode_b64,
                "session": self.session,
                "token": self.token,
            }

        # Some other status
        return {
            "status": status,
            "message": f"Session status: {status}",
            "details": status_resp,
            "qrcode": status_resp.get("qrcode"),
        }

    # 1. Instance/session related

    def status(self) -> dict:
        """GET /status-session"""
        return self.send_rest_request("status-session", method="GET")

    def show_all_sessions(self) -> dict:
        """
        GET /api/{secretkey}/show-all-sessions
        Retrieves all sessions using the secret key.

        Returns:
            dict: Response from the server.
        """
        if not self.secret_key:
            return {"ok": False, "error": "secret_key required"}

        url = f"{self.api_url}/{self.secret_key}/show-all-sessions"
        return self.send_rest_request(url, method="GET", use_full_url=True)

    def check_connection(self) -> dict:
        """
        GET /api/{session}/check-connection-session
        Checks the connection status of the session.

        Returns:
            dict: Response from the server.
        """
        return self.send_rest_request("check-connection-session", method="GET")

    def start_session(self, webhook: str = "", wait_qr_code: bool = False) -> dict:
        """POST /start-session"""
        data = {"webhook": webhook, "waitQrCode": wait_qr_code}
        result = self.send_rest_request("start-session", data=data)
        if result.get("status"):
            return result
        else:
            result = self.send_rest_request("start-session", data=data)
            return result

    def close_session(self) -> dict:
        """POST /close-session"""
        return self.send_rest_request("close-session")

    def logout_session(self) -> None:
        """POST /logout-session"""
        # fist logout close second
        self.send_rest_request("logout-session")

    def qrcode(self) -> dict:
        """GET /qrcode-session (base64 encoded image returned)"""
        response = requests.get(
            f"{self.api_url}/{self.session}/qrcode-session",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        if response.ok:
            return {"qrcode_base64": base64.b64encode(response.content).decode("ascii")}
        else:
            return {"ok": False, "error": response.text}

    def get_host_device(self) -> dict:
        """GET /host-device"""
        return self.send_rest_request("host-device", method="GET")

    def profile_exists(self) -> dict:
        """GET /profile-exists"""
        return self.send_rest_request("profile-exists", method="GET")

    def create_session(self) -> dict:
        """POST /{session}/{secretKey}/generate-token"""
        if not self.secret_key:
            return {"ok": False, "error": "secret_key required"}
        url = f"{self.api_url}/{self.session}/{self.secret_key}/generate-token"
        return self.send_rest_request(url, method="POST", use_full_url=True)

    # 2. Messaging

    def send_message(
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
            return self.send_rest_request("send-reply", data=data)

        return self.send_rest_request("send-message", data=data)

    def send_reply(
        self, phone: str, message: str, message_id: str, is_group: bool = False
    ) -> dict:
        """POST /send-reply"""
        data = {
            "phone": phone,
            "message": message,
            "isGroup": is_group,
            "messageId": message_id,
        }
        return self.send_rest_request("reply-message", data=data)

    def send_location(
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
        return self.send_rest_request("send-location", data=data)

    def send_contact(self, phone: str, contactid: str, is_group: bool = False) -> dict:
        """POST /send-contact"""
        data = {"phone": phone, "contactid": contactid, "isGroup": is_group}
        return self.send_rest_request("send-contact", data=data)

    def send_image(
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
        data = {
            "phone": phone,
            "isGroup": is_group,
            "isNewsletter": is_newsletter,
            "isLid": is_lid,
            "filename": filename,
            "caption": caption,
            "base64": self.file_url_to_base64(file_url),
        }
        return self.send_rest_request("send-image", data=data)

    def send_file(
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

        data = {
            "phone": phone,
            "isGroup": is_group,
            "isNewsletter": is_newsletter,
            "isLid": is_lid,
            "filename": filename,
            "caption": caption,
            "base64": self.file_url_to_base64(file_url),
        }
        return self.send_rest_request("send-file", data=data)

    def send_file_base64(
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
        return self.send_rest_request("send-file-base64", data=data)

    def send_voice(
        self,
        phone: str,
        file_url: str,
        is_group: bool = False,
        quoted_message_id: str = "",
    ) -> dict:
        """
        POST /api/{session}/send-voice

        Args:
            phone (str): Recipient phone number/group id.
            file_url (str): Path to the audio file (voice message).
            is_group (bool): True if the recipient is a group. Defaults to False.
            quoted_message_id (str): Optional; message id to quote/reply to. Defaults to "".

        Returns:
            dict: API response
        """
        data = {
            "phone": phone,
            "isGroup": is_group,
            "path": file_url,
            "quotedMessageId": quoted_message_id,
        }
        return self.send_rest_request("send-voice", data=data)

    def send_voice_base64(
        self, phone: str, base64_ptt: str, is_group: bool = False
    ) -> dict:
        """POST /send-voice-base64"""
        data = {"phone": phone, "isGroup": is_group, "base64Ptt": base64_ptt}
        return self.send_rest_request("send-voice-base64", data=data)

    def send_poll_message(
        self,
        phone: str,
        name: str,
        choices: list,
        options: Optional[dict] = None,
        is_group: bool = False,
    ) -> dict:
        """
        POST /api/{session}/send-poll-message

        Args:
            phone (str): The recipient phone number.
            name (str): The poll name/title.
            choices (list): A list of choice strings.
            options (dict, optional): Poll options, e.g. {"selectableCount": 1}. Defaults to None.
            is_group (bool, optional): True if sending to a group. Defaults to False.

        Returns:
            dict: API response
        """
        data = {
            "phone": phone,
            "isGroup": is_group,
            "name": name,
            "choices": choices,
        }
        if options:
            data["options"] = options

        return self.send_rest_request("send-poll-message", data=data)

    def send_status_message(
        self, phone: str, message: str, is_group: bool, message_id: Optional[str] = None
    ) -> dict:
        """
        POST /api/{session}/send-status
        Sends a status message to a contact or group.

        Args:
            phone (str): The phone number or group ID to send the message to.
            message (str): The message text.
            is_group (bool): Whether the message is being sent to a group.
            message_id (str, optional): The ID of the original message to reply to. Default is None.

        Returns:
            dict: Response from the server.
        """
        data = {"phone": phone, "isGroup": is_group, "message": message}
        if message_id:
            data["messageId"] = message_id

        return self.send_rest_request("send-status", method="POST", data=data)

    def send_link_preview(
        self, phone: str, url: str, caption: str, is_group: bool = False
    ) -> dict:
        """
        POST /api/{session}/send-link-preview
        Sends a message with a link preview to a contact or group.

        Args:
            phone (str): The phone number or group ID to send the message to.
            url (str): The URL to include in the message.
            caption (str): The caption or text to accompany the link.
            is_group (bool): Whether the message is being sent to a group. Default is False.

        Returns:
            dict: Response from the server.
        """
        data = {"phone": phone, "isGroup": is_group, "url": url, "caption": caption}
        return self.send_rest_request("send-link-preview", method="POST", data=data)

    def send_mentioned_message(
        self, phone: str, message: str, mentioned: List[str], is_group: bool = True
    ) -> dict:
        """
        POST /api/{session}/send-mentioned
        Sends a message with mentions to specific contacts or a group.

        Args:
            phone (str): The phone number or group ID to send the message to.
            message (str): The message text.
            mentioned (list of str): List of contacts to mention in the message.
            is_group (bool): Whether the message is being sent to a group. Default is True.

        Returns:
            dict: Response from the server.
        """
        data = {
            "phone": phone,
            "isGroup": is_group,
            "message": message,
            "mentioned": mentioned,
        }
        return self.send_rest_request("send-mentioned", method="POST", data=data)

    def send_buttons_message(
        self, phone: str, text: str, buttons: List[dict], is_group: bool = False
    ) -> dict:
        """
        POST /api/{session}/send-buttons
        Sends a button message to a contact or group. Note: This endpoint is deprecated.

        Args:
            phone (str): The phone number or group ID to send the message to.
            text (str): Text to accompany the buttons.
            buttons (list of dict): List of buttons, each with properties such as 'buttonId', 'button_text', 'type'.
            is_group (bool): Whether the message is being sent to a group. Default is False.

        Returns:
            dict: Response from the server.
        """
        data = {"phone": phone, "isGroup": is_group, "text": text, "buttons": buttons}
        return self.send_rest_request("send-buttons", method="POST", data=data)

    def send_list_message(
        self,
        phone: str,
        description: str,
        button_text: str,
        sections: List[dict],
        is_group: bool = False,
    ) -> dict:
        """
        POST /api/{session}/send-list-message
        Sends a list message to a contact or group.

        Args:
            phone (str): The phone number or group ID to send the message to.
            description (str): Description for the list message.
            button_text (str): Text for the button.
            sections (list of dict): List of sections, each with a title and rows containing rowId, title, and description.
            is_group (bool): Whether the message is being sent to a group. Default is False.

        Returns:
            dict: Response from the server.
        """
        data = {
            "phone": phone,
            "isGroup": is_group,
            "description": description,
            "buttonText": button_text,
            "sections": sections,
        }
        return self.send_rest_request("send-list-message", method="POST", data=data)

    def send_order_message(
        self,
        phone: str,
        items: List[dict],
        is_group: bool = False,
        options: Optional[dict] = None,
    ) -> dict:
        """
        POST /api/{session}/send-order-message
        Sends an order message to a contact or group.

        Args:
            phone (str): The phone number or group ID to send the order message to.
            items (list of dict): A list of items in the order, each with properties such as 'type', 'name', 'price', and 'qnt'.
            is_group (bool): Whether the message is being sent to a group. Default is False.
            options (dict, optional): Additional options such as 'tax', 'shipping', and 'discount'.

        Returns:
            dict: Response from the server.
        """
        data = {"phone": phone, "isGroup": is_group, "items": items}
        if options:
            data["options"] = options
        return self.send_rest_request("send-order-message", method="POST", data=data)

    # 3. Groups

    def create_group(self, name: str, participants: List[str]) -> dict:
        """POST /create-group"""
        data = {"name": name, "participants": participants}
        return self.send_rest_request("create-group", data=data)

    def group_members(self, group_id: str) -> dict:
        """GET /group-members/{group_id}"""
        if not group_id:
            return {}
        return self.send_rest_request(f"group-members/{group_id}", method="GET")

    def leave_group(self, group_id: str) -> dict:
        """POST /leave-group"""
        data = {"groupId": group_id}
        return self.send_rest_request("leave-group", data=data)

    def add_group_participant(self, group_id: str, phone: str) -> dict:
        """POST /add-participant-group"""
        data = {"groupId": group_id, "phone": phone}
        return self.send_rest_request("add-participant-group", data=data)

    def remove_group_participant(self, group_id: str, phone: str) -> dict:
        """POST /remove-participant-group"""
        data = {"groupId": group_id, "phone": phone}
        return self.send_rest_request("remove-participant-group", data=data)

    def promote_group_admin(self, group_id: str, phone: str) -> dict:
        """POST /promote-participant-group"""
        data = {"groupId": group_id, "phone": phone}
        return self.send_rest_request("promote-participant-group", data=data)

    def demote_group_admin(self, group_id: str, phone: str) -> dict:
        """POST /demote-participant-group"""
        data = {"groupId": group_id, "phone": phone}
        return self.send_rest_request("demote-participant-group", data=data)

    def set_group_subject(self, group_id: str, title: str) -> dict:
        """POST /group-subject"""
        data = {"groupId": group_id, "title": title}
        return self.send_rest_request("group-subject", data=data)

    def set_group_description(self, group_id: str, description: str) -> dict:
        """POST /group-description"""
        data = {"groupId": group_id, "description": description}
        return self.send_rest_request("group-description", data=data)

    # 4. Contacts

    def get_contacts(self) -> dict:
        """GET /all-contacts"""
        return self.send_rest_request("all-contacts", method="GET")

    def get_contact(self, phone: str) -> dict:
        """GET /contact/{phone}"""
        return self.send_rest_request(f"contact/{phone}", method="GET")

    def block_contact(self, phone: str, is_group: bool = False) -> dict:
        """POST /block-contact"""
        data = {"phone": phone, "isGroup": is_group}
        return self.send_rest_request("block-contact", data=data)

    def unblock_contact(self, phone: str, is_group: bool = False) -> dict:
        """POST /unblock-contact"""
        data = {"phone": phone, "isGroup": is_group}
        return self.send_rest_request("unblock-contact", data=data)

    def get_blocklist(self) -> dict:
        """GET /blocklist"""
        return self.send_rest_request("blocklist", method="GET")

    # 5. Chats

    def list_chats(self, options: Optional[dict] = None) -> dict:
        """
        POST /api/{session}/list-chats
        Retrieves a list of chats. You can pass options to filter the chats.

        Args:
            options (dict, optional): Options to filter the chats.
                                    Keys can include 'id', 'count', 'direction',
                                    'onlyGroups', 'onlyUsers',
                                    'onlyWithUnreadMessage', 'withLabels'.

        Returns:
            dict: Response from the server.
        """
        return self.send_rest_request("list-chats", method="POST", data=options or {})

    def get_chat_by_id(self, phone: str) -> dict:
        """GET /chat-by-id/{phone}"""
        return self.send_rest_request(f"chat-by-id/{phone}", method="GET")

    def clear_chat(self, phone: str, is_group: bool = False) -> dict:
        """POST /clear-chat"""
        data = {"phone": phone, "isGroup": is_group}
        return self.send_rest_request("clear-chat", data=data)

    def archive_chat(self, phone: str, is_group: bool = False) -> dict:
        """POST /archive-chat"""
        data = {"phone": phone, "isGroup": is_group, "value": True}
        return self.send_rest_request("archive-chat", data=data)

    def unarchive_chat(self, phone: str, is_group: bool = False) -> dict:
        """POST /unarchive-chat"""
        data = {"phone": phone, "isGroup": is_group, "value": False}
        return self.send_rest_request("archive-chat", data=data)

    def set_typing_status(
        self, phone: str, is_group: bool = False, value: bool = True
    ) -> dict:
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
        return self.send_rest_request("typing", method="POST", data=data)

    def set_recording_status(
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
        return self.send_rest_request("recording", method="POST", data=data)

    # 6. Media (Download/Upload helpers)

    @staticmethod
    def file_url_to_base64(file_url: str, force_prefix: bool = True) -> Optional[str]:
        """
        Downloads a file from a URL and returns its base64-encoded content with MIME type.

        Args:
            file_url (str): URL of the file to download.
            force_prefix (bool): If True, prepends 'data:{mime};base64,' to the result.

        Returns:
            Optional[str]: Base64 string with or without MIME prefix, or None if download fails.
        """
        try:
            response = requests.get(file_url, timeout=15)
            response.raise_for_status()
            content = response.content

            # Use filetype to guess MIME type from content
            kind = filetype.guess(content)
            content_type = kind.mime if kind else "application/octet-stream"

            # Base64 encode the file content
            encoded = base64.b64encode(content).decode("utf-8")

            if force_prefix:
                return f"data:{content_type};base64,{encoded}"
            return encoded

        except Exception as e:
            WPPConnectAPI.logger.error(f"[ERROR] Failed to fetch or encode file: {e}")
            return None

    # 7. Utility & info

    def device_battery(self) -> dict:
        """GET /battery-level"""
        return self.send_rest_request("battery-level", method="GET")

    def mark_unread(self, chatid: str) -> dict:
        """POST /mark-unread"""
        data = {"chatId": chatid}
        return self.send_rest_request("mark-unread", data=data)

    def read_chat(self, chatid: str) -> dict:
        """POST /send-seen"""
        data = {"chatId": chatid}
        return self.send_rest_request("send-seen", data=data)

    def get_profile_picture(self, phone: str) -> dict:
        """GET /profile-pic"""
        return self.send_rest_request(
            "profile-pic", method="GET", params={"phone": phone}
        )

    def get_message_by_id(self, message_id: str) -> dict:
        """GET /message-by-id"""
        return self.send_rest_request(
            "message-by-id", method="GET", params={"messageId": message_id}
        )

    def forward_messages(
        self, phone: str, message_ids: list, is_group: bool = False
    ) -> dict:
        """POST /forward-messages"""
        data = {"phone": phone, "messageIds": message_ids, "isGroup": is_group}
        return self.send_rest_request("forward-messages", data=data)

    def delete_message(
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
        return self.send_rest_request("delete-message", data=data)

    # Profile

    def change_username(self, name: str) -> dict:
        """POST /change-username"""
        data = {"name": name}
        return self.send_rest_request("change-username", data=data)

    def set_profile_status(self, status: str) -> dict:
        """POST /profile-status"""
        data = {"status": status}
        return self.send_rest_request("profile-status", data=data)

    def set_profile_pic(self, file_data: bytes) -> dict:
        """POST /set-profile-pic"""
        url = f"{self.api_url}/{self.session}/set-profile-pic"
        headers = {
            "Authorization": f"Bearer {self.token}",
        }
        files = {"file": file_data}
        response = requests.post(url, files=files, headers=headers)
        return response.json()

    # Catalog & Business

    def add_product(self, product_data: Dict[str, str]) -> dict:
        """POST /add-product"""
        return self.send_rest_request("add-product", data=product_data)

    def edit_product(self, product_id: str, options: dict) -> dict:
        """POST /edit-product"""
        data = {"id": product_id, "options": options}
        return self.send_rest_request("edit-product", data=data)

    def delete_product(self, product_id: str) -> dict:
        """POST /del-products"""
        data = {"id": product_id}
        return self.send_rest_request("del-products", data=data)

    def change_product_image(self, product_id: str, base64_image: str) -> dict:
        """POST /change-product-image"""
        data = {"id": product_id, "base64": base64_image}
        return self.send_rest_request("change-product-image", data=data)

    def get_products(
        self, phone: Optional[str] = None, qnt: Optional[int] = None
    ) -> dict:
        """GET /get-products"""
        params = {"phone": phone, "qnt": qnt} if phone or qnt else None
        return self.send_rest_request("get-products", method="GET", params=params)

    # Misc

    def health_check(self) -> dict:
        """GET /healthz"""
        return self.send_rest_request("/healthz", method="GET")

    def get_metrics(self) -> dict:
        """GET /metrics"""
        return self.send_rest_request("/metrics", method="GET")

    def list_files_in_folder(
        self, directory: str, within_seconds: int = 0
    ) -> List[str]:
        """
        Returns filenames created within the last X seconds.

        Args:
            directory: Path to scan
            within_seconds: Files created within this time window (seconds)

        Returns:
            List of filenames created recently
        """
        dir_path = Path(directory)

        # Create the directory if it doesn't exist
        dir_path.mkdir(parents=True, exist_ok=True)

        if not dir_path.is_dir():
            raise ValueError(f"Directory not found: {directory}")

        current_time = time.time()
        recent_files = []

        for file in dir_path.iterdir():
            if file.is_file():
                if within_seconds > 0:
                    # Get creation time
                    if os.name == "nt":  # Windows
                        created = os.path.getctime(file)
                    else:  # Mac/Linux
                        stat = file.stat()
                        # created = (
                        #     stat.st_birthtime
                        #     if hasattr(stat, "st_birthtime")
                        #     else stat.st_ctime
                        # )
                        # Use getattr with a default value instead of hasattr
                        created = getattr(stat, "st_birthtime", stat.st_ctime)

                    # Check if created within time window
                    if (current_time - created) <= within_seconds:
                        recent_files.append(file.name)
                else:
                    recent_files.append(file.name)

        return recent_files
