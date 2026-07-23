"""Admin REST endpoints for Conversation Health."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Body, Query
from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError

from jvagent.core.agent import Agent
from jvagent.memory.conversation import Conversation
from jvagent.memory.interaction import Interaction

from . import service as health_service
from .config import is_enabled_for_agent, load_conversation_health_config
from .constants import DIMENSIONS

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
    return False


def _enrich_health(health: Dict[str, Any]) -> Dict[str, Any]:
    """Fill composite scores only when all four dimensions are present."""
    h = dict(health or {})
    if h.get("avg_score") is None and isinstance(h.get("avg"), dict):
        avg = h["avg"]
        if all(avg.get(d) is not None for d in DIMENSIONS):
            try:
                h["avg_score"] = round(
                    sum(float(avg[d]) for d in DIMENSIONS) / len(DIMENSIONS), 2
                )
            except (TypeError, ValueError):
                pass
    if h.get("min_score") is None and isinstance(h.get("min"), dict):
        mn = h["min"]
        if all(mn.get(d) is not None for d in DIMENSIONS):
            try:
                h["min_score"] = round(min(float(mn[d]) for d in DIMENSIONS), 2)
            except (TypeError, ValueError):
                pass
    return h


async def _require_agent(agent_id: str) -> Agent:
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent '{agent_id}' not found",
            details={"agent_id": agent_id},
        )
    return agent  # type: ignore[return-value]


def _service_status(agent: Agent) -> Dict[str, Any]:
    cfg = load_conversation_health_config()
    enabled = is_enabled_for_agent(agent, cfg)
    return {
        "enabled": enabled,
        "app_enabled": cfg.enabled,
        "agent_override": getattr(agent, "conversation_health_enabled", None),
        "flag_threshold": cfg.flag_threshold,
    }


async def _conversation_belongs_to_agent(conv: Conversation, agent_id: str) -> bool:
    """True if conversation is owned by agent (graph or health stamp)."""
    h = getattr(conv, "health", None) or {}
    stamped = h.get("agent_id")
    if stamped and str(stamped) == str(agent_id):
        return True
    try:
        agent = await conv.get_agent()
        if agent is not None and str(getattr(agent, "id", "")) == str(agent_id):
            return True
    except Exception:
        logger.debug(
            "conversation ownership lookup failed conv=%s",
            getattr(conv, "id", None),
            exc_info=True,
        )
    return False


async def _require_conversation_for_agent(
    agent_id: str, conversation_id: str
) -> Conversation:
    conv = await Conversation.get(conversation_id)
    if not conv or not await _conversation_belongs_to_agent(conv, agent_id):
        # 404 for both missing and foreign — avoid ID enumeration
        raise ResourceNotFoundError(
            message=f"Conversation '{conversation_id}' not found",
            details={"conversation_id": conversation_id, "agent_id": agent_id},
        )
    return conv  # type: ignore[return-value]


async def _iter_agent_conversations(agent_id: str) -> List[Conversation]:
    """Conversations under this agent's memory graph (safe scope)."""
    agent = await Agent.get(agent_id)
    if not agent:
        return []
    memory = await agent.get_memory()
    if not memory:
        return []
    out: List[Conversation] = []
    try:
        users = await memory.get_users()
    except Exception:
        logger.debug("list agent users failed agent=%s", agent_id, exc_info=True)
        return []
    for user in users or []:
        try:
            convs = await user.nodes(node=Conversation)
        except Exception:
            continue
        for c in convs or []:
            out.append(c)
    return out


async def _iter_agent_interactions_recent(
    agent_id: str,
    *,
    limit: int,
    offset: int,
    max_conversations: int = 100,
    max_per_conversation: int = 50,
) -> Tuple[List[Interaction], int]:
    """Page recent interactions for one agent (bounded graph walk).

    Caps conversations and turns per conversation so backfill cannot load an
    unbounded history into memory.
    """
    convs = await _iter_agent_conversations(agent_id)
    convs.sort(
        key=lambda c: getattr(c, "last_interaction_at", None)
        or getattr(c, "created_at", None)
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    max_conversations = max(1, min(int(max_conversations), 500))
    max_per_conversation = max(1, min(int(max_per_conversation), 200))
    convs = convs[:max_conversations]

    collected: List[Interaction] = []
    for conv in convs:
        try:
            ixs = await conv.get_interactions(limit=max_per_conversation, reverse=True)
        except Exception:
            continue
        for ix in ixs or []:
            collected.append(ix)

    collected.sort(
        key=lambda ix: getattr(ix, "started_at", None)
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    total = len(collected)
    page = collected[offset : offset + limit]
    return page, total


@endpoint(
    "/api/agents/{agent_id}/conversation/health",
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
    agent = await _require_agent(agent_id)
    status = _service_status(agent)
    if not status["enabled"]:
        return {
            "reading": {
                "agent_id": agent_id,
                "enabled": False,
                "interaction_count": 0,
                "flagged_count": 0,
                "avg_score": None,
                "avg_dimensions": None,
                "trend": [],
            },
            "status": status,
        }
    reading = await health_service.get_agent_reading(agent_id, days=days)
    return {"reading": reading, "status": status}


@endpoint(
    "/api/agents/{agent_id}/conversation/health/stats",
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
    agent = await _require_agent(agent_id)
    status = _service_status(agent)
    if not status["enabled"]:
        return {"stats": {"enabled": False}, "status": status}
    reading = await health_service.get_agent_reading(agent_id, days=days)
    return {
        "stats": {
            "enabled": True,
            "interaction_count": reading.get("interaction_count"),
            "flagged_count": reading.get("flagged_count"),
            "flag_rate": reading.get("flag_rate"),
            "avg_score": reading.get("avg_score"),
            "avg_dimensions": reading.get("avg_dimensions"),
            "top_issues": reading.get("top_issues"),
            "ambient": reading.get("ambient"),
        },
        "status": status,
    }


@endpoint(
    "/api/agents/{agent_id}/conversation/health/trend",
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
    agent = await _require_agent(agent_id)
    status = _service_status(agent)
    if not status["enabled"]:
        return {"trend": [], "status": status}
    reading = await health_service.get_agent_reading(agent_id, days=days)
    return {"trend": reading.get("trend") or [], "status": status}


@endpoint(
    "/api/agents/{agent_id}/conversation/health/conversations",
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
    agent = await _require_agent(agent_id)
    status = _service_status(agent)
    window_days = days if days is not None else None
    cfg = load_conversation_health_config()

    # Strict agent scope: stamped health.agent_id OR graph ownership (no unscoped fallback)
    query_agent: Dict[str, Any] = {
        "context.health.scored_turn_count": {"$gt": 0},
        "context.health.agent_id": agent_id,
    }
    if flagged is not None:
        query_agent["context.health.flagged"] = flagged

    try:
        stamped = list(await Conversation.find(query_agent) or [])
    except Exception:
        logger.debug("conversation health stamped query failed", exc_info=True)
        stamped = []

    # Also include scored conversations under this agent's memory that lack stamp
    # (legacy / first-score race) — still ownership-checked via graph walk
    stamped_ids = {str(c.id) for c in stamped}
    graph_rows: List[Conversation] = []
    for conv in await _iter_agent_conversations(agent_id):
        if str(conv.id) in stamped_ids:
            continue
        h = getattr(conv, "health", None) or {}
        if not h.get("scored_turn_count"):
            continue
        if flagged is not None and bool(h.get("flagged")) != flagged:
            continue
        # Only if health is for this agent or unstamped (then ownership is graph)
        hid = h.get("agent_id")
        if hid and str(hid) != str(agent_id):
            continue
        graph_rows.append(conv)

    rows: List[Conversation] = stamped + graph_rows
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
    skip = (page - 1) * page_size
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
                    c.last_interaction_at.isoformat() if c.last_interaction_at else None
                ),
                "health": h,
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
            "flag_threshold": cfg.flag_threshold,
            "enabled": status["enabled"],
        },
        "status": status,
    }


@endpoint(
    "/api/agents/{agent_id}/conversation/health/conversations/{conversation_id}",
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
    await _require_agent(agent_id)
    conv = await _require_conversation_for_agent(agent_id, conversation_id)
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
    "/api/agents/{agent_id}/conversation/health/conversations/{conversation_id}/deep-review",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Conversation Health"],
    response=success_response(
        data={
            "results": ResponseField(field_type=list, description="Per-turn AI results")
        }
    ),
)
async def deep_review_conversation(
    agent_id: str,
    conversation_id: str,
    interaction_id: Optional[str] = Query(
        None, description="If set, only review this interaction"
    ),
) -> Dict[str, Any]:
    agent = await _require_agent(agent_id)
    status = _service_status(agent)
    if not status["enabled"]:
        raise ResourceNotFoundError(
            message="Conversation Health is disabled",
            details={"agent_id": agent_id, "status": status},
        )
    conv = await _require_conversation_for_agent(agent_id, conversation_id)

    targets: List[Interaction] = []
    ordered_for_ai: Optional[List[Interaction]] = None
    if interaction_id:
        ix = await Interaction.get(interaction_id)
        if not ix or str(getattr(ix, "conversation_id", "")) != str(conversation_id):
            raise ResourceNotFoundError(
                message=f"Interaction '{interaction_id}' not found",
                details={
                    "interaction_id": interaction_id,
                    "conversation_id": conversation_id,
                },
            )
        targets = [ix]  # type: ignore[list-item]
        ordered_for_ai = await conv.get_interactions(limit=0, reverse=False)
    else:
        targets = await conv.get_interactions(limit=0, reverse=False)
        ordered_for_ai = targets

    results = []
    for ix in targets:
        scorable, reason = health_service.is_scorable(ix)
        if not scorable:
            results.append({"interaction_id": ix.id, "skipped": True, "reason": reason})
            continue
        if not (getattr(ix, "health", None) or {}).get("scored"):
            await health_service.score_interaction(
                ix, agent_id=agent_id, schedule_ai=False
            )
        out = await health_service.run_ai_for_interaction(
            str(ix.id),
            agent_id=agent_id,
            ordered_interactions=ordered_for_ai,
        )
        results.append({"interaction_id": ix.id, **out})

    return {"results": results, "status": status}


@endpoint(
    "/api/agents/{agent_id}/conversation/health/backfill",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Conversation Health"],
    response=success_response(
        data={
            "processed": ResponseField(field_type=int),
            "scored": ResponseField(field_type=int),
            "cursor": ResponseField(field_type=str, description="Next offset cursor"),
            "total_candidates": ResponseField(field_type=int),
        }
    ),
)
async def backfill_health(
    agent_id: str,
    body: Optional[Dict[str, Any]] = Body(default=None),
) -> Dict[str, Any]:
    """Heuristic-only backfill of this agent's interactions (memory-scoped)."""
    agent = await _require_agent(agent_id)
    status = _service_status(agent)
    if not status["enabled"]:
        raise ResourceNotFoundError(
            message="Conversation Health is disabled",
            details={"agent_id": agent_id, "status": status},
        )
    body = body or {}
    limit = min(int(body.get("limit") or 50), 200)
    offset = int(body.get("cursor") or body.get("offset") or 0)
    enqueue_critical_ai = bool(body.get("enqueue_critical_ai") or False)
    force = bool(body.get("force") or False)
    max_conversations = min(int(body.get("max_conversations") or 100), 500)
    max_per_conversation = min(int(body.get("max_per_conversation") or 50), 200)

    rows, total = await _iter_agent_interactions_recent(
        agent_id,
        limit=limit,
        offset=offset,
        max_conversations=max_conversations,
        max_per_conversation=max_per_conversation,
    )

    processed = 0
    scored = 0
    for ix in rows:
        processed += 1
        h = getattr(ix, "health", None) or {}
        if h.get("scored") and not force:
            continue
        prior_agent = h.get("agent_id")
        if prior_agent and str(prior_agent) != str(agent_id):
            continue
        result = await health_service.score_interaction(
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
        "total_candidates": total,
        "status": status,
    }
