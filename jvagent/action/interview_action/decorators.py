"""Optional decorator for InterviewAction custom tools.

Prefer declaring tools in ``contract.yaml`` (``tools:`` with ``name``,
``description``, ``parameters``, ``function``) and implementing plain
``async def`` functions in ``scripts/custom_tools.py``. Contract-declared
tools do not need ``@interview_tool``.

The decorator remains for legacy auto-discovery when a function is not
listed in ``contract.tools``.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def interview_tool(
    name: str = "",
    description: str = "",
    parameters_schema: Optional[Dict[str, Any]] = None,
) -> Callable[[F], F]:
    """Decorator to mark a function as an interview custom tool.

    Functions decorated with ``@interview_tool`` are discovered by
    InterviewAction during contract loading and registered as
    tools prefixed with ``{contract.name}__`` (e.g. ``pre_alert_interview__my_tool``).

    Parameters
    ----------
    name : str
        Tool name.  Falls back to ``func.__name__`` when empty.
    description : str
        Tool description for the model.  Falls back to the function docstring.
    parameters_schema : dict | None
        JSON-schema-style parameter description.  Defaults to an empty object
        schema ``{"type": "object", "properties": {}}``.
    """

    def decorator(func: F) -> F:
        setattr(func, "_interview_tool", True)
        setattr(func, "_tool_name", name or func.__name__)
        setattr(func, "_tool_description", description or (func.__doc__ or ""))
        setattr(
            func,
            "_tool_parameters_schema",
            parameters_schema
            or {
                "type": "object",
                "properties": {},
            },
        )
        return func

    return decorator
