"""Reuse PageIndex's direct retrieval service without its LLM transport stack."""

from typing import Any, Literal

from jvspatial.core.annotations import attribute

from jvagent.action.pageindex.pageindex_action import PageIndexAction


class PageIndexReadAction(PageIndexAction):
    """Publish direct search only; indexing and model-based retrieval stay separate."""

    strategy: Literal["direct"] = attribute(default="direct")

    async def get_tools(self) -> list[Any]:
        """Expose the inherited reviewed direct search tool, without write tools."""
        return [
            tool
            for tool in await super().get_tools()
            if tool.name == "pageindex__search"
        ]
