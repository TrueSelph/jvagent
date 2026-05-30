# Executive Agent (demo)

A runnable reference for the **SkillExecutive** pattern (ADR-0012). One
orchestrator at weight `-200` runs a think-act-observe loop over the agent's
unified tool surface — reply/respond egress, anchored IAs-as-tools, action
tools, core tools, and native SOP skills. See [`docs/EXECUTIVE.md`](../../../../../docs/EXECUTIVE.md)
and [`.planning/adr/0012-skill-executive-architecture.md`](../../../../../.planning/adr/0012-skill-executive-architecture.md).

```
SkillExecutiveInteractAction (-200)
  ├─ think-act-observe loop over unified tool surface
  ├─ signup interview (IA-as-tool, multi-turn flow via TaskStore)
  └─ ReplyAction — egress voice (identity from Agent alias + role)
intro_interact_action runs BEFORE the executive (always_execute sidecar)
```

## Run it

1. Set `OPENAI_API_KEY` in `examples/jvagent_app/.env`.
2. Enable the agent: uncomment `- jvagent/executive_agent` under `agents:` in
   `examples/jvagent_app/app.yaml`.
3. Boot with YAML sync, then serve:

   ```bash
   jvagent examples/jvagent_app --update      # installs the agent
   jvagent examples/jvagent_app               # serve
   ```

4. Interact:

   ```bash
   curl -s -X POST localhost:8000/agents/jvagent/executive_agent/interact \
     -H 'Content-Type: application/json' \
     -d '{"utterance": "hi there", "session_id": "demo-1"}'
   ```

## What each sample turn exercises

| Utterance | Path |
|---|---|
| `hi there` | SkillExecutive loop → `reply` tool (slim publish) |
| `what is 19 x 23?` | Loop → web search or skill → `reply` |
| `what do our internal docs say about X?` | `answer` skill → `pageindex__search` (internal KB) first, web fallback, cited synthesis → `respond` |
| `sign up for jvagent training` | Model selects signup interview IA-as-tool → flow starts, TaskStore control-task recorded |
| your answers on following turns | With `lock_active_flow: true` (default), orchestrator dispatches the interview IA directly; with `false`, model may continue or detour |
| `stop` / `cancel` mid-interview | Interview IA handles cancellation via its own session logic |

## Notes

- Skills live under `skills/` (app-local) and `jvagent/skills/` (library); configure via `skills_source` and `skills` in `agent.yaml`.
- `jvagent/pageindex_action` provides the internal knowledge base (`pageindex__search/assimilate/list/delete`). Ingest documents first (`pageindex__assimilate`), then the `answer` skill searches them before falling back to the web. PageIndex auto-installs its pip deps (litellm, pdf libs) on first load.
- The signup interview (`signup_interview_interact_action`) is the demo multi-turn flow. Swap in your own anchored `InteractAction`s the same way.
- This agent occupies weight `-200` as the single pattern orchestrator; it runs fine beside other agents in the same app.
