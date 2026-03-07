"""WhatsApp channel adapter example for response bus."""

import asyncio
import logging
import os
import re
from typing import Any, Dict, List, Optional

from jvagent.action.response.channel_adapter import ChannelAdapter
from jvagent.action.response.message import ResponseMessage

logger = logging.getLogger(__name__)


class WhatsAppAdapter(ChannelAdapter):
    """WhatsApp channel adapter for response bus.

    This adapter sends adhoc messages to WhatsApp via the WhatsApp API.

    WhatsApp uses non-streaming mode (stream=False), so messages are published
    as complete 'adhoc' messages. ResponseBus only calls send() for adhoc messages.

    This adapter is automatically created and registered by WhatsAppAction
    in its on_register() method. Messages published with channel="whatsapp"
    are automatically delivered to this adapter.

    Example usage in action:
        class WhatsAppAction(Action):
            async def on_register(self):
                adapter = WhatsAppAdapter(action=self)
                await adapter.initialize()
                # Adapter is now stored in ResponseBus registry
    """

    def __init__(self, action: Any):
        """Initialize WhatsApp adapter.

        Args:
            action: WhatsAppAction instance
        """
        super().__init__(channel="whatsapp")
        self.action = action
        # Per-user locks to serialize message sends and ensure ordering
        self._user_locks: Dict[str, asyncio.Lock] = {}

    def _get_user_lock(self, user_id: str) -> asyncio.Lock:
        """Get or create a lock for a specific user to serialize message sends.

        Implements LRU-style eviction to prevent unbounded memory growth.
        Keeps at most 1000 locks cached.
        """
        if user_id not in self._user_locks:
            # Evict oldest locks if we exceed the limit
            if len(self._user_locks) >= 1000:
                # Remove the oldest 100 entries to avoid frequent evictions
                keys_to_remove = list(self._user_locks.keys())[:100]
                for key in keys_to_remove:
                    del self._user_locks[key]
            self._user_locks[user_id] = asyncio.Lock()
        return self._user_locks[user_id]

    async def send(self, message: ResponseMessage) -> bool:
        """Send adhoc message to WhatsApp.

        This method is called by ResponseBus when an adhoc message is published
        for the 'whatsapp' channel.

        Args:
            message: ResponseMessage object to send

        Returns:
            True if message was sent successfully, False otherwise
        """
        logger.debug(
            f"WhatsAppAdapter: send() called - message_id={message.id}, "
            f"session_id={message.session_id}, interaction_id={message.interaction_id}"
        )

        if not self.action or not self.action.is_configured():
            logger.debug(
                "WhatsAppAdapter: Skipping message - WhatsApp action is not configured. "
                "Set WHATSAPP_API_URL and WHATSAPP_API_KEY environment variables."
            )
            return False

        if not message.user_id:
            logger.error(
                f"WhatsAppAdapter: Cannot send message {message.id} - no user_id in message"
            )
            return False

        media_url = message.metadata.get("media_url")
        media_type = message.metadata.get("media_type")

        # Ensure relative URLs (e.g. from TTS /api/storage/...) are absolute for fetch
        if media_url and media_url.startswith("/"):
            base = (self.action.base_url or "").strip() or os.environ.get(
                "APP_BASE_URL", ""
            ).strip()
            if base:
                media_url = f"{base.rstrip('/')}{media_url}"

        if not media_url and (not message.content or not message.content.strip()):
            logger.debug(
                f"WhatsAppAdapter: Skipping empty message {message.id} for user {message.user_id}"
            )
            return False

        logger.debug(
            f"WhatsAppAdapter: Processing adhoc message {message.id} for user {message.user_id}"
        )

        api = self.action.api()

        # Extract is_group from message.metadata.whatsapp_payload
        whatsapp_payload = message.metadata.get("whatsapp_payload", {})
        is_group = whatsapp_payload.get("isGroup", False)
        if is_group is None and len(message.user_id) > 10:
            is_group = True

        # Handle media messages
        if media_url and media_type:
            logger.debug(
                f"WhatsAppAdapter: Sending media message - type={media_type}, url={media_url}"
            )
            try:
                send_result = {
                    "ok": False,
                    "error": f"Unsupported media type: {media_type}",
                }

                if media_type == "image":
                    send_result = await api.send_image(
                        phone=message.user_id,
                        file_url=media_url,
                        caption=message.content,
                        is_group=is_group,
                    )
                elif media_type in ["document", "file", "docs"]:
                    filename = (
                        message.metadata.get("filename") or media_url.split("/")[-1]
                    )
                    send_result = await api.send_file(
                        phone=message.user_id,
                        file_url=media_url,
                        caption=message.content,
                        filename=filename,
                        is_group=is_group,
                    )
                elif media_type == "video":
                    if hasattr(api, "send_video"):
                        send_result = await api.send_video(
                            phone=message.user_id,
                            file_url=media_url,
                            caption=message.content,
                            is_group=is_group,
                        )
                    else:
                        send_result = await api.send_file(
                            phone=message.user_id,
                            file_url=media_url,
                            caption=message.content,
                            is_group=is_group,
                        )
                elif media_type in ["audio", "voice"]:
                    send_result = await api.send_voice(
                        phone=message.user_id,
                        file_url=media_url,
                        is_group=is_group,
                    )

                if send_result.get("ok", True):
                    logger.debug(
                        f"WhatsAppAdapter: Media sent successfully to {message.user_id}"
                    )
                    # Clear typing status after sending
                    await self._clear_typing_status(api, message.user_id, is_group)
                    return True
                else:
                    error_msg = send_result.get("error", "Unknown error")
                    logger.error(
                        f"WhatsAppAdapter: Media send failed for {message.user_id}: {error_msg}"
                    )
                    await self._clear_typing_status(api, message.user_id, is_group)
                    return False
            except Exception as e:
                logger.error(
                    f"WhatsAppAdapter: Exception sending media to {message.user_id}: {e}",
                    exc_info=True,
                )
                await self._clear_typing_status(api, message.user_id, is_group)
                return False

        # Message content is already transformed by filters before reaching the adapter
        chunks = self.chunk_long_message(
            message.content,
            max_length=self.action.chunk_length,
            chunk_length=self.action.chunk_length,
        )

        if not chunks or all(not chunk.strip() for chunk in chunks):
            logger.debug(
                f"WhatsAppAdapter: No valid message chunks to send for user {message.user_id}"
            )
            await self._clear_typing_status(api, message.user_id, is_group)
            return False

        try:
            for chunk_idx, chunk in enumerate(chunks):
                send_result = await api.send_message(
                    phone=message.user_id,
                    message=chunk,
                    is_group=is_group,
                )

                # Check if send was successful
                if not send_result.get("ok", True):
                    error_msg = send_result.get("error", "Unknown error")
                    logger.error(
                        f"WhatsAppAdapter: send_message failed for {message.user_id} "
                        f"(chunk {chunk_idx + 1}/{len(chunks)}): {error_msg}. "
                        f"Message ID: {message.id}, is_group: {is_group}"
                    )
                    return False

            logger.debug(
                f"WhatsAppAdapter: Message sent successfully to {message.user_id} "
                f"(is_group: {is_group}, chunks: {len(chunks)})"
            )
            success = True
        except Exception as e:
            logger.error(
                f"WhatsAppAdapter: Failed to send message to WhatsApp for user {message.user_id}: {e}. "
                f"Message ID: {message.id}, Chunks: {len(chunks)}, is_group: {is_group}",
                exc_info=True,
            )
            raise
        finally:
            await self._clear_typing_status(api, message.user_id, is_group)

        return success

    async def _clear_typing_status(self, api, phone, is_group):
        """Helper to clear typing status."""
        try:
            await api.set_typing_status(phone=phone, value=False, is_group=is_group)
        except Exception as e:
            logger.debug(
                f"WhatsAppAdapter: Failed to clear typing status for {phone}: {e}"
            )

    def chunk_long_message(
        self, message: str, max_length: int = 1024, chunk_length: int = 1024
    ) -> List[str]:
        """
        Splits a long message into smaller chunks of no more than chunk_length characters,
        ensuring no single chunk exceeds max_length.

        Args:
            message: The text to chunk
            max_length: Maximum allowed length for any chunk
            chunk_length: Target length for chunks

        Returns:
            List of message chunks
        """
        if len(message) <= max_length:
            return [message]

        # Initialize variables
        final_chunks = []
        current_chunk = ""
        current_chunk_length = 0

        # Split the message into words while preserving newline characters
        words = re.findall(r"\S+\n*|\n+", message)
        words = [word for word in words if word.strip()]  # Filter out empty strings

        for word in words:
            word_length = len(word)
            # Calculate space needed (1 for space separator, except for first word)
            space_needed = 1 if current_chunk else 0

            if current_chunk_length + word_length + space_needed <= chunk_length:
                # Add the word to the current chunk
                if current_chunk:
                    current_chunk += " "
                    current_chunk_length += 1  # Account for space
                current_chunk += word
                current_chunk_length += word_length
            else:
                # If the current chunk is full, add it to the list of chunks
                if current_chunk:  # Only add non-empty chunks
                    final_chunks.append(current_chunk)
                current_chunk = word  # Start a new chunk with the current word
                current_chunk_length = word_length

        if current_chunk:
            # Add the last chunk if it's non-empty
            final_chunks.append(current_chunk)

        return final_chunks
