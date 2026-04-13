"""This module provides the FacebookAPI class for interacting with the Facebook Graph API."""

import logging
import mimetypes
import traceback
from typing import Any, Dict, List, Optional, Tuple, Union

import requests


class FacebookAPI:
    """
    Graph API client with explicit tokens: Page vs app vs user (me / me/accounts).

    - ``page_access_token``: Page-scoped calls (feed, Messenger, comments, …).
    - ``app_access_token``: App-level calls (e.g. webhook subscriptions). Defaults to
      ``{app_id}|{app_secret}`` when not provided.
    - ``user_access_token``: User OAuth token for ``/me`` and ``/me/accounts``. If omitted,
      user-scoped helpers fall back to ``page_access_token`` for legacy single-token setups.
    """

    logger = logging.getLogger(__name__)

    def __init__(
        self,
        api_url: str,
        app_secret: str,
        app_id: str,
        page_id: str,
        page_access_token: str,
        verify_token: str,
        fields: Optional[str] = None,
        timeout: int = 10,
        published: bool = True,
        user_access_token: Optional[str] = None,
        app_access_token: Optional[str] = None,
    ) -> None:
        self.api_url = api_url
        self.app_secret = app_secret
        self.app_id = app_id
        self.page_id = page_id
        self.page_access_token = page_access_token.strip() if page_access_token else ""
        _u = user_access_token.strip() if user_access_token else None
        self.user_access_token = _u or None
        _a = app_access_token.strip() if app_access_token else None
        self.app_access_token = _a or None
        self.verify_token = verify_token
        self.fields = fields
        self.timeout = timeout
        self.published = published

    def _token_for_page(self) -> str:
        return self.page_access_token

    def _token_for_app(self) -> str:
        if self.app_access_token:
            return self.app_access_token
        return f"{self.app_id}|{self.app_secret}"

    def _token_for_user(self) -> str:
        if self.user_access_token:
            return self.user_access_token
        return self.page_access_token

    def send_rest_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        data: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        json_body: Optional[Dict] = None,
    ) -> Dict:
        """Centralized method to send HTTP requests with standardized error handling."""
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            url = endpoint
        else:
            url = f"{self.api_url}{endpoint}"

        try:
            response = requests.request(
                method=method.upper(),
                url=url,
                params=params,
                data=data,
                json=json_body,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json() if response.content else {}
        except requests.Timeout as e:
            self.logger.error(f"Request timed out after {self.timeout} seconds: {e}")
            return {"error": f"Timeout after {self.timeout} seconds"}
        except requests.RequestException as e:
            error_details = None
            if e.response is not None and e.response.content:
                try:
                    error_details = e.response.json()
                except ValueError:
                    error_details = {"raw": (e.response.text or "")[:2000]}
            message = str(e)
            if isinstance(error_details, dict):
                fb_err = error_details.get("error")
                if isinstance(fb_err, dict) and fb_err.get("message"):
                    message = str(fb_err.get("message"))
                elif isinstance(fb_err, str):
                    message = fb_err
            self.logger.error("Graph request error: %s", message)
            return {"error": message, "details": error_details}
        except Exception as e:
            self.logger.error(f"Unexpected error: {traceback.format_exc()}")
            return {"error": str(e)}

    def parse_verification_request(self, request: Dict) -> Union[str, Dict[Any, Any]]:
        """Parses verification request payload and returns the challenge value if the token is valid."""
        try:
            hub_mode = request.get("hub.mode")
            hub_verify_token = request.get("hub.verify_token")
            hub_challenge = request.get("hub.challenge")

            if hub_verify_token == self.verify_token and hub_mode == "subscribe":
                return hub_challenge if hub_challenge is not None else ""
            return {"message": "Invalid token or mode", "code": 403}
        except Exception as e:
            self.logger.error(
                f"Unable to process verification request: {traceback.format_exc()}"
            )
            return {"error": str(e)}

    def register_session(self, webhook_url: str) -> Dict:
        """Update Facebook webhook (uses app access token).

        Uses ``application/x-www-form-urlencoded`` body, matching common Graph API
        examples for ``POST /{app-id}/subscriptions``.
        """
        endpoint = f"{self.app_id}/subscriptions"
        app_token = self._token_for_app()
        params = {"access_token": app_token}
        fields_val = self.fields or "messages"
        if isinstance(fields_val, list):
            fields_val = ",".join(str(x) for x in fields_val)
        body = {
            "object": "page",
            "callback_url": webhook_url,
            "fields": str(fields_val),
            "verify_token": str(self.verify_token or ""),
            "include_values": "true",
        }
        return self.send_rest_request(
            "POST", endpoint, params=params, data=body, json_body=None
        )

    @staticmethod
    def _messenger_coordinate_value(coords: Any, *keys: str) -> Optional[float]:
        if not isinstance(coords, dict):
            return None
        for k in keys:
            v = coords.get(k)
            if v is None:
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _messenger_attachment_location_coords(
        att: Any,
    ) -> Optional[tuple[float, float]]:
        """Return (lat, lon) if this is a Messenger ``location`` attachment."""
        if (
            not isinstance(att, dict)
            or str(att.get("type") or "").lower() != "location"
        ):
            return None
        payload = att.get("payload")
        if not isinstance(payload, dict):
            return None
        coords = payload.get("coordinates")
        if not isinstance(coords, dict):
            return None
        lat = FacebookAPI._messenger_coordinate_value(coords, "lat")
        lon = FacebookAPI._messenger_coordinate_value(coords, "long", "lng", "lon")
        if lat is None or lon is None:
            return None
        return (lat, lon)

    @staticmethod
    def _messenger_attachment_url(att: Any) -> Optional[str]:
        if not isinstance(att, dict):
            return None
        payload = att.get("payload")
        if not isinstance(payload, dict):
            return None
        u = payload.get("url")
        if isinstance(u, str) and u.strip():
            return u.strip()
        return None

    @staticmethod
    def _messenger_message_has_processable_content(msg: Dict[str, Any]) -> bool:
        text = msg.get("text")
        if text is not None and str(text).strip():
            return True
        attachments = msg.get("attachments") or []
        if not isinstance(attachments, list):
            return False
        for att in attachments:
            if FacebookAPI._messenger_attachment_url(att):
                return True
            if FacebookAPI._messenger_attachment_location_coords(att) is not None:
                return True
        return False

    @staticmethod
    def download_messenger_attachment(
        url: str,
        page_access_token: str,
        timeout: int = 60,
    ) -> Tuple[Optional[bytes], Optional[str]]:
        """Fetch attachment bytes from a Graph CDN URL (requires Page access token)."""
        u = (url or "").strip()
        tok = (page_access_token or "").strip()
        if not u or not tok:
            return None, None
        try:
            r = requests.get(
                u,
                params={"access_token": tok},
                timeout=timeout,
            )
            r.raise_for_status()
            ct = r.headers.get("Content-Type")
            mime: Optional[str] = None
            if ct:
                mime = ct.split(";", 1)[0].strip() or None
            return r.content, mime
        except requests.RequestException as e:
            FacebookAPI.logger.warning(
                "Messenger attachment download failed: %s", str(e)[:500]
            )
            return None, None

    @staticmethod
    def iter_messenger_user_text_events(
        request: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Parse Meta Page webhook JSON and return inbound Messenger user **message** events.

        Emits an event when the user message has non-empty ``text`` after strip **or**
        at least one attachment whose ``payload.url`` is a non-empty string **or** a
        ``location`` attachment with ``payload.coordinates``.

        Skips: feed ``changes``, ``delivery``, ``read``, ``postback``, ``reaction``,
        ``message.is_echo``, and messages with no usable text and no processable
        attachments.

        Each item includes: ``sender_id`` (PSID), ``page_id``, ``message`` (text,
        possibly empty when attachment-only), ``attachments``, ``mid``,
        ``timestamp`` (messaging epoch ms), ``reply_to`` (optional),
        ``sender_name`` (often empty), ``messaging`` (raw event subset for metadata).
        """
        out: List[Dict[str, Any]] = []
        if not isinstance(request, dict):
            return out
        if request.get("object") != "page":
            return out
        entries = request.get("entry")
        if not isinstance(entries, list):
            return out

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            page_id = str(entry.get("id", ""))
            if "changes" in entry:
                continue
            messaging_list = entry.get("messaging")
            if not isinstance(messaging_list, list):
                continue
            for messaging in messaging_list:
                if not isinstance(messaging, dict):
                    continue
                if any(
                    k in messaging for k in ("postback", "delivery", "read", "reaction")
                ):
                    continue
                msg = messaging.get("message")
                if not isinstance(msg, dict):
                    continue
                if msg.get("is_echo"):
                    continue
                if not FacebookAPI._messenger_message_has_processable_content(msg):
                    continue
                raw_text = msg.get("text")
                message_text = str(raw_text).strip() if raw_text is not None else ""
                sender = messaging.get("sender")
                if not isinstance(sender, dict):
                    continue
                sender_id = sender.get("id")
                if not sender_id:
                    continue
                attachments = msg.get("attachments") or []
                if not isinstance(attachments, list):
                    attachments = []
                raw_reply = msg.get("reply_to")
                reply_to = raw_reply if isinstance(raw_reply, dict) else None
                ts = messaging.get("timestamp")
                try:
                    timestamp = int(ts) if ts is not None else 0
                except (TypeError, ValueError):
                    timestamp = 0
                out.append(
                    {
                        "sender_name": "",
                        "sender_id": str(sender_id),
                        "page_id": page_id,
                        "message_type": "message",
                        "message": message_text,
                        "attachments": attachments,
                        "caption": "",
                        "mid": str(msg.get("mid", "")) if msg.get("mid") else "",
                        "parent_message_id": "",
                        "timestamp": timestamp,
                        "reply_to": reply_to,
                        "data": request,
                        "messaging": messaging,
                    }
                )
        return out

    @staticmethod
    def parse_inbound_message(request: Dict) -> Dict:
        """Parses message request payload and returns extracted values.

        For Messenger webhooks with multiple events, prefer
        :meth:`iter_messenger_user_text_events`.
        """
        payload: Dict[str, Any] = {}
        try:
            entries = request.get("entry")
            if not isinstance(entries, list) or not entries:
                return {"ok": False, "error": "missing entry"}
            entry = entries[0]
            if not isinstance(entry, dict):
                return {"ok": False, "error": "invalid entry"}
            page_id = entry.get("id", "")
            sender_id = ""
            message_type = ""
            message = ""
            sender_name = ""
            attachments: List[Any] = []
            caption = ""
            parent_message_id = ""

            if "changes" in entry:
                changes = entry.get("changes") or []
                if not changes or not isinstance(changes[0], dict):
                    return {"ok": False, "error": "invalid changes"}
                change = changes[0].get("value") or {}
                if not isinstance(change, dict):
                    return {"ok": False, "error": "invalid change value"}
                from_obj = change.get("from") or {}
                sender_id = (
                    str(from_obj.get("id", "")) if isinstance(from_obj, dict) else ""
                )
                sender_name = (
                    str(from_obj.get("name", "")) if isinstance(from_obj, dict) else ""
                )
                message_type = str(change.get("item", ""))
                message = change.get("message") or change.get("reaction_type")
            elif "messaging" in entry:
                messaging_list = entry.get("messaging") or []
                if not messaging_list or not isinstance(messaging_list[0], dict):
                    return {"ok": False, "error": "invalid messaging"}
                messaging = messaging_list[0]
                sender = messaging.get("sender") or {}
                if isinstance(sender, dict):
                    sender_id = str(sender.get("id", ""))
                msg = messaging.get("message")
                if isinstance(msg, dict):
                    message_type = "message"
                    text = msg.get("text")
                    message = str(text).strip() if text is not None else ""
                    raw_att = msg.get("attachments") or []
                    attachments = raw_att if isinstance(raw_att, list) else []

            payload = {
                "sender_name": sender_name,
                "sender_id": sender_id,
                "page_id": page_id,
                "message_type": message_type,
                "message": message,
                "attachments": attachments,
                "caption": caption,
                "data": request,
                "parent_message_id": parent_message_id,
            }
            return payload
        except Exception as e:
            FacebookAPI.logger.error(
                f"Facebook API: Error processing inbound message: {e}"
            )
            return {"ok": False, "error": str(e)}

    def send_text_message(self, recipient_id: str, message: str) -> Dict:
        """Send text message to a Facebook user via Messenger."""
        endpoint = f"{self.page_id}/messages"
        headers = {"Content-Type": "application/json"}
        data = {
            "recipient": {"id": recipient_id},
            "messaging_type": "RESPONSE",
            "message": {"text": message},
        }
        params = {"access_token": self._token_for_page()}
        return self.send_rest_request(
            "POST", endpoint, headers=headers, json_body=data, params=params
        )

    def send_sender_action(self, recipient_id: str, sender_action: str) -> Dict:
        """Messenger Send API sender action: ``mark_seen``, ``typing_on``, or ``typing_off``.

        POSTs to ``{page_id}/messages`` with only ``recipient`` and ``sender_action``
        (no ``messaging_type`` or ``message`` body).
        """
        endpoint = f"{self.page_id}/messages"
        headers = {"Content-Type": "application/json"}
        data = {
            "recipient": {"id": recipient_id},
            "sender_action": sender_action,
        }
        params = {"access_token": self._token_for_page()}
        return self.send_rest_request(
            "POST", endpoint, headers=headers, json_body=data, params=params
        )

    def get_messenger_psid_profile(
        self,
        psid: str,
        fields: str = "first_name,last_name,profile_pic",
    ) -> Dict:
        """Graph user profile for a Messenger PSID (Page-Scoped ID).

        Uses the **page** access token. For apps in Live mode, Meta may require
        ``business_asset_user_profile_access`` (and standard ``pages_messaging``)
        to return full name and profile fields.
        """
        endpoint = (psid or "").strip()
        if not endpoint:
            return {"error": "psid is required"}
        params = {
            "fields": fields,
            "access_token": self._token_for_page(),
        }
        return self.send_rest_request("GET", endpoint, params=params)

    def send_media(
        self,
        recipient_id: str,
        media_url: str,
        media_type: str,
    ) -> Dict:
        """Send a media message (audio, image, video, or document) to a user via Messenger."""
        endpoint = f"{self.page_id}/messages"
        headers = {"Content-Type": "application/json"}
        data = {
            "recipient": {"id": recipient_id},
            "messaging_type": "RESPONSE",
            "message": {
                "attachment": {
                    "type": media_type,
                    "payload": {"url": media_url, "is_reusable": True},
                }
            },
        }
        params = {"access_token": self._token_for_page()}
        return self.send_rest_request(
            "POST", endpoint, headers=headers, json_body=data, params=params
        )

    def get_user_info(self, fields: str = "id,name") -> Dict:
        """Fetches user information from the Facebook Graph API (user token)."""
        endpoint = "me"
        params = {"fields": fields, "access_token": self._token_for_user()}
        return self.send_rest_request("GET", endpoint, params=params)

    def list_all_pages(self, limit: int = 100) -> Union[List, Dict]:
        """Lists all pages managed by the user (user token)."""

        try:
            all_pages = []
            endpoint = "me/accounts"
            token = self._token_for_user()
            params = {"access_token": token, "limit": limit}

            while True:
                response = self.send_rest_request("GET", endpoint, params=params)
                if response.get("error"):
                    return response
                all_pages.extend(response.get("data", []))
                paging = response.get("paging", {})
                next_page = paging.get("next")
                if not next_page:
                    break
                endpoint = next_page
                params = {}
            return all_pages
        except Exception as e:
            self.logger.error(f"Facebook API: Error listing pages: {e}")
            return {"ok": False, "error": str(e)}

    def get_page_details(
        self,
        fields: str = "id,name,about,fan_count,access_token",
    ) -> Dict:
        """Fetches details of a Facebook page (page token)."""
        endpoint = self.page_id
        params = {"fields": fields, "access_token": self._token_for_page()}
        return self.send_rest_request("GET", endpoint, params=params)

    def post_message_to_page(self, message: str) -> Dict:
        """Posts a message to a Facebook page."""
        endpoint = f"{self.page_id}/feed"
        headers = {"Content-Type": "application/json"}
        json_data = {"message": message}
        params = {"access_token": self._token_for_page()}
        if not self.published:
            params["published"] = "false"
            params["unpublished_content_type"] = "DRAFT"
        return self.send_rest_request(
            "POST", endpoint, headers=headers, json_body=json_data, params=params
        )

    def get_page_posts(
        self,
        limit: int = 10,
        fields: Optional[str] = None,
        post_filter: Optional[str] = None,
    ) -> Union[List, Dict]:
        """Retrieves posts via the Page feed edge (page token).
        Args:
            limit: The number of posts to retrieve.
            fields: The fields to retrieve.
            post_filter: The filter to apply to the posts. e.g. since=2024-11-11&until=2024-11-16
        Returns:
            A list of posts.
        """
        endpoint = f"{self.page_id}/feed"
        if post_filter:
            endpoint = f"{endpoint}?{post_filter}"

        field_list = fields or (
            "id,message,story,created_time,permalink_url,status_type,updated_time"
        )
        params = {
            "access_token": self._token_for_page(),
            "limit": limit,
            "fields": field_list,
        }
        return self.send_rest_request("GET", endpoint, params=params)

    def get_single_post(self, post_id: str) -> Dict:
        """Retrieves a single post from a Facebook page by post ID."""
        endpoint = post_id
        params = {"access_token": self._token_for_page()}
        return self.send_rest_request("GET", endpoint, params=params)

    def get_messenger_message(
        self,
        message_id: str,
        fields: str = "message,attachments,from,timestamp",
    ) -> Dict:
        """Fetch a Messenger message node by Graph ID (e.g. parent of ``reply_to``).

        Requires a Page access token with permissions to read the message.
        """
        mid = (message_id or "").strip()
        if not mid:
            return {"error": {"message": "missing message_id"}}
        params = {
            "fields": fields,
            "access_token": self._token_for_page(),
        }
        return self.send_rest_request("GET", mid, params=params)

    def comment_on_post(self, post_id: str, message: str) -> Dict:
        """Comments on a Facebook post."""
        endpoint = f"{post_id}/comments"
        params = {"message": message, "access_token": self._token_for_page()}
        return self.send_rest_request("POST", endpoint, params=params)

    def post_images_to_page(self, image_urls: List[str], caption: str) -> Dict:
        """Uploads multiple photos to a Facebook page using URLs."""

        try:
            image_ids = []
            for image_url in image_urls:
                endpoint = f"{self.page_id}/photos"
                params = {
                    "access_token": self._token_for_page(),
                    "url": image_url,
                    "published": "false",
                }
                response = self.send_rest_request("POST", endpoint, params=params)
                if "error" not in response:
                    image_ids.append(response.get("id"))

            if not image_ids:
                return {"error": "Failed to upload any images"}

            endpoint = f"{self.page_id}/feed"
            params = {"access_token": self._token_for_page()}
            if not self.published:
                params["published"] = "false"
                params["unpublished_content_type"] = "DRAFT"
            json_data = {
                "message": caption,
                "attached_media": [{"media_fbid": _id} for _id in image_ids],
            }
            return self.send_rest_request(
                "POST", endpoint, params=params, json_body=json_data
            )
        except Exception as e:
            self.logger.error(f"Facebook API: Error posting images: {e}")
            return {"ok": False, "error": str(e)}

    def post_videos_to_page(
        self, title: str, caption: str, video_urls: List[str]
    ) -> Dict:
        """Uploads multiple videos to a Facebook page using URLs."""
        try:
            video_ids = []
            for video_url in video_urls:
                endpoint = f"{self.page_id}/videos"
                params = {
                    "access_token": self._token_for_page(),
                    "title": title,
                    "file_url": video_url,
                    "published": "false",
                }
                response = self.send_rest_request("POST", endpoint, params=params)
                if "error" not in response:
                    video_ids.append(response.get("id"))

            if not video_ids:
                return {"error": "Failed to upload any videos"}

            endpoint = f"{self.page_id}/feed"
            params = {"access_token": self._token_for_page()}
            if not self.published:
                params["published"] = "false"
                params["unpublished_content_type"] = "DRAFT"
            json_data = {
                "message": caption,
                "attached_media": [{"media_fbid": _id} for _id in video_ids],
            }
            return self.send_rest_request(
                "POST", endpoint, params=params, json_body=json_data
            )
        except Exception as e:
            self.logger.error(f"Facebook API: Error posting videos: {e}")
            return {"ok": False, "error": str(e)}

    @staticmethod
    def get_mime_type(
        file_path: Optional[str] = None,
        url: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> Optional[Dict]:
        """Determine the MIME type of a file or URL and categorize it."""
        detected_mime_type = None

        if file_path:
            detected_mime_type, _ = mimetypes.guess_type(file_path)
        elif url:
            try:
                response = requests.head(url, allow_redirects=True)
                detected_mime_type = response.headers.get("Content-Type")

                # Fallback if server lies or sends generic type
                if not detected_mime_type or detected_mime_type == "application/json":
                    detected_mime_type, _ = mimetypes.guess_type(url)
            except requests.RequestException:
                return None
        else:
            detected_mime_type = mime_type

        image_types = ["image/jpeg", "image/png", "image/gif", "image/webp"]
        document_types = ["application/pdf", "text/plain"]
        audio_types = ["audio/mpeg", "audio/wav"]
        video_types = ["video/mp4", "video/quicktime"]

        if detected_mime_type in image_types:
            return {"file_type": "image", "mime": detected_mime_type}
        elif detected_mime_type in document_types:
            return {"file_type": "document", "mime": detected_mime_type}
        elif detected_mime_type in audio_types:
            return {"file_type": "audio", "mime": detected_mime_type}
        elif detected_mime_type in video_types:
            return {"file_type": "video", "mime": detected_mime_type}
        else:
            return {"file_type": "unknown", "mime": detected_mime_type}

    def post_media_to_page(self, caption: str, media_urls: List[Dict]) -> Dict:
        """Posts media (images or videos) to a Facebook page using URLs."""

        try:

            media_ids = []
            for media in media_urls:
                media_url = media.get("url")
                mime_info = self.get_mime_type(url=media_url)
                media_type = mime_info.get("file_type") if mime_info else None

                if media_type == "video":
                    endpoint = f"{self.page_id}/videos"
                    params = {
                        "access_token": self._token_for_page(),
                        "file_url": media_url,
                    }
                elif media_type == "image":
                    endpoint = f"{self.page_id}/photos"
                    params = {"access_token": self._token_for_page(), "url": media_url}
                else:
                    continue

                params["published"] = "false"

                response = self.send_rest_request("POST", endpoint, params=params)
                if "error" not in response:
                    media_ids.append(response.get("id"))

            if not media_ids:
                return {"error": "No valid media uploaded"}

            endpoint = f"{self.page_id}/feed"
            params = {"access_token": self._token_for_page()}
            if not self.published:
                params["published"] = "false"
                params["unpublished_content_type"] = "DRAFT"
            json_data = {
                "message": caption,
                "attached_media": [{"media_fbid": _id} for _id in media_ids],
            }
            return self.send_rest_request(
                "POST", endpoint, params=params, json_body=json_data
            )
        except Exception as e:
            self.logger.error(f"Facebook API: Error posting media: {e}")
            return {"ok": False, "error": str(e)}

    def get_post_comments(self, post_id: str, limit: int = 10) -> Union[List, Dict]:
        """Retrieves comments on a Facebook post."""
        endpoint = f"{post_id}/comments"
        params = {"access_token": self._token_for_page(), "limit": limit}
        return self.send_rest_request("GET", endpoint, params=params)

    def reply_to_comment(self, comment_id: str, message: str) -> Dict:
        """Replies to a comment on a Facebook post."""
        endpoint = f"{comment_id}/comments"
        params = {"message": message, "access_token": self._token_for_page()}
        return self.send_rest_request("POST", endpoint, params=params)

    def reply_to_comment_with_attachment(
        self, comment_id: str, attachment_url: str
    ) -> Dict:
        """Replies to a comment with an attachment."""
        endpoint = f"{comment_id}/comments"
        data = {
            "attachment_url": attachment_url,
            "access_token": self._token_for_page(),
        }
        return self.send_rest_request("POST", endpoint, data=data)

    def update_comment(self, comment_id: str, message: str) -> Dict:
        """Updates a comment on a Facebook post."""
        endpoint = comment_id
        data = {"message": message, "access_token": self._token_for_page()}
        return self.send_rest_request("POST", endpoint, data=data)

    def like_comment(self, comment_id: str) -> Dict:
        """Likes a comment on a Facebook post."""
        endpoint = f"{comment_id}/likes"
        params = {"access_token": self._token_for_page()}
        return self.send_rest_request("POST", endpoint, params=params)

    def get_reactions(self, post_id: str) -> Union[List, Dict]:
        """Retrieves reactions on a Facebook post."""
        endpoint = f"{post_id}/reactions"
        params = {"access_token": self._token_for_page()}
        return self.send_rest_request("GET", endpoint, params=params)

    def share_facebook_post(self, post_id: str) -> Dict:
        """Fetches the permalink URL of a Facebook post."""
        endpoint = post_id
        params = {"fields": "permalink_url", "access_token": self._token_for_page()}
        response = self.send_rest_request("GET", endpoint, params=params)
        if "permalink_url" in response:
            return {"status": "success", "data": response["permalink_url"]}
        return {"status": "error", "message": response.get("error", "Unknown error")}
