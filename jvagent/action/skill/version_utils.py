"""Shared semver-like comparison for skill and dependency constraints."""

from typing import List, Tuple


def version_satisfies(actual: str, constraint: str) -> bool:
    """Return True if *actual* satisfies simple *constraint* (same rules as skill deps).

    Supported operators: ``>=``, ``>``, ``<=``, ``<``, ``==``, ``~``, ``^``.
    Bare constraint versions are treated as ``>=``.
    """
    import re as _re

    if not actual or not constraint:
        return False

    constraint = str(constraint).strip()
    actual = str(actual).strip()

    m = _re.match(r"^\s*(>=|>|<=|<|==|~|\^)?\s*(.+)$", constraint)
    if not m:
        return False
    op = m.group(1) or ">="
    target = m.group(2).strip()

    def _parse(v: str) -> Tuple[int, ...]:
        parts: List[int] = []
        for p in v.split("."):
            try:
                parts.append(int(p))
            except ValueError:
                break
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts)

    a = _parse(actual)
    t = _parse(target)

    if op == ">=":
        return a >= t
    if op == ">":
        return a > t
    if op == "<=":
        return a <= t
    if op == "<":
        return a < t
    if op == "==":
        return a == t
    if op == "~":
        return a[0] == t[0] and a[1] >= t[1]
    if op == "^":
        return a[0] == t[0] and a >= t
    return False
