# Reference signup hook approval and factual completion

Parent revision: `8414257d`. This advances reference migration, not app cutover.
[Machine evidence](2026-11-06-native-signup-policy-smoke.json) records source,
SDK effects/results, timing, tasks and spend. [Browser observations](2026-11-06-native-signup-policy-browser.json)
retain the rejection, review, confirmation and cold readback.

## Review and changes

The previously qualified native hook interface remains unchanged. Review of the
reference signup found that its fuzzy fallback ignored explicit AM/PM, minutes,
end times, negation and alternative days. Seven new negative time cases fail on
the parent. The extension now matches one declared day/start time and optional
range, preserving shorthand only when it identifies one offered slot. Unknown or
conflicting input is refused rather than silently corrected. Source:
`examples/jvagent_app/agents/jvagent/orchestrator_agent/skills/signup-interview/scripts/custom_tools.py:34`.

Completion previously returned success even with missing required fields. Its
failure now preserves the reviewed interview/task instead of closing it. The
extension no longer logs personal contact fields or promises contact, email
updates or booking without a service operation. Its SOP and handlers describe
only the local confirmed record. Sources: that extension's `signup_review` and
`signup_complete`; the skill's `Local record and validation` instructions.
The updated completion-text assertion also fails on the parent. Incomplete-record and required-branch regressions check failure and no task
closure. Required fields use the existing domain branch resolver rather than
duplicated branch conditions; removing this check makes both branch cases fail. Old review-gate
fixtures now supply required fields only in their successful-completion cases;
unknown-field rejection still starts from an empty interview.

Imports and every referenced callback were reviewed: the extension uses local
regex/typing and the existing HookExecutionContext, without network, model,
filesystem, subprocess or independently scheduled effects. It mutates interview
session scratch through the existing domain API. The reference InterviewAction
now pins the framed SOP/source digest in `agent.yaml`. This is host approval of
trusted Python for conversation-local work, not sandboxing or approval for
arbitrary external work. Source drift invalidates approval through the existing
native loader. No core engine, new dispatcher, dependency or database is added.

## Actual-model browser qualification

The fresh PostgreSQL fixture on 8175 uses GPT-4.1 and jvchat on 3140. Generic host
instructions ask for actual skill/tool use; domain policy stays in the skill.
The copied SOP/extension bytes match the reviewed reference exactly.

Two three-turn cohorts are retained, with the final cohort running the required-branch
completion check and collecting the employer branch as well. Each cohort shows:

1. The user explicitly asks to validate Saturday at 9 PM unchanged. The SDK
   `set_fields` result stores the name but refuses that availability. The visible
   reply offers valid slots; no silent conversion to 9 AM occurs.
2. The user chooses Saturday at 9 AM, virtual attendance, a test contact email
   and no phone. Validators normalize the slot/format, phone is skipped, review
   is shown and the persisted task remains active.
3. A later explicit user confirmation invokes completion once. The task becomes
   completed. The visible response says the details are recorded locally and
   that no booking or notification has been made.

All six Interactions close as delivered. Twelve SDK effects complete, with one
terminal run event per run. Eighteen model requests reconcile exactly to 55,100
reported tokens and USD 0.056638 estimated cost in the conversation journals.
The final-source cohort accounts for nine requests, 27,981 tokens and USD 0.023778
estimated; the earlier cohort is identified separately in the machine record. Provider dollar
billing is unavailable. No external service operation is performed.

The first restart began during old-worker SSE drain, so it is excluded from cold
restart evidence. Both owned handles subsequently terminated before a final clean
worker started. No model work occurred during the overlapping drain. The final
cold restart and browser transcript reload leave all Interaction and Conversation
records, task state and spend unchanged. The browser restores tool activity and
statistics. Module observations show no legacy execution imports or patched runtime
functions. This does not qualify distributed ownership or arbitrary hook recovery.

## Checks and remaining work

The affected interview/native-hook/migration slice passes 476 cases on each Python
3.11 and 3.12. Initial broad-slice failures exposed stale wording assertions and
success fixtures with missing domain data; these were corrected without relaxing
the review-before-confirmation or unknown-field gates. Required all-file hooks and
normal full-suite push checks remain publication gates. An intermediate full-suite
run failed only the tracked-asset snapshot check after further source edits; the
snapshot is regenerated for the final publication gate.

The reference offline assessment now has [five blockers](2026-11-06-native-signup-policy-reference-blockers.json):
reviewed code image/resource policy, channel overrides, vision, active-flow policy
and model-service callers. Stored legacy task owners/configuration, whole-app
cutover, broader extension/deployment/provider/recovery qualification, engine
deletion and performance comparison remain incomplete. Existing hooks still do
not book training, verify an address or schedule notifications. No enterprise
readiness or measured optimization claim is made.
