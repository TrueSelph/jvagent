"""Runtime validator for ``Tool.parameters_schema``.

Catches schema patterns that strict-mode model providers (OpenAI gpt-4.1, etc.)
reject at the API layer. Running this at Tool construction / registration
time means a malformed tool fails immediately during boot rather than at the
first model call.

Rules enforced (provider-portable subset):

1. ``type`` must be a single string, not a list. Multi-type arrays
   (``"type": ["string", "object"]``) are not portable across providers.

2. When ``type == "array"``, an ``items`` field MUST be present (and itself
   a valid schema).

3. ``properties`` must be a dict; each property must itself be a valid schema.

4. ``required`` (when present) must be a list of strings, each referencing
   a key in ``properties``.

The validator is intentionally lenient about ``additionalProperties``,
``default``, ``description``, etc. — those are not portability blockers.

Usage:
    from jvagent.tooling.tool_schema_validator import validate_parameters_schema
    issues = validate_parameters_schema(my_schema)
    if issues:
        raise ValueError(...)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


_PRIMITIVE_TYPES = {"string", "number", "integer", "boolean", "null", "object", "array"}


def _walk(
    schema: Any, path: str, issues: List[Tuple[str, str]], depth: int = 0
) -> None:
    """Walk ``schema`` and append (path, message) issues found."""
    if depth > 32:
        return  # paranoia guard
    if not isinstance(schema, dict):
        return

    t = schema.get("type")
    if isinstance(t, list):
        issues.append(
            (path, f"'type' is a list {t!r}; use a single string for portability")
        )
    elif isinstance(t, str) and t not in _PRIMITIVE_TYPES:
        issues.append(
            (path, f"'type' = {t!r} is not a recognised JSON Schema primitive")
        )

    if t == "array":
        if "items" not in schema:
            issues.append(
                (
                    path,
                    "'type': 'array' requires 'items' (strict providers like "
                    "OpenAI gpt-4.1 reject array schemas without items)",
                )
            )
        else:
            _walk(schema["items"], f"{path}.items", issues, depth + 1)

    props = schema.get("properties")
    if props is not None:
        if not isinstance(props, dict):
            issues.append((path, "'properties' must be a dict"))
        else:
            for k, sub in props.items():
                _walk(sub, f"{path}.properties.{k}", issues, depth + 1)

    req = schema.get("required")
    if req is not None:
        if not isinstance(req, list) or not all(isinstance(x, str) for x in req):
            issues.append((path, "'required' must be a list of strings"))
        elif isinstance(props, dict):
            unknown = [k for k in req if k not in props]
            if unknown:
                issues.append(
                    (path, f"'required' references unknown properties: {unknown!r}")
                )


def validate_parameters_schema(
    schema: Dict[str, Any],
) -> List[Tuple[str, str]]:
    """Return a list of (path, message) issues; empty if the schema is clean.

    Path is a JSON-Pointer-like dotted string ($, $.properties.foo, etc.)
    so failures are easy to locate.
    """
    issues: List[Tuple[str, str]] = []
    _walk(schema, "$", issues)
    return issues


def assert_parameters_schema_clean(tool_name: str, schema: Dict[str, Any]) -> None:
    """Raise ``ValueError`` if the schema has portability issues.

    Includes the ``tool_name`` in the error message so the offending tool
    is obvious in tracebacks.
    """
    issues = validate_parameters_schema(schema)
    if not issues:
        return
    detail = "; ".join(f"{p}: {m}" for p, m in issues)
    raise ValueError(f"Tool {tool_name!r} has invalid parameters_schema: {detail}")
