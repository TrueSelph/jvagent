"""Focused tests for proposal workflow refactor tools."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from jvagent.skills.pdf_generation._document_args import parse_document_pdf_arguments


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REPO_ROOT = Path(__file__).resolve().parents[3]
PROPOSAL_ROOT = (
    REPO_ROOT / "examples" / "jvagent_app" / "agents" / "jvagent" / "proposal_agent"
)


class _Resolver:
    async def resolve(self, _name: str):
        return None


class _Visitor:
    def __init__(self):
        self.action_resolver = _Resolver()
        self._skill_state = {}
        self._current_action = SimpleNamespace(
            specimens_path=None,
            output_dir="output",
            brand_primary_color="#1a237e",
            brand_accent_color="#0d47a1",
        )


@pytest.mark.asyncio
async def test_draft_tool_returns_structured_state():
    mod = _load_module(
        PROPOSAL_ROOT / "skills" / "proposal_draft" / "draft_proposal.py",
        "draft_proposal_test_mod",
    )
    visitor = _Visitor()
    result = await mod.execute(
        {
            "client_name": "IPED",
            "project_title": "Digital Lending Platform",
            "company_name": "V75",
            "transcript_analysis": {
                "needs": ["online applications", "workflow visibility"],
                "scope": "Loan lifecycle platform with mobile touchpoints",
                "timeline": "20 weeks",
                "budget": "TBD",
                "uncertainties": ["final branch rollout model"],
            },
        },
        visitor=visitor,
    )
    assert "proposal_state" in result
    assert result["proposal_state"]["client"] == "IPED"
    assert "[PRICING PLACEHOLDER]" in result["rendered_markdown"]
    assert "quality" in result


@pytest.mark.asyncio
async def test_specimen_retrieval_returns_selected_content(tmp_path: Path):
    mod = _load_module(
        PROPOSAL_ROOT / "skills" / "proposal_draft" / "specimen_retrieval.py",
        "specimen_retrieval_test_mod",
    )
    corpus = tmp_path / "specimens"
    corpus.mkdir()
    (corpus / "template.md").write_text("# Template", encoding="utf-8")
    (corpus / "guide.md").write_text("# Guide", encoding="utf-8")
    (corpus / "README.md").write_text("# Index", encoding="utf-8")
    (corpus / "retail_mobile.md").write_text(
        "# Retail Mobile\nclient self-service flow",
        encoding="utf-8",
    )
    (corpus / "ai_automation.md").write_text(
        "# AI Proposal\nautomation and orchestration",
        encoding="utf-8",
    )
    visitor = _Visitor()
    visitor._current_action.specimens_path = str(corpus)
    result = await mod.execute(
        {"client_tags": ["retail", "mobile"], "max_specimens": 1},
        visitor=visitor,
    )
    assert result["selected_count"] == 1
    assert len(result["selected_specimen_contents"]) == 1
    assert "Retail Mobile" in result["selected_specimen_contents"][0]


@pytest.mark.asyncio
async def test_revision_tracker_persists_in_skill_state():
    mod = _load_module(
        PROPOSAL_ROOT / "skills" / "authoring" / "revision_tracker.py",
        "revision_tracker_test_mod",
    )
    visitor = _Visitor()
    add_res = await mod.execute(
        {
            "action": "add",
            "markers": [
                {"id": "m1", "location": "Timeline", "text": "Confirm deadline"}
            ],
        },
        visitor=visitor,
    )
    assert add_res["added"] == 1
    list_res = await mod.execute({"action": "list"}, visitor=visitor)
    assert list_res["pending"] == 1
    assert visitor._skill_state["revision_markers"][0]["id"] == "m1"


@pytest.mark.asyncio
async def test_feedback_handler_schema_and_apply_mode():
    mod = _load_module(
        PROPOSAL_ROOT / "skills" / "authoring" / "feedback_handler.py",
        "feedback_handler_test_mod",
    )
    definition = mod.get_tool_definition()
    assert "properties" in definition["parameters"]
    visitor = _Visitor()
    result = await mod.execute(
        {
            "mode": "apply",
            "revision_request": "Make the summary more concise",
            "current_content": "## I. Executive Summary\nLong text",
        },
        visitor=visitor,
    )
    assert result["status"] == "ready"
    assert "current_content_hash" in result


@pytest.mark.asyncio
async def test_pricing_builder_replaces_placeholder():
    mod = _load_module(
        PROPOSAL_ROOT / "skills" / "pricing" / "build_investment_section.py",
        "build_investment_section_test_mod",
    )
    result = await mod.execute(
        {
            "assessment": {
                "line_items": [
                    {"activity": "Build", "hours": 40, "rate": 150, "total": 6000}
                ],
                "total_engineering_hours": 40,
                "blended_rate": 150,
                "total": 6000,
                "valid_until": "2026-05-01",
                "assumptions": ["Scope stable"],
            },
            "proposal_markdown": "## X. Investment\n\n[PRICING PLACEHOLDER]\n",
        },
        visitor=_Visitor(),
    )
    assert result["placeholder_replaced"] is True
    assert "Total investment" in result["updated_markdown"]


def test_parse_document_pdf_arguments_branding_fields():
    params = parse_document_pdf_arguments(
        {
            "title": "Proposal",
            "content": "Body",
            "brand_primary_color": "#123456",
            "brand_accent_color": "#654321",
            "brand_logo_path": "/tmp/logo.png",
            "company_letterhead": "V75 Incorporated",
        }
    )
    assert params.brand_primary_color == "#123456"
    assert params.brand_accent_color == "#654321"
    assert params.brand_logo_path == "/tmp/logo.png"
    assert params.company_letterhead == "V75 Incorporated"
