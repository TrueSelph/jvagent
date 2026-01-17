"""
WWebJS API Wrapper with WPPConnect-compatible interface (Async Version).

This module provides a drop-in replacement for WWebJSAPI that works with WWebJS backend.
All method signatures remain the same for compatibility with existing code.

New Features:
- Async/await support for all API calls
- Custom webhook URL support per session via register_session(webhook_url=...)
- Session creation with webhook URLs via start_session() and create_session()
- Dynamic webhook configuration without environment variable changes
"""

import base64
import logging
import mimetypes
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

import aiohttp
import filetype
from dotenv import load_dotenv

load_dotenv()


class WWebJSAPI:
    """WWebJS API wrapper with WPPConnect-compatible interface (Async)."""

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
        Initialize the API wrapper.

        Args:
            api_url: WWebJS API base URL (e.g., http://localhost:3000)
            session: Session ID for this WhatsApp instance
            token: API key for authentication (x-api-key header)
            secret_key: Not used in WWebJS, kept for compatibility
            timeout: Request timeout in seconds
        """
        self.api_url = api_url.rstrip("/")
        self.session = session
        self.token = token
        self.secret_key = secret_key or os.environ.get("WPP_SECRET_KEY", "")
        self.timeout = timeout

    def _format_chat_id(self, phone: str, is_group: bool = False) -> str:
        """Format phone number to WWebJS chat ID format."""
        if "@" in phone:
            return phone
        suffix = "@g.us" if is_group else "@c.us"
        return f"{phone}{suffix}"

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
        """Generic HTTP request to WWebJS API."""
        if headers is None:
            headers = {}

        if "x-api-key" not in headers:
            if not self.secret_key:
                return {"ok": False, "error": "secret_key required for authentication"}
            headers["x-api-key"] = self.secret_key

        if json_body and "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"

        url = endpoint if use_full_url else f"{self.api_url}/{endpoint.lstrip('/')}"

        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                kwargs = {
                    "headers": headers,
                    "params": params,
                }

                if json_body and data:
                    kwargs["json"] = data
                elif data:
                    kwargs["data"] = data

                print("kwargskwargskwargskwargskwargs")
                print(kwargs)
                print(url)

                async with session.request(method, url, **kwargs) as response:
                    response.raise_for_status()

                    if response.content_length and response.content_length > 0:
                        try:
                            result = await response.json()
                            if "success" in result:
                                result["ok"] = result["success"]
                            return result
                        except Exception:
                            self.logger.debug("Response is not JSON, returning raw content")
                            raw_content = await response.read()
                            return {"ok": True, "raw": raw_content}

                    return {"ok": True, "no_content": True}

        except aiohttp.ClientError as e:
            self.logger.error(f"WPPConnect request error: {str(e)}")
            return {"ok": False, "error": str(e)}

    # Utility methods (static, same as WPPConnect)

    async def parse_inbound_message(self, request: dict) -> dict:
        """Parses an inbound message request payload and returns extracted values."""
        request = await WWebJSAPI.translate_wwebjs_to_wppconnect(request)

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
                "mentionedIds": request.get("mentionedJidList", []),
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
            elif payload["message_type"] in ["image", "video", "document"] and payload:
                payload["media"] = request.get("body", "")
                payload["filename"] = request.get("filename", "")
                payload["mime_type"] = request.get("mimetype", "")
                if not request.get("mimetype", ""):
                    payload["message_type"] = "ignored"

            elif payload["message_type"] == "location":
                payload["location"] = {
                    "latitude": request.get("lat", ""),
                    "longitude": request.get("lng", ""),
                }
            elif payload["message_type"] in ["audio", "ptt", "sticker"]:
                payload["media"] = request.get("body", "")
            elif payload["message_type"] in ["contacts", "vcard"]:
                payload["contact"] = request.get("body", {})
            elif payload["message_type"] == "poll":
                payload["poll_id"] = (
                    request.get("body", {})
                    .get("parentMessage", {})
                    .get("_data", {})
                    .get("id", "")
                    .get("id", "")
                )
                payload["selectedOptions"] = request.get("body", {}).get("selectedOptions", "")
                payload["sender"] = str(request.get("chatId", "").replace("@c.us", ""))
                payload["message_type"] = "poll"

                _to = request.get("body", {}).get("parentMessage", {}).get("to", "").split("@")[0]
                _from = (
                    request.get("body", {}).get("parentMessage", {}).get("from", "").split("@")[0]
                )
                payload["sender"] = _to
                payload["receiver"] = _from
                if _to == _from:
                    payload["fromMe"] = True
                else:
                    payload["fromMe"] = False
            else:
                return {}

            return payload

        except Exception as e:
            WWebJSAPI.logger.error("Error parsing inbound message: %s", str(e))
            return {}

    @staticmethod
    async def get_file_type(
        file_path: Optional[str] = None,
        url: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> dict:
        """Determines the MIME type of a file or URL and categorizes it."""
        detected_mime_type = None
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

        if file_path:
            detected_mime_type, _ = mimetypes.guess_type(file_path)
        elif url:
            for _, value in mime_categories.items():
                for mime in value:
                    ext = mime.split("/")[1]
                    if f".{ext}" in url:
                        detected_mime_type = mime

            if not detected_mime_type:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.head(url, allow_redirects=True) as response:
                            detected_mime_type = response.headers.get("Content-Type")
                except aiohttp.ClientError as e:
                    print(f"Error making HEAD request: {e}")
        else:
            detected_mime_type = mime_type

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

    @staticmethod
    async def file_url_to_base64(file_url: str, force_prefix: bool = True) -> Optional[str]:
        """Downloads a file from a URL and returns its base64-encoded content."""
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(file_url) as response:
                    response.raise_for_status()
                    content = await response.read()

            kind = filetype.guess(content)
            content_type = kind.mime if kind else "application/octet-stream"

            encoded = base64.b64encode(content).decode("utf-8")

            if force_prefix:
                return f"data:{content_type};base64,{encoded}"
            return encoded

        except Exception as e:
            WWebJSAPI.logger.error(f"[ERROR] Failed to fetch or encode file: {e}")
            return None

    def list_files_in_folder(self, directory: str, within_seconds: int = 0) -> List[str]:
        """Returns filenames created within the last X seconds."""
        dir_path = Path(directory)
        dir_path.mkdir(parents=True, exist_ok=True)

        if not dir_path.is_dir():
            raise ValueError(f"Directory not found: {directory}")

        current_time = time.time()
        recent_files = []

        for file in dir_path.iterdir():
            if file.is_file():
                if within_seconds > 0:
                    if os.name == "nt":
                        created = os.path.getctime(file)
                    else:
                        stat = file.stat()
                        created = getattr(stat, "st_birthtime", stat.st_ctime)

                    if (current_time - created) <= within_seconds:
                        recent_files.append(file.name)
                else:
                    recent_files.append(file.name)

        return recent_files

    async def register_session(
        self,
        webhook_url: str = "",
        wait_qr_code: bool = True,
        auto_register: bool = True,
    ) -> dict:
        """
        Initializes the WWebJS session with optional custom webhook URL.
        If webhook_url is provided, it will be set for this specific session.
        """
        status_resp = await self.status()
        state = (status_resp.get("state") or status_resp.get("status", "")).upper()

        if "error" in status_resp or "Unauthorized" in str(status_resp.get("error", "")):
            self.logger.debug(
                f"Unauthorized or error detected: {status_resp.get('error', 'Unknown error')}"
            )
            create_res = await self.create_session(webhook=webhook_url)

            if not create_res.get("ok"):
                return {
                    "status": "ERROR",
                    "message": "Could not create instance.",
                    "details": create_res,
                }

            status_resp = await self.status()
            state = (status_resp.get("state") or status_resp.get("status", "")).upper()

        if state == "CONNECTED":
            device_info = await self.get_host_device()
            return {
                "status": "CONNECTED",
                "message": "Session is already active and connected.",
                "device": device_info,
                "session": self.session,
                "token": self.token,
            }
        elif state in {"QRCODE", "DISCONNECTED", "UNPAIRED", ""} and auto_register:
            start_res = await self.start_session(webhook=webhook_url, wait_qr_code=wait_qr_code)

            if wait_qr_code and start_res.get("qrcode_base64"):
                qrcode_b64 = start_res.get("qrcode_base64")
            else:
                qr_resp = await self.qrcode()
                qrcode_b64 = (
                    qr_resp.get("qrcode_base64") or qr_resp.get("qr") or qr_resp.get("qrcode")
                )

            return {
                "status": "AWAITING_QR_SCAN",
                "message": "Session created or started. Awaiting QR Code scan.",
                "qrcode": qrcode_b64,
                "session": self.session,
                "token": self.token,
            }

        self.logger.debug(f"Unexpected state: {state}, returning status")

        qrcode_b64 = None
        if state in {"QRCODE", "DISCONNECTED", "UNPAIRED"}:
            self.logger.debug(f"State {state} might need QR code, attempting to get it...")
            qr_resp = await self.qrcode()
            if qr_resp.get("ok"):
                qrcode_b64 = qr_resp.get("qrcode_base64") or qr_resp.get("qrcode")
                self.logger.debug("Successfully retrieved QR code for fallback case")
            else:
                self.logger.debug(
                    f"Failed to get QR code for state {state}: {qr_resp.get('error')}"
                )

        result = {
            "status": state,
            "message": f"Session status: {state}",
            "details": status_resp,
        }

        if qrcode_b64:
            result["qrcode"] = qrcode_b64

        return result

    # 1. Instance/session related

    async def status(self) -> dict:
        """GET /session/status/{sessionId}"""
        self.logger.debug(f"Checking status for session: {self.session}")
        result = await self.send_rest_request(f"session/status/{self.session}", method="GET")

        message = result.get("message", "")
        state = result.get("state")

        if message == "session_not_found":
            result["status"] = ""
            self.logger.debug("Session not found, setting empty status")
        elif message in ["browser tab closed", "session closed"]:
            result["status"] = ""
            self.logger.debug("Session closed, setting empty status")
        elif message == "session_not_connected":
            result["status"] = state if state else "DISCONNECTED"
            self.logger.debug(f"Session not connected, status: {result['status']}")
        elif message == "session_connected":
            result["status"] = "CONNECTED"
            self.logger.debug("Session is connected")
        elif "state" in result and state:
            result["status"] = state
            self.logger.debug(f"Using state as status: {state}")
        elif "error" in result and not result.get("ok"):
            result["status"] = ""
            self.logger.debug(f"Error in status response: {result.get('error')}")
        else:
            result["status"] = ""
            self.logger.debug(f"Unknown status, setting empty. Message: {message}, State: {state}")

        self.logger.debug(f"Final status result: {result}")
        return result

    async def show_all_sessions(self) -> dict:
        """GET /session/getSessions"""
        self.logger.debug("Getting all sessions")
        return await self.send_rest_request("session/getSessions", method="GET")

    async def check_connection(self) -> dict:
        """GET /client/getState/{sessionId}"""
        self.logger.debug(f"Checking connection state for session: {self.session}")
        response = await self.send_rest_request(f"client/getState/{self.session}", method="GET")
        return {"status": response.get("success"), "message": response.get("state")}

    async def start_session(self, webhook: str = "", wait_qr_code: bool = False) -> dict:
        """POST /session/start/{sessionId} with optional webhook URL"""
        self.logger.debug(
            f"Starting session {self.session} with webhook={webhook}, wait_qr_code={wait_qr_code}"
        )

        if webhook:
            data = {"webhookUrl": webhook}
            result = await self.send_rest_request(
                f"session/start/{self.session}", method="POST", data=data
            )
        else:
            self.logger.debug("Using GET for backwards compatibility (no webhook URL provided)")
            result = await self.send_rest_request(f"session/start/{self.session}", method="GET")

        self.logger.debug(f"Start session response: {result}")

        if wait_qr_code and result.get("ok", True):
            self.logger.debug("wait_qr_code=True, getting QR code...")
            qr_result = await self.qrcode()
            if qr_result.get("ok"):
                result["qrcode_base64"] = qr_result.get("qrcode_base64")
                result["qrcode"] = qr_result.get("qrcode")
                self.logger.debug("Successfully added QR code to response")
            else:
                self.logger.debug(
                    f"Failed to get QR code: {qr_result.get('error', 'Unknown error')}"
                )

        self.logger.debug(f"Final start_session result: {result}")
        return result

    async def close_session(self) -> dict:
        """GET /session/stop/{sessionId}"""
        self.logger.debug(f"Closing session: {self.session}")
        return await self.send_rest_request(f"session/stop/{self.session}", method="GET")

    async def logout_session(self) -> None:
        """GET /session/terminate/{sessionId}"""
        self.logger.debug(f"Logging out session: {self.session}")
        await self.send_rest_request(f"session/terminate/{self.session}", method="GET")

    async def qrcode(self) -> dict:
        """GET /session/qr/{sessionId}/image - Returns QR code as base64 image"""
        self.logger.debug(f"Getting QR code for session: {self.session}")
        try:
            response = await self.send_rest_request(
                f"session/qr/{self.session}/image", method="GET"
            )

            if "raw" in response:
                qr_base64 = base64.b64encode(response["raw"]).decode("ascii")
                self.logger.debug(f"Successfully encoded QR code, length: {len(qr_base64)}")
                return {"ok": True, "qrcode_base64": qr_base64, "qrcode": qr_base64}
            else:
                error_msg = response.get("message") or response.get("error") or "Unknown error"
                return {"ok": False, "error": error_msg}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def get_host_device(self) -> dict:
        """GET /client/getClassInfo/{sessionId}"""
        self.logger.debug(f"Getting host device info for session: {self.session}")
        result = await self.send_rest_request(f"client/getClassInfo/{self.session}", method="GET")

        if "sessionInfo" in result:
            info = result["sessionInfo"]
            result = {
                "status": "success" if result.get("success") else "error",
                "response": {
                    "phoneNumber": info["wid"].get("_serialized"),
                    "platform": info.get("platform"),
                    "pushname": info.get("pushname"),
                },
                "mapper": "device",
            }

        self.logger.debug(f"Host device info: {result}")
        return result

    async def profile_exists(self) -> dict:
        """Not directly supported in WWebJS"""
        self.logger.debug("profile_exists called - not supported in WWebJS")
        return {"ok": False, "error": "profile_exists not supported in WWebJS"}

    async def create_session(self, webhook: str = "") -> dict:
        """POST /session/start/{sessionId} with optional webhook URL - Start/create session in WWebJS"""
        self.logger.debug(f"Creating session {self.session} with webhook={webhook}")

        if not self.secret_key:
            return {"ok": False, "error": "secret_key required"}

        if webhook:
            data = {"webhookUrl": webhook}
            self.logger.debug(f"Using POST with webhook data: {data}")
            result = await self.send_rest_request(
                f"session/start/{self.session}", method="POST", data=data
            )
        else:
            self.logger.debug("Using GET for backwards compatibility (no webhook URL provided)")
            result = await self.send_rest_request(f"session/start/{self.session}", method="GET")

        self.logger.debug(f"Create session response: {result}")

        if result.get("ok") or result.get("success"):
            result["token"] = self.token
            result["session"] = self.session
            self.logger.debug("Added token and session to response for compatibility")

        self.logger.debug(f"Final create_session result: {result}")
        return result

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
        """POST /client/sendMessage/{sessionId}"""
        self.logger.debug(
            f"Sending message to {phone}, is_group={is_group}, message_id={message_id}"
        )

        chat_id = self._format_chat_id(phone, is_group)
        payload_options: dict = {}

        if message_id:
            payload_options["quotedMessageId"] = message_id
        elif options:
            payload_options = options

        data = {
            "chatId": chat_id,
            "contentType": "string",
            "content": message,
            "options": payload_options,
        }

        result = await self.send_rest_request(f"client/sendMessage/{self.session}", data=data)
        return result

    async def send_reply(
        self, phone: str, message: str, message_id: str, is_group: bool = False
    ) -> dict:
        """POST /client/sendMessage/{sessionId} with quotedMessageId"""
        return await self.send_message(phone, message, is_group, message_id=message_id)

    async def send_location(
        self,
        phone: str,
        latitude: float,
        longitude: float,
        title: str = "",
        is_group: bool = False,
    ) -> dict:
        """POST /client/sendMessage/{sessionId} with Location"""
        chat_id = self._format_chat_id(phone, is_group)

        data = {
            "chatId": chat_id,
            "contentType": "Location",
            "content": {
                "latitude": latitude,
                "longitude": longitude,
                "description": title,
            },
        }

        return await self.send_rest_request(f"client/sendMessage/{self.session}", data=data)

    async def send_contact(self, phone: str, contactid: str, is_group: bool = False) -> dict:
        """POST /client/sendMessage/{sessionId} with Contact"""
        chat_id = self._format_chat_id(phone, is_group)
        contact_chat_id = self._format_chat_id(contactid, False)

        data = {
            "chatId": chat_id,
            "contentType": "Contact",
            "content": {"contactId": contact_chat_id},
        }

        return await self.send_rest_request(f"client/sendMessage/{self.session}", data=data)

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
        """POST /client/sendMessage/{sessionId} with MessageMedia"""
        chat_id = self._format_chat_id(phone, is_group)

        base64_data = await self.file_url_to_base64(file_url, force_prefix=False)
        if not base64_data:
            return {"ok": False, "error": "Failed to encode file"}

        file_info = await self.get_file_type(url=file_url)

        data = {
            "chatId": chat_id,
            "contentType": "MessageMedia",
            "content": {
                "mimetype": file_info["mime"],
                "data": base64_data,
                "filename": filename or "image.jpg",
            },
        }

        if caption:
            data["options"] = {"caption": caption}

        return await self.send_rest_request(f"client/sendMessage/{self.session}", data=data)

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
        """POST /client/sendMessage/{sessionId} with MessageMedia"""
        chat_id = self._format_chat_id(phone, is_group)

        base64_data = self.file_url_to_base64(file_url, force_prefix=False)
        if not base64_data:
            return {"ok": False, "error": "Failed to encode file"}

        file_info = self.get_file_type(url=file_url)

        data = {
            "chatId": chat_id,
            "contentType": "MessageMedia",
            "content": {
                "mimetype": file_info["mime"],
                "data": base64_data,
                "filename": filename or "file",
            },
        }

        if caption:
            data["options"] = {"caption": caption}

        return await self.send_rest_request(f"client/sendMessage/{self.session}", data=data)

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
        """POST /client/sendMessage/{sessionId} with MessageMedia"""
        chat_id = self._format_chat_id(phone, is_group)

        # Remove data URI prefix if present
        if "base64," in base64:
            base64 = base64.split("base64,")[1]

        # Detect MIME type from filename
        file_info = self.get_file_type(file_path=filename)

        data = {
            "chatId": chat_id,
            "contentType": "MessageMedia",
            "content": {
                "mimetype": file_info["mime"],
                "data": base64,
                "filename": filename or "file",
            },
        }

        if caption:
            data["options"] = {"caption": caption}

        return await self.send_rest_request(f"client/sendMessage/{self.session}", data=data)

    async def send_voice(
        self,
        phone: str,
        file_url: str,
        is_group: bool = False,
        quoted_message_id: str = "",
    ) -> dict:
        """POST /client/sendMessage/{sessionId} with MessageMediaFromURL and sendAudioAsVoice"""

        chat_id = self._format_chat_id(phone, is_group)

        # Create a typed dictionary for options
        options: dict = {"sendAudioAsVoice": True}
        if quoted_message_id:
            options["quotedMessageId"] = quoted_message_id

        # Construct the data payload
        data = {
            "chatId": chat_id,
            "contentType": "MessageMediaFromURL",
            "content": file_url,
            "options": options,
        }

        # Send the REST request
        return await self.send_rest_request(f"client/sendMessage/{self.session}", data=data)

    async def send_voice_base64(self, phone: str, base64_ptt: str, is_group: bool = False) -> dict:
        """POST /client/sendMessage/{sessionId} with MessageMedia as voice"""
        chat_id = self._format_chat_id(phone, is_group)

        # Remove data URI prefix if present
        if "base64," in base64_ptt:
            base64_ptt = base64_ptt.split("base64,")[1]

        data = {
            "chatId": chat_id,
            "contentType": "MessageMedia",
            "content": {
                "mimetype": "audio/mp3;",
                "data": base64_ptt,
                "filename": "voice.mp3",
            },
            "options": {"sendAudioAsVoice": True},
        }

        result = await self.send_rest_request(f"client/sendMessage/{self.session}", data=data)

        if result.get("success"):
            result["status"] = "success"
            return result

        result["status"] = "fail"
        return result

    async def send_poll_message(
        self,
        phone: str,
        name: str,
        choices: list,
        options: Optional[dict] = None,
        is_group: bool = False,
    ) -> dict:
        """POST /client/sendMessage/{sessionId} with Poll"""
        chat_id = self._format_chat_id(phone, is_group)

        poll_options = {}
        if options and "selectableCount" in options:
            poll_options["allowMultipleAnswers"] = options["selectableCount"] > 1

        data = {
            "chatId": chat_id,
            "contentType": "Poll",
            "content": {
                "pollName": name,
                "pollOptions": choices,
                "options": poll_options,
            },
        }

        if options:
            data["options"] = options

        result = await self.send_rest_request(f"client/sendMessage/{self.session}", data=data)
        if result.get("success"):
            return {
                "status": "success",
                "response": [{"id": result["message"]["_data"]["id"]["id"]}],
                "message": result,
            }
        return {"status": False}

    async def send_status_message(
        self, phone: str, message: str, is_group: bool, message_id: Optional[str] = None
    ) -> dict:
        """TODO: Status/Story messages - need to investigate WWebJS support"""
        return {
            "ok": False,
            "error": "send_status_message not yet implemented for WWebJS",
        }

    async def send_link_preview(
        self, phone: str, url: str, caption: str, is_group: bool = False
    ) -> dict:
        """Send a message containing a link and caption that generates a preview."""
        # Try sending as regular message - WWebJS should auto-generate preview
        return await self.send_message(phone, f"{caption}\n{url}", is_group)

    async def send_mentioned_message(
        self, phone: str, message: str, mentioned: List[str], is_group: bool = True
    ) -> dict:
        """TODO: Mentioned messages - need to check WWebJS options"""
        return {
            "ok": False,
            "error": "send_mentioned_message not yet implemented for WWebJS",
        }

    async def send_buttons_message(
        self, phone: str, text: str, buttons: List[dict], is_group: bool = False
    ) -> dict:
        """Send an interactive buttons message (deprecated)."""
        return {"ok": False, "error": "send_buttons_message deprecated"}

    async def send_list_message(
        self,
        phone: str,
        description: str,
        button_text: str,
        sections: List[dict],
        is_group: bool = False,
    ) -> dict:
        """Send an interactive list message (deprecated)."""
        return {"ok": False, "error": "send_list_message deprecated"}

    async def send_order_message(
        self,
        phone: str,
        items: List[dict],
        is_group: bool = False,
        options: Optional[dict] = None,
    ) -> dict:
        """TODO: Order messages - Business API feature"""
        return {
            "ok": False,
            "error": "send_order_message not yet implemented for WWebJS",
        }

    # 3. Groups

    async def create_group(self, name: str, participants: List[str]) -> dict:
        """POST /client/createGroup/{sessionId}"""
        formatted_participants = [self._format_chat_id(p, False) for p in participants]

        data = {"title": name, "participants": formatted_participants}

        return await self.send_rest_request(f"client/createGroup/{self.session}", data=data)

    async def group_members(self, group_id: str) -> dict:
        """POST /groupChat/getClassInfo/{sessionId}"""
        if not group_id:
            return {}
        group_chat_id = self._format_chat_id(group_id, True)
        data = {"chatId": group_chat_id}
        result = await self.send_rest_request(f"groupChat/getClassInfo/{self.session}", data=data)

        participants = result.get("chat", {}).get("groupMetadata", {}).get("participants", [])

        host_device = self.get_host_device().get("response", {})
        host_number = host_device.get("phoneNumber").split("@")[0]

        response = [
            {
                "id": {
                    "user": participant.get("id", {}).get("user").split("@")[0],
                },
                "formattedName": (
                    "You"
                    if participant.get("id", {}).get("user").split("@")[0] == host_number
                    else host_number
                ),
            }
            for participant in participants
        ]

        return {
            "status": "success" if result.get("success") else "error",
            "response": response,
        }

    async def leave_group(self, group_id: str) -> dict:
        """POST /group/leaveGroup/{sessionId}"""
        group_chat_id = self._format_chat_id(group_id, True)
        data = {"groupId": group_chat_id}
        return await self.send_rest_request(f"group/leaveGroup/{self.session}", data=data)

    async def add_group_participant(self, group_id: str, phone: str) -> dict:
        """POST /group/addParticipant/{sessionId}"""
        group_chat_id = self._format_chat_id(group_id, True)
        participant_id = self._format_chat_id(phone, False)
        data = {"groupId": group_chat_id, "participantId": participant_id}
        return await self.send_rest_request(f"group/addParticipant/{self.session}", data=data)

    async def remove_group_participant(self, group_id: str, phone: str) -> dict:
        """POST /group/removeParticipant/{sessionId}"""
        group_chat_id = self._format_chat_id(group_id, True)
        participant_id = self._format_chat_id(phone, False)
        data = {"groupId": group_chat_id, "participantId": participant_id}
        return await self.send_rest_request(f"group/removeParticipant/{self.session}", data=data)

    async def promote_group_admin(self, group_id: str, phone: str) -> dict:
        """POST /group/promoteParticipant/{sessionId}"""
        group_chat_id = self._format_chat_id(group_id, True)
        participant_id = self._format_chat_id(phone, False)
        data = {"groupId": group_chat_id, "participantId": participant_id}
        return await self.send_rest_request(f"group/promoteParticipant/{self.session}", data=data)

    async def demote_group_admin(self, group_id: str, phone: str) -> dict:
        """POST /group/demoteParticipant/{sessionId}"""
        group_chat_id = self._format_chat_id(group_id, True)
        participant_id = self._format_chat_id(phone, False)
        data = {"groupId": group_chat_id, "participantId": participant_id}
        return await self.send_rest_request(f"group/demoteParticipant/{self.session}", data=data)

    async def set_group_subject(self, group_id: str, title: str) -> dict:
        """POST /group/setSubject/{sessionId}"""
        group_chat_id = self._format_chat_id(group_id, True)
        data = {"groupId": group_chat_id, "title": title}
        return await self.send_rest_request(f"group/setSubject/{self.session}", data=data)

    async def set_group_description(self, group_id: str, description: str) -> dict:
        """POST /group/setDescription/{sessionId}"""
        group_chat_id = self._format_chat_id(group_id, True)
        data = {"groupId": group_chat_id, "description": description}
        return await self.send_rest_request(f"group/setDescription/{self.session}", data=data)

    # 4. Contacts

    async def get_contacts(self) -> dict:
        """GET /client/getContacts/{sessionId}"""
        return await self.send_rest_request(f"client/getContacts/{self.session}", method="GET")

    async def get_contact(self, phone: str) -> dict:
        """POST /client/getContactById/{sessionId}"""
        contact_id = self._format_chat_id(phone, False)
        data = {"contactId": contact_id}
        return await self.send_rest_request(f"client/getContactById/{self.session}", data=data)

    async def block_contact(self, phone: str, is_group: bool = False) -> dict:
        """POST /contact/block/{sessionId}"""
        contact_id = self._format_chat_id(phone, is_group)
        data = {"contactId": contact_id}
        return await self.send_rest_request(f"contact/block/{self.session}", data=data)

    async def unblock_contact(self, phone: str, is_group: bool = False) -> dict:
        """POST /contact/unblock/{sessionId}"""
        contact_id = self._format_chat_id(phone, is_group)
        data = {"contactId": contact_id}
        return await self.send_rest_request(f"contact/unblock/{self.session}", data=data)

    async def get_blocklist(self) -> dict:
        """POST /client/getBlockedContacts/{sessionId}"""
        return await self.send_rest_request(f"client/getBlockedContacts/{self.session}")

    # 5. Chats

    async def list_chats(self, options: Optional[dict] = None) -> dict:
        """POST /client/getChats/{sessionId}"""
        data = {"searchOptions": options} if options else {}
        return await self.send_rest_request(f"client/getChats/{self.session}", data=data)

    async def get_chat_by_id(self, phone: str) -> dict:
        """POST /client/getChatById/{sessionId}"""
        chat_id = self._format_chat_id(phone, False)
        data = {"chatId": chat_id}
        return await self.send_rest_request(f"client/getChatById/{self.session}", data=data)

    async def clear_chat(self, phone: str, is_group: bool = False) -> dict:
        """POST /chat/clearMessages/{sessionId}"""
        chat_id = self._format_chat_id(phone, is_group)
        data = {"chatId": chat_id}
        return await self.send_rest_request(f"chat/clearMessages/{self.session}", data=data)

    async def archive_chat(self, phone: str, is_group: bool = False) -> dict:
        """POST /client/archiveChat/{sessionId}"""
        chat_id = self._format_chat_id(phone, is_group)
        data = {"chatId": chat_id}
        return await self.send_rest_request(f"client/archiveChat/{self.session}", data=data)

    async def unarchive_chat(self, phone: str, is_group: bool = False) -> dict:
        """POST /client/archiveChat/{sessionId} - WWebJS toggles archive state"""
        return await self.archive_chat(phone, is_group)

    async def set_typing_status(
        self, phone: str, is_group: bool = False, value: bool = True
    ) -> dict:
        """Set or clear typing status in chat"""
        chat_id = self._format_chat_id(phone, is_group)
        data = {"chatId": chat_id}

        if value:
            # Start typing - lasts for 25 seconds
            return await self.send_rest_request(f"chat/sendStateTyping/{self.session}", data=data)
        else:
            # Stop typing immediately
            return await self.send_rest_request(f"chat/clearState/{self.session}", data=data)

    async def set_recording_status(
        self, phone: str, is_group: bool = False, duration: int = 5, value: bool = True
    ) -> dict:
        """Set or clear recording status in chat"""
        chat_id = self._format_chat_id(phone, is_group)
        data = {"chatId": chat_id}

        if value:
            # Start recording - lasts for 25 seconds (duration parameter ignored by WWebJS)
            return await self.send_rest_request(
                f"chat/sendStateRecording/{self.session}", data=data
            )
        else:
            # Stop recording immediately
            return await self.send_rest_request(f"chat/clearState/{self.session}", data=data)

    # 6. Media (Download/Upload helpers) - already implemented as static methods

    # 7. Utility & info

    async def device_battery(self) -> dict:
        """GET /device/getBatteryLevel/{sessionId}"""
        return await self.send_rest_request(f"device/getBatteryLevel/{self.session}", method="GET")

    async def mark_unread(self, chatid: str) -> dict:
        """POST /client/markChatUnread/{sessionId}"""
        data = {"chatId": chatid}
        return await self.send_rest_request(f"client/markChatUnread/{self.session}", data=data)

    async def read_chat(self, chatid: str) -> dict:
        """POST /chat/sendSeen/{sessionId}"""
        data = {"chatId": chatid}
        return await self.send_rest_request(f"chat/sendSeen/{self.session}", data=data)

    async def get_profile_picture(self, phone: str) -> dict:
        """POST /client/getProfilePicUrl/{sessionId}"""
        contact_id = self._format_chat_id(phone, False)
        data = {"contactId": contact_id}
        return await self.send_rest_request(f"client/getProfilePicUrl/{self.session}", data=data)

    async def get_message_by_id(self, message_id: str) -> dict:
        """POST /message/getMessageById/{sessionId}"""
        data = {"messageId": message_id}
        return await self.send_rest_request(f"message/getMessageById/{self.session}", data=data)

    async def forward_messages(self, phone: str, message_ids: list, is_group: bool = False) -> dict:
        """POST /message/forward/{sessionId}"""
        chat_id = self._format_chat_id(phone, is_group)
        data = {"chatId": chat_id, "messageIds": message_ids}
        return await self.send_rest_request(f"message/forward/{self.session}", data=data)

    async def delete_message(
        self,
        phone: str,
        message_id: str,
        is_group: bool = False,
        only_local: bool = False,
        delete_media_in_device: bool = False,
    ) -> dict:
        """POST /message/delete/{sessionId}"""
        chat_id = self._format_chat_id(phone, is_group)
        data = {
            "chatId": chat_id,
            "messageId": message_id,
            "onlyLocal": only_local,
            # Note: delete_media_in_device parameter not supported in WWebJS
        }
        return await self.send_rest_request(f"message/delete/{self.session}", data=data)

    # Profile

    async def change_username(self, name: str) -> dict:
        """POST /client/setDisplayName/{sessionId}"""
        data = {"displayName": name}
        return await self.send_rest_request(f"client/setDisplayName/{self.session}", data=data)

    async def set_profile_status(self, status: str) -> dict:
        """POST /client/setStatus/{sessionId}"""
        data = {"status": status}
        return await self.send_rest_request(f"client/setStatus/{self.session}", data=data)

    async def set_profile_pic(self, file_data: bytes) -> dict:
        """POST /client/setProfilePicture/{sessionId}"""
        # Convert bytes to base64
        base64_data = base64.b64encode(file_data).decode("utf-8")

        data = {"base64": base64_data}
        return await self.send_rest_request(f"client/setProfilePicture/{self.session}", data=data)

    # Catalog & Business

    async def add_product(self, product_data: Dict[str, str]) -> dict:
        """TODO: Business API - add product"""
        return {"ok": False, "error": "add_product not yet implemented for WWebJS"}

    async def edit_product(self, product_id: str, options: dict) -> dict:
        """TODO: Business API - edit product"""
        return {"ok": False, "error": "edit_product not yet implemented for WWebJS"}

    async def delete_product(self, product_id: str) -> dict:
        """TODO: Business API - delete product"""
        return {"ok": False, "error": "delete_product not yet implemented for WWebJS"}

    async def change_product_image(self, product_id: str, base64_image: str) -> dict:
        """TODO: Business API - change product image"""
        return {
            "ok": False,
            "error": "change_product_image not yet implemented for WWebJS",
        }

    async def get_products(self, phone: Optional[str] = None, qnt: Optional[int] = None) -> dict:
        """TODO: Business API - get products"""
        return {"ok": False, "error": "get_products not yet implemented for WWebJS"}

    # Misc

    async def health_check(self) -> dict:
        """GET /ping - WWebJS uses ping instead of healthz"""
        # self.logger.debug("Performing health check")
        result = await self.send_rest_request("ping", method="GET")
        # self.logger.debug(f"Health check result: {result}")
        return result

    async def get_metrics(self) -> dict:
        """Not supported in WWebJS"""
        # self.logger.debug("get_metrics called - not supported in WWebJS")
        return {"ok": False, "error": "get_metrics not supported in WWebJS"}

    @staticmethod
    async def translate_wwebjs_to_wppconnect(wwebjs_data: dict) -> dict:
        # WWebJSAPI.logger.info(f"wwebjs_data: {wwebjs_data}")
        """
        Translates message data from WWEBJS format to WPPConnect format.

        Args:
            wwebjs_data (dict): Message data in WWEBJS format

        Returns:
            dict: Message data in WPPConnect format
        """
        # Extract the message data from WWEBJS structure
        message = wwebjs_data.get("data", {}).get("message", {})

        if wwebjs_data.get("dataType") == "vote_update":
            message = wwebjs_data.get("data", {}).get("vote", {}).get("parentMessage", {})

        msg_data = message.get("_data", {})
        msg_id = msg_data.get("id", {})

        # Build WPPConnect format
        wppconnect_data = {
            # WPPConnect specific fields
            "event": "onmessage",
            "session": wwebjs_data.get("sessionId", "Dispatcher"),
            # ID fields
            "id": msg_id.get("_serialized", ""),
            # Message content fields - from _data
            "viewed": msg_data.get("viewed", False),
            "body": msg_data.get("body", ""),
            "type": msg_data.get("type", "chat"),
            "t": msg_data.get("t", 0),
            "notifyName": msg_data.get("notifyName", ""),
            "from": msg_data.get("from", ""),
            "to": msg_data.get("to", ""),
            "ack": msg_data.get("ack", 0),
            "invis": msg_data.get("invis", False),
            "isNewMsg": msg_data.get("isNewMsg", True),
            "star": msg_data.get("star", False),
            "kicNotified": msg_data.get("kicNotified", False),
            "recvFresh": msg_data.get("recvFresh", True),
            "isFromTemplate": msg_data.get("isFromTemplate", False),
            "pollInvalidated": msg_data.get("pollInvalidated", False),
            "isSentCagPollCreation": msg_data.get("isSentCagPollCreation", False),
            "latestEditMsgKey": msg_data.get("latestEditMsgKey"),
            "latestEditSenderTimestampMs": msg_data.get("latestEditSenderTimestampMs"),
            "mentionedJidList": msg_data.get("mentionedJidList", []),
            "groupMentions": msg_data.get("groupMentions", []),
            "isEventCanceled": msg_data.get("isEventCanceled", False),
            "eventInvalidated": msg_data.get("eventInvalidated", False),
            "isVcardOverMmsDocument": msg_data.get("isVcardOverMmsDocument", False),
            "isForwarded": msg_data.get("isForwarded", False),
            "isQuestion": msg_data.get("isQuestion", False),
            "hasReaction": msg_data.get("hasReaction", False),
            "viewMode": msg_data.get("viewMode", "VISIBLE"),
            "messageSecret": msg_data.get("messageSecret", {}),
            "productHeaderImageRejected": msg_data.get("productHeaderImageRejected", False),
            "lastPlaybackProgress": msg_data.get("lastPlaybackProgress", 0),
            "isDynamicReplyButtonsMsg": msg_data.get("isDynamicReplyButtonsMsg", False),
            "isCarouselCard": msg_data.get("isCarouselCard", False),
            "parentMsgId": msg_data.get("parentMsgId"),
            "callSilenceReason": msg_data.get("callSilenceReason"),
            "isVideoCall": msg_data.get("isVideoCall", False),
            "callDuration": msg_data.get("callDuration"),
            "callCreator": msg_data.get("callCreator"),
            "callParticipants": msg_data.get("callParticipants"),
            "isCallLink": msg_data.get("isCallLink"),
            "callLinkToken": msg_data.get("callLinkToken"),
            "isMdHistoryMsg": msg_data.get("isMdHistoryMsg", False),
            "stickerSentTs": msg_data.get("stickerSentTs", 0),
            "isAvatar": msg_data.get("isAvatar", False),
            "lastUpdateFromServerTs": msg_data.get("lastUpdateFromServerTs", 0),
            "invokedBotWid": msg_data.get("invokedBotWid"),
            "bizBotType": msg_data.get("bizBotType"),
            "botResponseTargetId": msg_data.get("botResponseTargetId"),
            "botPluginType": msg_data.get("botPluginType"),
            "botPluginReferenceIndex": msg_data.get("botPluginReferenceIndex"),
            "botPluginSearchProvider": msg_data.get("botPluginSearchProvider"),
            "botPluginSearchUrl": msg_data.get("botPluginSearchUrl"),
            "botPluginSearchQuery": msg_data.get("botPluginSearchQuery"),
            "botPluginMaybeParent": msg_data.get("botPluginMaybeParent", False),
            "botReelPluginThumbnailCdnUrl": msg_data.get("botReelPluginThumbnailCdnUrl"),
            "botMessageDisclaimerText": msg_data.get("botMessageDisclaimerText"),
            "botMsgBodyType": msg_data.get("botMsgBodyType"),
            "reportingTokenInfo": msg_data.get("reportingTokenInfo", {}),
            "requiresDirectConnection": msg_data.get("requiresDirectConnection"),
            "bizContentPlaceholderType": msg_data.get("bizContentPlaceholderType"),
            "hostedBizEncStateMismatch": msg_data.get("hostedBizEncStateMismatch", False),
            "senderOrRecipientAccountTypeHosted": msg_data.get(
                "senderOrRecipientAccountTypeHosted", False
            ),
            "placeholderCreatedWhenAccountIsHosted": msg_data.get(
                "placeholderCreatedWhenAccountIsHosted", False
            ),
            # WPPConnect specific fields from message level
            "chatId": msg_data.get("from", ""),
            "fromMe": msg_id.get("fromMe", False),
            "timestamp": msg_data.get("t", 0),
            "content": msg_data.get("body", ""),
            "isGroupMsg": "@g.us" in msg_data.get("from", ""),
            "mediaData": {},
            "quotedMsg": msg_data.get("quotedMsg", {}),
            "mentionedIds": msg_data.get("mentionedIds", []),
            "mimetype": wwebjs_data.get("data", {}).get("messageMedia", {}).get("mimetype", ""),
            "caption": msg_data.get("caption", ""),
        }

        # Build sender object for WPPConnect
        sender_id = msg_data.get("from", "")
        wppconnect_data["sender"] = {
            "id": sender_id,
            "name": msg_data.get("notifyName", ""),
            "shortName": msg_data.get("notifyName", ""),
            "pushname": msg_data.get("notifyName", ""),
            "type": "in",
            "isBusiness": False,
            "isEnterprise": False,
            "isSmb": False,
            "isContactSyncCompleted": 0,
            "textStatusLastUpdateTime": -1,
            "syncToAddressbook": True,
            "formattedName": msg_data.get("notifyName", ""),
            "isMe": False,
            "isMyContact": True,
            "isPSA": False,
            "isUser": True,
            "isWAContact": True,
            "profilePicThumbObj": {"id": sender_id, "tag": ""},
            "msgs": None,
        }

        # adjust payload for special message types
        if wwebjs_data.get("dataType") == "media":
            wppconnect_data["body"] = wwebjs_data["data"].get("messageMedia", {}).get("data", "")
            wppconnect_data["mimetype"] = (
                wwebjs_data["data"].get("messageMedia", {}).get("mimetype", "")
            )

        if wwebjs_data.get("dataType") == "vote_update":
            wppconnect_data["type"] = "poll"
            wppconnect_data["body"] = wwebjs_data["data"].get("vote", {})

        # note this pulls from wppconnect_data instead of wwebjs_data
        if message.get("type") == "location":
            wppconnect_data["lat"] = message.get("location", {}).get("latitude", "")
            wppconnect_data["lng"] = message.get("location", {}).get("longitude", "")

        return wppconnect_data

    async def convert_lid_to_phone_number(self, lid: str) -> str:
        """POST /client/getContactLidAndPhone/{sessionId}"""
        data = {"userIds": [f"{lid}@lid"]}
        result = await self.send_rest_request(
            f"client/getContactLidAndPhone/{self.session}", data=data
        )

        if result.get("success") and (phone_number := result["data"][0].get("pn")):
            return str(phone_number.split("@")[0])

        return lid
