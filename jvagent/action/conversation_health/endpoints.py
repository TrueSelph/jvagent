"""Admin REST endpoints for Conversation Health."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import Body, Query
from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError

from jvagent.core.agent import Agent
from jvagent.memory.conversation import Conversation
from jvagent.memory.interaction import Interaction

from .conversation_health_action import ConversationHealthAction

logger = logging.getLogger(__name__)


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    if isinstance(value, str) and value:
        try:
            # support trailing Z
            s = value.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None
    return None


def _conversation_in_window(conv: Conversation, cutoff: datetime) -> bool:
    """True if conversation activity is on/after cutoff."""
    for attr in ("last_interaction_at", "created_at"):
        dt = _parse_dt(getattr(conv, attr, None))
        if dt is not None:
            return dt >= cutoff
    h = getattr(conv, "health", None) or {}
    scored_at = _parse_dt(h.get("last_scored_at"))
    if scored_at is not None:
        return scored_at >= cutoff
    # No timestamps: exclude when a window is requested
    return False


def _enrich_health(health: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure composite scores exist on a health payload (backward compatible)."""
    h = dict(health or {})
    if h.get("avg_score") is None and isinstance(h.get("avg"), dict):
        avg = h["avg"]
        if avg and all(avg.get(d) is not None for d in avg):
            try:
                h["avg_score"] = round(
                    sum(float(v) for v in avg.values()) / max(1, len(avg)), 2
                )
            except (TypeError, ValueError):
                pass
    if h.get("min_score") is None and isinstance(h.get("min"), dict):
        mn = h["min"]
        if mn and all(mn.get(d) is not None for d in mn):
            try:
                h["min_score"] = round(min(float(v) for v in mn.values()), 2)
            except (TypeError, ValueError):
                pass
    return h


async def _get_health_action(agent_id: str) -> ConversationHealthAction:
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent '{agent_id}' not found",
            details={"agent_id": agent_id},
        )
    action = await ConversationHealthAction.find_one(
        {
            "context.agent_id": agent_id,
            "context.enabled": True,
        }
    )
    if not action:
        # try any action for agent even if disabled filter missed
        action = await ConversationHealthAction.find_one(
            {"context.agent_id": agent_id}
        )
    if not action:
        raise ResourceNotFoundError(
            message=f"Conversation Health is not configured for agent '{agent_id}'",
            details={"agent_id": agent_id},
        )
    return action


@endpoint(
    "/api/agents/{agent_id}/health",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Conversation Health"],
    response=success_response(
        data={
            "reading": ResponseField(
                field_type=dict,
                description="Agent Health Reading for the window",
            )
        }
    ),
)
async def get_agent_health(
    agent_id: str,
    days: Optional[int] = Query(
        None, description="Window in days (default action config, max 30)"
    ),
) -> Dict[str, Any]:
    action = await _get_health_action(agent_id)
    return {"reading": action.get_agent_reading(days=days)}


@endpoint(
    "/api/agents/{agent_id}/health/stats",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Conversation Health"],
    response=success_response(
        data={"stats": ResponseField(field_type=dict, description="Aggregate stats")}
    ),
)
async def get_health_stats(
    agent_id: str,
    days: Optional[int] = Query(None),
) -> Dict[str, Any]:
    action = await _get_health_action(agent_id)
    reading = action.get_agent_reading(days=days)
    return {
        "stats": {
            "interaction_count": reading.get("interaction_count"),
            "flagged_count": reading.get("flagged_count"),
            "flag_rate": reading.get("flag_rate"),
            "avg_dimensions": reading.get("avg_dimensions"),
            "top_issues": reading.get("top_issues"),
            "ambient": reading.get("ambient"),
        }
    }


@endpoint(
    "/api/agents/{agent_id}/health/trend",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Conversation Health"],
    response=success_response(
        data={"trend": ResponseField(field_type=list, description="Daily trend")}
    ),
)
async def get_health_trend(
    agent_id: str,
    days: Optional[int] = Query(None),
) -> Dict[str, Any]:
    action = await _get_health_action(agent_id)
    reading = action.get_agent_reading(days=days)
    return {"trend": reading.get("trend") or []}


@endpoint(
    "/api/agents/{agent_id}/health/conversations",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Conversation Health"],
    response=success_response(
        data={
            "conversations": ResponseField(field_type=list),
            "pagination": ResponseField(field_type=dict),
        }
    ),
)
async def list_health_conversations(
    agent_id: str,
    flagged: Optional[bool] = Query(
        None, description="Filter by conversation.health.flagged"
    ),
    days: Optional[int] = Query(
        None,
        ge=1,
        le=90,
        description=(
            "Only conversations with activity in the last N days "
            "(last_interaction_at / created_at / last_scored_at). "
            "Default: no time filter."
        ),
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> Dict[str, Any]:
    action = await _get_health_action(agent_id)
    window_days = days if days is not None else None

    # Filter conversations that have been scored (health.scored_turn_count > 0)
    # and optionally stamped with this agent_id
    query: Dict[str, Any] = {
        "context.health.scored_turn_count": {"$gt": 0},
    }
    if flagged is not None:
        query["context.health.flagged"] = flagged

    # Prefer agent stamp when present
    query_agent = {
        **query,
        "context.health.agent_id": agent_id,
    }

    skip = (page - 1) * page_size
    try:
        rows = await Conversation.find(query_agent)
        if not rows:
            rows = await Conversation.find(query)
    except Exception:
        logger.debug("conversation health list query failed", exc_info=True)
        rows = []

    rows = list(rows or [])
    if window_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(window_days))
        rows = [c for c in rows if _conversation_in_window(c, cutoff)]

    rows.sort(
        key=lambda c: getattr(c, "last_interaction_at", None)
        or getattr(c, "created_at", None)
        or "",
        reverse=True,
    )
    total = len(rows)
    page_rows = rows[skip : skip + page_size]

    conversations = []
    for c in page_rows:
        h = _enrich_health(getattr(c, "health", None) or {})
        conversations.append(
            {
                "id": c.id,
                "session_id": c.session_id,
                "user_id": c.user_id,
                "status": c.status,
                "channel": c.channel,
                "last_interaction_at": (
                    c.last_interaction_at.isoformat()
                    if c.last_interaction_at
                    else None
                ),
                "health": h,
                # Convenience mirrors for list UIs
                "avg_score": h.get("avg_score"),
                "min_score": h.get("min_score"),
                "flagged": h.get("flagged"),
            }
        )

    return {
        "conversations": conversations,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "count": len(conversations),
            "total": total,
            "days": window_days,
            "flag_threshold": action.flag_threshold,
        },
    }


@endpoint(
    "/api/agents/{agent_id}/health/conversations/{conversation_id}",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Conversation Health"],
    response=success_response(
        data={
            "conversation": ResponseField(field_type=dict),
            "turns": ResponseField(field_type=list),
        }
    ),
)
async def get_health_conversation(
    agent_id: str,
    conversation_id: str,
) -> Dict[str, Any]:
    await _get_health_action(agent_id)
    conv = await Conversation.get(conversation_id)
    if not conv:
        raise ResourceNotFoundError(
            message=f"Conversation '{conversation_id}' not found",
            details={"conversation_id": conversation_id},
        )
    interactions = await conv.get_interactions(limit=0, reverse=False)
    turns = []
    for ix in interactions:
        h = getattr(ix, "health", None) or {}
        turns.append(
            {
                "id": ix.id,
                "utterance": ix.utterance,
                "response": ix.response,
                "started_at": ix.started_at.isoformat() if ix.started_at else None,
                "completed_at": (
                    ix.completed_at.isoformat() if ix.completed_at else None
                ),
                "response_posture": ix.response_posture,
                "health": h,
            }
        )
    health = _enrich_health(getattr(conv, "health", None) or {})
    return {
        "conversation": {
            "id": conv.id,
            "session_id": conv.session_id,
            "user_id": conv.user_id,
            "health": health,
            "avg_score": health.get("avg_score"),
            "min_score": health.get("min_score"),
        },
        "turns": turns,
    }


@endpoint(
    "/api/agents/{agent_id}/health/conversations/{conversation_id}/deep-review",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Conversation Health"],
    response=success_response(
        data={"results": ResponseField(field_type=list, description="Per-turn AI results")}
    ),
)
async def deep_review_conversation(
    agent_id: str,
    conversation_id: str,
    interaction_id: Optional[str] = Query(
        None, description="If set, only review this interaction"
    ),
) -> Dict[str, Any]:
    action = await _get_health_action(agent_id)
    conv = await Conversation.get(conversation_id)
    if not conv:
        raise ResourceNotFoundError(
            message=f"Conversation '{conversation_id}' not found",
            details={"conversation_id": conversation_id},
        )

    targets: List[Interaction] = []
    if interaction_id:
        ix = await Interaction.get(interaction_id)
        if not ix or ix.conversation_id != conversation_id:
            raise ValidationError(
                message="interaction_id not in conversation",
                details={"interaction_id": interaction_id},
            )
        targets = [ix]
    else:
        targets = await conv.get_interactions(limit=0, reverse=False)

    results = []
    for ix in targets:
        scorable, reason = action.is_scorable(ix)
        if not scorable:
            results.append(
                {"interaction_id": ix.id, "skipped": True, "reason": reason}
            )
            continue
        # Ensure heuristic baseline
        if not (getattr(ix, "health", None) or {}).get("scored"):
            await action.score_interaction(ix, schedule_ai=False)
        out = await action.run_ai_for_interaction(str(ix.id))
        results.append({"interaction_id": ix.id, **out})

    return {"results": results}


@endpoint(
    "/api/agents/{agent_id}/health/backfill",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Conversation Health"],
    response=success_response(
        data={
            "processed": ResponseField(field_type=int),
            "scored": ResponseField(field_type=int),
            "cursor": ResponseField(field_type=str, description="Next offset cursor"),
        }
    ),
)
async def backfill_health(
    agent_id: str,
    body: Optional[Dict[str, Any]] = Body(default=None),
) -> Dict[str, Any]:
    """Heuristic-only backfill of recent interactions (no ambient AI flood)."""
    action = await _get_health_action(agent_id)
    body = body or {}
    limit = min(int(body.get("limit") or 50), 200)
    offset = int(body.get("cursor") or body.get("offset") or 0)
    enqueue_critical_ai = bool(body.get("enqueue_critical_ai") or False)
    force = bool(body.get("force") or False)

    # Load recent interactions — best-effort page via find + sort
    try:
        all_rows = await Interaction.find({})
    except Exception:
        logger.debug("backfill interaction find failed", exc_info=True)
        all_rows = []

    all_rows = list(all_rows or [])
    all_rows.sort(
        key=lambda ix: getattr(ix, "started_at", None) or "",
        reverse=True,
    )
    rows = all_rows[offset : offset + limit]

    processed = 0
    scored = 0
    for ix in rows:
        processed += 1
        h = getattr(ix, "health", None) or {}
        if h.get("scored") and not force:
            continue
        # Only score if we can attribute loosely (no agent_id on interaction — score anyway for agent backfill)
        result = await action.score_interaction(
            ix,
            agent_id=agent_id,
            force_rescore=force,
            schedule_ai=enqueue_critical_ai,
        )
        if result.get("scored"):
            scored += 1

    return {
        "processed": processed,
        "scored": scored,
        "cursor": str(offset + processed),
    }
