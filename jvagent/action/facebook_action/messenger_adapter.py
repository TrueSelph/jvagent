"""Messenger channel adapter for the response bus (outbound Page messages)."""

import asyncio
import logging
import os
from typing import Any, Dict, List

from jvagent.action.response.channel_adapter import ChannelAdapter
from jvagent.action.response.message import ResponseMessage

logger = logging.getLogger(__name__)

# Messenger text body limit (characters); see Meta documentation.
MESSENGER_TEXT_CHUNK = 2000


class MessengerAdapter(ChannelAdapter):
    """Deliver adhoc ``ResponseMessage`` content via Graph ``/messages``."""

    def __init__(self, action: Any) -> None:
        super().__init__(channel="messenger")
        self.action = action
        self._user_locks: Dict[str, asyncio.Lock] = {}

    def _get_user_lock(self, user_id: str) -> asyncio.Lock:
        if user_id not in self._user_locks:
            if len(self._user_locks) >= 1000:
                for key in list(self._user_locks.keys())[:100]:
                    del self._user_locks[key]
            self._user_locks[user_id] = asyncio.Lock()
        return self._user_locks[user_id]

    def _chunk_text(self, text: str) -> List[str]:
        if len(text) <= MESSENGER_TEXT_CHUNK:
            return [text]
        return [
            text[i : i + MESSENGER_TEXT_CHUNK]
            for i in range(0, len(text), MESSENGER_TEXT_CHUNK)
        ]

    def _graph_failed(self, result: Any) -> bool:
        if not isinstance(result, dict):
            return True
        return bool(result.get("error"))

    async def send(self, message: ResponseMessage) -> bool:
        if not self.action or not self.action.is_configured():
            logger.debug("MessengerAdapter: FacebookAction not configured")
            return False
        if not message.user_id:
            logger.error("MessengerAdapter: missing user_id on message %s", message.id)
            return False

        media_url = message.metadata.get("media_url")
        media_type = (message.metadata.get("media_type") or "").strip().lower()

        if media_url and media_url.startswith("/"):
            base = os.environ.get("APP_BASE_URL", "").strip()
            if base:
                media_url = f"{base.rstrip('/')}{media_url}"

        if not media_url and (not message.content or not str(message.content).strip()):
            logger.debug("MessengerAdapter: empty message %s", message.id)
            return False

        lock = self._get_user_lock(message.user_id)
        async with lock:
            api = self.action.api()
            try:
                if media_url and media_type:
                    fb_type = media_type
                    if fb_type in ("file", "docs", "document"):
                        fb_type = "file"
                    if fb_type == "voice":
                        fb_type = "audio"

                    def _send_media() -> Any:
                        return api.send_media(
                            message.user_id, media_url, fb_type
                        )

                    result = await asyncio.to_thread(_send_media)
                    if self._graph_failed(result):
                        logger.error(
                            "MessengerAdapter: media send failed: %s", result
                        )
                        return False
                    return True

                chunks = self._chunk_text(str(message.content or "").strip())
                if not chunks:
                    return False

                for part in chunks:
                    result = await asyncio.to_thread(
                        lambda p=part: api.send_text_message(message.user_id, p)
                    )
                    if self._graph_failed(result):
                        logger.error(
                            "MessengerAdapter: text send failed: %s", result
                        )
                        return False
                return True
            except Exception as e:
                logger.error(
                    "MessengerAdapter: send error for %s: %s",
                    message.user_id,
                    e,
                    exc_info=True,
                )
                return False
            finally:
                try:
                    uid = message.user_id
                    await asyncio.to_thread(
                        lambda: api.send_sender_action(uid, "typing_off")
                    )
                except Exception as e:
                    logger.debug(
                        "MessengerAdapter: typing_off failed for %s: %s",
                        message.user_id,
                        e,
                    )
