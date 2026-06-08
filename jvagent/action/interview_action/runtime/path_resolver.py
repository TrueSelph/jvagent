"""Reachable question path resolution for interview specs."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Set

from ..core.interview_loader import InterviewSpec, QuestionDef
from ..core.session import InterviewSession
from .branch_eval import matches_branch_condition


def _has_branching(spec: InterviewSpec) -> bool:
    return any(q.branches or q.default_next for q in spec.questions)


def _question_index(spec: InterviewSpec) -> Dict[str, int]:
    return {q.name: i for i, q in enumerate(spec.questions)}


async def resolve_next_question_name(
    session: InterviewSession,
    spec: InterviewSpec,
    load_function: Callable[[str], Optional[Callable]],
    visitor: Any = None,
    interview_action: Any = None,
) -> Optional[str]:
    """Return the name of the next unanswered reachable question, or None."""
    reachable = await compute_reachable_question_names(
        session, spec, load_function, visitor, interview_action
    )
    for name in reachable:
        if session.is_skipped(name):
            continue
        if not session.has_field(name):
            return name
    return None


async def compute_reachable_question_names(
    session: InterviewSession,
    spec: InterviewSpec,
    load_function: Callable[[str], Optional[Callable]],
    visitor: Any = None,
    interview_action: Any = None,
) -> List[str]:
    """Ordered list of question names on the active path from start to end."""
    if not spec.questions:
        return []

    if not _has_branching(spec):
        return spec.question_names()

    by_name = {q.name: q for q in spec.questions}
    order = spec.question_names()
    reachable: List[str] = []
    visited: Set[str] = set()
    current = order[0]

    while current and current not in visited:
        visited.add(current)
        q = by_name.get(current)
        if not q:
            break
        reachable.append(current)

        if not session.has_field(current) and not session.is_skipped(current):
            break

        nxt = await _resolve_next_from_question(
            q, session, spec, load_function, visitor, interview_action
        )
        if not nxt:
            break
        current = nxt

    return reachable


async def _resolve_next_from_question(
    q: QuestionDef,
    session: InterviewSession,
    spec: InterviewSpec,
    load_function: Callable[[str], Optional[Callable]],
    visitor: Any = None,
    interview_action: Any = None,
) -> Optional[str]:
    for branch in q.branches:
        if not branch.target:
            continue
        if await matches_branch_condition(
            branch.condition,
            session,
            q.name,
            load_function,
            visitor,
            interview_action,
        ):
            return branch.target
    if q.default_next:
        return q.default_next
    names = spec.question_names()
    try:
        idx = names.index(q.name)
    except ValueError:
        return None
    if idx + 1 < len(names):
        return names[idx + 1]
    return None


async def compute_reachable_required(
    session: InterviewSession,
    spec: InterviewSpec,
    load_function: Callable[[str], Optional[Callable]],
    visitor: Any = None,
    interview_action: Any = None,
) -> List[str]:
    """Required field names that are reachable on the current path."""
    reachable = await compute_reachable_question_names(
        session, spec, load_function, visitor, interview_action
    )
    required = set(spec.get_required_fields())
    return [n for n in reachable if n in required]


def missing_required_reachable(
    session: InterviewSession,
    required_names: List[str],
) -> List[str]:
    missing = []
    for f in required_names:
        if not session.has_field(f) and not session.is_skipped(f):
            missing.append(f)
    return missing


async def resolve_store_continuation(
    session: InterviewSession,
    spec: InterviewSpec,
    load_function: Callable[[str], Optional[Callable]],
    visitor: Any = None,
    interview_action: Any = None,
) -> tuple[str, Optional[str]]:
    """Next mechanical step after a successful store or skip."""
    from ..core.responses import call_tool_directive

    next_name = await resolve_next_question_name(
        session, spec, load_function, visitor, interview_action
    )
    if next_name:
        return (
            call_tool_directive("interview__next_question"),
            "interview__next_question",
        )
    return call_tool_directive("interview__review"), "interview__review"


async def build_next_questions(
    session: InterviewSession,
    spec: InterviewSpec,
    load_function: Callable[[str], Optional[Callable]],
    visitor: Any = None,
    interview_action: Any = None,
) -> List[Dict[str, Any]]:
    """Build next_questions list (0 or 1 entry) for tool responses."""
    nxt = await resolve_next_question_name(
        session, spec, load_function, visitor, interview_action
    )
    if not nxt:
        return []
    q = spec.get_question(nxt)
    if not q:
        return []
    entry: Dict[str, Any] = {
        "name": q.name,
        "question": q.question,
        "required": q.required,
        "validator": q.validator,
    }
    if q.description:
        entry["description"] = q.description
    if q.input_context_provider:
        entry["input_context_provider"] = q.input_context_provider
    if q.pre_tools:
        entry["pre_tools"] = q.pre_tools
    if q.post_tools:
        entry["post_tools"] = q.post_tools
    return [entry]


def prune_unreachable_fields(
    session: InterviewSession,
    reachable_names: List[str],
) -> List[str]:
    """Remove field values no longer on the reachable path. Returns pruned names."""
    reachable = set(reachable_names)
    pruned: List[str] = []
    for name in list(session.fields.keys()):
        if name not in reachable:
            pruned.append(name)
            session.fields.pop(name, None)
    if pruned:
        audit = session.context.setdefault("pruned_fields", [])
        if isinstance(audit, list):
            audit.extend(pruned)
    return pruned
