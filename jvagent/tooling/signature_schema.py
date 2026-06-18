"""Derive a portable JSON Schema for a callable from its signature.

Turns a Python function's type hints into the ``parameters_schema`` shape that
:class:`jvagent.tooling.tool.Tool` expects, so tool authors no longer hand-write
JSON Schema dicts. Output is deliberately the *portable subset* enforced by
:mod:`jvagent.tooling.tool_schema_validator` (single string ``type``, ``items``
always present on arrays, no ``$defs``/``anyOf``/``title`` noise) — it is the
test oracle for this module.

Supported annotations:

- ``str`` / ``int`` / ``float`` / ``bool``  → the matching primitive
- ``list`` / ``List[T]``                     → ``array`` (with ``items``)
- ``dict`` / ``Dict[...]``                   → ``object``
- ``Literal[...]``                           → primitive + ``enum``
- ``Enum`` subclass                          → primitive + ``enum``
- ``Optional[T]`` / ``Union[T, None]``       → schema of ``T`` (nullability is
  expressed via ``required``, never a list ``type``)
- ``Annotated[T, "description", ...]``       → schema of ``T`` + ``description``

Anything else (bare params, multi-member Unions, unknown classes) degrades to an
untyped ``{}`` schema, which the validator tolerates.
"""

from __future__ import annotations

import enum
import inspect
import typing
from typing import Any, Dict, List, Tuple, Union, get_args, get_origin

__all__ = ["python_type_to_json_schema", "build_parameters_schema"]

# JSON-serialisable scalar defaults we are willing to echo into the schema.
_JSON_SCALARS = (str, int, float, bool)


def _is_optional(annotation: Any) -> Tuple[bool, Any]:
    """If *annotation* is ``Optional[X]``/``Union[..., None]`` return (True, inner).

    ``inner`` is ``X`` when exactly one non-None member remains, else the
    original Union with ``NoneType`` stripped (still a Union) so the caller can
    decide how to render it. Non-optional annotations return (False, annotation).
    """
    if get_origin(annotation) is Union:
        args = [a for a in get_args(annotation) if a is not type(None)]  # noqa: E721
        if len(args) < len(get_args(annotation)):
            if len(args) == 1:
                return True, args[0]
            return True, Union[tuple(args)]  # type: ignore[valid-type]
    return False, annotation


def _enum_schema(members: List[Any]) -> Dict[str, Any]:
    """Build a ``{type, enum}`` schema from literal/enum member values."""
    values = [m.value if isinstance(m, enum.Enum) else m for m in members]
    schema: Dict[str, Any] = {"enum": values}
    inferred = {_primitive_name(type(v)) for v in values if v is not None}
    if len(inferred) == 1:
        only = next(iter(inferred))
        if only:
            schema = {"type": only, "enum": values}
    return schema


def _primitive_name(tp: Any) -> str:
    """Map a concrete scalar type to its JSON Schema primitive name (or "")."""
    # bool is a subclass of int — check it first.
    if tp is bool:
        return "boolean"
    if tp is int:
        return "integer"
    if tp is float:
        return "number"
    if tp is str:
        return "string"
    return ""


def python_type_to_json_schema(annotation: Any) -> Dict[str, Any]:
    """Return a portable JSON Schema fragment for a single type annotation."""
    if annotation is inspect.Parameter.empty or annotation is Any or annotation is None:
        return {}

    # Annotated[T, ...] — unwrap to the underlying type (description is handled
    # by build_parameters_schema, which can see the metadata).
    if get_origin(annotation) is typing.Annotated:
        return python_type_to_json_schema(get_args(annotation)[0])

    # Optional / Union
    optional, inner = _is_optional(annotation)
    if optional:
        return python_type_to_json_schema(inner)
    if get_origin(annotation) is Union:
        return {}  # genuine multi-member union — leave untyped

    # Literal[...]
    if get_origin(annotation) is typing.Literal:
        return _enum_schema(list(get_args(annotation)))

    origin = get_origin(annotation)

    # list / List[T]
    if annotation is list or origin in (list, typing.List):
        args = get_args(annotation)
        items = python_type_to_json_schema(args[0]) if args else {}
        return {"type": "array", "items": items}

    # dict / Dict[...]
    if annotation is dict or origin in (dict, typing.Dict):
        return {"type": "object"}

    # Enum subclass
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return _enum_schema(list(annotation))

    # Scalars
    name = _primitive_name(annotation)
    if name:
        return {"type": name}

    return {}  # unknown — untyped but validator-safe


def _annotation_description(annotation: Any) -> str:
    """Pull the first ``str`` metadata out of ``Annotated[T, "desc", ...]``."""
    if get_origin(annotation) is typing.Annotated:
        for meta in get_args(annotation)[1:]:
            if isinstance(meta, str):
                return meta
    return ""


def build_parameters_schema(func: Any) -> Dict[str, Any]:
    """Build a ``parameters_schema`` object for *func* from its signature.

    ``self``/``cls`` and ``*args``/``**kwargs`` are skipped. Parameters with no
    default and no ``Optional`` wrapper are ``required``. JSON-scalar defaults
    are echoed under ``default``.
    """
    try:
        sig = inspect.signature(func)
        hints = typing.get_type_hints(func, include_extras=True)
    except Exception:
        return {"type": "object", "properties": {}}

    properties: Dict[str, Any] = {}
    required: List[str] = []

    for pname, param in sig.parameters.items():
        if pname in ("self", "cls"):
            continue
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue

        annotation = hints.get(pname, param.annotation)
        prop = python_type_to_json_schema(annotation)

        desc = _annotation_description(annotation)
        if desc:
            prop["description"] = desc

        has_default = param.default is not inspect.Parameter.empty
        optional, _ = _is_optional(annotation)

        if has_default:
            default = param.default
            # bool is a _JSON_SCALARS member; None defaults are intentionally
            # not echoed (the absence already signals optional).
            if isinstance(default, _JSON_SCALARS):
                prop["default"] = default
        elif not optional:
            required.append(pname)

        properties[pname] = prop

    schema: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema
