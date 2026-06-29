"""Declarative activation seeding — match ``activation_utterance`` to field values.

Invariant **I-INT-SEED-01**: trigger phrases for gated-resume field seeding live in
``fields[].validator_args.seed_from_activation`` (SKILL.md contract), not in skill
Python. Use built-in pre_processor ``seed_field_from_activation``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .session import ACTIVATION_UTTERANCE_KEY, InterviewSession
from .spec import FieldDef, InterviewSpec

SEED_FROM_ACTIVATION_KEY = "seed_from_activation"
ALLOWED_ITEMS_KEY = "allowed_items"


def resolve_activation_utterance(
    session: Optional[InterviewSession], visitor: Any = None
) -> str:
    """Original user request stashed on activation or carried through task seed."""
    if session is not None and isinstance(getattr(session, "context", None), dict):
        seed = str(session.context.get(ACTIVATION_UTTERANCE_KEY) or "").strip()
        if seed:
            return seed
    return str(getattr(visitor, "utterance", "") or "").strip()


def normalize_seed_from_activation(raw: Any) -> Dict[str, List[str]]:
    """Parse ``seed_from_activation`` mapping into canonical value → phrase list."""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, List[str]] = {}
    for key, phrases in raw.items():
        canon = str(key).strip()
        if not canon:
            continue
        if isinstance(phrases, str):
            phrase_list = [phrases]
        elif isinstance(phrases, list):
            phrase_list = phrases
        else:
            continue
        cleaned = [str(p).strip().lower() for p in phrase_list if str(p).strip()]
        if cleaned:
            out[canon] = cleaned
    return out


def match_seed_from_activation(
    text: str,
    seed_map: Dict[str, List[str]],
    *,
    allowed_values: Optional[List[str]] = None,
) -> Optional[str]:
    """Return the best-matching canonical value, or None.

    Rules (deterministic):
    1. Full utterance equals an ``allowed_items`` value (case-insensitive) → that value.
    2. Otherwise longest matching trigger phrase wins; ties → earlier YAML key.
    3. When ``allowed_values`` is set, only those canonical values are eligible.
    """
    blob = (text or "").strip().lower()
    if not blob or not seed_map:
        return None

    allowed_lookup: Dict[str, str] = {}
    if allowed_values:
        for item in allowed_values:
            canon = str(item).strip()
            if canon:
                allowed_lookup[canon.lower()] = canon

    if allowed_lookup and blob in allowed_lookup:
        return allowed_lookup[blob]

    best_len = -1
    best_order = len(seed_map)
    best_value: Optional[str] = None

    for order, (value, phrases) in enumerate(seed_map.items()):
        low = value.lower()
        if allowed_lookup and low not in allowed_lookup:
            continue
        canon = allowed_lookup.get(low, value)
        for phrase in phrases:
            if phrase not in blob:
                continue
            if len(phrase) > best_len or (
                len(phrase) == best_len and order < best_order
            ):
                best_len = len(phrase)
                best_order = order
                best_value = canon

    return best_value


def infer_field_from_activation(
    session: Optional[InterviewSession],
    field_def: Optional[FieldDef],
    visitor: Any = None,
) -> Optional[str]:
    """Infer a single field value from activation text using its frontmatter config."""
    if field_def is None:
        return None
    args = field_def.validator_args or {}
    seed_map = normalize_seed_from_activation(args.get(SEED_FROM_ACTIVATION_KEY))
    if not seed_map:
        return None
    allowed = args.get(ALLOWED_ITEMS_KEY) or args.get("allowed_values")
    if allowed is not None and not isinstance(allowed, list):
        allowed = None
    text = resolve_activation_utterance(session, visitor)
    return match_seed_from_activation(text, seed_map, allowed_values=allowed)


async def seed_field_from_activation(ctx) -> str:
    """Built-in activation ``pre_processor`` — seed empty field from utterance."""
    session = ctx.session
    field_def = ctx.field_def
    if session is None or field_def is None:
        return ctx.tool_response(ok=True, status="ok")

    if (session.get_value(field_def.key) or "").strip():
        return ctx.tool_response(ok=True, status="ok")

    inferred = infer_field_from_activation(session, field_def, ctx.visitor)
    if not inferred:
        return ctx.tool_response(ok=True, status="ok")

    session.set_value(field_def.key, inferred)
    interview_action = ctx.interview
    if interview_action is not None:
        await interview_action._save_session(session, ctx.visitor)

    return ctx.tool_response(
        ok=True,
        status="ok",
        suggested_value=inferred,
        system_message=(
            f"Seeded {field_def.key}={inferred!r} from activation_utterance "
            f"(declarative seed_from_activation)."
        ),
    )
