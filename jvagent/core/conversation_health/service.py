"""Conversation Health Service — post-turn hybrid evaluation (core, not an action)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvagent.memory import Interaction
from jvagent.memory.conversation import Conversation

from .aggregates import (
    agent_reading_from_buckets,
    apply_contribution,
    bump_sampling_eligible,
    bump_sampling_selected,
    prune_old_days,
    utc_day_str,
    window_day_list,
)
from .ai_eval import apply_ai_to_health, run_ai_evaluation
from .config import (
    ConversationHealthConfig,
    is_enabled_for_agent,
    load_conversation_health_config,
)
from .constants import DEFERRED_TASK_TYPE, DIMENSIONS
from .heuristics import response_duration_seconds, run_heuristics
from .history import (
    history_for_ai,
    prior_interactions,
    prior_responses_for_heuristics,
)
from .sampling import assign_bucket, decide_ai_schedule
from .scoring import (
    build_contribution,
    build_interaction_health,
    heuristic_health_score,
    is_flagged,
    recompute_conversation_rollup,
    score_dimensions,
)
from .state import ConversationHealthState

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.model.language.base import LanguageModelAction

logger = logging.getLogger(__name__)


def is_scorable(interaction: Interaction) -> tuple[bool, Optional[str]]:
    """True only for user-facing turns we can fairly evaluate."""
    utterance = (getattr(interaction, "utterance", None) or "").strip()
    if not utterance:
        return False, "no_utterance"
    posture = (getattr(interaction, "response_posture", None) or "").upper()
    response = getattr(interaction, "response", None)
    has_response = response is not None and str(response).strip() != ""
    emitted = bool(getattr(interaction, "emitted", False))
    if has_response or emitted:
        return True, None
    if posture in ("DEFER", "SUPPRESS"):
        return False, f"posture_{posture.lower()}_no_reply"
    if posture == "RESPOND":
        return True, None
    return False, "no_user_facing_reply"


async def maybe_score_after_interaction(walker: "InteractWalker") -> None:
    """Hook: run after background InteractActions; never raises to caller."""
    try:
        interaction = getattr(walker, "interaction", None)
        if not interaction:
            return
        agent_id = getattr(walker, "agent_id", None) or ""
        if not agent_id:
            return

        from jvagent.core.agent import Agent

        agent = await Agent.get(agent_id)
        if not agent:
            return

        cfg = load_conversation_health_config()
        if not is_enabled_for_agent(agent, cfg):
            return

        await score_interaction(
            interaction,
            agent_id=agent_id,
            config=cfg,
            schedule_ai=True,
        )
    except Exception:
        logger.error(
            "Conversation Health post-turn scoring failed",
            exc_info=True,
            extra={
                "agent_id": getattr(walker, "agent_id", None),
                "interaction_id": getattr(
                    getattr(walker, "interaction", None), "id", None
                ),
            },
        )


async def score_interaction(
    interaction: Interaction,
    *,
    agent_id: str,
    config: Optional[ConversationHealthConfig] = None,
    force_rescore: bool = False,
    schedule_ai: bool = True,
) -> Dict[str, Any]:
    """Heuristic score + optional deferred AI schedule. Returns health dict."""
    cfg = config or load_conversation_health_config()
    state = await ConversationHealthState.get_or_create_for_agent(agent_id)
    day_buckets = state.day_buckets if isinstance(state.day_buckets, dict) else {}
    state.day_buckets = day_buckets

    scorable, skip_reason = is_scorable(interaction)
    if not scorable:
        health = build_interaction_health(
            scored=False,
            skip_reason=skip_reason,
            ai_status="none",
            agent_id=agent_id,
            scored_at=datetime.now(timezone.utc).isoformat(),
        )
        interaction.health = health
        await interaction.save()
        return health

    prev = dict(getattr(interaction, "health", None) or {})
    prev_contribution = prev.get("contribution") if prev.get("scored") else None
    already_selected = bool(prev.get("ai_selected"))

    prior_responses: List[str] = []
    conversation = None
    if interaction.conversation_id:
        conversation = await Conversation.get(interaction.conversation_id)
        if conversation:
            try:
                ordered = await conversation.get_interactions(limit=0, reverse=False)
                priors = prior_interactions(
                    ordered,
                    str(interaction.id),
                    limit=cfg.history_limit,
                )
                prior_responses = prior_responses_for_heuristics(priors)
            except Exception:
                logger.debug("Could not load prior interactions", exc_info=True)

    utterance = str(getattr(interaction, "utterance", None) or "")
    response = str(getattr(interaction, "response", None) or "")
    duration = response_duration_seconds(interaction)
    bands = [
        (cfg.latency_band_low, "low"),
        (cfg.latency_band_medium, "medium"),
        (cfg.latency_band_high, "high"),
    ]
    issues = run_heuristics(
        utterance=utterance,
        response=response,
        duration=duration,
        prior_agent_responses=prior_responses,
        interaction=interaction,
        latency_bands=bands,
        excerpt_max=cfg.evidence_excerpt_max_chars,
    )
    dimensions = score_dimensions(issues)
    flagged = is_flagged(dimensions, issues, flag_threshold=cfg.flag_threshold)
    hs = heuristic_health_score(dimensions)
    bucket = assign_bucket(
        health_score=hs,
        dimensions=dimensions,
        issues=issues,
        flagged=flagged,
        flag_threshold=cfg.flag_threshold,
        optimization_ceiling=cfg.optimization_ceiling,
    )

    day = utc_day_str(
        getattr(interaction, "completed_at", None)
        or getattr(interaction, "started_at", None)
    )
    contribution = build_contribution(
        day=day, dimensions=dimensions, flagged=flagged, issues=issues
    )

    if prev_contribution:
        apply_contribution(day_buckets, prev_contribution, sign=-1)
    apply_contribution(day_buckets, contribution, sign=+1)

    ai_status = "none"
    ai_select_reason: Optional[str] = None
    ai_selected = already_selected
    should_schedule = False
    first_score = not bool(prev.get("scored"))

    if first_score and bucket in ("A", "B"):
        bump_sampling_eligible(day_buckets, day, bucket)

    if schedule_ai and cfg.enable_ai:
        days = window_day_list(cfg.reading_window_days)
        should_schedule, ai_status, ai_select_reason = decide_ai_schedule(
            interaction_id=str(interaction.id or ""),
            bucket=bucket,
            day_buckets=day_buckets,
            window_days=days,
            ambient_b_target_rate=cfg.ambient_b_target_rate,
            ambient_a_target_rate=cfg.ambient_a_target_rate,
            unflagged_ambient_max_rate=cfg.unflagged_ambient_max_rate,
            ambient_b_share=cfg.ambient_b_share,
            ambient_a_share=cfg.ambient_a_share,
            ambient_spillover=cfg.ambient_spillover,
            already_selected=already_selected,
        )
        if should_schedule and not already_selected:
            bump_sampling_selected(day_buckets, day, bucket)
            ai_selected = True
        elif already_selected:
            should_schedule = prev.get("ai_status") not in ("completed", "error")
            ai_status = str(prev.get("ai_status") or "queued")
            ai_select_reason = str(prev.get("ai_select_reason") or "already_selected")
            ai_selected = True
    elif not cfg.enable_ai:
        ai_status = "none"
        ai_select_reason = "ai_disabled"

    keep_prior_ai = (
        not force_rescore
        and prev.get("scored")
        and prev.get("ai_status") == "completed"
        and prev.get("evaluation_tier") in ("heuristic+ai", "ai")
        and isinstance(prev.get("issues"), list)
        and prev.get("dimensions")
    )
    if keep_prior_ai:
        apply_contribution(day_buckets, contribution, sign=-1)
        if prev_contribution:
            apply_contribution(day_buckets, prev_contribution, sign=+1)
        health = dict(prev)
        health["agent_id"] = agent_id
        if not health.get("ai_bucket"):
            health["ai_bucket"] = bucket
        interaction.health = health
        await interaction.save()
        if conversation:
            await _update_conversation_rollup(conversation, agent_id=agent_id)
        prune_old_days(day_buckets, keep_days=max(30, cfg.reading_window_days * 2))
        state.day_buckets = day_buckets
        await state.save()
        return health

    health = build_interaction_health(
        scored=True,
        dimensions=dimensions,
        issues=issues,
        flagged=flagged,
        health_score=hs,
        ai_bucket=bucket,
        ai_status=ai_status,
        ai_select_reason=ai_select_reason,
        evaluation_tier="heuristic",
        scored_at=datetime.now(timezone.utc).isoformat(),
        contribution=contribution,
        agent_id=agent_id,
        ai_selected=ai_selected,
    )

    interaction.health = health
    await interaction.save()

    if conversation:
        await _update_conversation_rollup(conversation, agent_id=agent_id)

    prune_old_days(day_buckets, keep_days=max(30, cfg.reading_window_days * 2))
    state.day_buckets = day_buckets
    await state.save()

    if should_schedule and schedule_ai and cfg.enable_ai:
        await _schedule_ai_eval(
            interaction_id=str(interaction.id),
            agent_id=agent_id,
            priority=ai_select_reason or "ambient",
        )

    return health


async def _update_conversation_rollup(
    conversation: Conversation, *, agent_id: str
) -> None:
    try:
        interactions = await conversation.get_interactions(limit=50, reverse=True)
    except Exception:
        logger.debug("rollup: failed to load interactions", exc_info=True)
        return
    turn_healths = []
    for ix in interactions:
        h = getattr(ix, "health", None) or {}
        if h.get("scored"):
            turn_healths.append(h)
    rollup = recompute_conversation_rollup(turn_healths)
    rollup["agent_id"] = agent_id
    conversation.health = rollup
    await conversation.save()


async def _schedule_ai_eval(
    *,
    interaction_id: str,
    agent_id: str,
    priority: str = "ambient",
) -> None:
    payload = {
        "interaction_id": interaction_id,
        "agent_id": agent_id,
        "priority": priority,
    }
    try:
        from jvspatial import create_task

        await create_task(DEFERRED_TASK_TYPE, payload)
        logger.debug(
            "Scheduled %s for interaction %s", DEFERRED_TASK_TYPE, interaction_id
        )
    except Exception:
        logger.warning(
            "Failed to schedule AI eval for %s; trying inline fallback",
            interaction_id,
            exc_info=True,
        )
        try:
            await run_ai_for_interaction(interaction_id, agent_id=agent_id)
        except Exception:
            logger.error("Inline AI eval failed for %s", interaction_id, exc_info=True)


async def run_ai_for_interaction(
    interaction_id: str,
    *,
    agent_id: str = "",
    ordered_interactions: Optional[List[Interaction]] = None,
) -> Dict[str, Any]:
    """Execute AI Evaluation and merge (deferred handler / Deep Review).

    ``ordered_interactions``: optional chrono (oldest-first) list for the
    conversation. Deep Review passes this once to avoid N reloads; history
    is always **prior turns only** (never later turns).
    """
    cfg = load_conversation_health_config()
    interaction = await Interaction.get(interaction_id)
    if not interaction:
        return {"error": "interaction_not_found", "interaction_id": interaction_id}

    agent_id = agent_id or str(
        (getattr(interaction, "health", None) or {}).get("agent_id") or ""
    )
    if not agent_id:
        return {"error": "missing_agent_id", "interaction_id": interaction_id}

    state = await ConversationHealthState.get_or_create_for_agent(agent_id)
    day_buckets = state.day_buckets if isinstance(state.day_buckets, dict) else {}
    state.day_buckets = day_buckets

    health = dict(getattr(interaction, "health", None) or {})
    if not health.get("scored"):
        health = await score_interaction(
            interaction,
            agent_id=agent_id,
            config=cfg,
            schedule_ai=False,
            force_rescore=False,
        )

    from jvagent.core.agent import Agent

    agent = await Agent.get(agent_id)
    model_action: Optional["LanguageModelAction"] = None
    if agent is not None:
        model_action = await agent.get_action_by_type(cfg.model_action_type)

    if not model_action:
        health["ai_status"] = "error"
        health["ai_select_reason"] = "no_language_model"
        interaction.health = health
        await interaction.save()
        return {"error": "no_language_model", "health": health}

    history: List[Dict[str, str]] = []
    try:
        ordered = ordered_interactions
        if ordered is None and interaction.conversation_id:
            conv = await Conversation.get(interaction.conversation_id)
            if conv:
                ordered = await conv.get_interactions(limit=0, reverse=False)
        if ordered:
            priors = prior_interactions(
                ordered,
                str(interaction.id),
                limit=cfg.history_limit,
            )
            history = history_for_ai(priors)
    except Exception:
        logger.debug("Could not load prior interactions for AI", exc_info=True)

    try:
        ai_payload = await run_ai_evaluation(
            model_action=model_action,
            utterance=str(interaction.utterance or ""),
            response=str(interaction.response or ""),
            history=history,
            model=cfg.model,
            temperature=cfg.model_temperature,
            max_tokens=cfg.model_max_tokens,
        )
    except Exception as e:
        logger.error("AI evaluation failed: %s", e, exc_info=True)
        health["ai_status"] = "error"
        health["ai_error"] = str(e)[:300]
        interaction.health = health
        await interaction.save()
        return {"error": "ai_failed", "detail": str(e), "health": health}

    prev_contribution = health.get("contribution")
    day = str((prev_contribution or {}).get("day") or utc_day_str())
    updated = apply_ai_to_health(
        health,
        ai_payload,
        day=day,
        flag_threshold=cfg.flag_threshold,
        excerpt_max=cfg.evidence_excerpt_max_chars,
    )
    updated["contribution"] = build_contribution(
        day=day,
        dimensions=updated.get("dimensions") or {},
        flagged=bool(updated.get("flagged")),
        issues=updated.get("issues") or [],
    )

    if prev_contribution:
        apply_contribution(day_buckets, prev_contribution, sign=-1)
    apply_contribution(day_buckets, updated["contribution"], sign=+1)

    interaction.health = updated
    await interaction.save()

    if interaction.conversation_id:
        conv = await Conversation.get(interaction.conversation_id)
        if conv:
            await _update_conversation_rollup(conv, agent_id=agent_id)

    state.day_buckets = day_buckets
    await state.save()
    return {"ok": True, "health": updated}


async def get_agent_reading(
    agent_id: str, days: Optional[int] = None
) -> Dict[str, Any]:
    cfg = load_conversation_health_config()
    state = await ConversationHealthState.get_or_create_for_agent(agent_id)
    day_buckets = state.day_buckets if isinstance(state.day_buckets, dict) else {}
    n = days if days is not None else cfg.reading_window_days
    n = max(1, min(int(n), 30))
    reading = agent_reading_from_buckets(day_buckets, days=n)
    reading["agent_id"] = agent_id
    reading["flag_threshold"] = cfg.flag_threshold
    reading["dimensions"] = list(DIMENSIONS)
    reading["enabled"] = True
    return reading
