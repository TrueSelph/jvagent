"""Skill discovery: source selection (app | library | both, plus aliases) and
finite-list selection by name. The resolver and app-root are stubbed so the test
exercises discover_skill_docs' mapping/filtering, not the filesystem."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import jvagent.core.app_context as app_context
import jvagent.scaffold.skill_resolve as skill_resolve
from jvagent.action.skill_executive.skills import discover_skill_docs

pytestmark = pytest.mark.asyncio


def _stub_resolver(monkeypatch):
    """A library skill (research) + an app-local skill (web_lookup)."""

    def _resolve(app_root, namespace, agent_name, *, include_builtin=True):
        bundles = {
            "web_lookup": {
                "name": "web_lookup",
                "description": "app-local lookup",
                "content": "app SOP",
                "allowed_tools": ["web_search__search"],
                "source": "app",
                "metadata": {},
            }
        }
        if include_builtin:
            bundles["research"] = {
                "name": "research",
                "description": "library research",
                "content": "library SOP",
                "allowed_tools": [],
                "source": "builtin",
                "metadata": {},
            }
        return bundles

    monkeypatch.setattr(app_context, "get_app_root", lambda: "/fake/app")
    monkeypatch.setattr(skill_resolve, "resolve_merged_skill_bundles", _resolve)


_AGENT = SimpleNamespace(namespace="jvagent", name="executive_agent")


def _names(docs):
    return sorted(d.name for d in docs)


async def test_source_both_returns_app_and_library(monkeypatch):
    _stub_resolver(monkeypatch)
    docs = discover_skill_docs(_AGENT, skills_source="both", selector="-all")
    assert _names(docs) == ["research", "web_lookup"]


async def test_source_app_returns_only_adjacent(monkeypatch):
    _stub_resolver(monkeypatch)
    docs = discover_skill_docs(_AGENT, skills_source="app", selector="-all")
    assert _names(docs) == ["web_lookup"]


async def test_source_library_returns_only_builtin(monkeypatch):
    _stub_resolver(monkeypatch)
    docs = discover_skill_docs(_AGENT, skills_source="library", selector="-all")
    assert _names(docs) == ["research"]


async def test_aliases_map_to_canonical_sources(monkeypatch):
    _stub_resolver(monkeypatch)
    assert _names(
        discover_skill_docs(_AGENT, skills_source="local", selector="-all")
    ) == ["web_lookup"]
    assert _names(
        discover_skill_docs(_AGENT, skills_source="builtin", selector="-all")
    ) == ["research"]
    # registry is retired → treated as library
    assert _names(
        discover_skill_docs(_AGENT, skills_source="registry", selector="-all")
    ) == ["research"]


async def test_finite_list_selector_by_name(monkeypatch):
    _stub_resolver(monkeypatch)
    docs = discover_skill_docs(_AGENT, skills_source="both", selector=["research"])
    assert _names(docs) == ["research"]  # web_lookup excluded by the finite list


async def test_denied_subtracts_from_selection(monkeypatch):
    _stub_resolver(monkeypatch)
    docs = discover_skill_docs(
        _AGENT, skills_source="both", selector="-all", denied=["web_lookup"]
    )
    assert _names(docs) == ["research"]
