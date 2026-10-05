"""Research citations must resolve to bounded Action-result references."""

import asyncio
import json

import pytest

pytest.importorskip("pydantic_ai")

from jvagent.action.orchestrator.pilot.contracts import (
    ConversationalReply,
    PilotCaller,
    PilotRunContext,
    ResearchBrief,
)
from jvagent.action.orchestrator.pilot.runtime import (
    PilotEvidenceCollector,
    PilotModelAdapterError,
)


def _context() -> PilotRunContext:
    return PilotRunContext(
        caller=PilotCaller(agent_id="a", user_id="u", session_id="s"),
        task_id="task-1",
        run_id="run-1",
        skill_id="research",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
    )


def test_research_output_must_cite_a_tool_result_url() -> None:
    collector = PilotEvidenceCollector()
    content = json.dumps(
        [
            {
                "title": "Evidence",
                "link": "https://example.test/article?x=1",
                "snippet": "A retrieved claim.",
            }
        ]
    )
    asyncio.run(collector.observe(_context(), "web_search__search", {}, content))
    valid = ResearchBrief(
        question="q",
        findings=("A retrieved claim.",),
        source_ids=("https://example.test/article?x=1",),
        brief="A retrieved claim. [source]",
    )
    collector.validate(valid)

    forged = valid.model_copy(update={"source_ids": ("https://invented.test",)})
    with pytest.raises(PilotModelAdapterError, match="not returned by an Action"):
        collector.validate(forged)


def test_conversational_reply_is_validated_without_fabricated_evidence():
    collector = PilotEvidenceCollector()

    collector.validate(ConversationalReply(answer="I can help with research."))
    assert collector.snapshot() == ()


def test_evidence_references_are_bounded_and_url_fragments_are_removed() -> None:
    collector = PilotEvidenceCollector()
    asyncio.run(
        collector.observe(
            _context(),
            "web_fetch__fetch",
            {"url": "https://example.test/article#section"},
            "Page content " * 300,
        )
    )
    references = collector.snapshot()
    assert len(references) == 1
    assert references[0].source_id == "https://example.test/article"
    assert len(references[0].excerpt) <= PilotEvidenceCollector.max_excerpt_chars
