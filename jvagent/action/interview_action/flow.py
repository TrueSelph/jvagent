"""Reachable-field path resolution and branch condition evaluation."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Set

from .session import InterviewSession
from .spec import FieldDef, InterviewSpec

logger = logging.getLogger(__name__)

LoadFn = Callable[[str], Optional[Callable]]


# ---------------------------------------------------------------------------
# Branch condition evaluation
# ---------------------------------------------------------------------------


def _normalize(value: Any) -> Any:
    return value.strip().lower() if isinstance(value, str) else value


def _coerce_number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _op_equals(actual: Any, expected: Any) -> bool:
    if actual is None or expected is None:
        return actual == expected
    return _normalize(actual) == _normalize(expected)


def _op_compare(actual: Any, expected: Any, op: Callable[[float, float], bool]) -> bool:
    a, e = _coerce_number(actual), _coerce_number(expected)
    if a is None or e is None:
        return False
    return op(a, e)


def _op_in(actual: Any, expected: Any) -> bool:
    if not isinstance(expected, (list, tuple, set)):
        return False
    return _normalize(actual) in [_normalize(v) for v in expected]


def _op_contains(actual: Any, expected: Any) -> bool:
    if isinstance(actual, str):
        return str(expected).lower() in actual.lower()
    if isinstance(actual, (list, tuple, set)):
        return _normalize(expected) in [_normalize(v) for v in actual]
    return False


def _op_exists(actual: Any, _expected: Any = None) -> bool:
    if actual is None:
        return False
    if isinstance(actual, str):
        return bool(actual.strip())
    return True


_OPERATORS: Dict[str, Callable[[Any, Any], bool]] = {
    "equals": _op_equals,
    "==": _op_equals,
    "!=": lambda a, e: not _op_equals(a, e),
    "not_equals": lambda a, e: not _op_equals(a, e),
    ">": lambda a, e: _op_compare(a, e, lambda x, y: x > y),
    "greater_than": lambda a, e: _op_compare(a, e, lambda x, y: x > y),
    ">=": lambda a, e: _op_compare(a, e, lambda x, y: x >= y),
    "greater_than_or_equal": lambda a, e: _op_compare(a, e, lambda x, y: x >= y),
    "<": lambda a, e: _op_compare(a, e, lambda x, y: x < y),
    "less_than": lambda a, e: _op_compare(a, e, lambda x, y: x < y),
    "<=": lambda a, e: _op_compare(a, e, lambda x, y: x <= y),
    "less_than_or_equal": lambda a, e: _op_compare(a, e, lambda x, y: x <= y),
    "in": _op_in,
    "in_list": _op_in,
    "not_in": lambda a, e: not _op_in(a, e),
    "not_in_list": lambda a, e: not _op_in(a, e),
    "contains": _op_contains,
    "not_contains": lambda a, e: not _op_contains(a, e),
    "exists": _op_exists,
    "is_set": _op_exists,
    "not_exists": lambda a, e: not _op_exists(a, e),
    "is_not_set": lambda a, e: not _op_exists(a, e),
}


def evaluate_operator(operator: str, actual: Any, expected: Any = None) -> bool:
    fn = _OPERATORS.get(operator.lower().strip())
    if fn is None:
        raise ValueError(f"Unknown condition operator: {operator}")
    return fn(actual, expected)


async def matches_branch_condition(
    condition: Dict[str, Any],
    session: InterviewSession,
    field_key: str,
    load_function: LoadFn,
    visitor: Any = None,
    interview_action: Any = None,
) -> bool:
    """Return True when a branch condition matches the current session state."""
    from .hooks import call_hook

    if not condition or not field_key:
        return False

    if "function" in condition:
        function_name = condition.get("function")
        if not function_name:
            return False
        operator = condition.get("op")
        is_existence = operator in ("exists", "is_set", "not_exists", "is_not_set")
        if not is_existence and field_key not in session.fields:
            return False
        func = load_function(function_name)
        if not func:
            logger.error("Branch function '%s' not found", function_name)
            return False
        result = await call_hook(
            func,
            session=session,
            visitor=visitor,
            interview_action=interview_action,
        )
        if operator:
            try:
                return evaluate_operator(operator, result, condition.get("value"))
            except ValueError:
                return False
        return bool(result)

    try:
        return evaluate_operator(
            condition.get("op", "equals"),
            session.get_value(field_key),
            condition.get("value"),
        )
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Path walking
# ---------------------------------------------------------------------------


def _has_branching(spec: InterviewSpec) -> bool:
    return any(f.branches or f.else_field for f in spec.fields)


async def _resolve_next_from_field(
    fdef: FieldDef,
    session: InterviewSession,
    spec: InterviewSpec,
    load_function: LoadFn,
    visitor: Any = None,
    interview_action: Any = None,
) -> Optional[str]:
    for branch in fdef.branches:
        if not branch.goto:
            continue
        if await matches_branch_condition(
            branch.when, session, fdef.key, load_function, visitor, interview_action
        ):
            return branch.goto
    if fdef.else_field:
        return fdef.else_field
    if fdef.branches:
        return None
    keys = spec.field_keys()
    try:
        idx = keys.index(fdef.key)
    except ValueError:
        return None
    return keys[idx + 1] if idx + 1 < len(keys) else None


async def _walk_path(
    session: InterviewSession,
    spec: InterviewSpec,
    load_function: LoadFn,
    visitor: Any = None,
    interview_action: Any = None,
    *,
    stop_at_first_gap: bool,
) -> List[str]:
    """Walk the interview graph from the first field.

    With ``stop_at_first_gap`` (collectible path), stop after appending the
    first field that has no stored value and is not skipped. Without it
    (active projection for prune), continue through ``else`` branches and stop
    only at unresolved branch points.
    """
    if not spec.fields:
        return []

    if not _has_branching(spec):
        names = spec.field_keys()
        if not stop_at_first_gap:
            return names
        path: List[str] = []
        for key in names:
            path.append(key)
            if not session.has_field(key) and not session.is_skipped(key):
                break
        return path

    by_key = {f.key: f for f in spec.fields}
    reachable: List[str] = []
    visited: Set[str] = set()
    current: Optional[str] = spec.fields[0].key

    while current and current not in visited:
        visited.add(current)
        fdef = by_key.get(current)
        if not fdef:
            break
        reachable.append(current)

        if (
            stop_at_first_gap
            and not session.has_field(current)
            and not session.is_skipped(current)
        ):
            break

        current = await _resolve_next_from_field(
            fdef, session, spec, load_function, visitor, interview_action
        )

    return reachable


async def compute_collectible_path_names(
    session: InterviewSession,
    spec: InterviewSpec,
    load_function: LoadFn,
    visitor: Any = None,
    interview_action: Any = None,
) -> List[str]:
    """Prefix of the active path up to the first unanswered field."""
    return await _walk_path(
        session, spec, load_function, visitor, interview_action, stop_at_first_gap=True
    )


async def compute_active_path_for_prune(
    session: InterviewSession,
    spec: InterviewSpec,
    load_function: LoadFn,
    visitor: Any = None,
    interview_action: Any = None,
) -> List[str]:
    """Full projected path for prune — retains valid downstream answers after branch pivots."""
    return await _walk_path(
        session,
        spec,
        load_function,
        visitor,
        interview_action,
        stop_at_first_gap=False,
    )


async def resolve_next_field_name(
    session: InterviewSession,
    spec: InterviewSpec,
    load_function: LoadFn,
    visitor: Any = None,
    interview_action: Any = None,
) -> Optional[str]:
    """Return the key of the next reachable unanswered field, or None."""
    reachable = await compute_collectible_path_names(
        session, spec, load_function, visitor, interview_action
    )
    for key in reachable:
        if not session.has_field(key) and not session.is_skipped(key):
            return key
    return None


async def compute_review_field_keys(
    session: InterviewSession,
    spec: InterviewSpec,
    load_function: LoadFn,
    visitor: Any = None,
    interview_action: Any = None,
) -> List[str]:
    """Stored field keys on the active path for review — skips off-path and skipped."""
    reachable = await compute_active_path_for_prune(
        session, spec, load_function, visitor, interview_action
    )
    return [
        key
        for key in reachable
        if session.has_field(key) and not session.is_skipped(key)
    ]


async def compute_missing_required(
    session: InterviewSession,
    spec: InterviewSpec,
    load_function: LoadFn,
    visitor: Any = None,
    interview_action: Any = None,
) -> List[str]:
    """Required field keys on the collectible path that are still unanswered."""
    reachable = await compute_collectible_path_names(
        session, spec, load_function, visitor, interview_action
    )
    required = set(spec.get_required_fields())
    return session.missing_required([n for n in reachable if n in required])


async def build_next_field(
    session: InterviewSession,
    spec: InterviewSpec,
    load_function: LoadFn,
    visitor: Any = None,
    interview_action: Any = None,
) -> Optional[Dict[str, Any]]:
    """Build next_field object for tool responses, or None when nothing remains."""
    nxt = await resolve_next_field_name(
        session, spec, load_function, visitor, interview_action
    )
    fdef = spec.get_field(nxt) if nxt else None
    if not fdef:
        return None
    entry: Dict[str, Any] = {
        "key": fdef.key,
        "prompt": fdef.prompt,
        "required": fdef.required,
        "validator": fdef.validator,
    }
    if fdef.guidance:
        entry["guidance"] = fdef.guidance
    return entry


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
            session.skipped_fields.discard(name)
    if pruned and isinstance(session.context, dict):
        audit = session.context.setdefault("pruned_fields", [])
        if isinstance(audit, list):
            audit.extend(pruned)
    return pruned
