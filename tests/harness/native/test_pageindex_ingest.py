"""PageIndex Markdown summaries use bounded native execution before publication."""

from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from jvagent.action.native_harness import NativeHarnessAction
from jvagent.action.pageindex import documents, md_tree_enriched
from jvagent.action.pageindex.native_ingest import CHANNEL, run_native_markdown_ingest
from jvagent.harness import native_model
from jvagent.harness.completion_tool import CompleteText
from jvagent.memory.interaction import Interaction


@pytest.mark.asyncio
@pytest.mark.parametrize("max_requests", [1, 2])
async def test_native_markdown_publishes_after_summary_and_description(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, max_requests: int
) -> None:
    action = await NativeHarnessAction.create(max_requests=max_requests)
    doc = tmp_path / "notes.md"
    doc.write_text("# Field notes\n\n" + "Check in at the north gate. " * 18)
    publish = AsyncMock(return_value="document-root")
    monkeypatch.setattr(
        documents, "_ensure_pageindex_work_dir", AsyncMock(return_value=str(tmp_path))
    )
    legacy_tokens = Mock(side_effect=AssertionError("Legacy tokenizer"))
    monkeypatch.setattr(documents, "tree_to_graph", publish)
    monkeypatch.setattr(md_tree_enriched, "count_tokens", legacy_tokens)
    calls = []

    def respond(messages: Any, info: Any) -> ModelResponse:
        calls.append(messages[-1].parts[-1].content)
        assert not info.function_tools
        return ModelResponse(
            parts=[TextPart("Gate summary" if len(calls) == 1 else "Gate description")],
            usage=RequestUsage(input_tokens=10, output_tokens=5, cost=Decimal("0.001")),
        )

    monkeypatch.setattr(
        native_model,
        "qualified_model",
        lambda _: FunctionModel(respond, model_name="gpt-4.1"),
    )

    async def assimilate(complete: CompleteText) -> dict[str, Any]:
        return await documents.assimilate_document(
            doc,
            doc_name="Fixture",
            collection_name="agent",
            if_add_node_summary=True,
            if_add_doc_description=True,
            complete=complete,
        )

    async def run() -> dict[str, Any]:
        return await run_native_markdown_ingest(
            action,
            agent_id="agent",
            actor="operator",
            doc_name="Fixture",
            requested_model=None,
            assimilate=assimilate,
        )

    if max_requests == 1:
        with pytest.raises(UsageLimitExceeded):
            await run()
        publish.assert_not_called()
    else:
        result = await run()
        assert result["_root_id"] == "document-root"
        assert result["structure"][0]["summary"] == "Gate summary"
        assert result["doc_description"] == "Gate description"
        publish.assert_awaited_once()

    assert len(calls) == max_requests
    legacy_tokens.assert_not_called()
    rows = await Interaction.find({"channel": CHANNEL})
    assert len(rows) == 1 and rows[0].closed
    assert rows[0].usage["total_tokens"] == 15 * max_requests
    if max_requests == 2:
        assert '"_root_id": "document-root"' in rows[0].response
        assert "structure" not in rows[0].response
