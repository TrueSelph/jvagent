"""Base API module with shared functionality for WhatsApp API wrappers."""

import base64
import logging
import mimetypes
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

import aiohttp
import filetype


@dataclass
class MessagePayload:
    """Structured message payload."""
    message_id: str
    event_type: str
    message_type: str
    author: str
    sender: str
    receiver: str
    caption: str = ""
    location: Dict[str, Any] = None
    fromMe: bool = False
    isGroup: bool = False
    isForwarded: bool = False
    sender_name: str = ""
    mentionedIds: List[str] = None
    body: str = ""
    media: str = ""
    filename: str = ""
    mime_type: str = ""
    quoted_message: Dict[str, Any] = None
    contact: Dict[str, Any] = None
    poll_id: str = ""
    selectedOptions: str = ""

    def __post_init__(self):
        if self.location is None:
            self.location = {}
        if self.mentionedIds is None:
            self.mentionedIds = []
        if self.quoted_message is None:
            self.quoted_message = {}
        if self.contact is None:
            self.contact = {}


class BaseWhatsAppAPI(ABC):
    """Base class with shared functionality for WhatsApp API wrappers."""

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
            api_url: API base URL
            session: Session/instance ID
            token: API authentication token
            secret_key: Optional secret key for session creation
            timeout: Request timeout in seconds
        """
        self.api_url = api_url.rstrip("/")
        self.session = session
        self.token = token
        self.secret_key = secret_key or os.environ.get("WPP_SECRET_KEY", "")
        self.timeout = timeout

    @abstractmethod
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
        """Generic async HTTP request to API. Must be implemented by subclass."""
        pass

    # Common HTTP request helper
    async def _make_request(
        self,
        url: str,
        method: str,
        headers: dict,
        data: Optional[dict] = None,
        params: Optional[dict] = None,
        json_body: bool = True,
    ) -> dict:
        """Internal helper for making HTTP requests."""
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
            self.logger.error(f"Request error: {str(e)}")
            return {"ok": False, "error": str(e)}

    # Message parsing utilities
    async def parse_inbound_message(self, request: dict) -> Optional[MessagePayload]:
        """Parses an inbound message request payload and returns structured data."""
        try:
            event = request.get("event")
            if event not in ["onmessage", "onpollresponse", "onack"]:
                return None

            payload = MessagePayload(
                message_id=self._extract_message_id(request),
                event_type=request.get("dataType", event),
                message_type=request.get("type", "unknown"),
                author=self._clean_phone_number(request.get("author", "")),
                sender=self._clean_phone_number(request.get("from", "")),
                receiver=self._clean_phone_number(request.get("to", "")),
                caption=request.get("caption", ""),
                location=request.get("location", {}),
                fromMe=self._extract_from_me(request),
                isGroup=request.get("isGroupMsg", False),
                isForwarded=request.get("isForwarded", False),
                sender_name=request.get("notifyName", ""),
                mentionedIds=request.get("mentionedJidList", []),
            )

            # Handle quoted messages
            if "quotedMsg" in request:
                payload.quoted_message = request["quotedMsg"]

            # Detect group messages
            if payload.author and payload.sender and payload.author != payload.sender:
                payload.isGroup = True

            # Parse content by type
            self._parse_message_content(payload, request)

            return payload

        except Exception as e:
            self.logger.error(f"Error parsing inbound message: {e}")
            return None

    def _extract_message_id(self, request: dict) -> str:
        """Extract message ID from various formats."""
        msg_id = request.get("id", "")
        if isinstance(msg_id, dict):
            return msg_id.get("id", "")
        return str(msg_id)

    def _extract_from_me(self, request: dict) -> bool:
        """Extract fromMe flag from various formats."""
        if isinstance(request.get("fromMe"), bool):
            return request.get("fromMe")
        elif isinstance(request.get("fromMe"), dict):
            return request.get("fromMe").get("fromMe", True)
        elif isinstance(request.get("id"), dict):
            return request.get("id").get("fromMe", True)

        return True

    def _clean_phone_number(self, phone: str) -> str:
        """Remove WhatsApp suffixes from phone numbers."""
        return str(phone).replace("@c.us", "").replace("@g.us", "")

    def _parse_message_content(self, payload: MessagePayload, request: dict) -> None:
        """Parse message content based on type."""
        if payload.message_type == "chat":
            payload.body = request.get("content", request.get("body", ""))
        
        elif payload.message_type in ["image", "video", "document"]:
            payload.media = request.get("body", "")
            payload.filename = request.get("filename", "")
            payload.mime_type = request.get("mimetype", "")
            if not payload.mime_type:
                payload.message_type = "ignored"
        
        elif payload.message_type == "location":
            payload.location = {
                "latitude": request.get("lat", ""),
                "longitude": request.get("lng", ""),
            }
        
        elif payload.message_type in ["audio", "ptt", "sticker"]:
            payload.media = request.get("body", "")
        
        elif payload.message_type in ["contacts", "vcard"]:
            payload.contact = request.get("body", {})
        
        elif payload.message_type == "poll" or payload.event_type == "onpollresponse":
            payload.poll_id = request.get("msgId", {}).get("_serialized", "")
            payload.selectedOptions = request.get("selectedOptions", "")
            payload.sender = self._clean_phone_number(request.get("chatId", ""))

    # File utilities
    @staticmethod
    async def get_file_type(
        file_path: Optional[str] = None,
        url: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> dict:
        """Determines the MIME type and category of a file."""
        mime_categories = {
            "image": ["image/jpeg", "image/png", "image/gif", "image/webp", "image/heic"],
            "document": ["application/pdf", "application/msword", "text/plain", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
            "audio": ["audio/mpeg", "audio/wav", "audio/ogg", "audio/mp3", "audio/webm"],
            "video": ["video/mp4", "video/mpeg", "video/webm", "video/quicktime"],
            "poll": ["application/poll", "application/vnd.jivas.poll"],
        }

        detected_mime = await BaseWhatsAppAPI._detect_mime_type(
            file_path, url, mime_type, mime_categories
        )

        # Categorize
        for category, mime_list in mime_categories.items():
            if detected_mime in mime_list:
                return {"file_type": category, "mime": detected_mime}

        return {"file_type": "unknown", "mime": detected_mime}

    @staticmethod
    async def _detect_mime_type(
        file_path: Optional[str],
        url: Optional[str],
        mime_type: Optional[str],
        mime_categories: dict,
    ) -> str:
        """Internal helper to detect MIME type."""
        if mime_type:
            return mime_type

        # Try from file path
        if file_path:
            detected, _ = mimetypes.guess_type(file_path)
            if detected:
                return detected

        # Try from URL
        if url:
            # Check URL extension
            for mimes in mime_categories.values():
                for mime in mimes:
                    ext = mime.split("/")[1]
                    if f".{ext}" in url:
                        return mime

            # Make HEAD request
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.head(url, allow_redirects=True) as response:
                        content_type = response.headers.get("Content-Type")
                        if content_type:
                            return content_type.split(";")[0]
            except Exception:
                pass

        return "application/octet-stream"

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

            return f"data:{content_type};base64,{encoded}" if force_prefix else encoded

        except Exception as e:
            BaseWhatsAppAPI.logger.error(f"Failed to fetch or encode file: {e}")
            return None

    @staticmethod
    def list_files_in_folder(directory: str, within_seconds: int = 0) -> List[str]:
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
                    created = os.path.getctime(file) if os.name == "nt" else file.stat().st_ctime
                    if (current_time - created) <= within_seconds:
                        recent_files.append(file.name)
                else:
                    recent_files.append(file.name)

        return recent_files