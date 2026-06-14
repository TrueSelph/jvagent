import logging
from typing import Dict, List, Optional

from jvagent.tooling.tool import Tool

logger = logging.getLogger(__name__)

# Reserved prefixes the orchestrator assigns at registration time. Action /
# skill / MCP authors must not ship tool names that already start with
# these — doing so leads to confusing ``action__action__foo`` double
# prefixes when collisions hit, or silent registration as the wrong
# source. AUDIT-interact HIGH-11.
RESERVED_TOOL_PREFIXES = ("action__", "harness__", "skill__", "mcp__")


class ToolRegistry:
    """Collision-safe registry for ``Tool`` instances with namespace prefixing.

    When a tool name is already registered, an optional *prefix* is used to
    create a namespaced name (``prefix__name``).  This avoids collisions
    between tools from different sources (actions, skills, MCP servers).

    Tool names that start with one of the framework-reserved prefixes
    (``action__``, ``harness__``, ``skill__``, ``mcp__``) are rejected at
    register time so the author's name doesn't collide with the
    orchestrator's own prefixing strategy.

    Usage::

        registry = ToolRegistry()
        registry.register(tool, prefix="pageindex_search")
        registry.register(another_tool, prefix="web_search")
        all_tools = registry.list()
    """

    def __init__(self) -> None:
        self._by_name: Dict[str, Tool] = {}
        self._sources: Dict[str, str] = {}

    def register(self, tool: Tool, *, prefix: Optional[str] = None) -> str:
        if not tool.name:
            raise ValueError("Tool name is required")

        # AUDIT-interact HIGH-11: reject tool names that smuggle a
        # framework-reserved prefix. Caller must drop the prefix and let
        # ``register(..., prefix=...)`` apply it instead.
        for reserved in RESERVED_TOOL_PREFIXES:
            if tool.name.startswith(reserved):
                raise ValueError(
                    f"Tool '{tool.name}' starts with reserved prefix "
                    f"'{reserved}' — let the registry apply prefixing via "
                    f"the ``prefix=`` kwarg instead."
                )

        name = tool.name
        if name in self._by_name:
            if not prefix:
                raise ValueError(
                    f"Tool '{name}' is already registered and no prefix was supplied"
                )
            name = f"{prefix}__{tool.name}"
            if name in self._by_name:
                raise ValueError(
                    f"Tool '{tool.name}' collides even after prefixing ({name})"
                )

        self._by_name[name] = tool
        self._sources[name] = prefix or "global"
        logger.debug(
            "ToolRegistry: registered '%s' (source: %s)", name, prefix or "global"
        )
        return name

    def remove(self, name: str) -> bool:
        if name in self._by_name:
            del self._by_name[name]
            self._sources.pop(name, None)
            return True
        return False

    def remove_by_prefix(self, prefix: str) -> int:
        prefix_key = f"{prefix}__"
        removed = 0
        for name in list(self._by_name):
            if name.startswith(prefix_key):
                del self._by_name[name]
                self._sources.pop(name, None)
                removed += 1
        return removed

    def get(self, name: str) -> Optional[Tool]:
        return self._by_name.get(name)

    def list(self) -> List[Tool]:
        return list(self._by_name.values())

    def names(self) -> List[str]:
        return list(self._by_name.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._by_name

    def __len__(self) -> int:
        return len(self._by_name)
