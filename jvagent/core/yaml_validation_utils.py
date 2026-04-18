"""Shared helpers for app.yaml and agent.yaml validators."""

from __future__ import annotations

from typing import Any, Callable, Iterable, List, Set


def warn_once(
    *,
    warnings: Iterable[Any],
    source: str,
    seen_keys: Set[str],
    emit: Callable[[str], None],
) -> None:
    """Emit warning objects once per (source,path,message,hint)."""
    for warning in warnings:
        path = getattr(warning, "path", "")
        message = getattr(warning, "message", "")
        hint = getattr(warning, "hint", "")
        key = f"{source}|{path}|{message}|{hint}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        suffix = f" Hint: {hint}" if hint else ""
        emit(f"[{path}] {message}.{suffix}")


def expect_type(
    *,
    warnings: List[Any],
    path: str,
    value: Any,
    types: tuple[type, ...],
    factory: Callable[[str, str, str], Any],
    hint: str = "",
) -> None:
    """Append a warning when value is present but has unexpected type."""
    if value is None:
        return
    if not isinstance(value, types):
        expected = "/".join(t.__name__ for t in types)
        warnings.append(
            factory(path, f"Expected {expected}, got {type(value).__name__}", hint)
        )


def warn_unknown_keys(
    *,
    warnings: List[Any],
    base_path: str,
    payload: dict[str, Any],
    allowed_keys: set[str],
    factory: Callable[[str, str, str], Any],
) -> None:
    """Append warnings for keys that are not in allowed_keys."""
    for key in payload.keys():
        if key not in allowed_keys:
            full_path = f"{base_path}.{key}" if base_path else key
            warnings.append(factory(full_path, "Unexpected key", ""))
