"""Harness-owned resilience primitives for model calls (ADR-0046, Phase 3).

Adapter-agnostic: nothing here knows a provider. The Orchestrator composes
these around whatever ``LanguageModelAction`` a slot resolves to.

- :class:`CircuitBreaker` — per ``(action class, model)`` failure streaks with a
  cooldown. Consecutive failures past ``threshold`` open the circuit for
  ``cooldown_seconds``; an open circuit is skipped by the fallback chain so a
  dead provider is not re-tried on every turn. A success closes it. State is
  kept **per event loop** (serverless warm starts reuse the process on a fresh
  loop; a breaker tripped on the old loop must not leak into the new one — the
  same rule ``core/app.py`` applies to its locks).
- :func:`fallback_candidates` — the ordered ``(action, model_id)`` list a slot
  tries: the primary first, then the configured fallbacks that resolve.

Budget accounting helpers (per-turn / per-conversation spend) live in
``jvagent.action.orchestrator`` because they read interaction telemetry.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _loop_id() -> int:
    try:
        return id(asyncio.get_running_loop())
    except RuntimeError:
        return 0


def breaker_key(action: Any, model: Optional[str]) -> str:
    """Stable identity for a (provider action, model) pair."""
    name = ""
    getter = getattr(action, "get_class_name", None)
    if callable(getter):
        try:
            name = str(getter() or "")
        except Exception:  # pragma: no cover - defensive
            name = ""
    if not name:
        name = type(action).__name__ if action is not None else "model"
    return f"{name}:{model or getattr(action, 'model', '') or ''}"


@dataclass
class _BreakerState:
    failures: int = 0
    opened_at: float = 0.0
    open_until: float = 0.0
    last_error: str = ""
    trips: int = 0


@dataclass
class CircuitBreaker:
    """Failure-streak breaker with cooldown, keyed by :func:`breaker_key`.

    ``threshold <= 0`` disables tripping (every circuit reads closed).
    """

    threshold: int = 3
    cooldown_seconds: float = 60.0
    _states: Dict[int, Dict[str, _BreakerState]] = field(default_factory=dict)

    def _bucket(self) -> Dict[str, _BreakerState]:
        return self._states.setdefault(_loop_id(), {})

    def state(self, key: str) -> _BreakerState:
        return self._bucket().setdefault(key, _BreakerState())

    def is_open(self, key: str, now: Optional[float] = None) -> bool:
        if self.threshold <= 0:
            return False
        st = self._bucket().get(key)
        if st is None or not st.open_until:
            return False
        now = time.monotonic() if now is None else now
        if now >= st.open_until:
            # Cooldown elapsed: half-open — allow one attempt through. The
            # attempt's outcome re-closes or re-opens the circuit.
            st.open_until = 0.0
            return False
        return True

    def record_success(self, key: str) -> None:
        st = self._bucket().get(key)
        if st is not None:
            st.failures = 0
            st.open_until = 0.0
            st.last_error = ""

    def record_failure(
        self, key: str, error: Any = None, now: Optional[float] = None
    ) -> bool:
        """Note a failure; return True when this one tripped the circuit open."""
        if self.threshold <= 0:
            return False
        st = self.state(key)
        st.failures += 1
        st.last_error = str(error or "")[:200]
        if st.failures >= self.threshold:
            now = time.monotonic() if now is None else now
            st.opened_at = now
            st.open_until = now + max(0.0, float(self.cooldown_seconds))
            st.trips += 1
            st.failures = 0  # the next streak starts after the half-open probe
            logger.warning(
                "model circuit OPEN for %s (%d consecutive failures; cooldown %.0fs): %s",
                key,
                self.threshold,
                self.cooldown_seconds,
                st.last_error,
            )
            return True
        return False

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        """Current-loop circuits for health reporting."""
        now = time.monotonic()
        out: Dict[str, Dict[str, Any]] = {}
        for key, st in self._bucket().items():
            out[key] = {
                "open": bool(st.open_until and now < st.open_until),
                "failures": st.failures,
                "trips": st.trips,
                "cooldown_remaining_s": (
                    max(0, int(st.open_until - now)) if st.open_until else 0
                ),
                "last_error": st.last_error,
            }
        return out

    def reset(self) -> None:
        self._states.clear()


# Process-wide default breaker; the Orchestrator configures threshold/cooldown
# on it from agent.yaml at each turn (cheap, idempotent).
MODEL_BREAKER = CircuitBreaker()


async def fallback_candidates(
    primary: Tuple[Any, Optional[str]],
    fallbacks: Any,
    resolve_action: Any,
) -> List[Tuple[Any, Optional[str]]]:
    """Ordered ``(action, model_id)`` candidates for one slot.

    ``fallbacks`` is the operator list — each entry ``{"model": ..., "model_action_type": ...}``
    (or a bare model-id string, meaning the primary's action). ``resolve_action``
    is an async ``(action_type: str) -> action | None``. Entries that do not
    resolve are skipped with a warning rather than failing the turn.
    """
    out: List[Tuple[Any, Optional[str]]] = [primary]
    primary_action = primary[0]
    for entry in list(fallbacks or []):
        if isinstance(entry, str):
            model, action_type = entry.strip(), ""
        elif isinstance(entry, dict):
            model = str(entry.get("model") or "").strip()
            action_type = str(
                entry.get("model_action_type") or entry.get("action_type") or ""
            ).strip()
        else:
            continue
        if not model and not action_type:
            continue
        action = primary_action
        if action_type:
            try:
                action = await resolve_action(action_type)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("fallback: resolving %s failed: %s", action_type, exc)
                action = None
        if action is None:
            logger.warning(
                "fallback: action %r for model %r is not available — skipped",
                action_type,
                model,
            )
            continue
        out.append((action, model or None))
    return out


__all__ = [
    "CircuitBreaker",
    "MODEL_BREAKER",
    "breaker_key",
    "fallback_candidates",
]
