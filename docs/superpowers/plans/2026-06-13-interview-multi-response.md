# Interview Multi-Response Extraction + Orchestrator Decoupling — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one user utterance fill multiple interview fields in a single `set_fields` call with strict per-field `pre → validator → post` discipline and incremental branch settlement, slim the tool-return payloads, deliver field metadata once at activation (re-pullable via `get_status`), and remove the two interview-specific references from the orchestrator.

**Architecture:** `interview__set_fields` already iterates fields in definition order through `pre_processor → run_validator → store → post_processor` ([engine.py:702-834](../../../jvagent/action/interview/engine.py)); we tighten it with an incremental reachability gate, replace the returned key-soup with a lean `results`/`pruned`/`ignored`/`response_directive` envelope, and move full field metadata into a single `field_reference` struct delivered at activation and on demand via `interview__get_status`. The orchestrator loses its `interview__set_fields` compound-rule duplicate ([skill_tasks.py:506-512](../../../jvagent/action/orchestrator/skill_tasks.py)) and its `interview__` prep filter ([orchestrator_interact_action.py:2405-2409](../../../jvagent/action/orchestrator/orchestrator_interact_action.py)), both replaced by generic mechanisms.

**Tech Stack:** Python 3, pytest / pytest-asyncio, jvspatial nodes. Lint: black, isort (profile black), flake8, mypy. Spec: [docs/superpowers/specs/2026-06-13-interview-multi-response-design.md](../specs/2026-06-13-interview-multi-response-design.md).

**Status-vocabulary note:** This plan **preserves** the existing `status` values (`active`/`review`/`completed` on success; `error`/`partial_success`/`validation_failed` on failure from `_batch_failure_status`). The slimming is about removing redundant *data* keys, not redefining `status`. Per-field outcome lives in `results` + top-level `ok`.

---

## File structure

| File | Change |
|---|---|
| `jvagent/action/interview/spec.py` | Add `fields_reference(spec)` helper (full ordered `field_def_to_dict` list). |
| `jvagent/action/interview/engine.py` | `field_reference` in activation envelope (`handle_start`); full `field_reference` in `handle_get_status`; incremental reachability gate + slim returns in `handle_set_fields`; slim `handle_next_field`. |
| `jvagent/action/interview/SKILL.md` | SOP reads `field_reference` (not `guidance_page`); confirm compound rule wording. |
| `jvagent/action/orchestrator/skill_tasks.py` | Delete compound-rule duplicate (506-512); tag prep observations with `kind: "server_prep"`. |
| `jvagent/action/orchestrator/orchestrator_interact_action.py` | Generalize `_emit_server_prep_tool_thoughts` to the `server_prep` marker. |
| `examples/.../skills/signup_interview/`, `jvagent/action/interview/examples/example_interview/`, `tests/.../fixtures/skills/*` | Audit + migrate any SOP/test reading dropped keys. |
| `jvagent/action/interview/docs/multi-turn-flow.md`, `docs/thin-harness.md` | Update documented contract. |
| `tests/action/interview/`, `tests/action/orchestrator/` | New + updated tests per task. |

---

### Task 1: `field_reference` serializer helper

**Files:**
- Modify: `jvagent/action/interview/spec.py` (add helper near `field_def_to_dict`, [spec.py:120](../../../jvagent/action/interview/spec.py))
- Test: `tests/action/interview/test_field_reference.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/action/interview/test_field_reference.py
"""field_reference serialization: full ordered field metadata."""

from __future__ import annotations

from jvagent.action.interview.spec import (
    fields_reference,
    load_interview_spec_from_skill,
)
from tests.action.interview.conftest import SIGNUP_INTERVIEW_SKILL_DIR


def test_fields_reference_lists_all_fields_in_order():
    spec = load_interview_spec_from_skill(SIGNUP_INTERVIEW_SKILL_DIR)
    ref = fields_reference(spec)

    assert [f["key"] for f in ref] == spec.field_keys()
    first = ref[0]
    assert "key" in first and "prompt" in first
    # guidance present as a key for every entry (may be empty string)
    assert all("prompt" in entry for entry in ref)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/action/interview/test_field_reference.py -v`
Expected: FAIL with `ImportError: cannot import name 'fields_reference'`

- [ ] **Step 3: Write minimal implementation**

In `jvagent/action/interview/spec.py`, directly after `field_def_to_dict`:

```python
def fields_reference(spec: "InterviewSpec") -> List[Dict[str, Any]]:
    """Full ordered field metadata for the activation reference struct."""
    return [field_def_to_dict(f) for f in spec.fields]
```

Add `fields_reference` to `__all__` if the module defines one.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/action/interview/test_field_reference.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jvagent/action/interview/spec.py tests/action/interview/test_field_reference.py
git commit -m "feat(interview): add fields_reference serializer for activation struct"
```

---

### Task 2: `field_reference` in the activation envelope

**Files:**
- Modify: `jvagent/action/interview/engine.py` — `_session_envelope` inside `handle_start` ([engine.py:1615-1629](../../../jvagent/action/interview/engine.py))
- Test: `tests/action/interview/test_interview_skill_activate.py` (add a test)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/action/interview/test_interview_skill_activate.py
@pytest.mark.asyncio
async def test_activation_includes_full_field_reference(signup_activation_action):
    action, _spec = signup_activation_action
    result = json.loads(await action._handle_start("signup_interview", visitor=_visitor()))

    ref = result["field_reference"]
    assert [f["key"] for f in ref] == _spec.field_keys()
    assert result["start_field"] == ref[0]["key"]
    assert "usage_note" in result
```

> Mirror the fixture/visitor helpers already used in this file (`signup_activation_action`, `_visitor`). If they are named differently, reuse the file's existing activation fixture and a `SimpleNamespace(utterance=...)` visitor.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/action/interview/test_interview_skill_activate.py::test_activation_includes_full_field_reference -v`
Expected: FAIL with `KeyError: 'field_reference'`

- [ ] **Step 3: Write minimal implementation**

In `engine.py`, import the helper (top of file with other `spec` imports):

```python
from .spec import fields_reference  # add to existing spec import block
```

Edit `_session_envelope` ([engine.py:1615](../../../jvagent/action/interview/engine.py)) to add three keys:

```python
    async def _session_envelope(session: InterviewSession, **extra: Any) -> str:
        field_ctx = await _session_field_context_and_record(
            action, session, spec, visitor
        )
        reference = fields_reference(spec)
        awaiting = field_ctx.get("awaiting_fields") or []
        start_field = awaiting[0]["key"] if awaiting else (
            reference[0]["key"] if reference else None
        )
        return interview_tool_response(
            ok=True,
            status=session.status.value,
            interview_type=session.interview_type,
            fields=session.get_collected_summary(),
            skipped_fields=sorted(session.skipped_fields),
            field_reference=reference,
            start_field=start_field,
            usage_note=(
                "field_reference lists every field's prompt and guidance. Later "
                "tool results return only outcomes and directives — re-pull this "
                "via interview__get_status if you lose it."
            ),
            **field_ctx,
            confirm=spec.confirm,
            custom_tools=[f"{spec.name}__{t.name}" for t in spec.skill_tools],
            **extra,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/action/interview/test_interview_skill_activate.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jvagent/action/interview/engine.py tests/action/interview/test_interview_skill_activate.py
git commit -m "feat(interview): deliver full field_reference in activation envelope"
```

---

### Task 3: `interview__get_status` returns full `field_reference` on demand

**Files:**
- Modify: `jvagent/action/interview/engine.py` — `handle_get_status` ([engine.py:1567-1589](../../../jvagent/action/interview/engine.py))
- Test: `tests/action/interview/test_interview_skill_activate.py` or a new `test_get_status_reference.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/action/interview/test_get_status_reference.py
"""get_status is the on-demand pull path for field_reference."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview.interview_action import InterviewAction
from jvagent.action.interview.session import InterviewSession
from jvagent.action.interview.spec import load_interview_spec_from_skill
from tests.action.interview.conftest import (
    ORCHESTRATOR_AGENT_DIR,
    SIGNUP_INTERVIEW_SKILL_DIR,
)


@pytest.mark.asyncio
async def test_get_status_returns_full_field_reference():
    action = InterviewAction(metadata={"agent_dir": str(ORCHESTRATOR_AGENT_DIR)})
    spec = load_interview_spec_from_skill(SIGNUP_INTERVIEW_SKILL_DIR)
    action._registry._specs[spec.name] = spec
    session = InterviewSession(interview_type="signup_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))

    result = json.loads(
        await action._handle_get_status(visitor=SimpleNamespace(utterance=""))
    )
    ref = result["field_reference"]
    assert [f["key"] for f in ref] == spec.field_keys()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/action/interview/test_get_status_reference.py -v`
Expected: FAIL with `KeyError: 'field_reference'`

- [ ] **Step 3: Write minimal implementation**

In `handle_get_status`, build the full reference unconditionally and pass it. Replace the `return interview_tool_response(...)` at [engine.py:1567](../../../jvagent/action/interview/engine.py) so it adds `field_reference=fields_reference(spec) if spec else None,` alongside the existing keys. Keep the existing paginated `field_definitions` for back-compat:

```python
    return interview_tool_response(
        ok=True,
        status=session.status.value,
        interview_type=session.interview_type,
        fields=session.get_collected_summary(),
        skipped_fields=sorted(session.skipped_fields),
        started_at=session.started_at,
        field_reference=fields_reference(spec) if spec else None,
        **field_ctx,
        field_definitions=definitions,
        field_definitions_total=definitions_total,
        field_definitions_offset=(
            max(0, int(definition_offset or 0)) if definitions is not None else None
        ),
        field_definitions_limit=(
            max(1, min(int(definition_limit or 50), 500))
            if definitions is not None
            else None
        ),
        confirm=spec.confirm if spec else None,
        custom_tools=(
            [f"{spec.name}__{t.name}" for t in spec.skill_tools] if spec else None
        ),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/action/interview/test_get_status_reference.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jvagent/action/interview/engine.py tests/action/interview/test_get_status_reference.py
git commit -m "feat(interview): get_status returns full field_reference for on-demand pull"
```

---

### Task 4: Slim the `set_fields` success / failure return

**Files:**
- Modify: `jvagent/action/interview/engine.py` — payload assembly ([engine.py:923-985](../../../jvagent/action/interview/engine.py)); add `error_code` to the validation-failure `results` entry ([engine.py:776-778](../../../jvagent/action/interview/engine.py)); add `error_code` to `_compact_field_updates` ([engine.py:361-378](../../../jvagent/action/interview/engine.py))
- Test: `tests/action/interview/test_set_fields.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/action/interview/test_set_fields.py
@pytest.mark.asyncio
async def test_set_fields_return_is_slim(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    visitor = SimpleNamespace(utterance="Jane Doe")
    result = json.loads(
        await action._handle_set_fields(
            fields={"user_name": "Jane Doe"}, visitor=visitor
        )
    )

    assert result["ok"] is True
    assert result["results"][0] == {
        "field": "user_name",
        "ok": True,
        "stored": True,
        "value": "Jane Doe",
    }
    # dropped redundant/data keys
    for gone in ("field_updates", "stored_fields", "fields_delta", "failed_fields",
                 "awaiting_fields", "field_keys", "guidance_page"):
        assert gone not in result
    assert "response_directive" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/action/interview/test_set_fields.py::test_set_fields_return_is_slim -v`
Expected: FAIL — `field_updates` present / `results` missing

- [ ] **Step 3: Write minimal implementation**

3a. Set `error_code` on the in-loop validation-failure entry. At [engine.py:776](../../../jvagent/action/interview/engine.py), after `entry["error"] = err`, add:

```python
            entry["error_code"] = "VALIDATION_FAILED"
```

3b. Carry `error_code` through compaction. In `_compact_field_updates` ([engine.py:373](../../../jvagent/action/interview/engine.py)), after the `error` block add:

```python
        if item.get("error_code"):
            entry["error_code"] = item.get("error_code")
```

3c. Replace the payload-assembly block ([engine.py:923-985](../../../jvagent/action/interview/engine.py), from `updates = _compact_field_updates(results)` through the final `return`) with:

```python
    updates = _compact_field_updates(results)

    payload: Dict[str, Any] = {
        "ok": not failures,
        "status": session.status.value,
        "results": updates,
    }
    if pruned_all:
        payload["pruned"] = pruned_all
    if ignored_fields:
        payload["ignored"] = sorted(ignored_fields)

    if failures:
        first_failure = failures[0]
        payload["status"] = _batch_failure_status(failures, stored_any=stored_any)
        payload["response_directive"] = _compose_directives(
            directive_queue, fallback=_batch_failure_directive(failures)
        )
        system_message = _compose_system_message(
            system_queue,
            fallback=str(first_failure.get("system_message") or "").strip(),
        )
        if system_message:
            payload["system_message"] = system_message
    else:
        directive, next_tool = await _chain_hint(action, session, spec, visitor)
        fallback = str(post_outcome.get("response_directive") or directive)
        payload["response_directive"] = _compose_directives(
            directive_queue, fallback=fallback
        )
        payload["next_tool"] = post_outcome.get("next_tool", next_tool)
        system_message = _compose_system_message(
            system_queue,
            fallback=str(post_outcome.get("system_message") or "").strip(),
        )
        if system_message:
            payload["system_message"] = system_message

    ok = payload.pop("ok")
    status = payload.pop("status")
    return interview_tool_response(ok=ok, status=status, **payload)
```

> This drops `stored_fields`, `fields_delta`, the single-field `field`/`stored`/`value` convenience, and `failed_fields` (failures already appear as entries in `results`). Renames `pruned_fields`→`pruned`, `ignored_fields`→`ignored`.

- [ ] **Step 4: Run test + the existing set_fields slice**

Run: `pytest tests/action/interview/test_set_fields.py tests/action/interview/test_interview_set_field_validation.py -v`
Expected: the new test PASSES; fix any now-stale assertions in the existing tests that referenced `field_updates`/`stored_fields`/`fields_delta`/`failed_fields` by renaming to `results`/`pruned`/`ignored`.

- [ ] **Step 5: Commit**

```bash
git add jvagent/action/interview/engine.py tests/action/interview/test_set_fields.py tests/action/interview/test_interview_set_field_validation.py
git commit -m "refactor(interview): slim set_fields return to results/pruned/ignored/directive"
```

---

### Task 5: Slim the `set_fields` completion return

**Files:**
- Modify: `jvagent/action/interview/engine.py` — completion return ([engine.py:912-921](../../../jvagent/action/interview/engine.py))
- Test: `tests/action/interview/test_signup_complete.py` (or `test_set_fields.py`)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/action/interview/test_set_fields.py
@pytest.mark.asyncio
async def test_set_fields_completion_return_is_slim(signup_action, monkeypatch):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()
    action._clear_interview_session = AsyncMock()

    # Force completion: stub run_validator to flag interview_complete on the field.
    from jvagent.action.interview import engine as eng

    async def _complete_validator(*a, **k):
        return {"valid": True, "value": "x", "interview_complete": True}

    monkeypatch.setattr(eng, "run_validator", _complete_validator)

    result = json.loads(
        await action._handle_set_fields(
            fields={spec.field_keys()[0]: "x"}, visitor=SimpleNamespace(utterance="x")
        )
    )

    assert result["status"] == "completed"
    assert result["interview_complete"] is True
    assert "results" in result
    assert "fields" not in result  # no full collected map in the terminal return
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/action/interview/test_set_fields.py::test_set_fields_completion_return_is_slim -v`
Expected: FAIL — `fields` still present / `results` missing

- [ ] **Step 3: Write minimal implementation**

Replace the completion `return` block ([engine.py:912-921](../../../jvagent/action/interview/engine.py)):

```python
        return interview_tool_response(
            ok=True,
            status="completed",
            interview_complete=True,
            results=_compact_field_updates(results),
            pruned=pruned_all or None,
            response_directive=directive,
            system_message=system_message,
        )
```

> Drops `field_updates` (→ `results`), `fields` (full collected map), and renames `pruned_fields`→`pruned`. The completion handler still owns any closing data via its own return.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/action/interview/test_set_fields.py tests/action/interview/test_signup_complete.py -v`
Expected: PASS (fix stale `fields`/`field_updates` assertions in `test_signup_complete.py` if any).

- [ ] **Step 5: Commit**

```bash
git add jvagent/action/interview/engine.py tests/action/interview/test_set_fields.py tests/action/interview/test_signup_complete.py
git commit -m "refactor(interview): slim set_fields completion return"
```

---

### Task 6: Incremental branch settlement in the `set_fields` loop

**Files:**
- Modify: `jvagent/action/interview/engine.py` — add a reachability gate inside the batch loop, after `fdef` resolution ([engine.py:727](../../../jvagent/action/interview/engine.py), before the `entry` dict)
- Test: `tests/action/interview/test_set_fields.py`

**Why:** Today reachability is computed once *after* the loop ([engine.py:836-840](../../../jvagent/action/interview/engine.py)), so a field that an earlier-in-call answer makes unreachable still runs its validator and **post_processor side effects** before being pruned. Gating per-iteration prevents that while keeping the same stored result.

- [ ] **Step 1: Write the failing test**

> Uses the signup branching fixture. If `signup_interview` lacks a branch that excludes a later field, use the branching fixture skill referenced by `test_signup_branching.py` / `test_interview_branching.py` and pick a determinant→excluded pair from it. Pseudocode with the real keys substituted:

```python
# append to tests/action/interview/test_interview_branching.py
@pytest.mark.asyncio
async def test_set_fields_skips_field_unreachable_by_earlier_value(branching_action):
    action, spec = branching_action
    session = make_session(spec)  # reuse this file's session builder
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    # DETERMINANT answer routes AWAY from EXCLUDED; both submitted together.
    result = json.loads(
        await action._handle_set_fields(
            fields={DETERMINANT: ROUTE_AWAY_VALUE, EXCLUDED: "should-not-store"},
            visitor=SimpleNamespace(utterance="..."),
        )
    )

    assert session.get_value(EXCLUDED) is None          # never stored
    assert EXCLUDED in result.get("ignored", [])         # reported as ignored
    entry = next(e for e in result["results"] if e["field"] == EXCLUDED)
    assert entry.get("ignored") is True and entry["stored"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/action/interview/test_interview_branching.py::test_set_fields_skips_field_unreachable_by_earlier_value -v`
Expected: FAIL — `EXCLUDED` is stored (current code stores then prunes after the loop, so `session.get_value(EXCLUDED)` is None *after* prune, but its post_processor ran and `ignored`/`results` ordering differs). Confirm the failing assertion is the `ignored`/entry-level one.

- [ ] **Step 3: Write minimal implementation**

Insert a reachability gate at the top of the loop body, immediately after the `if not fdef:` block closes ([engine.py:727](../../../jvagent/action/interview/engine.py), before `entry: Dict[str, Any] = {`):

```python
        reachable_now = await compute_active_path_for_prune(
            session, spec, load_fn, visitor, action
        )
        if reachable_now and fname not in set(reachable_now):
            results.append(
                {"field": fname, "ok": True, "stored": False, "ignored": True}
            )
            continue
```

> `compute_active_path_for_prune` is already imported and used after the loop. Because fields iterate in definition order, a branch-determining field is processed (and stored) before any field it gates, so this gate sees the settled path. The after-loop prune ([engine.py:836-880](../../../jvagent/action/interview/engine.py)) remains as the backstop for fields gated by determinants answered in prior turns.

- [ ] **Step 4: Run test + the branching slice**

Run: `pytest tests/action/interview/test_interview_branching.py tests/action/interview/test_signup_branching.py tests/action/interview/test_branch_path_invalidation.py -v`
Expected: PASS. If a previously-passing test asserted that an off-branch field was *stored-then-pruned*, update it to expect `ignored`.

- [ ] **Step 5: Commit**

```bash
git add jvagent/action/interview/engine.py tests/action/interview/test_interview_branching.py
git commit -m "feat(interview): incremental branch settlement skips unreachable fields in set_fields"
```

---

### Task 7: Slim the `next_field` return

**Files:**
- Modify: `jvagent/action/interview/engine.py` — `handle_next_field` ([engine.py:1048-1098](../../../jvagent/action/interview/engine.py))
- Test: `tests/action/interview/test_interview_next_field.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/action/interview/test_interview_next_field.py
@pytest.mark.asyncio
async def test_next_field_return_is_slim(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_next_field(visitor=SimpleNamespace(utterance=""))
    )

    nf = result["next_field"]
    assert "key" in nf and "prompt" in nf
    assert "guidance" not in nf and "required" not in nf   # pulled from field_reference
    for gone in ("awaiting_fields", "field_keys", "guidance_page",
                 "active_path_keys", "fields"):
        assert gone not in result
    assert "response_directive" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/action/interview/test_interview_next_field.py::test_next_field_return_is_slim -v`
Expected: FAIL — `awaiting_fields` present / `fields` present

- [ ] **Step 3: Write minimal implementation**

Rewrite `handle_next_field` to drop `field_ctx`, `fields`, `skipped_fields` and slim the `next_field` object. Replace the body from `field_ctx = ...` ([engine.py:1054](../../../jvagent/action/interview/engine.py)) through the final `return`:

```python
    load_fn = action._load_fn(spec)
    next_field = await build_next_field(session, spec, load_fn, visitor, action)

    if not next_field:
        return interview_tool_response(
            ok=True,
            status=session.status.value,
            next_tool="interview__review",
            response_directive=call_tool_directive("interview__review"),
        )

    fdef = spec.get_field(next_field["key"])
    directive, extras = await run_pre_processors(action, session, spec, fdef, visitor)
    pre_tools_results = extras.get("pre_tools_results") or []
    if any(not r.get("ok", True) for r in pre_tools_results):
        return interview_tool_response(
            ok=False,
            status="error",
            error="One or more pre_processor hooks failed.",
            next_field={"key": next_field["key"], "prompt": next_field.get("prompt")},
            pre_tools_results=pre_tools_results,
        )

    slim_next = {"key": next_field["key"], "prompt": next_field.get("prompt")}
    if extras.get("suggested_value") is not None:
        slim_next["suggested_value"] = extras["suggested_value"]

    if pre_tools_results:
        await action._save_session(session, visitor)

    return interview_tool_response(
        ok=True,
        status=session.status.value,
        next_field=slim_next,
        pre_tools_results=pre_tools_results or None,
        response_directive=directive,
    )
```

- [ ] **Step 4: Run test + next_field slice**

Run: `pytest tests/action/interview/test_interview_next_field.py tests/action/interview/test_awaiting_fields.py tests/action/interview/test_build_next_field.py -v`
Expected: PASS. `test_awaiting_fields.py` likely asserted `awaiting_fields` on the `next_field` return — repoint it to activation (`_handle_start`) or `get_status`, which still carry `awaiting_fields` via `field_ctx`.

- [ ] **Step 5: Commit**

```bash
git add jvagent/action/interview/engine.py tests/action/interview/test_interview_next_field.py tests/action/interview/test_awaiting_fields.py
git commit -m "refactor(interview): slim next_field return to key+prompt+directive"
```

---

### Task 8: Base SOP reads `field_reference`

**Files:**
- Modify: `jvagent/action/interview/SKILL.md` — context contract + activation steps + extraction pass ([SKILL.md:23-55](../../../jvagent/action/interview/SKILL.md))
- Test: `tests/action/interview/test_interview_procedure.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/action/interview/test_interview_procedure.py
def test_base_sop_references_field_reference():
    from jvagent.action.interview.procedure import get_standard_interview_procedure

    body = get_standard_interview_procedure()
    assert "field_reference" in body
    # compound rule still present (single source of truth, post orchestrator removal)
    assert "one initial interview__set_fields" in body or "one complete fields map" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/action/interview/test_interview_procedure.py::test_base_sop_references_field_reference -v`
Expected: FAIL — `field_reference` not in body

- [ ] **Step 3: Edit the SOP**

In `jvagent/action/interview/SKILL.md`:

- Replace the **Context contract** paragraph ([SKILL.md:23](../../../jvagent/action/interview/SKILL.md)) with:

```markdown
Context contract: activation via `use_skill` is the rich context snapshot — `field_reference` (every field's `key`, `prompt`, `guidance`, `required`, `branches`), `awaiting_fields`, `field_keys`, and `start_field`. `interview__set_fields` and `interview__next_field` responses are compact (results + directive, key + prompt); they do not repeat field metadata. Re-pull `field_reference` any time with `interview__get_status`.
```

- In the **Activation** step (item 2, [SKILL.md:31](../../../jvagent/action/interview/SKILL.md)), change "Read `awaiting_fields`, `field_keys`, and `guidance_page`" to "Read `field_reference`, `awaiting_fields`, and `field_keys`".

- In the **Extraction pass** ([SKILL.md:48-55](../../../jvagent/action/interview/SKILL.md)), change the key-source line to: "Map all confident values to canonical keys from `field_reference[].key` (preferred), then `awaiting_fields[].key`, then `field_keys`." Remove `guidance_page` references. Keep the "Submit one initial `interview__set_fields` call containing every extracted key/value" sentence (this is now the *only* home of the compound rule).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/action/interview/test_interview_procedure.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jvagent/action/interview/SKILL.md tests/action/interview/test_interview_procedure.py
git commit -m "docs(interview): base SOP reads field_reference; sole home of compound rule"
```

---

### Task 9: Orchestrator C1 — delete the compound-rule duplicate

**Files:**
- Modify: `jvagent/action/orchestrator/skill_tasks.py` — `task_lock_section_text` ([skill_tasks.py:506-512](../../../jvagent/action/orchestrator/skill_tasks.py))
- Test: `tests/action/orchestrator/test_apply_task_lock_turn.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/action/orchestrator/test_apply_task_lock_turn.py
def test_task_lock_section_has_no_interview_compound_rule():
    from jvagent.action.orchestrator.skill_tasks import task_lock_section_text

    doc = SimpleNamespace(
        name="signup_interview",
        body="PROCEDURE BODY",
        requires_tools=("interview__set_fields", "reply"),
    )
    text = task_lock_section_text(doc)
    assert "Compound extraction rule" not in text
    assert "interview__set_fields" not in text  # no interview literal in orchestrator
```

> Import `SimpleNamespace` at the top of the test file if not already present.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/action/orchestrator/test_apply_task_lock_turn.py::test_task_lock_section_has_no_interview_compound_rule -v`
Expected: FAIL — `Compound extraction rule` present

- [ ] **Step 3: Delete the block**

In `task_lock_section_text` ([skill_tasks.py:506-512](../../../jvagent/action/orchestrator/skill_tasks.py)) remove:

```python
    required_tools = set(getattr(skill_doc, "requires_tools", ()) or ())
    if "interview__set_fields" in required_tools:
        header += (
            "Compound extraction rule: for one user utterance, submit one initial "
            "interview__set_fields call with all confident key/value pairs from that "
            "utterance.\n"
        )
```

The function becomes header + `pending_directive` + `PROCEDURE:\n{skill_doc.body}`.

- [ ] **Step 4: Run test + orchestrator task-lock slice**

Run: `pytest tests/action/orchestrator/test_apply_task_lock_turn.py tests/action/orchestrator/test_use_skill_task_lock_prep.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jvagent/action/orchestrator/skill_tasks.py tests/action/orchestrator/test_apply_task_lock_turn.py
git commit -m "refactor(orchestrator): drop interview__set_fields compound-rule duplicate"
```

---

### Task 10: Orchestrator C2 — generic `server_prep` marker for prep visualization

**Files:**
- Modify: `jvagent/action/orchestrator/orchestrator_interact_action.py` — `_emit_server_prep_tool_thoughts` ([orchestrator_interact_action.py:2405-2409](../../../jvagent/action/orchestrator/orchestrator_interact_action.py))
- Modify: `jvagent/action/orchestrator/skill_tasks.py` — tag the session-note observation ([skill_tasks.py:616-622](../../../jvagent/action/orchestrator/skill_tasks.py)) and bound-action prep observations ([skill_tasks.py:637-638](../../../jvagent/action/orchestrator/skill_tasks.py)) with `kind: "server_prep"`
- Test: `tests/action/orchestrator/test_server_prep_thoughts.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/action/orchestrator/test_server_prep_thoughts.py
"""Server-prep visualization keys off a generic marker, not interview__."""

from __future__ import annotations

from jvagent.action.orchestrator import skill_tasks


def test_session_note_observation_carries_server_prep_marker():
    obs = []
    skill_tasks._append_session_note(obs, "bootstrap note")  # helper added below
    assert obs[-1]["kind"] == "server_prep"


def test_emitter_filters_on_marker(monkeypatch):
    # The emitter must select by kind == "server_prep", not by tool name prefix.
    import inspect
    from jvagent.action.orchestrator.orchestrator_interact_action import (
        OrchestratorInteractAction,
    )

    src = inspect.getsource(OrchestratorInteractAction._emit_server_prep_tool_thoughts)
    assert 'server_prep' in src
    assert 'interview__' not in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/action/orchestrator/test_server_prep_thoughts.py -v`
Expected: FAIL — `_append_session_note` missing / `interview__` still in emitter source

- [ ] **Step 3: Implement marker + generic filter**

3a. In `skill_tasks.py`, add a small helper and use it. Replace the session-note append ([skill_tasks.py:616-622](../../../jvagent/action/orchestrator/skill_tasks.py)):

```python
def _append_session_note(observations: List[Dict[str, Any]], note: str) -> None:
    observations.append(
        {
            "tool": "(skill-session)",
            "args": {},
            "observation": note,
            "kind": "server_prep",
        }
    )
```

and at the call site:

```python
    if note:
        _append_session_note(observations, note)
```

3b. Tag bound-action prep observations. After `observations.extend(prep.observations)` ([skill_tasks.py:638](../../../jvagent/action/orchestrator/skill_tasks.py)) ensure each carries the marker:

```python
            if prep.observations:
                for ob in prep.observations:
                    ob.setdefault("kind", "server_prep")
                observations.extend(prep.observations)
```

3c. Generalize the emitter ([orchestrator_interact_action.py:2405-2409](../../../jvagent/action/orchestrator/orchestrator_interact_action.py)):

```python
        """Surface server-injected skill prep in the TOOL CALLS panel."""
        for entry in observations[since_index:]:
            if entry.get("kind") != "server_prep":
                continue
            tool = str(entry.get("tool") or "(skill-prep)")
```

(Keep the rest of the loop body unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/action/orchestrator/test_server_prep_thoughts.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jvagent/action/orchestrator/skill_tasks.py jvagent/action/orchestrator/orchestrator_interact_action.py tests/action/orchestrator/test_server_prep_thoughts.py
git commit -m "refactor(orchestrator): generic server_prep marker replaces interview__ prep filter"
```

---

### Task 11: Decoupling grep-guard test

**Files:**
- Test: `tests/action/orchestrator/test_no_interview_coupling.py` (create)

- [ ] **Step 1: Write the test (acceptance gate)**

```python
# tests/action/orchestrator/test_no_interview_coupling.py
"""Guard: the orchestrator carries no interview-specific literals."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ORCH = Path(__file__).resolve().parents[3] / "jvagent/action/orchestrator"
_PATTERN = re.compile(r"interview__|interview_action|set_field")


@pytest.mark.parametrize("path", sorted(_ORCH.glob("*.py")))
def test_orchestrator_module_has_no_interview_literals(path):
    text = path.read_text(encoding="utf-8")
    offenders = [
        f"{path.name}:{i}: {line.strip()}"
        for i, line in enumerate(text.splitlines(), 1)
        if _PATTERN.search(line) and not line.lstrip().startswith("#")
    ]
    assert not offenders, "interview coupling remains:\n" + "\n".join(offenders)
```

- [ ] **Step 2: Run it**

Run: `pytest tests/action/orchestrator/test_no_interview_coupling.py -v`
Expected: PASS (Tasks 9–10 removed the only two literals). If it fails, the printed `offenders` name the exact remaining line — remove or genericize it.

- [ ] **Step 3: Commit**

```bash
git add tests/action/orchestrator/test_no_interview_coupling.py
git commit -m "test(orchestrator): guard against interview-specific coupling"
```

---

### Task 12: Audit consuming skills, fixtures, and docs

**Files:**
- Inspect/modify: `examples/jvagent_app/agents/jvagent/orchestrator_agent/skills/signup_interview/SKILL.md`, `jvagent/action/interview/examples/example_interview/SKILL.md`, `tests/action/interview/fixtures/skills/onboarding_interview/SKILL.md`, `tests/action/interview/fixtures/skills/pre_alert_interview/SKILL.md`
- Modify: `jvagent/action/interview/docs/multi-turn-flow.md`, `docs/thin-harness.md`, `jvagent/action/interview/docs/thin-harness.md`

- [ ] **Step 1: Find SOP/test references to dropped keys**

Run:
```bash
grep -rnE 'field_updates|stored_fields|fields_delta|failed_fields|guidance_page|pruned_fields|ignored_fields' \
  jvagent/action/interview/examples examples/jvagent_app tests/action/interview/fixtures \
  jvagent/action/interview/docs docs/thin-harness.md
```
Expected: a list of doc/SOP lines. Each is a migration target.

- [ ] **Step 2: Migrate each hit**

For every match: rename `field_updates`→`results`, `pruned_fields`→`pruned`, `ignored_fields`→`ignored`; replace `guidance_page`/`stored_fields`/`fields_delta`/`failed_fields` guidance with `field_reference` (activation) + `results` (per-call). Edit the prose to match the new contract from the spec §4.

- [ ] **Step 3: Update the contract docs**

In `jvagent/action/interview/docs/multi-turn-flow.md`, update the Turn-N and Activation sections to describe: activation ships `field_reference`; `set_fields` returns `results`/`pruned`/`ignored`/`response_directive`; `next_field` returns `{key, prompt}`; on-demand re-pull via `get_status`. In both `thin-harness.md` files, confirm the "no prep observations" invariant is restated and note that `field_reference` recovery is model-pull (no server push).

- [ ] **Step 4: Run the full interview + orchestrator suites**

Run: `pytest tests/action/interview/ tests/action/orchestrator/ -v`
Expected: green. Fix any remaining stale assertions.

- [ ] **Step 5: Commit**

```bash
git add jvagent/action/interview/examples examples/jvagent_app tests/action/interview/fixtures jvagent/action/interview/docs docs/thin-harness.md
git commit -m "docs(interview): migrate skills/fixtures/docs to slim multi-response contract"
```

---

### Task 13: Full verification pass

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite**

Run: `pytest tests/ -q`
Expected: all pass. Address failures at their source (do not weaken assertions to pass).

- [ ] **Step 2: Lint + type**

Run: `pre-commit run --all-files`
Expected: black/isort/flake8/mypy clean. Fix reported issues.

- [ ] **Step 3: Manual contract smoke (optional but recommended)**

Run: `jvagent examples/jvagent_app validate`
Expected: app + agents validate. Then exercise the signup interview locally per [.planning/runbooks/local-dev.md](../../../.planning/runbooks/local-dev.md) and confirm a compound utterance ("Jane Doe, jane@example.com") fills both fields in one `set_fields` call.

- [ ] **Step 4: Commit any fixups**

```bash
git add -A
git commit -m "chore(interview): verification fixups for multi-response contract"
```

---

## Self-review

- **Spec coverage:** §4.1 field_reference → Tasks 1,2,3,8. §4.2 slim set_fields → Tasks 4,5. §4.3 slim next_field → Task 7. §5 per-response discipline / order-store-settle-report → Task 6 (incremental settlement) on top of the existing ordered loop. §6 C1 → Task 9; §6 C2 → Task 10; §6 grep gate → Task 11. §8 risks (SOP/fixture audit, pull recovery) → Tasks 8,12. §9 acceptance → Task 13. All spec sections map to a task.
- **Placeholder scan:** Task 6's test uses `DETERMINANT`/`EXCLUDED`/`ROUTE_AWAY_VALUE` placeholders **intentionally** — the implementer substitutes the real branch keys from the branching fixture (the task says so explicitly); the implementation code is concrete. No other placeholders.
- **Type consistency:** `fields_reference(spec)` (Task 1) is the single serializer reused in Tasks 2 (`handle_start`) and 3 (`handle_get_status`). Return key names are consistent: `results`, `pruned`, `ignored`, `next_field={key,prompt}`, `field_reference`, `start_field`. `_compact_field_updates` gains `error_code` (Task 4) and is reused by the completion return (Task 5). `_append_session_note` (Task 10) is defined before its call site.
