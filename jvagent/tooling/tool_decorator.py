"""``@tool`` decorator and collector for Action tool publishing.

Instead of hand-building :class:`~jvagent.tooling.tool.Tool` instances inside an
``async get_tools()`` override, an Action can simply decorate a method::

    from typing import Annotated
    from jvagent.tooling.tool_decorator import tool

    class WebFetchAction(Action):
        @tool
        async def fetch(self, url: Annotated[str, "The http(s) URL to fetch."]) -> str:
            "Fetch a public web page and return clean markdown."
            ...

The base ``Action.get_tools`` calls :func:`collect_tools`, which discovers every
decorated method and builds a ``Tool`` for it:

- **name**  — ``@tool(name=...)`` if given, else ``{action_name}__{method_name}``
  where ``action_name`` is the action's loader package name (``metadata["name"]``)
  with a deterministic fallback derived from the class name (``WebFetchAction`` →
  ``web_fetch``).
- **description** — ``@tool(description=...)`` if given, else the first paragraph
  of the method docstring.
- **parameters_schema** — derived from the signature by
  :func:`jvagent.tooling.signature_schema.build_parameters_schema`.
- **execute** — the bound method.

The decorator only *marks* the function; it does not wrap or replace it, so the
method stays normally callable (and other code paths/tests can call it directly).
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from jvagent.tooling.signature_schema import build_parameters_schema
from jvagent.tooling.tool import Tool

__all__ = ["tool", "collect_tools", "ToolSpec", "TOOL_MARKER"]

#: Attribute name under which the :class:`ToolSpec` is stashed on a decorated fn.
TOOL_MARKER = "_jvagent_tool_spec"

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


@dataclass
class ToolSpec:
    """Author-supplied overrides attached to a ``@tool``-decorated function.

    All fields are optional. ``access_label``/``terminal``/``binds_visitor`` are
    carried through onto the produced :class:`Tool` for the orchestrator's wrap
    step to consume; they have no effect on plain capability tools.
    """

    name: Optional[str] = None
    description: Optional[str] = None
    access_label: Optional[str] = None
    terminal: Optional[bool] = None
    binds_visitor: Optional[bool] = None


def tool(
    _fn: Optional[Callable[..., Any]] = None,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    access_label: Optional[str] = None,
    terminal: Optional[bool] = None,
    binds_visitor: Optional[bool] = None,
) -> Callable[..., Any]:
    """Mark a method as an agent tool. Usable as ``@tool`` or ``@tool(name=...)``."""

    spec = ToolSpec(
        name=name,
        description=description,
        access_label=access_label,
        terminal=terminal,
        binds_visitor=binds_visitor,
    )

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        setattr(fn, TOOL_MARKER, spec)
        return fn

    if _fn is not None:  # bare @tool
        return decorate(_fn)
    return decorate  # @tool(...)


def _camel_to_snake(name: str) -> str:
    return _CAMEL_BOUNDARY.sub("_", name).lower()


_UNSET = object()


def _action_name(instance: Any) -> str:
    """Resolve the tool-name prefix for *instance*.

    Resolution order:

    1. A class-level ``tool_namespace`` attribute, when declared. This is the
       explicit, deterministic control: set it when the desired prefix differs
       from the package name (e.g. ``GoogleGmailAction`` → ``"gmail"``) or set
       it to ``""`` to publish bare, unprefixed tool names. ``@tool(name=...)``
       still overrides this per tool.
    2. The loader package name (``metadata["name"]``).
    3. The action ``label``.
    4. A deterministic class-name derivation (``WebFetchAction`` → ``web_fetch``)
       so names are stable even without loader metadata (e.g. in unit tests).
    """
    ns = getattr(instance, "tool_namespace", _UNSET)
    if ns is not _UNSET:
        return str(ns or "")
    meta = getattr(instance, "metadata", None) or {}
    pkg = meta.get("name")
    if pkg:
        return str(pkg)
    label = getattr(instance, "label", None)
    if label:
        return str(label)
    cls = type(instance).__name__
    cls = cls[:-6] if cls.endswith("Action") else cls
    return _camel_to_snake(cls)


def _description(spec: ToolSpec, fn: Callable[..., Any]) -> str:
    if spec.description:
        return spec.description
    doc = inspect.getdoc(fn) or ""
    # First blank-line-delimited paragraph, whitespace-normalised.
    para = doc.split("\n\n", 1)[0].strip()
    return re.sub(r"\s+", " ", para)


# Per-class discovery cache. Descriptions and parameter schemas are static
# per class (signature/docstring introspection is the expensive part and the
# orchestrator calls get_tools() on every enabled action each turn); only the
# bound ``execute`` and the (possibly instance-derived) name prefix vary per
# instance, so those are resolved per call in ``collect_tools``.
_CLASS_TOOL_CACHE: Dict[type, List[Tuple[str, "ToolSpec", str, Dict[str, Any]]]] = {}


def _discover_class_tools(
    instance: Any,
) -> List[Tuple[str, "ToolSpec", str, Dict[str, Any]]]:
    cls = type(instance)
    cached = _CLASS_TOOL_CACHE.get(cls)
    if cached is not None:
        return cached
    entries: List[Tuple[str, "ToolSpec", str, Dict[str, Any]]] = []
    # Walk the class MRO (not inspect.getmembers on the instance) so we never
    # trigger arbitrary property getters / descriptors during discovery. The
    # first definition wins, so a subclass override shadows a base method.
    seen: set = set()
    for klass in cls.__mro__:
        for attr_name, raw in vars(klass).items():
            if attr_name in seen:
                continue
            spec: Optional[ToolSpec] = getattr(raw, TOOL_MARKER, None)
            if spec is None:
                continue
            seen.add(attr_name)
            # Bound member: its signature excludes ``self``, which is what the
            # schema must describe.
            member = getattr(instance, attr_name)
            entries.append(
                (
                    attr_name,
                    spec,
                    _description(spec, member),
                    build_parameters_schema(member),
                )
            )
    _CLASS_TOOL_CACHE[cls] = entries
    return entries


def collect_tools(instance: Any) -> List[Tool]:
    """Build a ``Tool`` for every ``@tool``-decorated method on *instance*.

    Returns ``[]`` when nothing is decorated, so the base ``get_tools`` default
    is a no-op for the many actions that publish no tools.
    """
    tools: List[Tool] = []
    prefix = _action_name(instance)

    for attr_name, spec, description, schema in _discover_class_tools(instance):
        member = getattr(instance, attr_name)  # bound method
        func_name = getattr(member, "__name__", attr_name)
        tool_name = spec.name or (f"{prefix}__{func_name}" if prefix else func_name)

        built = Tool(
            name=tool_name,
            description=description,
            # Shallow copy so a consumer mutating one instance's schema can't
            # bleed into the class-level cache.
            parameters_schema=dict(schema),
            execute=member,
        )
        # Carry orchestrator wrap-step hints when supplied (no-ops otherwise).
        if spec.access_label is not None:
            built.access_label = spec.access_label
        if spec.terminal is not None:
            built.terminal = spec.terminal
        if spec.binds_visitor is not None:
            built.binds_visitor = spec.binds_visitor
        tools.append(built)

    tools.sort(key=lambda t: t.name)
    return tools
