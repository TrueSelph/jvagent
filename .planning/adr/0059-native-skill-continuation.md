# ADR-0059 — Skill-driven continuation on the native Harness

- Status: Accepted for native execution; qualification remains profile-specific
- Date: 2026-11-06
- Supersedes: [ADR-0013](0013-togglable-deterministic-turn-lock.md) for native execution only; transitional Orchestrator deployments retain their existing behavior
- Governing direction: [harness rebuild](../plans/2026-11-04-harness-rebuild-execution.md)

## Decision

Official Pydantic AI/Harness skill loading, model execution and persisted messages
own native turn execution. Domain Actions own their validated session/task state.
Retire the legacy `lock_active_flow` routing switch rather than adding another
native deterministic dispatcher or an old-loop fallback. The migration report
names this behavior change for explicit review, including the implicit legacy
default (`jvagent/cli/harness_migrate.py:86`).

An active domain task does not force every utterance through its workflow. Skills
describe how to handle detours and resume; the native interview SOP uses authoritative
domain tool results, requests status when progress is unclear and uses explicit
reset/cancel/start operations (`jvagent/action/interview/native_skill/SKILL.md:39`).
Loading a skill grants no caller identity or permission. Existing host authorization,
configured/loaded tool ownership, SDK effect records and domain validation remain
mandatory. There is no server intent classifier or automatic answer extraction.

TaskStore.status and the domain session remain authoritative for workflow progress;
chat history is not a second registration database. Review and confirmation do not
waive required fields. The shared completion path checks the current collectible
path before a completion handler or task closure. Reuse that domain resolver rather
than duplicating checks in every skill extension
(`jvagent/action/interview/engine.py:2061`).

## Compatibility and limits

The native model-led behavior intentionally differs from a mechanistic turn lock.
Business/authorization gates belong in host policy and Action validation, not in
model instructions or tool-selection restrictions. Unsupported legacy flow Actions,
task-lock metadata and retained active tasks are not made compatible by retiring
this boolean. Existing Action/skill admission and offline retained-state blockers
still apply (`jvagent/cli/harness_migration_state.py:1`).

The [qualification record](../plans/2026-11-06-native-continuation.md) bounds the
tested skill detour/resumption, cold readback, manual completion and simplified
signup-hook behavior. Generic legacy IA-flow migration, historical task conversion,
parked/abandoned policies, remote delegation and distributed recovery remain open.
Legacy routing code stays transitional until the existing cutover/deletion gates.

No second executor, state database, scheduler or provider protocol is introduced.
Remove transitional migration aliases and the old dispatcher when retained-state
and supported workflow qualification permit the single native execution path.
