# Orchestrator Egress Streamline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One user-facing emission per turn per channel — eliminate adapter duplicate responses by adding a single authoritative `emitted` latch, collapsing the orchestrator's four emission points into one egress authority, and making the response bus the sole delivery (`interaction.response` = persistence only).

**Architecture:** A persisted `Interaction.emitted` latch is set at the delivery choke points (`response_bus._deliver_flush` for `user` content, the first SSE `stream_chunk`, and `ReplyAction._pipe_response` no-bus branch). The orchestrator's post-loop re-emission paths gate on `interaction.emitted` instead of `interaction.response`-emptiness, and all four emission points route through one `_egress()` method that emits exactly once, composing the model's reply + queued `interaction.directives` in a single ReplyAction compose.

**Tech Stack:** Python 3, pytest/pytest-asyncio, jvspatial Node attributes. Spec: [.planning/specs/2026-06-13-orchestrator-egress-streamline-design.md](../specs/2026-06-13-orchestrator-egress-streamline-design.md). Run tests with `.venv/bin/python -m pytest`.

---

## File structure

| File | Responsibility / change |
|---|---|
| `jvagent/memory/interaction.py` | New `emitted` attribute + `mark_emitted()` + `has_emitted()`; the per-turn egress latch. |
| `jvagent/action/response/response_bus.py` | Set the latch where `user` content is actually delivered (`_deliver_flush`; first SSE `stream_chunk`). |
| `jvagent/action/reply/reply_action.py` | Set the latch in `_pipe_response` no-bus branch (the only delivery path with no bus). |
| `jvagent/action/orchestrator/orchestrator_interact_action.py` | Single `_egress()` authority; gate `_finalize_directives` / fallback / `_maybe_emit_final` on `interaction.emitted`. |
| `jvagent/action/whatsapp/`, `facebook_action/`, `email_action/` | Audit: confirm no post-walk re-send of `interaction.response` (read-only check; fix only if found). |
| `.planning/adr/0025-single-per-turn-egress.md` | New ADR recording the contract. |
| `tests/action/orchestrator/`, `tests/memory/`, `tests/action/response/` | Egress-contract + latch tests. |

**Phasing:** Phase 0 (Tasks 1-4) = the latch → kills the duplicate, shippable alone. Phase 1-2 (Tasks 5-6) = single egress authority + unified compose. Phase 3 (Task 7) = audit + ADR. Phase 4 (Task 8) = full verification.

---

### Task 1: `Interaction.emitted` latch

**Files:**
- Modify: `jvagent/memory/interaction.py` (attribute near `closed` ~line 170; methods near `set_response` ~line 388)
- Test: `tests/memory/test_interaction_emitted.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_interaction_emitted.py
"""Interaction.emitted — the per-turn egress latch."""

from __future__ import annotations

from jvagent.memory.interaction import Interaction


def test_emitted_defaults_false_and_latches():
    i = Interaction()
    assert i.has_emitted() is False
    assert i.mark_emitted() is True   # first call latches
    assert i.has_emitted() is True
    assert i.mark_emitted() is False  # idempotent: already latched
    assert i.has_emitted() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/memory/test_interaction_emitted.py -q`
Expected: FAIL — `AttributeError: 'Interaction' object has no attribute 'has_emitted'`

- [ ] **Step 3: Implement**

In `interaction.py`, add the attribute after `closed` (~line 172):

```python
    emitted: bool = attribute(
        default=False,
        description=(
            "Per-turn egress latch: True once a user-facing message has been "
            "delivered this turn. Gates all re-emission paths so each turn sends "
            "exactly one reply per channel."
        ),
    )
```

Add methods after `set_response` (~line 400):

```python
    def has_emitted(self) -> bool:
        """True if a user-facing message has already been delivered this turn."""
        return bool(self.emitted)

    def mark_emitted(self) -> bool:
        """Latch the egress flag. Returns True if it changed (first emission)."""
        if self.emitted:
            return False
        self.emitted = True
        return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/memory/test_interaction_emitted.py -q`
Expected: PASS

- [ ] **Step 5: Commit** — SKIP (no commits this session; leave in working tree).

---

### Task 2: latch on bus delivery (`_deliver_flush` + first SSE chunk)

**Files:**
- Modify: `jvagent/action/response/response_bus.py` — `_deliver_flush` ([:318-328](../../jvagent/action/response/response_bus.py)); the streaming chunk loop ([:398-415](../../jvagent/action/response/response_bus.py))
- Test: `tests/action/response/test_emitted_latch.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/action/response/test_emitted_latch.py
"""Bus delivery latches interaction.emitted for user content only."""

from __future__ import annotations

import pytest

from jvagent.action.response.response_bus import ResponseBus
from jvagent.memory.interaction import Interaction


@pytest.mark.asyncio
async def test_user_publish_latches_emitted():
    bus = ResponseBus()
    interaction = Interaction()
    await bus.publish(
        session_id="s1",
        content="hello",
        channel="default",
        interaction=interaction,
        interaction_id="i1",
        category="user",
    )
    assert interaction.has_emitted() is True


@pytest.mark.asyncio
async def test_thought_publish_does_not_latch():
    bus = ResponseBus()
    interaction = Interaction()
    await bus.publish(
        session_id="s1",
        content="(thinking)",
        channel="default",
        interaction=interaction,
        interaction_id="i1",
        category="thought",
        thought_type="reasoning",
    )
    assert interaction.has_emitted() is False


@pytest.mark.asyncio
async def test_transient_user_publish_does_not_latch():
    bus = ResponseBus()
    interaction = Interaction()
    await bus.publish(
        session_id="s1",
        content="typing...",
        channel="default",
        interaction=interaction,
        interaction_id="i1",
        category="user",
        transient=True,
    )
    assert interaction.has_emitted() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/action/response/test_emitted_latch.py -q`
Expected: FAIL — `assert False is True` (latch not set).

- [ ] **Step 3: Implement**

In `_deliver_flush` ([response_bus.py:318](../../jvagent/action/response/response_bus.py)), the existing `user`-category append block — add the latch alongside it:

```python
            if (
                flush_message.category == "user"
                and interaction is not None
                and full_content
                and not deliver_transient
            ):
                if hasattr(interaction, "mark_emitted"):
                    interaction.mark_emitted()
                await self._append_to_interaction_response_impl(
                    interaction=interaction,
                    message_type="adhoc",
                    content=full_content,
                )
```

In the streaming chunk loop ([response_bus.py:398-415](../../jvagent/action/response/response_bus.py)) — latch on the **first delivered** user chunk (honors "first delivered chunk"). Inside the `for chunk in chunk_text_by_lm_tokens(content):` loop, after `acc.chunks.append(chunk)`:

```python
                if (
                    message_category == "user"
                    and not transient
                    and interaction is not None
                    and chunk
                    and hasattr(interaction, "mark_emitted")
                ):
                    interaction.mark_emitted()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/action/response/test_emitted_latch.py -q`
Expected: PASS

- [ ] **Step 5: Commit** — SKIP.

---

### Task 3: latch on the no-bus reply path

**Files:**
- Modify: `jvagent/action/reply/reply_action.py` — `_pipe_response` no-bus branch ([:287-297](../../jvagent/action/reply/reply_action.py))
- Test: `tests/action/test_reply_emitted_latch.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/action/test_reply_emitted_latch.py
"""ReplyAction no-bus publish latches interaction.emitted."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jvagent.action.reply.reply_action import ReplyAction
from jvagent.memory.interaction import Interaction


@pytest.mark.asyncio
async def test_no_bus_publish_latches_emitted():
    action = ReplyAction()
    interaction = Interaction()
    visitor = SimpleNamespace(interaction=interaction, response_bus=None, session_id=None)
    await action.publish("hello there", visitor)
    assert interaction.has_emitted() is True
    assert interaction.response == "hello there"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/action/test_reply_emitted_latch.py -q`
Expected: FAIL — `assert False is True`

- [ ] **Step 3: Implement**

In `_pipe_response` no-bus branch ([reply_action.py:287](../../jvagent/action/reply/reply_action.py)), after the `set_response` block sets the response, latch it:

```python
        if not has_bus:
            if transient or interaction is None:
                return True
            current = interaction.response or ""
            if current and current.strip() and current != content:
                changed = interaction.set_response(f"{current}\n\n{content}")
            else:
                changed = interaction.set_response(content)
            if hasattr(interaction, "mark_emitted"):
                interaction.mark_emitted()
            if changed:
                await interaction.save()
            return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/action/test_reply_emitted_latch.py -q`
Expected: PASS

- [ ] **Step 5: Commit** — SKIP.

---

### Task 4: orchestrator gates re-emission on `emitted` (Phase 0 complete)

**Files:**
- Modify: `jvagent/action/orchestrator/orchestrator_interact_action.py` — `execute()` fallback ([:635-637](../../jvagent/action/orchestrator/orchestrator_interact_action.py)); `_finalize_directives` ([:669](../../jvagent/action/orchestrator/orchestrator_interact_action.py)); `_maybe_emit_final` ([:1656+](../../jvagent/action/orchestrator/orchestrator_interact_action.py))
- Test: `tests/action/orchestrator/test_egress_idempotent.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/action/orchestrator/test_egress_idempotent.py
"""Once a turn has emitted, no second user message is sent."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.memory.interaction import Interaction


@pytest.mark.asyncio
async def test_finalize_directives_skipped_when_emitted():
    ex = OrchestratorInteractAction()
    interaction = Interaction()
    interaction.mark_emitted()
    interaction.add_directive("Tell the user: hi", "SomeIA")  # unexecuted directive
    visitor = SimpleNamespace(interaction=interaction)
    responder = SimpleNamespace(respond=AsyncMock())
    ex.get_responder = AsyncMock(return_value=responder)

    await ex._finalize_directives(visitor)

    responder.respond.assert_not_awaited()  # latched → no second emission


@pytest.mark.asyncio
async def test_finalize_directives_runs_when_not_emitted():
    ex = OrchestratorInteractAction()
    interaction = Interaction()
    interaction.add_directive("Tell the user: hi", "SomeIA")
    visitor = SimpleNamespace(interaction=interaction)
    responder = SimpleNamespace(respond=AsyncMock())
    ex.get_responder = AsyncMock(return_value=responder)

    await ex._finalize_directives(visitor)

    responder.respond.assert_awaited_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/action/orchestrator/test_egress_idempotent.py -q`
Expected: FAIL — `test_finalize_directives_skipped_when_emitted` calls respond (gate still on `interaction.response`, which is empty).

- [ ] **Step 3: Implement**

In `_finalize_directives` ([:669](../../jvagent/action/orchestrator/orchestrator_interact_action.py)) replace the `if getattr(interaction, "response", "") ...: return` guard with the latch:

```python
        if interaction.has_emitted():
            return  # already delivered this turn
```

In `execute()` fallback ([:635-637](../../jvagent/action/orchestrator/orchestrator_interact_action.py)) replace the `after == before` check:

```python
        if not interaction.has_emitted():
            await self._emit_reply(visitor, self.clarify_text)
```
(Remove the now-unused `before`/`after` response snapshots if they have no other use; keep `before` only if referenced elsewhere — verify with grep.)

In `_maybe_emit_final` ([:1656+](../../jvagent/action/orchestrator/orchestrator_interact_action.py)) add an early return at the top, preserving the exact-text echo guard below it:

```python
        if interaction is not None and interaction.has_emitted():
            return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/action/orchestrator/test_egress_idempotent.py -q`
Expected: PASS

- [ ] **Step 5: Run the orchestrator suite (regression)**

Run: `.venv/bin/python -m pytest tests/action/orchestrator/ -q`
Expected: green; fix any test that asserted re-emission behavior on the old `interaction.response` sentinel by switching it to the `emitted` latch.

- [ ] **Step 6: Commit** — SKIP.

---

### Task 5: single `_egress()` authority

**Files:**
- Modify: `jvagent/action/orchestrator/orchestrator_interact_action.py` — add `_egress()`; route `execute()` post-loop + `final` through it
- Test: `tests/action/orchestrator/test_single_egress.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/action/orchestrator/test_single_egress.py
"""_egress emits exactly once and composes directives + text together."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.memory.interaction import Interaction


@pytest.mark.asyncio
async def test_egress_emits_once_then_noops():
    ex = OrchestratorInteractAction()
    interaction = Interaction()
    visitor = SimpleNamespace(interaction=interaction)

    async def _reply(text, v):
        interaction.mark_emitted()
        return True

    responder = SimpleNamespace(reply=AsyncMock(side_effect=_reply), respond=AsyncMock())
    ex.get_responder = AsyncMock(return_value=responder)

    await ex._egress(visitor, text="hello")
    await ex._egress(visitor, text="hello again")  # already emitted → no-op

    assert responder.reply.await_count == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/action/orchestrator/test_single_egress.py -q`
Expected: FAIL — `AttributeError: ... has no attribute '_egress'`

- [ ] **Step 3: Implement**

Add `_egress` near `_emit_reply`:

```python
    async def _egress(self, visitor: "InteractWalker", *, text: str = "") -> None:
        """The single per-turn user-facing emission authority.

        No-op if the turn already emitted. Otherwise composes the model's reply
        text together with any queued interaction.directives in one responder
        compose, delivered once (the responder/bus latch interaction.emitted).
        """
        interaction = getattr(visitor, "interaction", None)
        if interaction is None or interaction.has_emitted():
            return
        # Unexecuted directives compose together with text via ReplyAction.respond
        # (reply() routes to respond() when directives/params/format are present).
        if (text or "").strip():
            await self._emit_reply(visitor, text)
            return
        # No model text: render any queued directives (was _finalize_directives).
        try:
            unexecuted = interaction.get_unexecuted_directives()
        except Exception:
            unexecuted = None
        if not unexecuted:
            return
        responder = await self.get_responder()
        if responder is None:
            return
        try:
            await responder.respond(interaction, visitor=visitor)
        except Exception as exc:
            logger.warning("orchestrator: _egress directive compose failed: %s", exc)
```

Then in `execute()` replace the `_finalize_directives` + fallback tail with a single egress, keeping the clarify fallback inside `_egress` semantics:

```python
        await self._finalize_proactive_task(visitor)
        # Single egress authority — emits once (directives + any final text) or
        # falls back to clarify_text, gated by the interaction.emitted latch.
        await self._egress(visitor)
        if not getattr(interaction, "emitted", False):
            await self._emit_reply(visitor, self.clarify_text)
```

Route the loop's `final` action through `_egress` (replace the `_maybe_emit_final` call) so it converges:

```python
                if action == "final":
                    await self._egress(visitor, text=decision_answer)
                    ended_via = "final"
                    break
```
(Use the same answer variable currently passed to `_maybe_emit_final`. Keep `_maybe_emit_final`'s exact-text echo guard logic by moving it into `_emit_reply` if a product-skill closing-line test requires it — verify against `tests/action/orchestrator` product-skill tests.)

- [ ] **Step 4: Run test + orchestrator suite**

Run: `.venv/bin/python -m pytest tests/action/orchestrator/test_single_egress.py tests/action/orchestrator/ -q`
Expected: PASS / green. Fix fallout: any test asserting `_finalize_directives`/`_maybe_emit_final` directly → repoint to `_egress`.

- [ ] **Step 5: Commit** — SKIP.

---

### Task 6: adapter no-double-send integration test

**Files:**
- Test: `tests/action/orchestrator/test_adapter_no_double_send.py` (create)

- [ ] **Step 1: Write the test**

```python
# tests/action/orchestrator/test_adapter_no_double_send.py
"""A turn delivers exactly one user message to a channel adapter."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jvagent.action.response.channel_adapter import ChannelAdapter
from jvagent.action.response.response_bus import ResponseBus
from jvagent.memory.interaction import Interaction


class _RecordingAdapter(ChannelAdapter):
    def __init__(self):
        super().__init__("test")
        self.sends: list = []

    async def send(self, message) -> bool:
        self.sends.append(message.content)
        return True


@pytest.mark.asyncio
async def test_two_publish_attempts_one_adapter_send_when_latched():
    bus = ResponseBus()
    adapter = _RecordingAdapter()
    bus._channel_adapters["test"] = adapter
    interaction = Interaction()

    async def _publish():
        await bus.publish(
            session_id="s1", content="answer", channel="test",
            interaction=interaction, interaction_id="i1", category="user",
            relay_to_adapters=True,
        )

    await _publish()  # delivers + latches
    # Simulate the orchestrator gating a second emission on the latch:
    if not interaction.has_emitted():
        await _publish()

    assert adapter.sends == ["answer"]  # exactly one send
```

> Adjust `_can_send_to_adapter` expectations: if the recording adapter needs `deliver_thoughts`/relay wiring to receive a `user` message, set the minimal attributes the base `ChannelAdapter` requires (inspect `channel_adapter.py` and `_can_send_to_adapter`).

- [ ] **Step 2: Run it**

Run: `.venv/bin/python -m pytest tests/action/orchestrator/test_adapter_no_double_send.py -q`
Expected: PASS (the gated second publish never happens; one send).

- [ ] **Step 3: Commit** — SKIP.

---

### Task 7: audit channel re-sends + ADR

**Files:**
- Inspect: `jvagent/action/whatsapp/`, `jvagent/action/facebook_action/`, `jvagent/action/email_action/`
- Create: `.planning/adr/0025-single-per-turn-egress.md`

- [ ] **Step 1: Grep for post-walk re-sends**

Run:
```bash
grep -rnE 'interaction\.response|\.response\b' jvagent/action/whatsapp jvagent/action/facebook_action jvagent/action/email_action | grep -iE 'send|publish|reply'
```
Expected: a list. For each, confirm it is **logging/data**, not a channel send after the walk. WhatsApp is already clean (`finalize_whatsapp_interaction` only closes/saves). If any channel sends `interaction.response` after the walk, replace with reliance on the bus delivery during the walk (delete the post-walk send).

- [ ] **Step 2: Write the ADR**

Create `.planning/adr/0025-single-per-turn-egress.md` recording: one egress authority (`_egress`), one canonical stream (`interaction.directives`), one delivery (the response bus), one latch (`Interaction.emitted`, first-delivered-chunk for streaming); `interaction.response` is persistence/history (also returned verbatim in the non-streaming `/interact` JSON body for direct API callers); refines ADR-0013/0014. Follow the format of an existing ADR (e.g. `.planning/adr/0014-identity-on-agent-replyaction-egress.md`).

- [ ] **Step 3: Commit** — SKIP.

---

### Task 8: full verification

- [ ] **Step 1: Targeted suites**

Run: `.venv/bin/python -m pytest tests/action/orchestrator/ tests/action/response/ tests/memory/ tests/action/interview/ -q`
Expected: green (the 2 unrelated `web_fetch` failures are out of scope; do not run that dir).

- [ ] **Step 2: Lint**

Run: `.venv/bin/python -m flake8 --config=.flake8 jvagent/action/orchestrator/ jvagent/action/reply/ jvagent/action/response/ jvagent/memory/interaction.py` then `black --check` + `isort --check-only --profile black` on the same paths.
Expected: clean; apply `black`/`isort` if needed.

- [ ] **Step 3: Commit** — SKIP (await user instruction to commit).

---

## Self-review

- **Spec coverage:** §5.1 latch → Tasks 1-3 (+ first-chunk in Task 2). §5.2 single egress authority → Task 5. §5.3 unified directive compose → Task 5 (`_egress` composes directives+text once). §5.4 `interaction.response` persistence/audit → Task 7. §5.5 ADR → Task 7. §8 testing → Tasks 4,6,8. §9 streaming-first-chunk → Task 2 chunk-loop latch. All sections mapped.
- **Placeholder scan:** none — every code step is concrete. Task 5's `_maybe_emit_final` echo-guard note and Task 6's adapter-wiring note are explicit verification instructions, not deferred work.
- **Type consistency:** `has_emitted()` / `mark_emitted()` (Task 1) used identically in Tasks 2-5. `_egress(visitor, *, text="")` defined in Task 5, called consistently. `interaction.emitted` attribute name consistent throughout.
