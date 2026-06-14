"""Host skill provider registry and merge into discover_skill_docs."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import jvagent.core.app_context as app_context
import jvagent.scaffold.skill_resolve as skill_resolve
from jvagent.action.orchestrator.skill_providers import (
    clear_host_skill_providers,
    register_host_skill_provider,
)
from jvagent.action.orchestrator.skills import SkillDoc, discover_skill_docs

pytestmark = pytest.mark.asyncio

_AGENT = SimpleNamespace(namespace="jvagent", name="orchestrator_agent")


def _stub_resolver(monkeypatch):
    def _resolve(app_root, namespace, agent_name, *, include_builtin=True):
        return {
            "integral_identity": {
                "name": "integral_identity",
                "description": "base identity",
                "content": "base SOP",
                "allowed_tools": [],
                "source": "app",
                "metadata": {},
            }
        }

    monkeypatch.setattr(app_context, "get_app_root", lambda: "/fake/app")
    monkeypatch.setattr(skill_resolve, "resolve_merged_skill_bundles", _resolve)


@pytest.fixture(autouse=True)
def _clear_providers():
    clear_host_skill_providers()
    yield
    clear_host_skill_providers()


def _names(docs):
    return sorted(d.name for d in docs)


async def test_host_provider_merges_overlay_skills(monkeypatch):
    _stub_resolver(monkeypatch)

    def _provider(agent):
        return [
            SkillDoc(
                name="content_factory__carousel_drafter",
                description="Draft carousels",
                body="workspace overlay SOP",
                source="workspace",
            )
        ]

    register_host_skill_provider(_provider)
    docs = discover_skill_docs(_AGENT, skills_source="app", selector="-all")
    assert _names(docs) == ["content_factory__carousel_drafter", "integral_identity"]


async def test_filesystem_wins_on_name_collision(monkeypatch):
    _stub_resolver(monkeypatch)

    def _provider(agent):
        return [
            SkillDoc(
                name="integral_identity",
                description="host shadow attempt",
                body="should be dropped",
                source="workspace",
            ),
            SkillDoc(
                name="other_bundle__skill",
                description="unique overlay",
                body="kept",
                source="workspace",
            ),
        ]

    register_host_skill_provider(_provider)
    docs = discover_skill_docs(_AGENT, skills_source="app", selector="-all")
    by_name = {d.name: d for d in docs}
    assert by_name["integral_identity"].body == "base SOP"
    assert "other_bundle__skill" in by_name


async def test_multiple_providers_merge(monkeypatch):
    _stub_resolver(monkeypatch)

    register_host_skill_provider(
        lambda _a: [
            SkillDoc(name="a__one", description="", body="", source="workspace")
        ]
    )
    register_host_skill_provider(
        lambda _a: [
            SkillDoc(name="b__two", description="", body="", source="workspace")
        ]
    )
    docs = discover_skill_docs(_AGENT, skills_source="app", selector="-all")
    assert "a__one" in _names(docs)
    assert "b__two" in _names(docs)
