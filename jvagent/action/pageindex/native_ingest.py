"""Accounted local PageIndex Markdown ingestion."""

from collections.abc import Awaitable, Callable
from typing import Any

from jvagent.harness.completion_tool import CompleteText

from .native_search import Complete, admitted_operation_model, run_native_operation

CHANNEL = "pageindex_markdown_ingestion"


async def run_native_markdown_ingest(
    action: Any,
    *,
    agent_id: str,
    actor: str,
    doc_name: str,
    requested_model: str | None,
    assimilate: Callable[[CompleteText], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Share SDK accounting across summaries and retain the published root ID."""
    admitted_operation_model(action.model, requested_model)

    async def operation(complete: Complete) -> dict[str, Any]:
        async def text(prompt: str) -> str:
            if len(prompt.encode("utf-8")) > 100_000:
                raise ValueError(
                    "PageIndex summary input exceeds the native size limit"
                )
            return (await complete(prompt)).output

        return await assimilate(text)

    def publication(result: dict[str, Any]) -> dict[str, Any]:
        return {
            key: result[key]
            for key in ("doc_name", "_root_id", "doc_description")
            if key in result
        }

    return await run_native_operation(
        action,
        agent_id=agent_id,
        actor=actor,
        query=doc_name,
        channel=CHANNEL,
        operation=operation,
        result_receipt=publication,
    )
