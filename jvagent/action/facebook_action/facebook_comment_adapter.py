"""Facebook Page comment channel adapter for the response bus.

Delivers agent replies as comment replies on Facebook Page posts using
the Graph API ``/{comment_id}/comments`` endpoint.  Falls back to posting
a new comment on the original post (``/{post_id}/comments``) when only
``post_id`` is available, or to a Messenger DM when neither is present.
"""

import asyncio
import logging
from typing import Any, Dict

from jvagent.action.response.channel_adapter import ChannelAdapter
from jvagent.action.response.message import ResponseMessage

from .facebook_comment_text import to_facebook_comment_text

logger = logging.getLogger(__name__)


class FacebookCommentAdapter(ChannelAdapter):
    """Deliver ``ResponseMessage`` content as Facebook Page comment replies.

    The adapter resolves the reply target from ``message.metadata``, which
    carries the original webhook payload fields set in
    ``messenger_webhook_helpers.create_feed_comment_walker``:

    - ``comment_id`` – reply to the specific comment (preferred)
    - ``post_id`` – post a new comment on the original post (fallback)
    - ``user_id`` – fall back to a Messenger DM if neither is available
    """

    def __init__(self, action: Any) -> None:
        super().__init__(channel="facebook_comment")
        self.action = action
        self._user_locks: Dict[str, asyncio.Lock] = {}

    def _get_user_lock(self, user_id: str) -> asyncio.Lock:
        if user_id not in self._user_locks:
            if len(self._user_locks) >= 1000:
                for key in list(self._user_locks.keys())[:100]:
                    del self._user_locks[key]
            self._user_locks[user_id] = asyncio.Lock()
        return self._user_locks[user_id]

    def _graph_failed(self, result: Any) -> bool:
        if not isinstance(result, dict):
            return True
        return bool(result.get("error"))

    @staticmethod
    def _strip_markdown(text: str) -> str:
        """Reduce markdown to plain text for a public Facebook comment.

        Delegates to the shared helper so this and ``FacebookCommentFilter``
        cannot drift apart.
        """
        return to_facebook_comment_text(text)

    async def send(self, message: ResponseMessage) -> bool:
        if not self.action or not self.action.is_configured():
            logger.debug("FacebookCommentAdapter: FacebookAction not configured")
            return False

        comment_id = str(message.metadata.get("comment_id", "") or "").strip()
        post_id = str(message.metadata.get("post_id", "") or "").strip()
        user_id = str(message.user_id or "").strip()

        text = self._strip_markdown(str(message.content or "").strip())
        if not text:
            logger.debug("FacebookCommentAdapter: empty message %s", message.id)
            return False

        api = self.action.api()

        if comment_id:
            logger.info("FacebookCommentAdapter: replying to comment %s", comment_id)

            def _reply() -> Any:
                return api.reply_to_comment(comment_id, text)

            lock = self._get_user_lock(f"comment:{comment_id}")
            async with lock:
                try:
                    result = await asyncio.to_thread(_reply)
                    if self._graph_failed(result):
                        logger.error(
                            "FacebookCommentAdapter: reply_to_comment failed: %s",
                            result,
                        )
                        return False
                    return True
                except Exception as e:
                    logger.error(
                        "FacebookCommentAdapter: reply error for comment %s: %s",
                        comment_id,
                        e,
                        exc_info=True,
                    )
                    return False

        if post_id:
            logger.info("FacebookCommentAdapter: commenting on post %s", post_id)

            def _comment() -> Any:
                return api.comment_on_post(post_id, text)

            lock = self._get_user_lock(f"post:{post_id}")
            async with lock:
                try:
                    result = await asyncio.to_thread(_comment)
                    if self._graph_failed(result):
                        logger.error(
                            "FacebookCommentAdapter: comment_on_post failed: %s",
                            result,
                        )
                        return False
                    return True
                except Exception as e:
                    logger.error(
                        "FacebookCommentAdapter: comment error for post %s: %s",
                        post_id,
                        e,
                        exc_info=True,
                    )
                    return False

        if user_id:
            logger.info(
                "FacebookCommentAdapter: no comment_id/post_id, sending Messenger DM to %s",
                user_id,
            )

            def _dm() -> Any:
                return api.send_text_message(user_id, text)

            lock = self._get_user_lock(user_id)
            async with lock:
                try:
                    result = await asyncio.to_thread(_dm)
                    if self._graph_failed(result):
                        logger.error(
                            "FacebookCommentAdapter: Messenger DM fallback failed: %s",
                            result,
                        )
                        return False
                    return True
                except Exception as e:
                    logger.error(
                        "FacebookCommentAdapter: DM error for %s: %s",
                        user_id,
                        e,
                        exc_info=True,
                    )
                    return False

        logger.error(
            "FacebookCommentAdapter: no comment_id, post_id, or user_id on message %s",
            message.id,
        )
        return False
