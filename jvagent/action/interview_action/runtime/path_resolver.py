"""Reachable field path resolution for interview specs."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Set

from ..core.interview_loader import FieldDef, InterviewSpec
from ..core.session import InterviewSession
from .branch_eval import matches_branch_condition


def _has_branching(spec: InterviewSpec) -> bool:
    return any(f.branches or f.else_field for f in spec.fields)


async def resolve_next_question_name(
    session: InterviewSession,
    spec: InterviewSpec,
    load_function: Callable[[str], Optional[Callable]],
    visitor: Any = None,
    interview_action: Any = None,
) -> Optional[str]:
    """Return the key of the next unanswered reachable field, or None."""
    reachable = await compute_reachable_question_names(
        session, spec, load_function, visitor, interview_action
    )
    for key in reachable:
        if session.is_skipped(key):
            continue
        if not session.has_field(key):
            return key
    return None


async def compute_reachable_question_names(
    session: InterviewSession,
    spec: InterviewSpec,
    load_function: Callable[[str], Optional[Callable]],
    visitor: Any = None,
    interview_action: Any = None,
) -> List[str]:
    """Ordered list of field keys on the active path from start to end."""
    if not spec.fields:
        return []

    if not _has_branching(spec):
        return spec.field_keys()

    by_key = {f.key: f for f in spec.fields}
    order = spec.field_keys()
    reachable: List[str] = []
    visited: Set[str] = set()
    current = order[0]

    while current and current not in visited:
        visited.add(current)
        fdef = by_key.get(current)
        if not fdef:
            break
        reachable.append(current)

        if not session.has_field(current) and not session.is_skipped(current):
            break

        nxt = await _resolve_next_from_field(
            fdef, session, spec, load_function, visitor, interview_action
        )
        if not nxt:
            break
        current = nxt

    return reachable


async def _resolve_next_from_field(
    fdef: FieldDef,
    session: InterviewSession,
    spec: InterviewSpec,
    load_function: Callable[[str], Optional[Callable]],
    visitor: Any = None,
    interview_action: Any = None,
) -> Optional[str]:
    for branch in fdef.branches:
        if not branch.goto:
            continue
        if await matches_branch_condition(
            branch.when,
            session,
            fdef.key,
            load_function,
            visitor,
            interview_action,
        ):
            return branch.goto
    if fdef.else_field:
        return fdef.else_field
    keys = spec.field_keys()
    try:
        idx = keys.index(fdef.key)
    except ValueError:
        return None
    if idx + 1 < len(keys):
        return keys[idx + 1]
    return None


async def compute_reachable_required(
    session: InterviewSession,
    spec: InterviewSpec,
    load_function: Callable[[str], Optional[Callable]],
    visitor: Any = None,
    interview_action: Any = None,
) -> List[str]:
    """Required field keys that are reachable on the current path."""
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
    fdef = spec.get_field(nxt)
    if not fdef:
        return []
    entry: Dict[str, Any] = {
        "key": fdef.key,
        "name": fdef.key,
        "prompt": fdef.prompt,
        "question": fdef.prompt,
        "required": fdef.required,
        "validator": fdef.validator,
    }
    if fdef.guidance:
        entry["guidance"] = fdef.guidance
        entry["description"] = fdef.guidance
    if fdef.pre_processor:
        entry["pre_processor"] = fdef.pre_processor
    if fdef.post_processor:
        entry["post_processor"] = fdef.post_processor
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
