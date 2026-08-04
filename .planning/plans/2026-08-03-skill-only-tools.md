# Skill-only tools (`skill_only_tools`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `skill_only_tools` config knob to the Orchestrator so named tools are callable **only** while a skill that declares them in its `allowed-tools` is active — a middle position between freely-callable and `denied_tools`.

**Architecture:** A new focused module `jvagent/action/orchestrator/skill_gate.py` owns the owner index, the open/closed predicate, the steer strings and the guard-wrapper install. `_assemble_tools` computes the gated set + owner index just before the catalog build (so `find_tool` can annotate hits) and installs the wrappers at the very end (after deny + pins, so precedence falls out of ordering). The gate closure captures the live `activated` list, so activation on tick 2 unlocks the tool on tick 3 with no re-assembly.

**Tech Stack:** Python 3.12+, `jvspatial` attributes, pytest + pytest-asyncio, fnmatch globs, `dataclasses.replace`.

**Spec:** [.planning/specs/2026-08-03-skill-only-tools-design.md](../specs/2026-08-03-skill-only-tools-design.md)

---

## File Structure

| File | Responsibility |
|---|---|
| `jvagent/action/orchestrator/skill_gate.py` | **Create.** Owner index (`build_skill_gate`), open/closed predicate (`SkillGate.is_open`), steer strings (`skill_only_steer`), wrapper install (`install_skill_gate`). Self-contained and unit-testable without an orchestrator. |
| `jvagent/action/orchestrator/orchestrator_interact_action.py` | **Modify.** New `skill_only_tools` attribute; `channel_overrides` description; two call sites in `_assemble_tools`. |
| `jvagent/action/orchestrator/catalog.py` | **Modify.** Optional `gated` param on `build_catalog_tools`; annotation in `_find` / `_load`; `skill_only_tools` in the config hash. |
| `tests/action/orchestrator/test_skill_only_tools.py` | **Create.** Unit tests for `skill_gate`, integration tests through `_assemble_tools`. |
| `.planning/adr/0043-skill-only-tools.md` | **Create.** Decision record. |
| `docs/ORCHESTRATOR.md`, `.planning/reference/configuration-keys.md`, `docs/scaffolding.md`, `jvagent/scaffold/builtin_profiles/orchestrator.yaml`, `CHANGELOG.md` | **Modify.** Documentation. |
| `examples/jvagent_app/agents/jvagent/orchestrator_agent/agent.yaml` | **Modify.** Commented example key for the smoke check. |

Why a new module rather than more lines in `orchestrator_interact_action.py`: that file is already 3388 lines, and the repo's established pattern is one concern per module (`access.py`, `egress.py`, `catalog.py`, `continuation.py`). The gate is a closed, testable unit with no orchestrator dependencies.

**Conventions for every task:** the commit gate in [CLAUDE.md](../../CLAUDE.md) §6 is mandatory — `pre-commit run --all-files` **and** the affected pytest slice must pass before each commit. Do not use `--no-verify`. Commits are authored as the repo user with no Claude co-author trailer.

---

### Task 1: `skill_gate` module — owner index and predicate

**Files:**
- Create: `jvagent/action/orchestrator/skill_gate.py`
- Test: `tests/action/orchestrator/test_skill_only_tools.py`

- [ ] **Step 1: Write the failing test**

Create `tests/action/orchestrator/test_skill_only_tools.py`:

```python
"""Skill-only tools (ADR-0043): a gated tool is callable only while a skill that
declares it in ``allowed-tools`` is active. Config marks WHICH tools are gated;
ownership comes from SKILL.md, so it cannot drift from the YAML."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from jvagent.action.orchestrator.skill_gate import (
    build_skill_gate,
    install_skill_gate,
    skill_only_steer,
)
from jvagent.action.orchestrator.tools import SkillTool

pytestmark = pytest.mark.asyncio


def _doc(name, tools=(), always_active=False):
    """A minimal SkillDoc stand-in (duck-typed on the fields the gate reads)."""
    return SimpleNamespace(
        name=name, requires_tools=tuple(tools), always_active=always_active
    )


# --- unit: owner index ------------------------------------------------------


def test_build_skill_gate_indexes_owners():
    gate = build_skill_gate(
        {"pay__charge", "pay__refund"},
        [
            _doc("checkout", ["pay__charge", "email__send"]),
            _doc("refunds", ["pay__charge", "pay__refund"]),
            _doc("faq", ["kb__search"]),
        ],
    )
    # Only gated names are indexed; a skill's non-gated tools are ignored.
    assert gate.owners_for("pay__charge") == ("checkout", "refunds")
    assert gate.owners_for("pay__refund") == ("refunds",)
    assert gate.owners_for("email__send") == ()


def test_build_skill_gate_collects_always_active():
    gate = build_skill_gate(
        {"pay__charge"}, [_doc("checkout", ["pay__charge"], always_active=True)]
    )
    assert gate.always_on == frozenset({"checkout"})


def test_is_open_requires_an_active_owner():
    gate = build_skill_gate({"pay__charge"}, [_doc("checkout", ["pay__charge"])])
    assert gate.is_open("pay__charge", []) is False
    assert gate.is_open("pay__charge", ["faq"]) is False
    assert gate.is_open("pay__charge", ["faq", "checkout"]) is True


def test_is_open_for_always_active_owner_without_activation():
    gate = build_skill_gate(
        {"pay__charge"}, [_doc("checkout", ["pay__charge"], always_active=True)]
    )
    assert gate.is_open("pay__charge", []) is True


def test_orphan_is_never_open_and_warns(caplog):
    with caplog.at_level(logging.WARNING):
        gate = build_skill_gate({"pay__refund"}, [_doc("checkout", ["pay__charge"])])
    assert gate.is_open("pay__refund", ["checkout"]) is False
    assert "pay__refund" in caplog.text
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python3 -m pytest tests/action/orchestrator/test_skill_only_tools.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'jvagent.action.orchestrator.skill_gate'`.

- [ ] **Step 3: Write the implementation**

Create `jvagent/action/orchestrator/skill_gate.py`:

```python
"""Skill-only tool gating for the Orchestrator (ADR-0043).

A *gated* tool is one an operator has listed in ``skill_only_tools``: it stays on
the full surface (so ``find_tool`` can point at it) but refuses to run unless a
skill that declares it in ``allowed-tools`` is active. Config marks WHICH tools
are gated; ownership is derived from the skill docs, never restated in YAML, so
the two cannot drift.

The gate is installed on the tool object itself rather than checked at the loop's
dispatch site, so every dispatch path is covered — not only the one in
``loop.py``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any, Dict, Iterable, List, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SkillGate:
    """Which skills own which gated tools, and which skills are always in force."""

    owners: Dict[str, Tuple[str, ...]]
    always_on: frozenset

    def owners_for(self, name: str) -> Tuple[str, ...]:
        """Skills declaring *name* in their ``allowed-tools`` (empty = orphan)."""
        return self.owners.get(name, ())

    def is_open(self, name: str, activated: Iterable[str]) -> bool:
        """True when an owning skill is active this turn.

        Active means: activated (``use_skill``, auto-start, or holding the
        turn-lock — all of which append to ``activated``), or ``always-active``.
        An orphan is never open: config said "only via a skill" and no skill
        provides it, so it fails closed.
        """
        owning = self.owners.get(name, ())
        if not owning:
            return False
        active = set(activated or ())
        return any(o in active or o in self.always_on for o in owning)


def build_skill_gate(gated: Set[str], docs: Iterable[Any]) -> SkillGate:
    """Index ``gated`` tools by the skills declaring them, warning on orphans.

    ``docs`` must be the already-filtered skill docs the surface will offer
    (post ``requires-actions``, post per-channel gate) — a skill the model cannot
    reach must not confer ownership.
    """
    owners: Dict[str, Tuple[str, ...]] = {}
    always_on: Set[str] = set()
    for doc in docs or ():
        name = str(getattr(doc, "name", "") or "")
        if not name:
            continue
        for tool_name in getattr(doc, "requires_tools", ()) or ():
            if tool_name in gated:
                owners[tool_name] = owners.get(tool_name, ()) + (name,)
        if getattr(doc, "always_active", False):
            always_on.add(name)
    orphans = sorted(set(gated) - set(owners))
    if orphans:
        logger.warning(
            "orchestrator: skill_only_tools matched tools no available skill "
            "declares — uncallable this turn: %s",
            orphans,
        )
    return SkillGate(owners=owners, always_on=frozenset(always_on))


def skill_only_steer(name: str, owning: Tuple[str, ...]) -> str:
    """The observation returned when a gated tool is called with no owner active."""
    if not owning:
        # End the line of attack explicitly: without this the model retries the
        # same call into the repeat-guard and loses the turn to a condition it
        # can never satisfy.
        return (
            f"({name} is not directly callable and no available skill provides "
            "it. Tell the user you cannot do that; do not retry.)"
        )
    return (
        f"({name} is only available inside a skill. Call use_skill with one of: "
        f"{', '.join(owning)} — then call {name} again.)"
    )


def install_skill_gate(
    tools: Dict[str, Any],
    gated: Set[str],
    gate: SkillGate,
    activated: List[str],
) -> None:
    """Wrap each gated tool's runner with the gate, in place.

    ``activated`` is captured by reference, so a skill activated mid-loop opens
    its tools on the next tick without re-assembling the surface.
    """
    for name in sorted(gated):
        tool = tools.get(name)
        if tool is None:
            continue
        tools[name] = replace(tool, run=_gated_runner(name, tool.run, gate, activated))


def _gated_runner(name, inner, gate: SkillGate, activated: List[str]):
    """Bind one tool's guard (a factory, so the loop variable isn't captured)."""

    async def _run(args: Dict[str, Any]) -> str:
        if gate.is_open(name, activated):
            return await inner(args)
        return skill_only_steer(name, gate.owners_for(name))

    return _run


__all__ = [
    "SkillGate",
    "build_skill_gate",
    "install_skill_gate",
    "skill_only_steer",
]
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
python3 -m pytest tests/action/orchestrator/test_skill_only_tools.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add jvagent/action/orchestrator/skill_gate.py tests/action/orchestrator/test_skill_only_tools.py
git commit -m "feat(orchestrator): add skill_gate owner index and open/closed predicate"
```

---

### Task 2: Guard wrapper refuses and steers

**Files:**
- Modify: `jvagent/action/orchestrator/skill_gate.py` (no change expected — this task proves the install works)
- Test: `tests/action/orchestrator/test_skill_only_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/action/orchestrator/test_skill_only_tools.py`:

```python
# --- unit: guard wrapper ----------------------------------------------------


def _spy_tool(name):
    """A SkillTool whose runner records that it ran."""
    calls: list = []

    async def _run(args):
        calls.append(args)
        return "RAN"

    return SkillTool(name=name, description=f"{name} description", run=_run), calls


async def test_gated_call_refuses_and_names_the_skill():
    tool, calls = _spy_tool("pay__charge")
    tools = {"pay__charge": tool}
    activated: list = []
    gate = build_skill_gate({"pay__charge"}, [_doc("checkout", ["pay__charge"])])
    install_skill_gate(tools, {"pay__charge"}, gate, activated)

    out = await tools["pay__charge"].run({})
    assert "only available inside a skill" in out
    assert "checkout" in out
    assert calls == []  # the real runner never ran


async def test_gated_call_runs_once_owner_is_activated():
    tool, calls = _spy_tool("pay__charge")
    tools = {"pay__charge": tool}
    activated: list = []
    gate = build_skill_gate({"pay__charge"}, [_doc("checkout", ["pay__charge"])])
    install_skill_gate(tools, {"pay__charge"}, gate, activated)

    activated.append("checkout")  # what use_skill does, mid-loop
    assert await tools["pay__charge"].run({"amount": 5}) == "RAN"
    assert calls == [{"amount": 5}]


async def test_gated_orphan_call_tells_the_model_not_to_retry():
    tool, calls = _spy_tool("pay__refund")
    tools = {"pay__refund": tool}
    gate = build_skill_gate({"pay__refund"}, [_doc("checkout", ["pay__charge"])])
    install_skill_gate(tools, {"pay__refund"}, gate, [])

    out = await tools["pay__refund"].run({})
    assert "no available skill provides it" in out
    assert "do not retry" in out
    assert calls == []


def test_install_preserves_name_description_and_terminal():
    tool = SkillTool(name="ia__interview", description="Run it", run=None, terminal=True)
    tools = {"ia__interview": tool}
    gate = build_skill_gate({"ia__interview"}, [_doc("intake", ["ia__interview"])])
    install_skill_gate(tools, {"ia__interview"}, gate, [])
    wrapped = tools["ia__interview"]
    assert wrapped.name == "ia__interview"
    assert wrapped.description == "Run it"
    # terminal must survive: gating an IA-as-tool must not change end-of-turn
    # semantics once the tool is legitimately reached.
    assert wrapped.terminal is True
```

- [ ] **Step 2: Run the tests to verify they pass or fail**

```bash
python3 -m pytest tests/action/orchestrator/test_skill_only_tools.py -v
```

Expected: all pass — Task 1's implementation already satisfies these. If any fail, fix `skill_gate.py`; do **not** weaken the test. (These are written as a separate task because they pin the wrapper contract, which the rest of the plan depends on.)

- [ ] **Step 3: Commit**

```bash
git add tests/action/orchestrator/test_skill_only_tools.py
git commit -m "test(orchestrator): pin the skill-only guard wrapper contract"
```

---

### Task 3: `skill_only_tools` attribute + config hash

**Files:**
- Modify: `jvagent/action/orchestrator/orchestrator_interact_action.py:607-618` (`channel_overrides` description), `:669-678` (after `denied_tools`)
- Modify: `jvagent/action/orchestrator/catalog.py:48-61` (`compute_tool_surface_config_hash`)
- Test: `tests/action/orchestrator/test_skill_only_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/action/orchestrator/test_skill_only_tools.py`:

```python
# --- unit: config surface ---------------------------------------------------


def test_attribute_defaults_empty():
    from jvagent.action.orchestrator.orchestrator_interact_action import (
        OrchestratorInteractAction,
    )

    assert OrchestratorInteractAction().skill_only_tools == []


def test_config_hash_changes_with_skill_only_tools():
    from jvagent.action.orchestrator.catalog import compute_tool_surface_config_hash
    from jvagent.action.orchestrator.orchestrator_interact_action import (
        OrchestratorInteractAction,
    )

    ex = OrchestratorInteractAction()
    before = compute_tool_surface_config_hash(ex, ["A"])
    ex.skill_only_tools = ["pay__*"]
    after = compute_tool_surface_config_hash(ex, ["A"])
    assert before != after
```

- [ ] **Step 2: Run to verify it fails**

```bash
python3 -m pytest tests/action/orchestrator/test_skill_only_tools.py -k "attribute_defaults or config_hash" -v
```

Expected: FAIL — `AttributeError: 'OrchestratorInteractAction' object has no attribute 'skill_only_tools'`.

- [ ] **Step 3: Add the attribute**

In `jvagent/action/orchestrator/orchestrator_interact_action.py`, immediately after the `denied_tools` attribute (ends at `:678`):

```python
    skill_only_tools: List[str] = attribute(
        default_factory=list,
        description="Tool-name globs (e.g. 'payments__*') callable ONLY while a "
        "skill that declares them in its allowed-tools is active (activated this "
        "turn, holding the turn-lock, or always-active). They are not listed in "
        "the prompt; find_tool shows them annotated with the owning skill, and a "
        "direct call is refused with a steer to use_skill. A gated tool no "
        "available skill declares is uncallable (fail closed). denied_tools wins "
        "over this; a pinned_tools match cannot un-gate. Egress and catalog "
        "meta-tools cannot be gated. Empty by default. Channel-overridable via "
        "channel_overrides.skill_only_tools (replaces the action-level list on "
        "that channel).",
    )
```

- [ ] **Step 4: Document the channel key**

In the same file, in the `channel_overrides` description (`:615`), change:

```python
        "denied_tools (REPLACES the action-level deny list on that channel), and "
```

to:

```python
        "denied_tools (REPLACES the action-level deny list on that channel), "
        "skill_only_tools (REPLACES the action-level skill-only list on that "
        "channel), and "
```

- [ ] **Step 5: Add it to the surface-cache config hash**

In `jvagent/action/orchestrator/catalog.py`, in `compute_tool_surface_config_hash`, add after the `denied_tools` line (`:57`):

```python
        str(getattr(orch, "skill_only_tools", "") or ""),
```

- [ ] **Step 6: Run to verify it passes**

```bash
python3 -m pytest tests/action/orchestrator/test_skill_only_tools.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add jvagent/action/orchestrator/orchestrator_interact_action.py jvagent/action/orchestrator/catalog.py tests/action/orchestrator/test_skill_only_tools.py
git commit -m "feat(orchestrator): add skill_only_tools config attribute"
```

---

### Task 4: Catalog annotation in `find_tool` / `load_tool`

**Files:**
- Modify: `jvagent/action/orchestrator/catalog.py:136-209` (`build_catalog_tools`)
- Test: `tests/action/orchestrator/test_skill_only_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/action/orchestrator/test_skill_only_tools.py`:

```python
# --- unit: catalog annotation -----------------------------------------------


async def test_find_tool_annotates_gated_hits():
    from jvagent.action.orchestrator.catalog import build_catalog_tools

    all_tools = {
        "pay__charge": SkillTool("pay__charge", "Charge a saved card.", run=None),
        "pay__refund": SkillTool("pay__refund", "Refund a charge.", run=None),
        "kb__search": SkillTool("kb__search", "Search the knowledge base.", run=None),
    }
    cat = build_catalog_tools(
        all_tools,
        visible=set(),
        gated={"pay__charge": ("checkout",), "pay__refund": ()},
    )
    out = await cat["find_tool"].run({"query": ""})
    assert "pay__charge: Charge a saved card. (via skill: checkout)" in out
    assert "pay__refund: Refund a charge. (not directly callable; no skill provides it)" in out
    # An ungated tool is untouched.
    assert "kb__search: Search the knowledge base." in out
    assert "kb__search: Search the knowledge base. (" not in out


async def test_load_tool_annotates_gated_tool():
    from jvagent.action.orchestrator.catalog import build_catalog_tools

    all_tools = {"pay__charge": SkillTool("pay__charge", "Charge a card.", run=None)}
    cat = build_catalog_tools(
        all_tools, visible=set(), gated={"pay__charge": ("checkout", "refunds")}
    )
    out = await cat["load_tool"].run({"name": "pay__charge"})
    assert "Charge a card." in out
    assert "(via skill: checkout, refunds)" in out


async def test_catalog_gated_defaults_to_none():
    """Existing call sites pass no ``gated`` and must be unaffected."""
    from jvagent.action.orchestrator.catalog import build_catalog_tools

    all_tools = {"kb__search": SkillTool("kb__search", "Search.", run=None)}
    cat = build_catalog_tools(all_tools, visible=set())
    assert "(via skill" not in await cat["find_tool"].run({"query": ""})
    assert "(via skill" not in await cat["load_tool"].run({"name": "kb__search"})
```

- [ ] **Step 2: Run to verify it fails**

```bash
python3 -m pytest tests/action/orchestrator/test_skill_only_tools.py -k "annotates or gated_defaults" -v
```

Expected: FAIL — `TypeError: build_catalog_tools() got an unexpected keyword argument 'gated'`.

- [ ] **Step 3: Implement the annotation**

In `jvagent/action/orchestrator/catalog.py`, add this helper directly above `build_catalog_tools` (`:136`):

```python
def _gate_marker(name: str, gated: Optional[Dict[str, Tuple[str, ...]]]) -> str:
    """Suffix telling the model a hit is skill-only (ADR-0043); "" when ungated."""
    if not gated or name not in gated:
        return ""
    owning = gated.get(name) or ()
    if not owning:
        return " (not directly callable; no skill provides it)"
    return f" (via skill: {', '.join(owning)})"
```

Change the signature (`:136-139`) from:

```python
def build_catalog_tools(
    all_tools: Dict[str, SkillTool], visible: Set[str]
) -> Dict[str, SkillTool]:
    """``find_tool`` / ``load_tool`` over the full ``all_tools`` surface."""
```

to:

```python
def build_catalog_tools(
    all_tools: Dict[str, SkillTool],
    visible: Set[str],
    gated: Optional[Dict[str, Tuple[str, ...]]] = None,
) -> Dict[str, SkillTool]:
    """``find_tool`` / ``load_tool`` over the full ``all_tools`` surface.

    ``gated`` maps a skill-only tool name (ADR-0043) to the skills that own it
    (empty tuple = orphan). Hits are annotated so the model learns the correct
    move — ``use_skill`` — instead of dead-ending on a refused call.
    """
```

In `_find`, change the per-hit line (`:174-177`) from:

```python
            for t in groups[ns][:15]:
                listed += 1
                summary = _summarize(t.description)
                lines.append(f"- {t.name}: {summary}" if summary else f"- {t.name}")
```

to:

```python
            for t in groups[ns][:15]:
                listed += 1
                summary = _summarize(t.description)
                marker = _gate_marker(t.name, gated)
                lines.append(
                    f"- {t.name}: {summary}{marker}" if summary else f"- {t.name}{marker}"
                )
```

In `_load`, change the success return (`:196`) from:

```python
        return f"Loaded tool '{name}': {tool.description}"
```

to:

```python
        # Load still succeeds on a gated tool — it is a description fetch, and
        # the guard wrapper is the actual gate.
        return f"Loaded tool '{name}': {tool.description}{_gate_marker(name, gated)}"
```

- [ ] **Step 4: Run to verify it passes**

```bash
python3 -m pytest tests/action/orchestrator/test_skill_only_tools.py tests/action/orchestrator/test_lean_surfacing.py -v
```

Expected: all pass — including the pre-existing lean tests that call `build_catalog_tools` with two positional args.

- [ ] **Step 5: Commit**

```bash
git add jvagent/action/orchestrator/catalog.py tests/action/orchestrator/test_skill_only_tools.py
git commit -m "feat(orchestrator): annotate skill-only tools in find_tool and load_tool"
```

---

### Task 5: Wire the gate into `_assemble_tools`

**Files:**
- Modify: `jvagent/action/orchestrator/orchestrator_interact_action.py:1204-1244`
- Test: `tests/action/orchestrator/test_skill_only_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/action/orchestrator/test_skill_only_tools.py`:

```python
# --- integration: _assemble_tools ------------------------------------------


class _ToolsAction:
    """A plain action exposing namespaced capability tools (mirrors the fixture
    in test_lean_surfacing.py)."""

    def __init__(self, names_descs):
        self._t = [
            SimpleNamespace(name=n, description=d, call=None) for n, d in names_descs
        ]

    async def get_tools(self):
        return self._t


_PAY = [
    ("pay__charge", "Charge a saved payment method."),
    ("pay__refund", "Refund a settled charge."),
    ("kb__search", "Search the knowledge base."),
]


def _wire_skills(monkeypatch, ex, docs):
    """Surface ``docs`` as this agent's skills without touching the resolver."""
    monkeypatch.setattr(ex, "_discover_skills", lambda _agent: list(docs))
    monkeypatch.setattr(
        "jvagent.action.orchestrator.skill_tasks.compose_skill_activate_hooks",
        lambda *a, **k: (None, None),
    )


async def test_gated_tool_is_on_the_surface_but_not_listed(
    monkeypatch, make_orchestrator, make_visitor
):
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0  # list everything, so absence is unambiguous
    ex.skill_only_tools = ["pay__*"]
    _wire_skills(monkeypatch, ex, [_doc("checkout", ["pay__charge", "pay__refund"])])

    visible: set = set()
    tools = await ex._assemble_tools(
        make_visitor(utterance="charge me"), [], visible, None, "charge me", None, {}
    )
    assert "pay__charge" in tools  # still on the surface (find_tool reaches it)
    assert "pay__charge" not in visible  # but not in the prompt
    assert "kb__search" in visible  # ungated tools unaffected


async def test_gated_dispatch_refuses_then_runs_after_activation(
    monkeypatch, make_orchestrator, make_visitor
):
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = ["pay__*"]
    _wire_skills(monkeypatch, ex, [_doc("checkout", ["pay__charge"])])

    activated: list = []
    tools = await ex._assemble_tools(
        make_visitor(utterance="charge me"),
        activated,
        set(),
        None,
        "charge me",
        None,
        {},
    )
    refused = await tools["pay__charge"].run({})
    assert "only available inside a skill" in refused and "checkout" in refused

    # use_skill mutates the same list the gate captured.
    await tools["use_skill"].run({"name": "checkout"})
    assert "checkout" in activated
    opened = await tools["pay__charge"].run({})
    assert "only available inside a skill" not in opened


async def test_always_active_owner_opens_the_gate_on_tick_one(
    monkeypatch, make_orchestrator, make_visitor
):
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = ["pay__*"]
    _wire_skills(
        monkeypatch, ex, [_doc("checkout", ["pay__charge"], always_active=True)]
    )

    tools = await ex._assemble_tools(
        make_visitor(utterance="charge me"), [], set(), None, "charge me", None, {}
    )
    out = await tools["pay__charge"].run({})
    assert "only available inside a skill" not in out


async def test_orphaned_gated_tool_is_locked(
    monkeypatch, make_orchestrator, make_visitor
):
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = ["pay__*"]
    # 'checkout' declares only pay__charge — pay__refund has no owner.
    _wire_skills(monkeypatch, ex, [_doc("checkout", ["pay__charge"])])

    tools = await ex._assemble_tools(
        make_visitor(utterance="refund me"),
        ["checkout"],
        set(),
        None,
        "refund me",
        None,
        {},
    )
    out = await tools["pay__refund"].run({})
    assert "no available skill provides it" in out


async def test_find_tool_annotation_reaches_the_model(
    monkeypatch, make_orchestrator, make_visitor
):
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = ["pay__charge"]
    _wire_skills(monkeypatch, ex, [_doc("checkout", ["pay__charge"])])

    tools = await ex._assemble_tools(
        make_visitor(utterance="charge"), [], set(), None, "charge", None, {}
    )
    hit = await tools["find_tool"].run({"query": "charge"})
    assert "(via skill: checkout)" in hit
```

- [ ] **Step 2: Run to verify it fails**

```bash
python3 -m pytest tests/action/orchestrator/test_skill_only_tools.py -k "gated_tool_is_on_the_surface or refuses_then_runs" -v
```

Expected: FAIL — `pay__charge` is in `visible`, and the dispatch returns the tool's own result rather than a steer.

- [ ] **Step 3: Compute the gated set + owner index before the catalog build**

In `jvagent/action/orchestrator/orchestrator_interact_action.py`, insert immediately **before** the `# Tool catalog (find_tool/load_tool …)` comment at `:1204`:

```python
        # Skill-only gating (ADR-0043). The glob match + owner index are computed
        # HERE so the catalog can annotate its hits; the guard wrapper is
        # installed at the very END of assembly (after deny + pins) so precedence
        # falls out of ordering rather than needing explicit rules.
        skill_only = self._channel_cfg(
            visitor, "skill_only_tools", self.skill_only_tools
        )
        gated = self._match_tool_globs(list(skill_only or []), set(tools.keys()))
        # A pattern that matches nothing is the one silent failure this feature
        # can have: the operator believes a sensitive tool is gated and it is
        # freely callable. Fail-closed on unowned tools is meaningless if the
        # glob never matched, so name the dead patterns.
        dead = [
            p
            for p in (skill_only or [])
            if str(p).strip()
            and not self._match_tool_globs([p], set(tools.keys()))
        ]
        if dead:
            logger.warning(
                "orchestrator: skill_only_tools patterns matched no tool — "
                "nothing is gated by them: %s",
                dead,
            )
        protected_gated = gated & _STEER_EXEMPT
        if protected_gated:
            logger.warning(
                "orchestrator: skill_only_tools matched protected tools %s — ignored",
                sorted(protected_gated),
            )
            gated -= _STEER_EXEMPT
        gate = build_skill_gate(gated, docs)
```

Change the catalog build (`:1205`) from:

```python
        for name, t in build_catalog_tools(tools, visible).items():
```

to:

```python
        for name, t in build_catalog_tools(
            tools, visible, gated={n: gate.owners_for(n) for n in gated}
        ).items():
```

Add the import next to the other orchestrator-module imports at the top of the file:

```python
from jvagent.action.orchestrator.skill_gate import build_skill_gate, install_skill_gate
```

- [ ] **Step 4: Install the wrappers at the end of assembly**

In the same function, replace the final `return tools` (`:1244`) with:

```python
        # Skill-only gate install (ADR-0043), last: deny has already popped its
        # matches from ``tools`` (so a denied tool is simply gone), and pins have
        # already written to ``visible`` (so this discard is what survives — a
        # pin grants visibility, never callability).
        if gated:
            gated &= set(tools.keys())
            install_skill_gate(tools, gated, gate, activated)
            for name in gated:
                visible.discard(name)
                longtail.discard(name)
        return tools
```

- [ ] **Step 5: Run to verify it passes**

```bash
python3 -m pytest tests/action/orchestrator/test_skill_only_tools.py -v
```

Expected: all pass.

- [ ] **Step 6: Run the whole orchestrator slice for regressions**

```bash
python3 -m pytest tests/action/orchestrator/ -q
```

Expected: exit 0 (1 skip: `zoon-ai skills not available in this checkout`).

- [ ] **Step 7: Commit**

```bash
git add jvagent/action/orchestrator/orchestrator_interact_action.py tests/action/orchestrator/test_skill_only_tools.py
git commit -m "feat(orchestrator): gate skill_only_tools at dispatch"
```

---

### Task 6: Precedence and protection tests

**Files:**
- Test: `tests/action/orchestrator/test_skill_only_tools.py`

No implementation is expected in this task — the ordering in Task 5 should already produce these results. If a test fails, fix the ordering in `_assemble_tools`, not the test.

- [ ] **Step 1: Write the tests**

Append to `tests/action/orchestrator/test_skill_only_tools.py`:

```python
# --- integration: precedence -----------------------------------------------


async def test_denied_tools_beats_skill_only(
    monkeypatch, make_orchestrator, make_visitor
):
    """A denied tool is gone entirely — gating never sees it, find_tool can't
    return it, and no annotation is emitted."""
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = ["pay__*"]
    ex.denied_tools = ["pay__charge"]
    _wire_skills(monkeypatch, ex, [_doc("checkout", ["pay__charge", "pay__refund"])])

    tools = await ex._assemble_tools(
        make_visitor(utterance="charge"), ["checkout"], set(), None, "charge", None, {}
    )
    assert "pay__charge" not in tools
    hit = await tools["find_tool"].run({"query": "charge"})
    assert "pay__charge" not in hit
    # The other gated tool still exists and is gated.
    assert "pay__refund" in tools


async def test_pin_cannot_un_gate(monkeypatch, make_orchestrator, make_visitor):
    """A pin grants visibility, never callability — and gating wins on both."""
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 15
    ex.skill_only_tools = ["pay__charge"]
    ex.pinned_tools = ["pay__charge"]
    _wire_skills(monkeypatch, ex, [_doc("checkout", ["pay__charge"])])

    visible: set = set()
    tools = await ex._assemble_tools(
        make_visitor(utterance="hello"), [], visible, None, "hello", None, {}
    )
    assert "pay__charge" not in visible
    out = await tools["pay__charge"].run({})
    assert "only available inside a skill" in out


async def test_skill_only_cannot_gate_egress_or_meta(
    monkeypatch, make_orchestrator, make_visitor, caplog
):
    from jvagent.action.reply.reply_action import ReplyAction

    ex = make_orchestrator(actions=[_ToolsAction(_PAY), ReplyAction()])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = ["reply", "find_tool", "use_skill", "pay__*"]
    _wire_skills(monkeypatch, ex, [_doc("checkout", ["pay__charge"])])

    visible: set = set()
    with caplog.at_level(logging.WARNING):
        tools = await ex._assemble_tools(
            make_visitor(utterance="hi"), [], visible, None, "hi", None, {}
        )
    # Protected names stay listed and ungated.
    assert "reply" in visible and "find_tool" in visible and "use_skill" in visible
    assert "protected tools" in caplog.text
    # The non-protected match is still gated.
    assert "pay__charge" not in visible


async def test_skill_only_channel_override_replaces_the_list(
    monkeypatch, make_orchestrator, make_visitor
):
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = ["pay__*"]
    ex.channel_overrides = {"voice": {"skill_only_tools": ["kb__*"]}}
    _wire_skills(
        monkeypatch,
        ex,
        [_doc("checkout", ["pay__charge", "pay__refund"]), _doc("faq", ["kb__search"])],
    )

    visible_voice: set = set()
    await ex._assemble_tools(
        make_visitor(utterance="x", channel="voice"),
        [],
        visible_voice,
        None,
        "x",
        None,
        {},
    )
    assert "kb__search" not in visible_voice  # the channel's own list
    assert "pay__charge" in visible_voice  # the action-level list is REPLACED

    visible_web: set = set()
    await ex._assemble_tools(
        make_visitor(utterance="x", channel="web"), [], visible_web, None, "x", None, {}
    )
    assert "pay__charge" not in visible_web
    assert "kb__search" in visible_web


async def test_channel_blocked_owner_removes_its_tools_entirely(
    monkeypatch, make_orchestrator, make_visitor
):
    """A skill blocked on this channel already has its declared tools dropped from
    the surface by the ADR-0032 cleanup — gating never sees them, so there is no
    orphan to reason about and nothing leaks."""
    blocked = SimpleNamespace(
        name="checkout",
        requires_tools=("pay__charge",),
        always_active=False,
        allowed_channels=("web",),
        denied_channels=(),
        deny_access_directive="Not available here.",
    )
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = ["pay__*"]
    _wire_skills(monkeypatch, ex, [blocked])

    tools = await ex._assemble_tools(
        make_visitor(utterance="charge", channel="voice"),
        [],
        set(),
        None,
        "charge",
        None,
        {},
    )
    assert "pay__charge" not in tools
```

- [ ] **Step 2: Run them**

```bash
python3 -m pytest tests/action/orchestrator/test_skill_only_tools.py -v
```

Expected: all pass. If `test_pin_cannot_un_gate` fails with `pay__charge` visible, the install block is running before the pins — move it back to the end of `_assemble_tools`.

- [ ] **Step 3: Run the full orchestrator slice**

```bash
python3 -m pytest tests/action/orchestrator/ -q
```

Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add tests/action/orchestrator/test_skill_only_tools.py
git commit -m "test(orchestrator): pin skill_only_tools precedence and protection"
```

---

### Task 7: ADR + documentation

**Files:**
- Create: `.planning/adr/0043-skill-only-tools.md`
- Modify: `docs/ORCHESTRATOR.md:102`, `.planning/reference/configuration-keys.md`, `docs/scaffolding.md:193`, `jvagent/scaffold/builtin_profiles/orchestrator.yaml:49`, `CHANGELOG.md`

- [ ] **Step 1: Write the ADR**

Create `.planning/adr/0043-skill-only-tools.md`:

```markdown
# ADR 0043 — Skill-only tools (`skill_only_tools`)

**Status**: Accepted
**Date**: 2026-08-03
**Relation**: Extends [ADR-0018](0018-lean-tool-surfacing.md) §"Hard deny (capability gate)" with the missing middle between freely-callable and denied. Builds on [ADR-0012](0012-skill-executive-architecture.md) (unified surface, `find_tool`/`use_skill`).

---

## 1. Context

Three levers shape the assembled surface today: lean surfacing **hides**,
`pinned_tools` / `always-active` **force visible**, `denied_tools` **removes**.
There is nothing in between. An operator who wants a sensitive or
procedure-bound capability to run only inside its SOP must choose between
leaving it directly callable (the model fires it on a bare request, skipping the
SOP's preconditions) and denying it (the skill that needs it also loses it).

Hiding cannot substitute for a gate. Dispatch resolves against the full surface,
and `block_raw_tool_invocation` deliberately auto-promotes a real tool the model
names (`loop.py:1017`) so lean never dead-ends the loop.

## 2. Decision

Add `skill_only_tools`: fnmatch globs of tools callable **only while a skill
that declares them in its `allowed-tools` is active**.

- **Ownership is skill-derived.** Config marks *which* tools are gated; the
  owner index is built from `SkillDoc.requires_tools`, so YAML and SKILL.md
  cannot drift.
- **Active** = activated this turn (`use_skill`, auto-start, or holding the
  turn-lock — all append to `activated`) or `always-active: true`.
- **Not listed, but discoverable.** Gated tools are dropped from the prompt set;
  `find_tool` returns them annotated `(via skill: checkout)` so the model learns
  the right move instead of concluding the capability is absent.
- **Refuse and steer.** A direct call does not run; the observation names the
  skill to activate. This preserves the SOP's ordering, preconditions and
  `requires-tasks` detours — the reason the tool is gated at all.
- **Fail closed.** A gated tool no available skill declares is uncallable, with
  a warning at assembly. A glob typo cannot silently open a hole.
- **Precedence:** `denied_tools` > `skill_only_tools` > `pinned_tools`. Egress
  and catalog meta-tools cannot be gated. Channel-overridable via
  `channel_overrides.skill_only_tools` (replaces the action-level list).

Implemented as a guard wrapper installed on the tool object
(`skill_gate.install_skill_gate`), not a check at the loop's dispatch site, so
every dispatch path is covered.

## 3. Consequences

- A third, weaker capability gate exists between lean-hidden and denied.
- `use_skill` becomes load-bearing for gated capabilities rather than advisory.
- The gate composes with AccessControl (`access_label`) rather than replacing
  it: skill gate first, then per-user authorization.
- A misconfigured glob costs a capability, not a safety hole.

## 4. Relationship to the thin-harness principle

[`docs/thin-harness.md`](../../docs/thin-harness.md) says operations any user may
call directly belong in `Action.get_tools()`, "not buried in skill-only
wrappers." This does not contradict that: the capability remains a plain
`get_tools()` tool with an unchanged signature, and no wrapper action or
duplicate tool is introduced. What is new is a deployment-time *reachability
policy* over an unchanged capability. The harness stays thin; the judgment about
when the tool may fire stays in the SOP.

## 5. Alternatives considered

- **Check at the loop dispatch site** — readable, but guards only that one call
  site; other paths that call `tool.run` directly would bypass it.
- **Remove from `tools` and splice back on activation** — mirrors `denied_tools`
  exactly, but activation happens in several places mid-loop and each would need
  the splice; `find_tool` would need a side dict anyway for the annotation.
- **Explicit `{tool_glob: [skills]}` map in YAML** — most explicit, but restates
  ownership already declared in SKILL.md and drifts on rename.
- **Auto-activate the owning skill and run the tool** — fastest, but the SOP
  body arrives *after* the side effect fired and `requires-tasks` gating never
  gets to push its detour.

Covered by `tests/action/orchestrator/test_skill_only_tools.py`.
```

- [ ] **Step 2: Extend `docs/ORCHESTRATOR.md`**

Directly after the "**Hard deny (capability gate).**" bullet at `:102`, add:

```markdown
- **Skill-only (procedure gate).** `skill_only_tools: ["payments__*"]` (fnmatch globs) makes matching tools callable **only while a skill that declares them in its `allowed-tools` is active** — activated via `use_skill`, holding the turn-lock, or `always-active: true`. They are not listed in the prompt; `find_tool` shows them annotated `(via skill: checkout)`, and a direct call is refused with a steer to `use_skill` rather than running. A gated tool no available skill declares is uncallable (fail closed, warned at assembly). Precedence: `denied_tools` wins over this, and this wins over `pinned_tools` (a pin cannot un-gate). Egress and catalog meta-tools cannot be gated. Channel-overridable via `channel_overrides.skill_only_tools`. Use this when a capability must always run inside its SOP — a payment charge, a destructive write — where hard deny would also break the skill that legitimately needs it. See [ADR-0043](../.planning/adr/0043-skill-only-tools.md).
```

- [ ] **Step 3: Add the config key reference**

In `.planning/reference/configuration-keys.md`, the tool-surface table uses three
columns (`key | default | description`). Add this row immediately after the
`denied_tools` row at `:260`:

```markdown
| `skill_only_tools` | `[]` | tool-name globs (e.g. `["payments__*"]`) callable **only while a skill that declares them in its `allowed-tools` is active** (activated via `use_skill`, holding the turn-lock, or `always-active: true`). Not listed in the prompt; `find_tool` shows them annotated `(via skill: …)`; a direct call is refused with a steer to `use_skill` instead of running. Fail-closed: a gated tool no available skill declares is uncallable (warned at assembly). `denied_tools` wins over this; a `pinned_tools` match cannot un-gate. Egress/meta cannot be gated. Channel-overridable via `channel_overrides.skill_only_tools` (replaces the action-level list). ADR-0043 |
```

Then add a recipe row to the recipes table, immediately after the
"Hard-exclude named tools" row at `:281`:

```markdown
| **A capability must only ever run inside its SOP** — a payment charge, a destructive write; hard deny would also break the skill that legitimately needs it | `skill_only_tools: ["payments__*"]` — the owning skill is whichever `SKILL.md` lists the tool in `allowed-tools` |
```

- [ ] **Step 4: Add it to the scaffolding doc**

In `docs/scaffolding.md`, after the `denied_tools` bullet at `:193`:

```markdown
- `skill_only_tools`: optional tool-name globs callable only inside a skill that
  declares them (`allowed-tools`); not listed, `find_tool`-annotated, direct
  calls refused. Fail-closed when no skill owns them.
```

- [ ] **Step 5: Add the commented profile example**

In `jvagent/scaffold/builtin_profiles/orchestrator.yaml`, after the `denied_tools` comment at `:49`:

```yaml
      # skill_only_tools: ["payments__*"]  # callable only inside an owning skill
```

- [ ] **Step 6: Add the changelog entry**

In `CHANGELOG.md`, under the current unreleased/most-recent section:

```markdown
- **Orchestrator `skill_only_tools`.** Fnmatch globs make matching tools callable
  only while a skill that declares them in its `allowed-tools` is active. Not
  listed in the prompt; `find_tool` annotates them `(via skill: …)`; a direct
  call is refused with a steer to `use_skill`. Fail-closed when no available
  skill owns the tool. `denied_tools` wins over it; a `pinned_tools` match
  cannot un-gate. Channel-overridable via `channel_overrides.skill_only_tools`.
  See ADR-0043.
```

- [ ] **Step 7: Run the commit gate**

```bash
pre-commit run --all-files
```

Expected: all hooks pass with no files modified. If black/isort reformat anything, re-stage and re-run until clean.

- [ ] **Step 8: Commit**

```bash
git add .planning/adr/0043-skill-only-tools.md docs/ORCHESTRATOR.md .planning/reference/configuration-keys.md docs/scaffolding.md jvagent/scaffold/builtin_profiles/orchestrator.yaml CHANGELOG.md
git commit -m "docs(orchestrator): document skill_only_tools (ADR-0043)"
```

---

### Task 8: Example app smoke check

**Files:**
- Modify: `examples/jvagent_app/agents/jvagent/orchestrator_agent/agent.yaml:258` (next to `denied_tools: []`)

- [ ] **Step 1: Add the key to the example agent**

In `examples/jvagent_app/agents/jvagent/orchestrator_agent/agent.yaml`, next to the existing `denied_tools: []` at `:258`, add at the same indentation:

```yaml
          skill_only_tools: []
```

An empty list is deliberate: the example must keep its current behavior. The key's presence is what documents it for anyone copying the example.

- [ ] **Step 2: Validate the example app**

```bash
jvagent examples/jvagent_app validate
```

Expected: validation passes with no error mentioning `skill_only_tools` (an unknown attribute would be reported here).

- [ ] **Step 3: Bootstrap the example graph**

```bash
jvagent examples/jvagent_app bootstrap
```

Expected: completes without error — this exercises attribute persistence for the new field.

- [ ] **Step 4: Run the full test suite**

```bash
python3 -m pytest tests/ -q --deselect tests/action/pageindex/test_pageindex.py::test_docling_convert_requires_installed_package
echo "EXIT=$?"
```

Expected: `EXIT=0`. The deselect avoids a known libomp SIGABRT on this machine when docling is imported in-process; it is unrelated to this change. Capture the exit status directly — piping to `tail` masks it.

- [ ] **Step 5: Run the commit gate**

```bash
pre-commit run --all-files
```

Expected: all hooks pass with no files modified.

- [ ] **Step 6: Commit**

```bash
git add examples/jvagent_app/agents/jvagent/orchestrator_agent/agent.yaml
git commit -m "chore(examples): surface skill_only_tools in the orchestrator example agent"
```

---

## Self-Review Notes

**Spec coverage:** every spec section maps to a task — §4 config surface → Task 3; §5.1 placement → Task 5; §5.2 owner index → Task 1; §5.3 active predicate → Tasks 1, 5; §5.4 guard wrapper → Tasks 1, 2; §5.5 steer strings → Tasks 1, 2; §5.6 catalog annotation → Task 4; §5.7 composition → Tasks 5, 6; §6 tests → Tasks 1–6; §7 docs → Task 7.

**One deliberate deviation from the spec's test list.** Spec test #11 reads "a channel-blocked owning skill orphans its gated tools on that channel." Verified against the code, that is not what happens: the ADR-0032 cleanup at `orchestrator_interact_action.py:1164-1176` already **removes** a blocked skill's `requires_tools` from `tools` entirely, so gating never sees them. The test in Task 6 asserts the true behavior (`"pay__charge" not in tools`). The outcome the spec wanted — no leak — holds either way. Update the spec's test list to match when this lands.
