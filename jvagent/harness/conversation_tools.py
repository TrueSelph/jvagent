"""Reviewed conversation mutations; SDK owns dispatch and effect lifecycle."""

from typing import Any

from jvspatial import flush_deferred_entities

from jvagent.tooling.context import require_tool_visitor
from jvagent.tooling.tool import Tool
from jvagent.tooling.tool_result import ToolResult

CONVERSATION_EFFECT = "jv:conversation-mutation:v1"


class ConversationMutationTool(Tool):
    """Explicit Action opt-in for reviewed writes to the admitted Conversation.

    This is not a sandbox for arbitrary Python or permission to perform external
    writes. The SDK must journal and annotate the attempt before invoking it.
    A completed tool requires strict graph acknowledgement. Failures never imply
    rollback; failed runs with these effects require operator reconciliation.
    """

    async def call(self, **kwargs: Any) -> ToolResult:
        visitor = require_tool_visitor()
        conversation = getattr(visitor, "conversation", None)
        if (
            kwargs.get("visitor") is not visitor
            or conversation is None
            or conversation.user_id != visitor.user_id
            or conversation.session_id != visitor.session_id
            or visitor.interaction.conversation_id != conversation.id
        ):
            raise ValueError("Conversation mutation requires the admitted owner scope")
        result = await super().call(**kwargs)
        require_tool_visitor()
        if (
            visitor.conversation is not conversation
            or conversation.user_id != visitor.user_id
            or conversation.session_id != visitor.session_id
            or visitor.interaction.conversation_id != conversation.id
        ):
            raise ValueError("Conversation mutation changed its owner scope")
        if result.is_error:
            raise RuntimeError("Conversation mutation did not complete successfully")
        await conversation.save()
        await flush_deferred_entities(conversation, strict=True)
        return result
