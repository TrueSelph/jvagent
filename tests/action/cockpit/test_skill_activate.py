"""Tests for the dynamic ``skill_activate`` harness tool (cockpit feature C)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest

from jvagent.action.cockpit.catalog.skill_catalog import SkillCatalog
from jvagent.action.cockpit.tools.skill import _build_skill_tools


@dataclass
class _FakeVisitor:
    _skill_state: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _FakeConfig:
    max_dynamic_activations: int = 10


@dataclass
class _FakeCtx:
    visitor: _FakeVisitor
    config: _FakeConfig
    preloaded_skills: List[str] = field(default_factory=list)
    registry: Any = None
    action_resolver: Any = None
    registry_dirty: bool = False
    dynamic_activations: int = 0


class _StubRegistry:
    """Minimal stand-in: only needs to be non-None so the tool proceeds."""

    def __init__(self) -> None:
        self.calls: List[str] = []


def _make_ctx(
    skills: Dict[str, Dict[str, Any]],
    *,
    cap: int = 10,
    preloaded: Optional[List[str]] = None,
) -> _FakeCtx:
    visitor = _FakeVisitor()
    catalog = SkillCatalog(skills)
    visitor._skill_state["skill_catalog"] = catalog
    visitor._skill_state["discovered_skills"] = skills
    return _FakeCtx(
        visitor=visitor,
        config=_FakeConfig(max_dynamic_activations=cap),
        preloaded_skills=list(preloaded or []),
        registry=_StubRegistry(),
    )


def _get_activate(ctx: _FakeCtx):
    tools = _build_skill_tools(ctx)
    activate = next(t for t in tools if t.name == "skill_activate")
    return activate


@pytest.mark.asyncio
async def test_skill_activate_unknown_skill() -> None:
    ctx = _make_ctx({"a": {"allowed_tools": []}})
    activate = _get_activate(ctx)
    result = await activate.execute(skill_name="ghost")
    assert "not in catalog" in result


@pytest.mark.asyncio
async def test_skill_activate_blank_name() -> None:
    ctx = _make_ctx({"a": {}})
    activate = _get_activate(ctx)
    result = await activate.execute(skill_name="")
    assert "required" in result


@pytest.mark.asyncio
async def test_skill_activate_already_active_is_noop() -> None:
    ctx = _make_ctx({"a": {"allowed_tools": []}}, preloaded=["a"])
    activate = _get_activate(ctx)
    result = await activate.execute(skill_name="a")
    assert "already active" in result
    assert ctx.dynamic_activations == 0
    assert ctx.registry_dirty is False


@pytest.mark.asyncio
async def test_skill_activate_disabled_when_cap_is_zero() -> None:
    ctx = _make_ctx({"a": {"allowed_tools": []}}, cap=0)
    activate = _get_activate(ctx)
    result = await activate.execute(skill_name="a")
    assert "disabled" in result
    assert ctx.dynamic_activations == 0


@pytest.mark.asyncio
async def test_skill_activate_cap_reached() -> None:
    ctx = _make_ctx({"a": {"allowed_tools": []}}, cap=2)
    ctx.dynamic_activations = 2
    activate = _get_activate(ctx)
    result = await activate.execute(skill_name="a")
    assert "cap reached" in result
    assert ctx.dynamic_activations == 2


@pytest.mark.asyncio
async def test_skill_activate_no_registry_refuses() -> None:
    ctx = _make_ctx({"a": {"allowed_tools": []}})
    ctx.registry = None
    activate = _get_activate(ctx)
    result = await activate.execute(skill_name="a")
    assert "tool registry is not exposed" in result


@pytest.mark.asyncio
async def test_skill_activate_load_failure_surfaces_reason(monkeypatch) -> None:
    """When ``load_one_skill`` registers no tools and reports failures,
    the activator surfaces the failure rather than silently succeeding."""
    from jvagent.action.cockpit.registry import assembler as assembler_mod

    async def fake_loader(registry, name, data, catalog, resolver, ctx, report):
        report.entries.append(
            assembler_mod.SkillLoadEntry(
                skill_name=name,
                file="bogus.py",
                status="failed",
                reason="ImportError: synthetic",
            )
        )

    monkeypatch.setattr(assembler_mod, "load_one_skill", fake_loader)

    ctx = _make_ctx({"a": {"allowed_tools": ["a__foo"]}})
    activate = _get_activate(ctx)
    result = await activate.execute(skill_name="a")
    assert "no tools loaded" in result
    assert "synthetic" in result
    assert ctx.dynamic_activations == 0
    assert ctx.registry_dirty is False


@pytest.mark.asyncio
async def test_skill_activate_success(monkeypatch) -> None:
    """Happy path: loader registers a tool; counters + dirty flag advance."""
    from jvagent.action.cockpit.registry import assembler as assembler_mod

    async def fake_loader(registry, name, data, catalog, resolver, ctx, report):
        report.entries.append(
            assembler_mod.SkillLoadEntry(
                skill_name=name,
                file="t.py",
                status="loaded",
                tool_name=f"{name}__foo",
            )
        )

    monkeypatch.setattr(assembler_mod, "load_one_skill", fake_loader)

    ctx = _make_ctx({"a": {"allowed_tools": ["a__foo"]}})
    activate = _get_activate(ctx)
    result = await activate.execute(skill_name="a")
    assert "activated 'a'" in result
    assert "a__foo" in result
    assert ctx.dynamic_activations == 1
    assert ctx.registry_dirty is True
    assert "a" in ctx.preloaded_skills
