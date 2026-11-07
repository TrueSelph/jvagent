"""Reuse PageIndex's direct retrieval service without its LLM transport stack."""

from dataclasses import replace
from functools import partial
from typing import Any, Literal

from jvspatial.core.annotations import attribute

from jvagent.action.pageindex.pageindex_action import PageIndexAction
from jvagent.harness.completion_tool import CompleteText, NativeCompletionTool
from jvagent.tooling.tool import Tool


class PageIndexReadAction(PageIndexAction):
    """Publish graph reads, with opt-in tree ranking through native execution."""

    strategy: Literal["direct", "tree_search"] = attribute(default="direct")

    async def get_tools(self) -> list[Any]:
        """Expose the inherited reviewed direct search tool, without write tools."""
        return [
            tool
            for tool in await super().get_tools()
            if tool.name == "pageindex__search"
        ]

    async def get_native_tools(self) -> list[Tool]:
        """Expose model-assisted ranking only through the native host callback."""
        tools = await self.get_tools()
        strategy = (self.config or {}).get("strategy") or self.strategy
        source = tools[0]
        schema = {**source.parameters_schema, "additionalProperties": False}
        if strategy == "direct":
            return [
                replace(
                    source,
                    parameters_schema=schema,
                    execute=partial(self._search_tool, strategy="direct"),
                )
            ]
        if strategy != "tree_search":
            return tools
        return [
            NativeCompletionTool(
                name=source.name,
                description=source.description,
                parameters_schema=schema,
                access_label=source.access_label,
                effect_class="read",
                execute=self._native_tree_search,
            )
        ]

    async def _native_tree_search(
        self,
        query: str,
        *,
        complete: CompleteText,
        doc_name: str | None = None,
        limit: int | None = None,
    ) -> str:
        return await self._search_tool(
            query,
            doc_name=doc_name,
            limit=limit,
            complete=complete,
            strategy="tree_search",
        )
