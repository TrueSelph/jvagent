"""ClientPushAction — publish messages to a client session.

Exposes:

- ``client_push__send`` tool for the orchestrator loop.
- ``send_message()`` method for programmatic/endpoint invocation.

When called with a ``session_id``, the message is published directly to that
session's response bus — reaching the connected client regardless of caller
context. The ``user_id`` is passed along for adapter routing.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from jvagent.action.base import Action
from jvagent.tooling.tool_decorator import tool
from jvagent.tooling.tool_executor import get_dispatch_visitor

logger = logging.getLogger(__name__)


class ClientPushAction(Action):
    """Action that publishes messages to a client session and provides a
    long-lived SSE stream for reading messages from that session.

    Delivery strategies:

    1. **Targeted (preferred)** — when ``session_id`` is provided, gets the
       agent's ``ResponseBus`` and publishes directly to that session. Works
       from any context (HTTP endpoint, background task, etc.).
    2. **Orchestrator tool** — when called inside an agentic loop without a
       specific ``session_id``, publishes through the visitor's response bus.
    3. **Direct** — no session or visitor context; logs and returns metadata.
    """

    @tool(name="client_push__send")
    async def _t_send_message(
        self,
        message: str = "Hello from ClientPushAction!",
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """Send a test message to a connected client.

        When ``session_id`` is provided, publishes directly to that session's
        response bus — the connected client receives it immediately. Without
        ``session_id``, uses the current orchestrator visitor's response bus
        (the ongoing conversation).

        Args:
            message: The message text to send to the client (default: ``"Hello
              from ClientPushAction!"``).
            user_id: Optional user identifier for adapter routing.
            session_id: Target session to deliver to. When omitted, uses the
              current orchestrator conversation context.

        Returns:
            JSON string with delivery status: ``{"ok": true, "delivery": "<method>",
            "message": "<text>"}``.
        """
        logger.info(
            "ClientPushAction tool: message=%s user_id=%s session_id=%s",
            message,
            user_id,
            session_id,
        )
        result = await self._dispatch(message, user_id=user_id, session_id=session_id)
        return json.dumps(result)

    async def send_message(
        self,
        message: str = "Hello from ClientPushAction!",
        *,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a test message to a client session.

        Publishes the message directly to the given session via the agent's
        ``ResponseBus``. Both ``user_id`` and ``session_id`` are required for
        proper routing; the method returns an error dict if ``session_id`` is
        missing or the bus is unavailable.

        Args:
            message: The message text to deliver.
            user_id: User identifier for adapter routing.
            session_id: Target session to deliver to.

        Returns:
            Dict with ``ok``, ``delivery``, and ``message`` keys.
        """
        logger.info(
            "ClientPushAction.send_message: %s (user=%s, session=%s)",
            message,
            user_id,
            session_id,
        )
        return await self._dispatch(message, user_id=user_id, session_id=session_id)

    async def _dispatch(
        self,
        message: str,
        *,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Internal: deliver message to the targeted session.

        Resolution order:
        1. **Targeted session** — ``session_id`` provided → get the agent's
           ``ResponseBus`` and publish directly.
        2. **Orchestrator context** — no explicit ``session_id`` but running
           inside a tool dispatch with a visitor → publish via visitor's bus.
        3. **Direct** — no routing context available → log and return metadata.
        """
        # 1. Targeted delivery — publish to a specific session via agent ResponseBus
        if session_id:
            try:
                agent = await self.get_agent()
                if agent is None:
                    return {
                        "ok": False,
                        "delivery": "error",
                        "error": "Agent not found",
                        "message": message,
                    }
                bus = await agent.get_response_bus()
                if bus is None:
                    return {
                        "ok": False,
                        "delivery": "error",
                        "error": "ResponseBus not available on agent",
                        "message": message,
                    }
                await bus.publish(
                    session_id=session_id,
                    content=message,
                    channel="default",
                    user_id=user_id or "",
                )
                logger.info(
                    "ClientPushAction: published to session %s: %s",
                    session_id,
                    message,
                )
                return {
                    "ok": True,
                    "delivery": "response_bus",
                    "session_id": session_id,
                    "user_id": user_id,
                    "message": message,
                }
            except Exception as e:
                logger.warning(
                    "ClientPushAction: publish to session %s failed: %s",
                    session_id,
                    e,
                )
                return {
                    "ok": False,
                    "delivery": "error",
                    "error": str(e),
                    "session_id": session_id,
                    "message": message,
                }

        # 2. Orchestrator dispatch context — publish via visitor's response bus
        visitor = get_dispatch_visitor()
        if visitor is not None:
            response_bus = getattr(visitor, "response_bus", None)
            visitor_session_id = getattr(visitor, "session_id", None)
            if response_bus is not None and visitor_session_id:
                try:
                    await response_bus.publish(
                        session_id=visitor_session_id,
                        content=message,
                        channel=getattr(visitor, "channel", "default"),
                        user_id=user_id or getattr(visitor, "user_id", None) or "",
                    )
                    logger.info(
                        "ClientPushAction: published via visitor session %s: %s",
                        visitor_session_id,
                        message,
                    )
                    return {
                        "ok": True,
                        "delivery": "visitor_bus",
                        "session_id": visitor_session_id,
                        "message": message,
                    }
                except Exception as e:
                    logger.warning(
                        "ClientPushAction: visitor bus publish failed: %s",
                        e,
                    )
                    return {
                        "ok": False,
                        "delivery": "error",
                        "error": str(e),
                        "message": message,
                    }
            logger.debug(
                "ClientPushAction: visitor exists but no response_bus/session_id"
            )
            return {
                "ok": True,
                "delivery": "no_bus",
                "message": message,
            }

        # 3. No routing context at all
        logger.info("ClientPushAction: no routing context: %s", message)
        return {
            "ok": True,
            "delivery": "direct",
            "user_id": user_id,
            "session_id": session_id,
            "message": message,
        }
