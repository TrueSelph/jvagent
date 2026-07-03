"""Custom LLM that forwards user turns to jvagent Orchestrator."""

from __future__ import annotations

import logging
from typing import Any, Optional

from livekit.agents import llm

from .jvagent_bridge import interact, parse_dispatch_metadata, session_id_for_caller

logger = logging.getLogger(__name__)


class JvagentOrchestratorLLM(llm.LLM):
    """LiveKit LLM adapter that calls jvagent /interact instead of a model provider."""

    def __init__(
        self,
        *,
        agent_id: str,
        user_id: str,
        room_name: str = "",
        whatsapp_call_id: str = "",
    ) -> None:
        super().__init__()
        self._agent_id = agent_id
        self._user_id = user_id
        self._room_name = room_name
        self._whatsapp_call_id = whatsapp_call_id

    @classmethod
    def from_dispatch_metadata(
        cls,
        metadata: Optional[str],
        *,
        room_name: str = "",
    ) -> "JvagentOrchestratorLLM":
        meta = parse_dispatch_metadata(metadata)
        agent_id = str(meta.get("jvagent_agent_id") or "").strip()
        if not agent_id:
            raise ValueError(
                "jvagent_agent_id missing from LiveKit agent dispatch metadata"
            )
        caller = str(meta.get("caller_phone") or "unknown").strip() or "unknown"
        call_id = str(meta.get("whatsapp_call_id") or "").strip()
        return cls(
            agent_id=agent_id,
            user_id=caller,
            room_name=room_name,
            whatsapp_call_id=call_id,
        )

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: Optional[list] = None,
        conn_options: Optional[Any] = None,
        parallel_tool_calls: Optional[bool] = None,
        tool_choice: Optional[Any] = None,
        extra_kwargs: Optional[dict] = None,
    ) -> llm.LLMStream:
        return JvagentOrchestratorLLMStream(
            self,
            chat_ctx=chat_ctx,
            tools=tools or [],
            conn_options=conn_options,
        )


class JvagentOrchestratorLLMStream(llm.LLMStream):
    """Single-shot stream that returns one assistant message from jvagent."""

    def __init__(
        self,
        llm_instance: JvagentOrchestratorLLM,
        *,
        chat_ctx: llm.ChatContext,
        tools: list,
        conn_options: Optional[Any],
    ) -> None:
        super().__init__(
            llm_instance,
            chat_ctx=chat_ctx,
            tools=tools,
            conn_options=conn_options,
        )
        self._llm = llm_instance
        self._done = False

    async def _run(self) -> None:
        utterance = _last_user_message(self._chat_ctx)
        if not utterance:
            utterance = "Hello"
        try:
            response = await interact(
                agent_id=self._llm._agent_id,
                utterance=utterance,
                user_id=self._llm._user_id,
                session_id=session_id_for_caller(self._llm._user_id),
                room_name=self._llm._room_name,
                whatsapp_call_id=self._llm._whatsapp_call_id,
            )
        except Exception as exc:
            logger.error("jvagent interact failed: %s", exc, exc_info=True)
            response = (
                "I'm having trouble reaching the assistant right now. "
                "Please try again in a moment."
            )
        self._event_ch.send_nowait(
            llm.ChatChunk(
                id="jvagent",
                delta=llm.ChoiceDelta(role="assistant", content=response),
            )
        )


def _last_user_message(chat_ctx: llm.ChatContext) -> str:
    for item in reversed(chat_ctx.items):
        if item.type == "message" and item.role == "user":
            text = item.text_content
            if text and text.strip():
                return text.strip()
    return ""
