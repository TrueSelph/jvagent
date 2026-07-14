"""ConversationHealthAction — hybrid conversation quality evaluation."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
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
from .constants import DEFERRED_TASK_TYPE, DIMENSIONS
from .heuristics import response_duration_seconds, run_heuristics
from .sampling import assign_bucket, decide_ai_schedule
from .scoring import (
    build_contribution,
    build_interaction_health,
    heuristic_health_score,
    is_flagged,
    recompute_conversation_rollup,
    score_dimensions,
)

if TYPE_CHECKING:
    from jvagent.action.model.language.base import LanguageModelAction

logger = logging.getLogger(__name__)


class ConversationHealthAction(InteractAction):
    """Passive post-turn health scoring with bucketed async AI sampling."""

    # ── Execution ────────────────────────────────────────────────────────────
    weight: int = attribute(default=200, description="Late execution weight")
    always_execute: bool = attribute(default=True, description="Always run")
    run_in_background: bool = attribute(
        default=True,
        description="Run after user-facing response (awaited on Lambda)",
    )

    # ── Thresholds ───────────────────────────────────────────────────────────
    flag_threshold: float = attribute(default=70.0, description="Flag floor")
    optimization_ceiling: float = attribute(
        default=90.0, description="B is [flag, ceiling); A is >= ceiling"
    )
    reading_window_days: int = attribute(default=7, description="Agent reading window")
    evidence_excerpt_max_chars: int = attribute(default=120)

    # ── Ambient sampling ─────────────────────────────────────────────────────
    unflagged_ambient_max_rate: float = attribute(
        default=0.05, description="Max ambient AI rate for unflagged turns"
    )
    ambient_b_share: float = attribute(default=0.5)
    ambient_a_share: float = attribute(default=0.5)
    ambient_spillover: bool = attribute(default=True)
    ambient_b_target_rate: float = attribute(default=0.18)
    ambient_a_target_rate: float = attribute(default=0.02)

    # ── Latency bands (seconds) ──────────────────────────────────────────────
    latency_band_low: float = attribute(default=3.0)
    latency_band_medium: float = attribute(default=8.0)
    latency_band_high: float = attribute(default=15.0)

    # ── AI model ─────────────────────────────────────────────────────────────
    model_action_type: str = attribute(
        default="OpenAILanguageModelAction",
        description="LanguageModelAction class for AI Evaluation",
    )
    model: str = attribute(default="gpt-4o-mini")
    model_temperature: float = attribute(default=0.0)
    model_max_tokens: int = attribute(default=512)
    enable_ai: bool = attribute(
        default=True,
        description="If False, never schedule AI (heuristics only)",
    )
    history_limit: int = attribute(
        default=6, description="Prior turns for repetition + AI context"
    )

    # ── Persistent aggregates on the action node ─────────────────────────────
    day_buckets: Dict[str, Any] = attribute(
        default_factory=dict,
        description="Per-UTC-day health aggregates and ambient sampling counters",
    )

    # ── Interact entry ───────────────────────────────────────────────────────

    async def execute(self, visitor: InteractWalker) -> None:
        interaction = visitor.interaction
        if not interaction:
            return
        try:
            await self.score_interaction(interaction, agent_id=self.agent_id)
        except Exception:
            logger.error(
                "ConversationHealthAction failed for interaction %s",
                getattr(interaction, "id", None),
                exc_info=True,
            )

    def is_scorable(self, interaction: Interaction) -> tuple[bool, Optional[str]]:
        utterance = (getattr(interaction, "utterance", None) or "").strip()
        if not utterance:
            return False, "no_utterance"
        posture = (getattr(interaction, "response_posture", None) or "").upper()
        response = getattr(interaction, "response", None)
        has_response = response is not None and str(response).strip() != ""
        emitted = bool(getattr(interaction, "emitted", False))
        if posture in ("DEFER", "SUPPRESS") and not has_response and not emitted:
            return False, f"posture_{posture.lower()}_no_reply"
        if not has_response and not emitted and posture != "RESPOND":
            # RESPOND with empty response is scorable (empty_or_trivial)
            if posture and posture != "RESPOND":
                return False, "no_user_facing_reply"
        return True, None

    async def score_interaction(
        self,
        interaction: Interaction,
        *,
        agent_id: str = "",
        force_rescore: bool = False,
        schedule_ai: bool = True,
    ) -> Dict[str, Any]:
        """Heuristic score + optional deferred AI schedule. Returns health dict."""
        agent_id = agent_id or self.agent_id
        scorable, skip_reason = self.is_scorable(interaction)
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

        # Prior agent responses for repetition
        prior_responses: List[str] = []
        conversation = None
        if interaction.conversation_id:
            conversation = await Conversation.get(interaction.conversation_id)
            if conversation:
                try:
                    recent = await conversation.get_interactions(
                        limit=self.history_limit + 1, reverse=True
                    )
                    for other in recent:
                        if other.id == interaction.id:
                            continue
                        r = getattr(other, "response", None)
                        if r:
                            prior_responses.append(str(r))
                        if len(prior_responses) >= self.history_limit:
                            break
                    prior_responses.reverse()
                except Exception:
                    logger.debug("Could not load prior interactions", exc_info=True)

        utterance = str(getattr(interaction, "utterance", None) or "")
        response = str(getattr(interaction, "response", None) or "")
        duration = response_duration_seconds(interaction)
        bands = [
            (self.latency_band_low, "low"),
            (self.latency_band_medium, "medium"),
            (self.latency_band_high, "high"),
        ]
        issues = run_heuristics(
            utterance=utterance,
            response=response,
            duration=duration,
            prior_agent_responses=prior_responses,
            interaction=interaction,
            latency_bands=bands,
        )
        dimensions = score_dimensions(issues)
        flagged = is_flagged(
            dimensions, issues, flag_threshold=self.flag_threshold
        )
        hs = heuristic_health_score(dimensions)
        bucket = assign_bucket(
            health_score=hs,
            dimensions=dimensions,
            issues=issues,
            flagged=flagged,
            flag_threshold=self.flag_threshold,
            optimization_ceiling=self.optimization_ceiling,
        )

        day = utc_day_str(
            getattr(interaction, "completed_at", None)
            or getattr(interaction, "started_at", None)
        )
        contribution = build_contribution(
            day=day, dimensions=dimensions, flagged=flagged, issues=issues
        )

        # Day bucket contribution delta
        if prev_contribution:
            apply_contribution(self.day_buckets, prev_contribution, sign=-1)
        apply_contribution(self.day_buckets, contribution, sign=+1)

        # Sampling counters + decision
        ai_status = "none"
        ai_select_reason: Optional[str] = None
        ai_selected = already_selected
        should_schedule = False
        first_score = not bool(prev.get("scored"))

        if first_score and bucket in ("A", "B"):
            bump_sampling_eligible(self.day_buckets, day, bucket)

        if schedule_ai and self.enable_ai:
            days = window_day_list(self.reading_window_days)
            should_schedule, ai_status, ai_select_reason = decide_ai_schedule(
                interaction_id=str(interaction.id or ""),
                bucket=bucket,
                day_buckets=self.day_buckets,
                window_days=days,
                ambient_b_target_rate=self.ambient_b_target_rate,
                ambient_a_target_rate=self.ambient_a_target_rate,
                unflagged_ambient_max_rate=self.unflagged_ambient_max_rate,
                ambient_b_share=self.ambient_b_share,
                ambient_a_share=self.ambient_a_share,
                ambient_spillover=self.ambient_spillover,
                already_selected=already_selected,
            )
            if should_schedule and not already_selected:
                bump_sampling_selected(self.day_buckets, day, bucket)
                ai_selected = True
            elif already_selected:
                should_schedule = prev.get("ai_status") not in (
                    "completed",
                    "error",
                )
                ai_status = str(prev.get("ai_status") or "queued")
                ai_select_reason = str(
                    prev.get("ai_select_reason") or "already_selected"
                )
                ai_selected = True
        elif not self.enable_ai:
            ai_status = "none"
            ai_select_reason = "ai_disabled"

        # Preserve completed AI merge if re-heuristics without force wiping AI
        if (
            prev.get("evaluation_tier") in ("heuristic+ai", "ai")
            and prev.get("ai_status") == "completed"
            and not force_rescore
        ):
            # Keep AI-enriched issues if present; re-run only contribution from new heuristics
            # Spec: re-score heuristics refresh; for simplicity keep latest heuristic issues
            pass

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
        # Preserve completed AI if we had it and aren't forcing pure heuristic
        if prev.get("ai_status") == "completed" and prev.get("evaluation_tier") == "heuristic+ai":
            if not force_rescore:
                health["ai_status"] = "completed"
                health["evaluation_tier"] = prev.get("evaluation_tier")
                # Prefer previous AI-merged issues only if same contribution day re-score
                # Keep heuristic-only for v1 re-score simplicity

        interaction.health = health
        await interaction.save()

        if conversation:
            await self._update_conversation_rollup(conversation, agent_id=agent_id)

        prune_old_days(self.day_buckets, keep_days=max(30, self.reading_window_days * 2))
        await self.save()

        if should_schedule and schedule_ai and self.enable_ai:
            await self._schedule_ai_eval(
                interaction_id=str(interaction.id),
                agent_id=agent_id,
                priority=ai_select_reason or "ambient",
            )

        return health

    async def _update_conversation_rollup(
        self, conversation: Conversation, *, agent_id: str
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
        self,
        *,
        interaction_id: str,
        agent_id: str,
        priority: str = "ambient",
    ) -> None:
        payload = {
            "interaction_id": interaction_id,
            "agent_id": agent_id,
            "action_id": self.id,
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
            # Non-serverless / missing scheduler: best-effort inline
            try:
                await self.run_ai_for_interaction(interaction_id)
            except Exception:
                logger.error(
                    "Inline AI eval failed for %s", interaction_id, exc_info=True
                )

    async def run_ai_for_interaction(self, interaction_id: str) -> Dict[str, Any]:
        """Execute AI Evaluation and merge (deferred handler / Deep Review)."""
        interaction = await Interaction.get(interaction_id)
        if not interaction:
            return {"error": "interaction_not_found", "interaction_id": interaction_id}

        health = dict(getattr(interaction, "health", None) or {})
        if not health.get("scored"):
            health = await self.score_interaction(
                interaction, schedule_ai=False, force_rescore=False
            )

        model_action: Optional["LanguageModelAction"] = await self.get_action(
            self.model_action_type
        )
        if not model_action:
            health["ai_status"] = "error"
            health["ai_select_reason"] = "no_language_model"
            interaction.health = health
            await interaction.save()
            return {"error": "no_language_model", "health": health}

        history: List[Dict[str, str]] = []
        if interaction.conversation_id:
            conv = await Conversation.get(interaction.conversation_id)
            if conv:
                try:
                    recent = await conv.get_interactions(
                        limit=self.history_limit, reverse=True
                    )
                    for ix in reversed(recent):
                        if ix.utterance:
                            history.append(
                                {"role": "user", "content": str(ix.utterance)}
                            )
                        if ix.response:
                            history.append(
                                {"role": "assistant", "content": str(ix.response)}
                            )
                except Exception:
                    pass

        try:
            ai_payload = await run_ai_evaluation(
                model_action=model_action,
                utterance=str(interaction.utterance or ""),
                response=str(interaction.response or ""),
                history=history,
                model=self.model,
                temperature=self.model_temperature,
                max_tokens=self.model_max_tokens,
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
        updated = apply_ai_to_health(health, ai_payload, day=day)
        # Re-flag with action threshold
        updated["flagged"] = is_flagged(
            updated.get("dimensions") or {},
            updated.get("issues") or [],
            flag_threshold=self.flag_threshold,
        )
        updated["contribution"] = build_contribution(
            day=day,
            dimensions=updated.get("dimensions") or {},
            flagged=bool(updated.get("flagged")),
            issues=updated.get("issues") or [],
        )

        if prev_contribution:
            apply_contribution(self.day_buckets, prev_contribution, sign=-1)
        apply_contribution(self.day_buckets, updated["contribution"], sign=+1)

        interaction.health = updated
        await interaction.save()

        if interaction.conversation_id:
            conv = await Conversation.get(interaction.conversation_id)
            if conv:
                await self._update_conversation_rollup(
                    conv, agent_id=self.agent_id
                )

        await self.save()
        return {"ok": True, "health": updated}

    def get_agent_reading(self, days: Optional[int] = None) -> Dict[str, Any]:
        n = days if days is not None else self.reading_window_days
        n = max(1, min(int(n), 30))
        reading = agent_reading_from_buckets(self.day_buckets, days=n)
        reading["agent_id"] = self.agent_id
        reading["flag_threshold"] = self.flag_threshold
        reading["dimensions"] = list(DIMENSIONS)
        return reading


