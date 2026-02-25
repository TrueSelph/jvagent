"""WWebJS API Wrapper with WPPConnect-compatible interface (Async Version)."""

import asyncio
import base64
import logging
from typing import Dict, List, Optional

import aiohttp

from .base import BaseWhatsAppAPI, MessagePayload

logger = logging.getLogger(__name__)


class WWebJSAPI(BaseWhatsAppAPI):
    """WWebJS API wrapper with WPPConnect-compatible interface (Async)."""

    # ========================================================================
    # INTERNAL HELPERS
    # ========================================================================

    def _format_chat_id(self, phone: str, is_group: bool = False) -> str:
        """Format phone number to WWebJS chat ID format."""
        if "@" in phone:
            return phone
        suffix = "@g.us" if is_group else "@c.us"
        return f"{phone}{suffix}"

    def _build_headers(self, headers: Optional[dict] = None) -> dict:
        """Build request headers with authentication."""
        if headers is None:
            headers = {}

        if "x-api-key" not in headers:
            if not self.secret_key:
                return {"error": "secret_key required for authentication"}
            headers["x-api-key"] = self.secret_key

        if "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"

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
        """Generic HTTP request to WWebJS API."""
        headers = self._build_headers(headers)
        if "error" in headers:
            return {"ok": False, "error": headers["error"]}

        url = endpoint if use_full_url else f"{self.api_url}/{endpoint.lstrip('/')}"
        result = await self._make_request(url, method, headers, data, params, json_body)

        # Normalize WWebJS response format
        if "success" in result:
            result["ok"] = result["success"]

        return result

    # ========================================================================
    # MESSAGE FORMAT TRANSLATION
    # ========================================================================

    async def parse_inbound_message(self, request: dict) -> Optional[MessagePayload]:
        """Parses WWebJS format message and converts to standard format."""
        wppconnect_format = await self.translate_wwebjs_to_wppconnect(request)
        return await super().parse_inbound_message(wppconnect_format)

    @staticmethod
    async def translate_wwebjs_to_wppconnect(wwebjs_data: dict) -> dict:
        """Translates message data from WWEBJS format to WPPConnect format."""
        message = wwebjs_data.get("data", {}).get("message", {})

        if wwebjs_data.get("dataType") == "vote_update":
            message = (
                wwebjs_data.get("data", {}).get("vote", {}).get("parentMessage", {})
            )

        msg_data = message.get("_data", {})
        msg_id = msg_data.get("id", {})

        # Build standard format
        wppconnect_data = {
            "event": "onmessage",
            "session": wwebjs_data.get("sessionId", "Dispatcher"),
            "id": msg_id.get("_serialized", ""),
            "body": msg_data.get("body", ""),
            "type": msg_data.get("type", "chat"),
            "t": msg_data.get("t", 0),
            "notifyName": msg_data.get("notifyName", ""),
            "from": msg_data.get("from", ""),
            "to": msg_data.get("to", ""),
            "fromMe": msg_id.get("fromMe", False),
            "isGroupMsg": "@g.us" in msg_data.get("from", ""),
            "timestamp": msg_data.get("t", 0),
            "content": msg_data.get("body", ""),
            "mimetype": wwebjs_data.get("data", {})
            .get("messageMedia", {})
            .get("mimetype", ""),
            "caption": msg_data.get("caption", ""),
            "sender": {
                "id": msg_data.get("from", ""),
                "name": msg_data.get("notifyName", ""),
                "pushname": msg_data.get("notifyName", ""),
            },
            "quotedMsg": msg_data.get("quotedMsg", {})
        }

        # Handle special message types
        if wwebjs_data.get("dataType") == "media":
            wppconnect_data["body"] = (
                wwebjs_data["data"].get("messageMedia", {}).get("data", "")
            )
            wppconnect_data["mimetype"] = (
                wwebjs_data["data"].get("messageMedia", {}).get("mimetype", "")
            )

        if wwebjs_data.get("dataType") == "vote_update":
            wppconnect_data["type"] = "poll"
            wppconnect_data["body"] = wwebjs_data["data"].get("vote", {})

        if message.get("type") == "location":
            wppconnect_data["lat"] = message.get("location", {}).get("latitude", "")
            wppconnect_data["lng"] = message.get("location", {}).get("longitude", "")

        return wppconnect_data

    # ========================================================================
    # SESSION MANAGEMENT
    # ========================================================================

    async def register_session(
        self,
        webhook_url: str = "",
        wait_qr_code: bool = True,
        auto_register: bool = True,
    ) -> dict:
        """Initialize the WWebJS session. The WWebJS endpoint intelligently handles session creation and start."""
        return await self.create_session(webhook=webhook_url)

    async def status(self) -> dict:
        """GET /session/status/{sessionId}"""
        result = await self.send_rest_request(
            f"session/status/{self.session}", method="GET"
        )

        # Check if the HTTP request failed (connection error, not API business logic)
        # If result has _exception_type, it's an HTTP-level failure
        if result.get("_exception_type") or (
            result.get("ok") is False
            and result.get("error")
            and not result.get("message")
        ):
            # HTTP request failed (service is down or unreachable)
            error_msg = result.get("error", "Unknown error")
            return {
                "ok": False,
                "error": error_msg,
                "status": "",
                "state": "",
                "message": f"Failed to check session status: {error_msg}",
            }

        # Normalize status field
        message = result.get("message", "")
        state = result.get("state")

        # For valid API responses (HTTP succeeded), set ok: True
        # Only HTTP/connection errors should have ok: False
        if not result.get("_exception_type"):
            result["ok"] = True

        if message == "session_not_found" or message in [
            "browser tab closed",
            "session closed",
        ]:
            result["status"] = ""
        elif message == "session_not_connected":
            result["status"] = state if state else "DISCONNECTED"
        elif message == "session_connected":
            result["status"] = "CONNECTED"
        elif state:
            result["status"] = state
        else:
            result["status"] = ""

        return result

    async def start_session(
        self, webhook: str = "", wait_qr_code: bool = False
    ) -> dict:
        """POST /session/start/{sessionId} with optional webhook URL"""
        if webhook:
            data = {"webhookUrl": webhook}
            result = await self.send_rest_request(
                f"session/start/{self.session}", method="POST", data=data
            )
        else:
            result = await self.send_rest_request(
                f"session/start/{self.session}", method="GET"
            )

        if wait_qr_code and result.get("ok", True):
            qr_result = await self.qrcode()
            if qr_result.get("ok"):
                result["qrcode_base64"] = qr_result.get("qrcode_base64")

        return result

    async def create_session(self, webhook: str = "") -> dict:
        """POST /session/start/{sessionId} with optional webhook URL"""
        if not self.secret_key:
            return {"ok": False, "error": "secret_key required"}

        if webhook:
            data = {"webhookUrl": webhook}
            result = await self.send_rest_request(
                f"session/start/{self.session}", method="POST", data=data
            )
        else:
            result = await self.send_rest_request(
                f"session/start/{self.session}", method="GET"
            )

        if result.get("ok") or result.get("success"):
            result["token"] = self.token
            result["session"] = self.session

        return result

    async def qrcode(self) -> dict:
        """GET /session/qr/{sessionId}/image - Returns QR code as base64 image"""
        try:
            response = await self.send_rest_request(
                f"session/qr/{self.session}/image", method="GET"
            )

            if "raw" in response:
                qr_base64 = base64.b64encode(response["raw"]).decode("ascii")
                return {"ok": True, "qrcode_base64": qr_base64, "qrcode": qr_base64}

            error_msg = (
                response.get("message") or response.get("error") or "Unknown error"
            )
            return {"ok": False, "error": error_msg}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def close_session(self) -> dict:
        """GET /session/stop/{sessionId} - Close/terminate the session"""
        return await self.send_rest_request(
            f"session/stop/{self.session}", method="GET"
        )

    async def update_webhook(self, webhook: str) -> dict:
        """PATCH /session/webhook/{sessionId} - Update webhook URL for the session"""
        data = {"webhookUrl": webhook}
        return await self.send_rest_request(
            f"session/webhook/{self.session}", method="PATCH", data=data
        )

    async def logout_session(self) -> dict:
        """GET /session/terminate/{sessionId} - Logout from the session"""
        return await self.send_rest_request(
            f"session/terminate/{self.session}", method="GET"
        )

    async def check_connection(self) -> dict:
        """GET /session/status/{sessionId} - Check connection status (alias for status)"""
        return await self.status()

    async def set_typing_status(
        self, phone: str, value: bool = True, is_group: bool = False
    ) -> dict:
        """POST /chat/sendStateTyping/{sessionId} or /chat/clearState/{sessionId}

        Set or clear typing status in chat"""
        chat_id = self._format_chat_id(phone, is_group)
        data = {"chatId": chat_id}

        if value:
            # Start typing - lasts for 25 seconds
            return await self.send_rest_request(
                f"chat/sendStateTyping/{self.session}", data=data
            )
        else:
            # Stop typing immediately
            return await self.send_rest_request(
                f"chat/clearState/{self.session}", data=data
            )

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
        """POST /client/sendMessage/{sessionId}"""
        chat_id = self._format_chat_id(phone, is_group)

        payload_options = {}
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

        return await self.send_rest_request(
            f"client/sendMessage/{self.session}", data=data
        )

    async def _send_message_media(
        self,
        phone: str,
        content: dict,
        caption: str = "",
        is_group: bool = False,
    ) -> dict:
        """Helper for sending media messages."""
        chat_id = self._format_chat_id(phone, is_group)

        data = {
            "chatId": chat_id,
            "contentType": "MessageMedia",
            "content": content,
        }

        if caption:
            data["options"] = {"caption": caption}

        return await self.send_rest_request(
            f"client/sendMessage/{self.session}", data=data
        )

    async def send_image(self, phone: str, file_url: str = "", **kwargs) -> dict:
        """POST /client/sendMessage/{sessionId} with MessageMedia"""
        base64_data = await self.file_url_to_base64(file_url, force_prefix=False)
        if not base64_data:
            return {"ok": False, "error": "Failed to encode file"}

        file_info = await self.get_file_type(url=file_url)
        content = {
            "mimetype": file_info["mime"],
            "data": base64_data,
            "filename": kwargs.get("filename", "image.jpg"),
        }

        return await self._send_message_media(
            phone, content, kwargs.get("caption", ""), kwargs.get("is_group", False)
        )

    async def send_file(self, phone: str, file_url: str = "", **kwargs) -> dict:
        """POST /client/sendMessage/{sessionId} with MessageMedia"""
        base64_data = await self.file_url_to_base64(file_url, force_prefix=False)
        if not base64_data:
            return {"ok": False, "error": "Failed to encode file"}

        file_info = await self.get_file_type(url=file_url)
        content = {
            "mimetype": file_info["mime"],
            "data": base64_data,
            "filename": kwargs.get("filename", "file"),
        }

        return await self._send_message_media(
            phone, content, kwargs.get("caption", ""), kwargs.get("is_group", False)
        )

    async def send_voice(
        self,
        phone: str,
        file_url: str,
        is_group: bool = False,
        quoted_message_id: str = "",
    ) -> dict:
        """POST /client/sendMessage/{sessionId} with voice/audio MessageMedia"""
        base64_data = await self.file_url_to_base64(file_url, force_prefix=False)
        if not base64_data:
            return {"ok": False, "error": "Failed to encode audio file"}

        file_info = await self.get_file_type(url=file_url)
        content = {
            "mimetype": file_info["mime"],
            "data": base64_data,
            "filename": "voice.ogg",
        }

        chat_id = self._format_chat_id(phone, is_group)
        data = {
            "chatId": chat_id,
            "contentType": "MessageMedia",
            "content": content,
            "options": {"sendAudioAsVoice": True},
        }

        if quoted_message_id:
            data["options"]["quotedMessageId"] = quoted_message_id

        return await self.send_rest_request(
            f"client/sendMessage/{self.session}", data=data
        )

    async def send_location(
        self,
        phone: str,
        latitude: float,
        longitude: float,
        title: str = "",
        is_group: bool = False,
    ) -> dict:
        """POST /client/sendMessage/{sessionId} with location"""
        chat_id = self._format_chat_id(phone, is_group)

        data = {
            "chatId": chat_id,
            "contentType": "Location",
            "content": {
                "latitude": latitude,
                "longitude": longitude,
                "name": title or "Location",
            },
        }

        return await self.send_rest_request(
            f"client/sendMessage/{self.session}", data=data
        )

    # ========================================================================
    # GROUPS
    # ========================================================================

    async def create_group(self, name: str, participants: List[str]) -> dict:
        """POST /client/createGroup/{sessionId}"""
        formatted_participants = [self._format_chat_id(p, False) for p in participants]
        data = {"title": name, "participants": formatted_participants}
        return await self.send_rest_request(
            f"client/createGroup/{self.session}", data=data
        )

    async def _manage_group_participant(
        self, action: str, group_id: str, phone: str
    ) -> dict:
        """Helper for group participant management."""
        group_chat_id = self._format_chat_id(group_id, True)
        participant_id = self._format_chat_id(phone, False)
        data = {"groupId": group_chat_id, "participantId": participant_id}
        return await self.send_rest_request(
            f"group/{action}Participant/{self.session}", data=data
        )

    async def add_group_participant(self, group_id: str, phone: str) -> dict:
        """POST /group/addParticipant/{sessionId}"""
        return await self._manage_group_participant("add", group_id, phone)

    async def remove_group_participant(self, group_id: str, phone: str) -> dict:
        """POST /group/removeParticipant/{sessionId}"""
        return await self._manage_group_participant("remove", group_id, phone)

    async def promote_group_admin(self, group_id: str, phone: str) -> dict:
        """POST /group/promoteParticipant/{sessionId}"""
        return await self._manage_group_participant("promote", group_id, phone)

    async def demote_group_admin(self, group_id: str, phone: str) -> dict:
        """POST /group/demoteParticipant/{sessionId}"""
        return await self._manage_group_participant("demote", group_id, phone)

    async def group_members(self, group_id: str) -> dict:
        """GET /group/participants/{sessionId}"""
        if not group_id:
            return {"ok": False, "error": "group_id required"}

        # CORRECTED: Use the endpoint from old code
        group_chat_id = self._format_chat_id(group_id, True)
        data = {"chatId": group_chat_id}
        result = await self.send_rest_request(
            f"groupChat/getClassInfo/{self.session}", data=data
        )

        # Process response similar to old code
        participants = (
            result.get("chat", {}).get("groupMetadata", {}).get("participants", [])
        )

        host_device = await self.get_host_device()
        host_number = host_device.get("sessionInfo", {}).get("me", {}).get("user", "")

        response = [
            {
                "id": {
                    "user": participant.get("id", {}).get("user", "").split("@")[0],
                },
                "formattedName": (
                    "You"
                    if participant.get("id", {}).get("user", "").split("@")[0]
                    == host_number
                    else host_number
                ),
            }
            for participant in participants
        ]

        return {
            "status": "success" if result.get("success") else "error",
            "response": response,
        }

    # ========================================================================
    # UTILITIES
    # ========================================================================

    async def get_contacts(self) -> dict:
        """GET /client/getContacts/{sessionId}"""
        return await self.send_rest_request(
            f"client/getContacts/{self.session}", method="GET"
        )

    async def get_contact(self, phone: str) -> dict:
        """GET /client/getContactById/{sessionId}"""
        chat_id = self._format_chat_id(phone, False)
        data = {"contactId": chat_id}
        return await self.send_rest_request(
            f"client/getContactById/{self.session}", data=data
        )

    async def list_chats(self, options: Optional[dict] = None) -> dict:
        """POST /client/getChats/{sessionId}"""
        data = {"searchOptions": options} if options else {}
        return await self.send_rest_request(
            f"client/getChats/{self.session}", data=data
        )

    async def get_chat_by_id(self, phone: str) -> dict:
        """GET /client/getChatById/{sessionId}"""
        chat_id = self._format_chat_id(phone, False)
        data = {"chatId": chat_id}
        return await self.send_rest_request(
            f"client/getChatById/{self.session}", data=data
        )

    async def read_chat(self, chatid: str) -> dict:
        """POST /chat/sendSeen/{sessionId}"""
        return await self.send_rest_request(
            f"chat/sendSeen/{self.session}", data={"chatId": chatid}
        )

    async def get_profile_picture(self, phone: str) -> dict:
        """GET /client/getProfilePicUrl/{sessionId}"""
        chat_id = self._format_chat_id(phone, False)
        data = {"contactId": chat_id}
        return await self.send_rest_request(
            f"client/getProfilePicUrl/{self.session}", data=data
        )

    async def get_host_device(self) -> dict:
        """GET /client/getClassInfo/{sessionId}"""
        return await self.send_rest_request(
            f"client/getClassInfo/{self.session}", method="GET"
        )

    async def health_check(self) -> dict:
        """GET /ping"""
        return await self.send_rest_request("ping", method="GET")

    async def convert_lid_to_phone_number(self, lid: str) -> str:
        """POST /client/getContactLidAndPhone/{sessionId}"""
        data = {"userIds": [f"{lid}@lid"]}
        result = await self.send_rest_request(
            f"client/getContactLidAndPhone/{self.session}", data=data
        )

        if result.get("success") and (phone_number := result["data"][0].get("pn")):
            return str(phone_number.split("@")[0])

        return lid

    # ========================================================================
    # ADDITIONAL METHODS FROM OLD CODE
    # ========================================================================

    async def show_all_sessions(self) -> dict:
        """GET /session/getSessions"""
        return await self.send_rest_request("session/getSessions", method="GET")

    async def send_reply(
        self, phone: str, message: str, message_id: str, is_group: bool = False
    ) -> dict:
        """POST /client/sendMessage/{sessionId} with quotedMessageId"""
        return await self.send_message(phone, message, is_group, message_id=message_id)

    async def send_contact(
        self, phone: str, contactid: str, is_group: bool = False
    ) -> dict:
        """POST /client/sendMessage/{sessionId} with Contact"""
        chat_id = self._format_chat_id(phone, is_group)
        contact_chat_id = self._format_chat_id(contactid, False)

        data = {
            "chatId": chat_id,
            "contentType": "Contact",
            "content": {"contactId": contact_chat_id},
        }

        return await self.send_rest_request(
            f"client/sendMessage/{self.session}", data=data
        )

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
        file_info = await self.get_file_type(file_path=filename)

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

        return await self.send_rest_request(
            f"client/sendMessage/{self.session}", data=data
        )

    async def send_voice_base64(
        self, phone: str, base64_ptt: str, is_group: bool = False
    ) -> dict:
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

        result = await self.send_rest_request(
            f"client/sendMessage/{self.session}", data=data
        )

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

        result = await self.send_rest_request(
            f"client/sendMessage/{self.session}", data=data
        )
        if result.get("success"):
            return {
                "status": "success",
                "response": [{"id": result["message"]["_data"]["id"]["id"]}],
                "message": result,
            }
        return {"status": False}

    async def leave_group(self, group_id: str) -> dict:
        """POST /group/leaveGroup/{sessionId}"""
        group_chat_id = self._format_chat_id(group_id, True)
        data = {"groupId": group_chat_id}
        return await self.send_rest_request(
            f"group/leaveGroup/{self.session}", data=data
        )

    async def set_group_subject(self, group_id: str, title: str) -> dict:
        """POST /group/setSubject/{sessionId}"""
        group_chat_id = self._format_chat_id(group_id, True)
        data = {"groupId": group_chat_id, "title": title}
        return await self.send_rest_request(
            f"group/setSubject/{self.session}", data=data
        )

    async def set_group_description(self, group_id: str, description: str) -> dict:
        """POST /group/setDescription/{sessionId}"""
        group_chat_id = self._format_chat_id(group_id, True)
        data = {"groupId": group_chat_id, "description": description}
        return await self.send_rest_request(
            f"group/setDescription/{self.session}", data=data
        )

    async def block_contact(self, phone: str, is_group: bool = False) -> dict:
        """POST /contact/block/{sessionId}"""
        contact_id = self._format_chat_id(phone, is_group)
        data = {"contactId": contact_id}
        return await self.send_rest_request(f"contact/block/{self.session}", data=data)

    async def unblock_contact(self, phone: str, is_group: bool = False) -> dict:
        """POST /contact/unblock/{sessionId}"""
        contact_id = self._format_chat_id(phone, is_group)
        data = {"contactId": contact_id}
        return await self.send_rest_request(
            f"contact/unblock/{self.session}", data=data
        )

    async def get_blocklist(self) -> dict:
        """POST /client/getBlockedContacts/{sessionId}"""
        return await self.send_rest_request(f"client/getBlockedContacts/{self.session}")

    async def clear_chat(self, phone: str, is_group: bool = False) -> dict:
        """POST /chat/clearMessages/{sessionId}"""
        chat_id = self._format_chat_id(phone, is_group)
        data = {"chatId": chat_id}
        return await self.send_rest_request(
            f"chat/clearMessages/{self.session}", data=data
        )

    async def archive_chat(self, phone: str, is_group: bool = False) -> dict:
        """POST /client/archiveChat/{sessionId}"""
        chat_id = self._format_chat_id(phone, is_group)
        data = {"chatId": chat_id}
        return await self.send_rest_request(
            f"client/archiveChat/{self.session}", data=data
        )

    async def unarchive_chat(self, phone: str, is_group: bool = False) -> dict:
        """POST /client/archiveChat/{sessionId} - WWebJS toggles archive state"""
        return await self.archive_chat(phone, is_group)

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
            return await self.send_rest_request(
                f"chat/clearState/{self.session}", data=data
            )

    async def device_battery(self) -> dict:
        """GET /device/getBatteryLevel/{sessionId}"""
        return await self.send_rest_request(
            f"device/getBatteryLevel/{self.session}", method="GET"
        )

    async def mark_unread(self, chatid: str) -> dict:
        """POST /client/markChatUnread/{sessionId}"""
        data = {"chatId": chatid}
        return await self.send_rest_request(
            f"client/markChatUnread/{self.session}", data=data
        )

    async def get_message_by_id(self, message_id: str) -> dict:
        """POST /message/getMessageById/{sessionId}"""
        data = {"messageId": message_id}
        return await self.send_rest_request(
            f"message/getMessageById/{self.session}", data=data
        )

    async def forward_messages(
        self, phone: str, message_ids: list, is_group: bool = False
    ) -> dict:
        """POST /message/forward/{sessionId}"""
        chat_id = self._format_chat_id(phone, is_group)
        data = {"chatId": chat_id, "messageIds": message_ids}
        return await self.send_rest_request(
            f"message/forward/{self.session}", data=data
        )

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
        }
        return await self.send_rest_request(f"message/delete/{self.session}", data=data)

    async def change_username(self, name: str) -> dict:
        """POST /client/setDisplayName/{sessionId}"""
        data = {"displayName": name}
        return await self.send_rest_request(
            f"client/setDisplayName/{self.session}", data=data
        )

    async def set_profile_status(self, status: str) -> dict:
        """POST /client/setStatus/{sessionId}"""
        data = {"status": status}
        return await self.send_rest_request(
            f"client/setStatus/{self.session}", data=data
        )

    async def set_profile_pic(self, file_data: bytes) -> dict:
        """POST /client/setProfilePicture/{sessionId}"""
        # Convert bytes to base64
        base64_data = base64.b64encode(file_data).decode("utf-8")
        data = {"base64": base64_data}
        return await self.send_rest_request(
            f"client/setProfilePicture/{self.session}", data=data
        )
