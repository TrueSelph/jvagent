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


async def settle_interrupted_publication(
    interaction: Any,
    *,
    agent_id: str,
    model: str,
    publication: dict[str, Any],
) -> None:
    """Close a proved publication using completed SDK requests from a dead worker."""
    import json

    from jvspatial import flush_deferred_entities

    from jvagent.core.execution_owner import require_dead_native_owner
    from jvagent.harness.accounting import project_native_usage
    from jvagent.harness.step_store import InteractionStepStore

    identity = interaction.native_run.get("run_id")
    if (
        not identity
        or interaction.closed
        or interaction.native_run.get("status") != "running"
    ):
        raise ValueError("Publication has no interrupted operation to settle")

    store = InteractionStepStore(interaction, agent_id=agent_id)
    runs = await store.list_runs()
    roots = [run for run in runs if run.run_id == identity and not run.parent_run_id]
    if len(roots) != 1:
        raise ValueError("Publication operation has no unique SDK root")
    root = roots[0]
    require_dead_native_owner(root.metadata)
    scope = {
        **store.registration_metadata(),
        "jv_operation": interaction.channel,
    }
    worker_keys = ("jv_worker_host", "jv_worker_epoch", "jv_worker_pid")
    for run in runs:
        if (
            run.conversation_id != interaction.session_id
            or (run.run_id != identity and run.parent_run_id != identity)
            or any(run.metadata.get(key) != value for key, value in scope.items())
            or any(
                run.metadata.get(key) != root.metadata.get(key) for key in worker_keys
            )
        ):
            raise ValueError("Publication contains an unbound SDK run")
        events = await store.list_events(run_id=run.run_id)
        if [event.kind for event in events] != [
            "run_started",
            "model_request_started",
            "model_request_completed",
            "run_completed",
        ]:
            raise ValueError("Publication has an unsettled SDK request")

    projected = await project_native_usage(interaction, run_id=identity, model=model)
    usage = projected.usage
    if (
        usage.get("unknown_cost_call_count")
        or usage.get("unmeasured_model_attempt_count")
        or usage.get("model_attempt_count") != len(runs)
        or usage.get("model_call_count") != len(runs)
    ):
        raise ValueError("Publication usage cannot be settled from the SDK journal")

    interaction.observability_metrics = projected.observability_metrics
    interaction.usage = usage
    interaction.native_run = {
        **interaction.native_run,
        "status": "completed",
        "usage_complete": True,
        "publication_recovered": True,
    }
    interaction.response = json.dumps(publication, ensure_ascii=False)
    await interaction.close_interaction()
    await interaction.save()
    await flush_deferred_entities(interaction, strict=True)
