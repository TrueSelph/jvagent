# Example: account-gated service (ADR-0026 work-stack gating)

A domain-neutral witness that the work-stack orchestration is reusable by **any**
consumer, not just the account-gate it was first built for. Nothing here names a
tenant; the framework never learns what `signed_in` means — only its boolean
result. (The CI guard in `tests/test_framework_domain_agnostic.py` enforces that
no consumer vocabulary leaks into `jvagent/`.)

Two skills:

- **`example_booking_interview`** — a gated service. Its frontmatter declares
  `requires-tasks: [{when: signed_in, push: example_signin_interview, seed_from:
  [utterance]}]`.
- **`example_signin_interview`** — the prerequisite, an ordinary task-lock
  interview. It is a gate only because the graph pushes it as a blocker.

## The entire consumer wiring

A consumer registers the named precondition once at bootstrap:

```python
from jvagent.action.orchestrator.preconditions import register_precondition

def has_session(visitor) -> bool:
    ctx = getattr(getattr(visitor, "conversation", None), "context", {}) or {}
    return bool(ctx.get("session_email"))

register_precondition("signed_in", has_session)
```

That plus the frontmatter is the whole gate. At activation the harness:

1. evaluates `signed_in`; if unmet, pushes `example_signin_interview` as a task
   that **blocks** the booking task (snapshotting + seeding the original request);
2. drives the sign-in detour to completion;
3. drains the work graph and **resumes the booking**, re-injecting the original
   request — no rail, no per-field guard, no model-mediated resume.

## Beyond gating — plans

The same push/block/drain/resume primitives express a **multi-step plan**: a
parent task with ordered children it is `blocked_on`. `pick_top_runnable` walks
them in priority/order, and completion re-resolves the next runnable step — the
graph machinery is reused unchanged. See
`tests/action/orchestrator/test_example_gated_skill.py::test_plan_drains_in_order`.

## Conversation use cases (CUCS)

Declarative multi-turn scenarios for orchestrator E2E testing live in
[`use-cases/`](use-cases/). They follow the jvagent
[Conversation Use Case Specification](../../../../.planning/reference/conversation-use-cases.md)
(`schema: jvagent.use-case/v1`).

| ID | File | Demonstrates |
|----|------|--------------|
| `example.booking.gated-no-session` | `booking-gated-no-session.yaml` | `requires-tasks` push → sign-in blocks booking → seed utterance |
| `example.booking.with-session` | `booking-with-session.yaml` | precondition satisfied → no push |
