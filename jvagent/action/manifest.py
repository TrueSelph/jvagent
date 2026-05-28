"""Pattern-agnostic ``Manifest`` schema (BRIDGE-ROADMAP §D, ADR-0007 v0).

A manifest is an OPTIONAL block in an action's ``info.yaml`` that surfaces
runtime routing hints to any helm or scheduler that wants to consume them:

- ``purpose`` — short human-readable description of what the action does.
- ``activates_on`` — triggers / phrases that suggest this action is appropriate.
- ``terminates_when`` — completion / interrupt conditions.
- ``latency_class`` — one of ``instant | quick | deliberate | long``.
  Helms use this to decide whether to publish an ack-on-shift before
  delegating to the action.
- ``turn_lock`` — when True, other helms must DELEGATE or interrupt rather
  than running in parallel during this action's lifetime (think
  multi-turn forms).
- ``interrupt_phrases`` — phrases that may break ``turn_lock``. Read by
  the lock-owning rails IA's own intent classifier (e.g. an interview's
  CANCELLATION state). NOT a Bridge-level mechanic — Bridge always
  auto-DELEGATEs to the lock owner regardless of helm or utterance.
- ``expected_duration_seconds`` — operator hint; not enforced.

The manifest is read at loader-level (``loader/info_yaml.py``) into
``Action.metadata['manifest']``. Operators may override per-deployment via
``agent.yaml.context.manifest:``. Missing manifests resolve to safe defaults
via :func:`Manifest.from_payload` so existing actions continue to work
unchanged.

This module is intentionally pattern-agnostic — it does not import from
``jvagent.action.helm``. All patterns consume the same :class:`Manifest`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Valid latency classes (ordered fast → slow). The order is consulted by
# helms when deciding ack-on-shift policy: anything past ``quick`` warrants
# an ack publish before the shift to avoid perceived dead air.
VALID_LATENCY_CLASSES = ("instant", "quick", "deliberate", "long")
ACK_ELIGIBLE_LATENCY_CLASSES = frozenset({"deliberate", "long"})

DEFAULT_LATENCY_CLASS = "quick"
DEFAULT_TURN_LOCK = False


class ManifestValidationError(ValueError):
    """Raised when an ``info.yaml`` ``manifest:`` block fails validation.

    Loader callers MAY catch this to fall back to defaults rather than
    failing the action install. The exception carries the offending field
    name (when known) for diagnostic logging.
    """

    def __init__(self, message: str, *, field_name: Optional[str] = None):
        super().__init__(message)
        self.field_name = field_name


@dataclass(frozen=True)
class Manifest:
    """A parsed, validated manifest payload.

    Constructed via :meth:`from_payload` (handles defaults + validation)
    rather than the dataclass constructor directly.
    """

    purpose: str = ""
    activates_on: List[str] = field(default_factory=list)
    terminates_when: List[str] = field(default_factory=list)
    latency_class: str = DEFAULT_LATENCY_CLASS
    turn_lock: bool = DEFAULT_TURN_LOCK
    interrupt_phrases: List[str] = field(default_factory=list)
    expected_duration_seconds: Optional[float] = None

    @classmethod
    def from_payload(
        cls,
        payload: Optional[Dict[str, Any]],
        *,
        strict: bool = False,
    ) -> "Manifest":
        """Build a :class:`Manifest` from a raw dict.

        Args:
            payload: Parsed ``manifest:`` block from ``info.yaml`` or
                ``agent.yaml.context.manifest:``. ``None`` returns the
                defaults manifest.
            strict: If ``True``, raise :class:`ManifestValidationError` on
                any field type/value mismatch. If ``False`` (default), log
                a warning and substitute the default for the offending
                field so the loader can continue.

        Returns:
            A :class:`Manifest` instance.
        """
        if payload is None:
            return cls()
        if not isinstance(payload, dict):
            msg = f"manifest payload must be a dict, got {type(payload).__name__}"
            if strict:
                raise ManifestValidationError(msg)
            logger.warning("manifest: %s — using defaults", msg)
            return cls()

        return cls(
            purpose=_validate_string(payload, "purpose", "", strict=strict),
            activates_on=_validate_string_list(payload, "activates_on", strict=strict),
            terminates_when=_validate_string_list(
                payload, "terminates_when", strict=strict
            ),
            latency_class=_validate_latency_class(payload, strict=strict),
            turn_lock=_validate_bool(
                payload, "turn_lock", DEFAULT_TURN_LOCK, strict=strict
            ),
            interrupt_phrases=_validate_string_list(
                payload, "interrupt_phrases", strict=strict
            ),
            expected_duration_seconds=_validate_optional_float(
                payload, "expected_duration_seconds", strict=strict
            ),
        )

    def merged_with(self, override: Optional[Dict[str, Any]]) -> "Manifest":
        """Return a new :class:`Manifest` with ``override`` shallow-merged on top.

        Used when ``agent.yaml.context.manifest:`` overrides a field declared
        in ``info.yaml``. Any field present in ``override`` replaces the
        corresponding field on this manifest; absent fields keep their
        current value. Validation runs against the merged payload so an
        invalid override fails consistently with a fresh load.
        """
        if not override:
            return self
        merged: Dict[str, Any] = {
            "purpose": self.purpose,
            "activates_on": list(self.activates_on),
            "terminates_when": list(self.terminates_when),
            "latency_class": self.latency_class,
            "turn_lock": self.turn_lock,
            "interrupt_phrases": list(self.interrupt_phrases),
            "expected_duration_seconds": self.expected_duration_seconds,
        }
        merged.update(override)
        return Manifest.from_payload(merged)

    def is_ack_eligible(self) -> bool:
        """True iff the manifest's ``latency_class`` warrants ack-on-shift."""
        return (self.latency_class or "").lower() in ACK_ELIGIBLE_LATENCY_CLASSES

    def to_dict(self) -> Dict[str, Any]:
        """Serialise back to a dict suitable for re-embedding in YAML.

        Round-trip-safe: ``Manifest.from_payload(m.to_dict()) == m``.
        """
        return {
            "purpose": self.purpose,
            "activates_on": list(self.activates_on),
            "terminates_when": list(self.terminates_when),
            "latency_class": self.latency_class,
            "turn_lock": self.turn_lock,
            "interrupt_phrases": list(self.interrupt_phrases),
            "expected_duration_seconds": self.expected_duration_seconds,
        }


# ---------------------------------------------------------------------------
# Field validators
# ---------------------------------------------------------------------------


def _validate_string(
    payload: Dict[str, Any],
    key: str,
    default: str,
    *,
    strict: bool,
) -> str:
    val = payload.get(key, default)
    if val is None:
        return default
    if not isinstance(val, str):
        msg = f"manifest.{key} must be a string, got {type(val).__name__}"
        if strict:
            raise ManifestValidationError(msg, field_name=key)
        logger.warning("%s — using default %r", msg, default)
        return default
    return val


def _validate_string_list(
    payload: Dict[str, Any],
    key: str,
    *,
    strict: bool,
) -> List[str]:
    val = payload.get(key)
    if val is None:
        return []
    if not isinstance(val, list):
        msg = f"manifest.{key} must be a list of strings, got {type(val).__name__}"
        if strict:
            raise ManifestValidationError(msg, field_name=key)
        logger.warning("%s — using empty list", msg)
        return []
    out: List[str] = []
    for i, item in enumerate(val):
        if not isinstance(item, str):
            msg = f"manifest.{key}[{i}] must be a string, " f"got {type(item).__name__}"
            if strict:
                raise ManifestValidationError(msg, field_name=key)
            logger.warning("%s — skipping entry", msg)
            continue
        out.append(item)
    return out


def _validate_bool(
    payload: Dict[str, Any],
    key: str,
    default: bool,
    *,
    strict: bool,
) -> bool:
    val = payload.get(key, default)
    if isinstance(val, bool):
        return val
    msg = f"manifest.{key} must be a bool, got {type(val).__name__}"
    if strict:
        raise ManifestValidationError(msg, field_name=key)
    logger.warning("%s — using default %r", msg, default)
    return default


def _validate_latency_class(
    payload: Dict[str, Any],
    *,
    strict: bool,
) -> str:
    val = payload.get("latency_class", DEFAULT_LATENCY_CLASS)
    if val is None:
        return DEFAULT_LATENCY_CLASS
    if not isinstance(val, str):
        msg = f"manifest.latency_class must be a string, " f"got {type(val).__name__}"
        if strict:
            raise ManifestValidationError(msg, field_name="latency_class")
        logger.warning("%s — using default %r", msg, DEFAULT_LATENCY_CLASS)
        return DEFAULT_LATENCY_CLASS
    normalized = val.strip().lower()
    if normalized not in VALID_LATENCY_CLASSES:
        msg = f"manifest.latency_class={val!r} not in " f"{VALID_LATENCY_CLASSES!r}"
        if strict:
            raise ManifestValidationError(msg, field_name="latency_class")
        logger.warning("%s — using default %r", msg, DEFAULT_LATENCY_CLASS)
        return DEFAULT_LATENCY_CLASS
    return normalized


def _validate_optional_float(
    payload: Dict[str, Any],
    key: str,
    *,
    strict: bool,
) -> Optional[float]:
    val = payload.get(key)
    if val is None:
        return None
    if isinstance(val, bool):  # bool is a subclass of int — exclude explicitly
        pass
    elif isinstance(val, (int, float)):
        if val < 0:
            msg = f"manifest.{key} must be >= 0, got {val}"
            if strict:
                raise ManifestValidationError(msg, field_name=key)
            logger.warning("%s — dropping field", msg)
            return None
        return float(val)
    msg = f"manifest.{key} must be a number, got {type(val).__name__}"
    if strict:
        raise ManifestValidationError(msg, field_name=key)
    logger.warning("%s — dropping field", msg)
    return None


__all__ = [
    "ACK_ELIGIBLE_LATENCY_CLASSES",
    "DEFAULT_LATENCY_CLASS",
    "DEFAULT_TURN_LOCK",
    "Manifest",
    "ManifestValidationError",
    "VALID_LATENCY_CLASSES",
]
