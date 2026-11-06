# Reviewed interview extensions on the native Harness

## Scope and architecture

The existing training signup now runs its custom validators, processors and
review/completion handlers inside the existing InterviewAction tools, dispatched
and persisted by official Harness/SDK interfaces. There is no second hook agent,
model loop, executor, state database or intent classifier. Source:
`jvagent/action/interview/native.py:20`, `jvagent/action/interview/hooks.py:568`.

An operator supplies `InterviewAction.native_hook_digests[skill_name]` after
reviewing the SOP, extension code, imports and effects. The fingerprint covers
framed bytes of SKILL.md and scripts/custom_tools.py. Skill-authored trust metadata
cannot grant approval. Admission checks source bounds, ordinary non-symlink files,
the cached declaration and the local single-ctx callback ABI without importing
application code. The existing loader revalidates before cache use, compiles the
verified bytes and refuses native import/loader failures. Task-local approval
scope is reset after the tool call. Sources: `native_hooks.py:1` in that Action,
`jvagent/action/interview/interview_action.py:103`.

This is trusted host plugin code, **not a Python sandbox**. The digest does not
pin arbitrary imported libraries or prevent approved Python from making network
calls. Review and deploy it with the same controls as other Action code. This
profile qualifies conversation-local work only; independent external effects
need their own SDK receipts, permissions, idempotency and delivery evidence.
Custom LLM tools, proactive/TTL hooks, branch hooks, parameters and hooks on
repeated children/parents remain gated in `jvagent/action/interview/native.py:37`.

## Confirmed defects closed

The previous domain completion path closed an interview even when its handler
returned failure. It now preserves session/task state and returns the failed
domain result. Existing string/None success results remain compatible.
Source: `jvagent/action/interview/engine.py:2097`.

Shared interview tools could start or mutate another discovered interview outside
the selected skill catalog. The plugin now declares its owning-skill requirement;
generic public SDK argument validation checks configured and loaded ownership
before effects begin. Outgoing start-tool enums expose only admitted, loaded
owners. The core factory contains no interview-specific routing. Sources:
`jvagent/harness/mutation_tools.py:1`, `jvagent/harness/tool_schema.py:17`,
`jvagent/harness/native.py:175`, `jvagent/action/interview/native.py:20`.

The completion and cross-catalog tests failed against the previous behavior.
Nineteen hook cases additionally cover self-trust, stale source/cache/declaration,
bad callback names/signatures, symlinks, import/loader refusal, task-local scope,
unapproved selected skills despite another eligible interview, real signup
callbacks and outgoing SDK schemas. Source:
`tests/harness/native/test_interview_hooks.py:57`.

## Existing assets and migration

The signup folder/name becomes `signup-interview`, with explicit selector and
test references updated. Its callbacks retain their domain implementation apart
from name references. Native SOP instructions replace obsolete lock-companions
metadata for side questions. Old persisted signup_interview tasks are not
automatically renamed or qualified by this source change.

The deliberately malformed BOM/underscore regression skill moves byte-for-byte
from the runnable example into `tests/scaffold/fixtures/skills/smoke_frontmatter`.
Its selector is removed from the app; resolver tests still copy and exercise it
in a disposable app. No authored capability is silently discarded. Source:
`tests/scaffold/test_skill_resolve.py:1`.

Offline migration uses the same explicit interview Action base and domain
admission check. It does not import hooks or create host approval. Missing,
changed, disabled or malformed approval blocks candidates; even approved source
requires runtime qualification. Source:
`jvagent/cli/harness_migration_skills.py:61`.
The reference now has nine blockers and seven format-admitted selections: the
malformed fixture is retired; signup format passes but reviewed host approval is
still required. Three WhatsApp formats, code image/resource policy, channel/vision/
flow policy and model-service callers remain blockers. This is not app cutover.

## Actual-provider browser qualification

[Machine evidence](2026-11-06-native-signup-hooks-smoke.json) binds source/process
epochs, requests, elapsed time, usage, callback observations and graph comparisons.
[Browser evidence](2026-11-06-native-signup-hooks-browser.json) retains visible
answers, tool activity and statistics. The isolated PostgreSQL app uses backend
8168, source jvchat 3140 and OpenAI GPT-4.1. Runtime functions are not patched;
observational profiling records callback invocation and provider tool schemas.

The first cohort starts without auto-storing answers, rejects a filler name,
then explicitly collects/normalizes Saturday/virtual/work-email branch data,
skips phone and reviews. Actual callbacks include validation, store-time slot
lookup, email post-processing and review. The filler refusal is model behavior,
not an observed negative validator invocation; deterministic SDK tests exercise
that validator failure. An arithmetic side question answers 56 without changing
the reviewed state or completing its task. Cold restart preserves every existing
Interaction and Conversation. An employer correction receives a fresh review;
explicit confirmation invokes signup_complete once and completes the task.

Offline operator denial changes only the Action approval policy. A fresh browser
request then blocks before model/hook execution, despite another discovered
built-in interview. Original completed records remain unchanged. After restoring
approval and restarting, a fresh start/cancel completes and cancels its own task.
An adversarial hidden-interview request receives a model refusal with no effects;
deterministic SDK tests, rather than that model refusal alone, establish the
enforcement boundary. Every observed outgoing interview__start schema excludes
the hidden interview. Reload restores prior tools, answers and usage without new
Interaction/Conversation records or spending.

The final loader-failure tightening is followed by another actual-model signup
cohort on the final runtime source. Final results and totals are in the machine
record; prior epochs remain identified rather than being relabeled final-source
evidence.

Across twelve browser turns, eleven deliver and one correctly blocks at admission.
Twenty-six model requests consume 72,786 provider-reported tokens; SDK-priced
estimated cost is USD 0.069786, with zero unknown usage. Dollar amounts are not
provider-billed totals. All fifteen effects have exactly one terminal event:
fourteen complete, and one duplicate capability load fails before the model
continues successfully. That retry and its cost remain in the record. Shared
conversation spending reconciles to per-request accounting. This is a functional
cohort, not a representative latency/success-rate benchmark.

Completed field values and completion results are durable in SDK tool results/
snapshots. Task.data holds domain ownership, not a separate registration record.
The authored success message promises future contact; no email, training-service
write or notification is sent or qualified. Other side-question domains,
cross-provider behavior, retained legacy tasks and distributed/uncertain-effect
recovery remain open. No enterprise or performance-improvement claim is made.

## Verification

Final test and publication results are recorded alongside the machine evidence.
The earlier 721-case Python 3.11/3.12 lanes preceded the final loader regression;
they do not substitute for the final checks. The migration CLI has seven approval
cases including an approved module whose top-level code raises if imported,
proving planning remains offline. Publication requires clean staged all-files
pre-commit, affected pytest slices and the normal full pre-push suite.

Final native/host/real-PostgreSQL lanes pass 722 tests on each Python 3.11/3.12,
including the configured pinned Docker/Shell cases. The final interview/scaffold/
migration slice passes 482 on each Python 3.11/3.12. The nineteen-case hook slice
and full native lanes cover the final loader case. An earlier attempt with an
incorrect schema-test path ran no tests and is excluded from evidence.
