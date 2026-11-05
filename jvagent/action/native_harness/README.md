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
child histories and bounded depth ([composition](../../harness/native.py:123)).
Each child loads its own approved skill library and exposes only its declared,
qualified read tools. Both `delegate_task` and child Action calls pass host access
checks ([admission](../../harness/native.py:100)). No inherited parent tools or
model-created agents are enabled.

The host-bound public WrapperAgent keeps child spend in the caller's conversation
([scope adapter](../../harness/delegation.py:19)). Parent UsageLimits apply across
the tree; native StepPersistence records child linkage on the same Interaction.
Child tool events reach the existing response bus, and accounting settles recorded
descendants once ([usage](../../harness/accounting.py:55)). Conversation spend
retains the existing counter format. Native budget checks occur after reported
usage and cannot promise a hard pre-reservation ceiling.

This increment does not qualify background/remote delegation, crash-resumed child
work, arbitrary recursive trees, script/write tools, distributed ownership or
provider-billed cost reconciliation. An interrupted response without a snapshot
receipt remains unknown usage. It must not be represented as free.
