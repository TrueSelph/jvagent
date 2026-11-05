# Native development entrypoint

Configure `jvagent/native_harness` in place of the old Orchestrator while qualifying
a deployment. This remains an explicit development path, not a completed release
migration. See the [execution contract](../../../.planning/plans/2026-11-04-harness-rebuild-execution.md)
and [qualification record](../../../.planning/plans/2026-11-04-harness-rebuild-progress.md).

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
child histories and bounded depth ([composition](../../harness/native.py:140)).
Each child loads its own approved skill library and exposes only its declared,
qualified read tools. Both `delegate_task` and child Action calls pass host access
checks ([admission](../../harness/native.py:117)). No inherited parent tools or
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
([binding](../../harness/native.py:100), [policy](../../skills/research/native.py:54)).
Reusing or concurrently running one specialist cannot borrow another run's
observations. Research receives a structured view with the host source ID and
bounded `evidence.excerpt`; supporting quotes must match that field. WebFetch adds
a readable text excerpt extracted from the same cleaned HTML while preserving its
ordinary Markdown response ([extraction](../web_fetch/web_fetch_action.py:306)).
The research renderer includes the validated quote and host-resolved source link
([rendering](../../skills/research/contracts.py:214)). Raw-text receipts retain
strict formatting comparison. No fuzzy quote matching or source-free factual
fallback was added. Quote matching proves observation, not claim entailment.
