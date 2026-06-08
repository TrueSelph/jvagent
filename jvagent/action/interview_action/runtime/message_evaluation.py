"""Per-message entity evaluation — surface candidates; model extracts via set_field."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from ..core.field_extractors import extract_candidates_for_question
from ..core.interview_loader import (
    InterviewSpec,
    question_has_validator,
    resolve_validator_def,
    resolve_validator_kwargs,
)
from ..core.session import CTX_QUESTION_PRESENTED, InterviewSession
from .path_resolver import (
    compute_reachable_question_names,
    compute_reachable_required,
    missing_required_reachable,
)
from .pipeline import validate_field

if TYPE_CHECKING:
    from ..interview_action import InterviewAction

# Direct answers (e.g. "Jane Doe") may use the full utterance as a candidate.
_SHORT_DIRECT_ANSWER_MAX_LEN = 80


@dataclass
class FieldExtractionHint:
    field: str
    question: str
    description: str
    candidates: List[str] = dc_field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "question": self.question,
            "description": self.description,
            "candidates": list(self.candidates),
        }


@dataclass
class MessageEvaluation:
    utterance: str
    missing_required: List[str]
    applicable: List[FieldExtractionHint] = dc_field(default_factory=list)
    no_match_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "utterance": self.utterance,
            "missing_required": list(self.missing_required),
            "applicable": [h.to_dict() for h in self.applicable],
            "no_match_reason": self.no_match_reason,
        }

    @property
    def first_applicable_field(self) -> Optional[str]:
        for hint in self.applicable:
            if hint.field in self.missing_required:
                return hint.field
        return self.applicable[0].field if self.applicable else None


def _ordered_missing_fields(
    session: InterviewSession,
    spec: InterviewSpec,
    reachable: List[str],
    required: List[str],
) -> List[str]:
    missing_set = set(missing_required_reachable(session, required))
    return [name for name in reachable if name in missing_set]


def _include_full_utterance_candidate(
    session: InterviewSession,
    field_name: str,
    utterance: str,
) -> bool:
    ctx = session.context if isinstance(session.context, dict) else {}
    if ctx.get(CTX_QUESTION_PRESENTED) != field_name:
        return False
    return len(utterance) <= _SHORT_DIRECT_ANSWER_MAX_LEN


def _collect_candidates(
    session: InterviewSession,
    spec: InterviewSpec,
    field_name: str,
    utterance: str,
) -> List[str]:
    q = spec.get_question(field_name)
    if not q:
        return []
    vdef = resolve_validator_def(q, spec)
    if not vdef:
        if _include_full_utterance_candidate(session, field_name, utterance):
            return [utterance]
        return []

    kwargs = resolve_validator_kwargs(q, vdef)
    candidates = extract_candidates_for_question(q, vdef, utterance, kwargs)
    if _include_full_utterance_candidate(session, field_name, utterance):
        if utterance and utterance not in candidates:
            candidates.insert(0, utterance)
    return candidates


async def evaluate_message_for_extraction(
    action: "InterviewAction",
    session: InterviewSession,
    spec: InterviewSpec,
    utterance: str,
    visitor: Any,
    *,
    load_fn: Optional[Callable[[str], Any]] = None,
) -> MessageEvaluation:
    """Scan utterance for applicable entities on reachable missing fields (no store)."""
    msg = (utterance or "").strip()
    if not msg:
        return MessageEvaluation(utterance="", missing_required=[])

    if load_fn is None:
        load_fn = action._load_fn(spec)

    required = await compute_reachable_required(session, spec, load_fn, visitor, action)
    reachable = await compute_reachable_question_names(
        session, spec, load_fn, visitor, action
    )
    missing_ordered = _ordered_missing_fields(session, spec, reachable, required)

    applicable: List[FieldExtractionHint] = []
    for field_name in missing_ordered:
        q = spec.get_question(field_name)
        if not q:
            continue
        candidates = _collect_candidates(session, spec, field_name, msg)
        if not candidates:
            continue

        validated: List[str] = []
        if question_has_validator(q):
            for candidate in candidates:
                check = await validate_field(
                    action, spec, field_name, candidate, session, visitor
                )
                if check.get("valid"):
                    validated.append(check.get("value", candidate))
        else:
            validated = [c for c in candidates if c.strip()]

        if not validated:
            continue

        applicable.append(
            FieldExtractionHint(
                field=field_name,
                question=q.question or "",
                description=(q.description or "").strip(),
                candidates=validated,
            )
        )

    no_match_reason = None
    if not applicable and missing_ordered:
        no_match_reason = "no_valid_candidates_for_missing_fields"

    return MessageEvaluation(
        utterance=msg,
        missing_required=missing_ordered,
        applicable=applicable,
        no_match_reason=no_match_reason,
    )
