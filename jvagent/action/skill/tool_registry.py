"""Unified runtime registry for thinking-action tools."""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass(frozen=True)
class ToolHandle:
    """Metadata for one exposed runtime tool."""

    name: str
    fq_name: str
    source: str
    schema: Dict[str, Any]
    dispatch: Callable[..., Any]


class ToolRegistry:
    """Registry with source provenance and collision-safe aliases."""

    def __init__(self) -> None:
        self._by_name: Dict[str, ToolHandle] = {}

    def register(
        self,
        *,
        name: str,
        source: str,
        schema: Dict[str, Any],
        dispatch: Callable[..., Any],
        fq_name: Optional[str] = None,
        prefix: Optional[str] = None,
    ) -> ToolHandle:
        if not name:
            raise ValueError("Tool name is required")
        preferred_name = name
        if preferred_name in self._by_name:
            if not prefix:
                raise ValueError(f"Tool '{name}' is already registered")
            preferred_name = f"{prefix}__{name}"
            if preferred_name in self._by_name:
                raise ValueError(
                    f"Tool '{name}' collides even after namespacing ({preferred_name})"
                )
        handle = ToolHandle(
            name=preferred_name,
            fq_name=fq_name or preferred_name,
            source=source,
            schema=schema,
            dispatch=dispatch,
        )
        self._by_name[preferred_name] = handle
        return handle

    def remove(self, name: str) -> None:
        self._by_name.pop(name, None)

    def get(self, name: str) -> Optional[ToolHandle]:
        return self._by_name.get(name)

    def names(self) -> List[str]:
        return list(self._by_name.keys())
