"""Accounted local PageIndex ingestion."""

from collections.abc import Awaitable, Callable
from typing import Any

from jvagent.harness.completion_tool import CompleteText

from .native_search import Complete, admitted_operation_model, run_native_operation

CHANNEL = "pageindex_markdown_ingestion"
PDF_CHANNEL = "pageindex_pdf_ingestion"


def _publication(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: result[key]
        for key in ("doc_name", "_root_id", "doc_description")
        if key in result
    }


async def run_native_markdown_ingest(
    action: Any,
    *,
    agent_id: str,
    actor: str,
    doc_name: str,
    requested_model: str | None,
    on_started: Callable[[str], None] | None = None,
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

    return await run_native_operation(
        action,
        agent_id=agent_id,
        actor=actor,
        query=doc_name,
        channel=CHANNEL,
        operation=operation,
        result_receipt=_publication,
        on_started=on_started,
    )


async def run_native_pdf_ingest(
    action: Any,
    *,
    agent_id: str,
    actor: str,
    doc_name: str,
    requested_model: str | None,
    on_started: Callable[[str], None] | None = None,
    assimilate: Callable[[Complete], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Use the same host limits and receipts for the classic PDF parser."""
    admitted_operation_model(action.model, requested_model)
    return await run_native_operation(
        action,
        agent_id=agent_id,
        actor=actor,
        query=doc_name,
        channel=PDF_CHANNEL,
        operation=assimilate,
        result_receipt=_publication,
        on_started=on_started,
    )
