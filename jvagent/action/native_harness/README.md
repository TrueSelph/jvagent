# Native development entrypoint

Configure `jvagent/native_harness` in place of the old Orchestrator while qualifying
a deployment. This remains an explicit development path, not a completed release
migration. See the [execution contract](../../../.planning/plans/2026-11-04-harness-rebuild-execution.md)
and [qualification record](../../../.planning/plans/2026-11-04-harness-rebuild-progress.md).

`publish_reasoning` optionally publishes completed provider reasoning text through
the existing thought channel. It defaults to false and does not change model
reasoning settings or tool visibility. See [the visibility contract](../../../.planning/runbooks/native-reasoning.md).

Use one native execution Action and one worker. Declared Redis/Dynamo conversation
ownership and non-single `WEB_CONCURRENCY` / `UVICORN_WORKERS` are refused until
native distributed fencing is qualified ([guard](../../core/execution_owner.py:23)).
Native durable delivery cursors return an explicit unsupported error
([stream](../response/streaming.py:75)); bounded in-process queue replay remains
available. See the [shared-host evidence and remaining gates](../../../.planning/plans/2026-11-05-shared-host-results.md).

An explicitly supplied `data.host_system_context` must verify and be consumed;
otherwise native admission rejects the request, including non-empty requests and
explicit null values. Ordinary requests omit the field
([guard](../interact/interact_walker.py:461)). See the
[authenticated browser qualification](../../../.planning/plans/2026-11-05-native-host-context.md)
for signed context, rejection outcomes and the remaining deployment gates.

Public native sessions always enforce identity. New anonymous callers receive a
server-issued subject; existing sessions require an owner login or a bound
session capability. Historical unbound sessions require owner proof or a new
session. See [public identity migration and evidence](../../../.planning/plans/2026-11-05-public-identity-binding.md)
and [caller binding](../interact/session_token.py:604).

`skill_directory` selects one existing SKILL.md. For skill-based delegation,
select the shipped `delegate` skill and declare named specialists:

```yaml
actions:
  - action: jvagent/native_harness
    context:
      model: openai:gpt-4.1
      skill_directory: /app/skills/delegate
      delegates:
        - name: reader
          description: Answer from authorized documents, with citations.
          skill_directory: /app/skills/answer
          max_calls: 2
          timeout_seconds: 90
```

Supply the child skill's required Action packages too. Paths must resolve in the
host deployment. Delegate declarations reject unknown fields, including model or
caller overrides ([schema](../../harness/delegation.py:9)). The configured model is
shared. The official `SubAgents` capability owns foreground delegation, isolated
child histories and bounded depth ([composition](../../harness/native.py:196)).
Each child loads its own approved skill library and exposes only its declared,
qualified read tools. Both `delegate_task` and child Action calls pass host access
checks ([admission](../../harness/native.py:116)). No inherited parent tools or
model-created agents are enabled.

The host-bound public WrapperAgent keeps child spend in the caller's conversation
([scope adapter](../../harness/delegation.py:19)). Parent UsageLimits apply across
the tree; native StepPersistence records child linkage on the same Interaction.
Child tool events reach the existing response bus, and accounting settles recorded
descendants once ([usage](../../harness/accounting.py:152)). Conversation spend
retains the existing counter format. Native budget checks occur after reported
usage and cannot promise a hard pre-reservation ceiling.

This increment does not qualify background/remote delegation, crash-resumed child
work, arbitrary recursive trees, script/write tools, distributed ownership or
provider-billed cost reconciliation. Usage reporting persists library spend
receipts before message snapshots ([recorder](../../harness/accounting.py:11)).
The pinned library's streamed SpendRecordedEvent can be dropped when a limit
exception closes the stream. A public model-request hook binds host run context
and the supported, deprecated on_spend callback records the receipt immediately.
Remove that callback adapter when upstream provides failure-safe immediate event
delivery, verified by the cost-exhaustion regressions. No model/provider execution
or pricing is implemented by this adapter. A request with no spend or message
receipt still remains unknown; unpriced/unmeasured receipts are never called free.
Budget failures are visibly distinguished from provider-configuration failures.


Research output state is scoped by the public native run lifecycle
([binding](../../harness/native.py:107), [policy](../../skills/research/native.py:54)).
Reusing or concurrently running one specialist cannot borrow another run's
observations. Research receives a structured view with the host source ID and
bounded `evidence.excerpt`; supporting quotes must match that field. WebFetch adds
a readable text excerpt extracted from the same cleaned HTML while preserving its
ordinary Markdown response ([extraction](../web_fetch/web_fetch_action.py:306)).
The research renderer includes the validated quote and host-resolved source link
([rendering](../../skills/research/contracts.py:214)). Raw-text receipts retain
strict formatting comparison. No fuzzy quote matching or source-free factual
fallback was added. Quote matching proves observation, not claim entailment.


Native context compaction uses the official `SummarizingCompaction` capability
([composition](../../harness/native.py:188)). The Action exposes
`context_max_tokens` (default 32,000 estimated tokens) and `context_keep_tokens`
(default 16,000 tokens of recent messages). Both must be positive; retention must
be smaller than the trigger. The same limits apply to configured specialists
([configuration](native_harness_action.py:52)). These limits control working
context, separately from the total per-run token/spend budgets.

Older messages become a library-written summary and compaction receipt; tool
pairs and loaded skills are preserved by the library. No first-message pinning,
custom summarization prompt, text slicing or silent legacy fallback is added.
Summary model requests use the configured model, share native usage limits and
conversation spending, and persist as named child runs on the same Interaction.
Accounting includes them even when no delegates are configured
([settlement](native_harness_action.py:231)). A summary failure fails the turn.

The retained suffix is a minimum of whole messages, not a hard upper bound. A
single oversized current message or an indivisible tool dependency can exceed
the trigger; current input is never silently truncated. Summary quality,
provider-window headroom, graph snapshot retention, distributed ownership and
crash-resumed evidence rehydration remain qualification gates. A model-generated
summary is context, never a new authorization or a verified research receipt.


For a new user turn after a handled failure, native admission uses an
accepted-input checkpoint, not the failed run's final output
([policy](../../harness/recovery.py:18)). Official StepPersistence captures input
and tool frontiers with a versioned host policy marker
([configuration](../../harness/native.py:150)). Every recorded parent/child must
be terminal, in the same caller conversation, and free of unresolved tool effects.
Provider usage/cost must also be settled: missing receipts remain unknown and
block new model calls until reconciled.
The input checkpoint must predate execution and retain the original question.
Only a response actually emitted by JV is added to that history; undelivered
model answers and failed-run tool observations are not carried forward. A new
run gets a new ID, current permissions and fresh per-run skill policy. Previous
usage and conversation spend remain intact.

This host policy is qualified for the factory's current read-only foreground
profile. It is not external-write reconciliation, automatic crash replay or
restoration of research evidence. Pending/unknown delivery, abandoned workers,
unresolved effects, legacy records without the checkpoint marker and mixed
non-native predecessors are blocked visibly, with no model call
([admission](native_harness_action.py:179)). Empty predecessor state cannot reset
the conversation's recovery boundary. Start a new conversation or reconcile the
old state explicitly; migration and operator reconciliation tooling remain open.

The graph adapter's `first_snapshot` query reads public library records without
repairing history or executing tools ([storage](../../harness/step_store.py:107)).
Remove this storage extension when the public StepStore offers accepted-input
checkpoint selection. Run registrations cannot replace another registration's
identity or scope, while replays of the same registration preserve the first
record. These adapter guards do not provide multi-worker fencing, transactional
ownership or a bound on cancellation-settlement writes. Checkpoint retention and
long-run storage costs also remain qualification gates.
