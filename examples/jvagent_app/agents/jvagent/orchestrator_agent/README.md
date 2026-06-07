# Executive Agent (demo)

A runnable reference for the **Orchestrator** pattern (ADR-0012). One
orchestrator at weight `-200` runs a think-act-observe loop over the agent's
unified tool surface — reply/respond egress, anchored IAs-as-tools, action
tools, core tools, and native SOP skills. See [`docs/ORCHESTRATOR.md`](../../../../../docs/ORCHESTRATOR.md)
and [`.planning/adr/0012-skill-executive-architecture.md`](../../../../../.planning/adr/0012-skill-executive-architecture.md).

```
OrchestratorInteractAction (-200)
  ├─ think-act-observe loop over unified tool surface
  ├─ signup interview (signup_interview skill + InterviewAction, turn-lock)
  └─ ReplyAction — egress voice (identity from Agent alias + role)
intro_interact_action runs BEFORE the executive (always_execute sidecar)
```

## Run it

1. Set `OPENAI_API_KEY` in `examples/jvagent_app/.env`.
2. Enable the agent: uncomment `- jvagent/orchestrator_agent` under `agents:` in
   `examples/jvagent_app/app.yaml`.
3. Boot with YAML sync, then serve:

   ```bash
   jvagent examples/jvagent_app --update      # installs the agent
   jvagent examples/jvagent_app               # serve
   ```

4. Interact:

   ```bash
   curl -s -X POST localhost:8000/agents/jvagent/orchestrator_agent/interact \
     -H 'Content-Type: application/json' \
     -d '{"utterance": "hi there", "session_id": "demo-1"}'
   ```

## What each sample turn exercises

| Utterance | Path |
|---|---|
| `hi there` | Orchestrator loop → `reply` tool (slim publish) |
| `what is 19 x 23?` | Loop → web search or skill → `reply` |
| `what do our internal docs say about X?` | `answer` skill → `pageindex__search` (internal KB) first, web fallback, cited synthesis → `respond` |
| `sign up for jvagent training` | Model activates `signup_interview` skill → InterviewAction session starts, turn-lock via TaskStore |
| your answers on following turns | With `lock_active_flow: true` (default), orchestrator stays in the locked skill; interview tools drive collection |
| `stop` / `cancel` mid-interview | `interview__cancel` or `signup_interview__reset_signup_interview` |
| `make a PDF of a short status report` | **Claude skill** `pdf-generation` → `use_skill` stages it into your per-user slice → model writes markdown + runs `code_execution__bash` (`render_pdf.py`) → PDF lands under `output/` in your slice |
| `rank these issues by severity: …` | **Claude skill** `triage` → `code_execution__bash` runs `prioritize.py` to sort deterministically |
| `list the files in my workspace` | `file_interface__list_directory` (same per-user slice the PDF was written to) |

## Testing the two skill specs + code execution (ADR-0017)

This agent enables the new substrate so you can exercise both specs:

- **JV skills** (`web_lookup`, `research`, `answer`) — SOPs that reference action
  tools already on the surface; `use_skill` surfaces those tools.
- **Claude skills** (`pdf-generation`, `triage`, `spec: claude`) — standard
  folders whose bundled scripts run in **`jvagent/code_execution`**. Activation
  stages the skill at `staged_skills/<name>/` in the caller's **own** per-user slice
  (`<agent_id>/<user_id>/`), and the model runs the scripts with
  `code_execution__bash`. Artifacts persist in that slice and are visible via
  `file_interface__*` and the filesystem MCP — three views on one slice.

`jvagent/code_execution` is **off by default**; it is enabled here. The
subprocess backend bounds CPU/memory/time/output and scrubs the env but is not a
hard jail — fine for these trusted library skills. It needs **local** file
storage (the default `./.files`). For `pdf-generation` to produce a real PDF, the
host needs a PDF engine (`pandoc` + a LaTeX engine, or `weasyprint`); otherwise
the script reports the missing dependency.

> After editing `agent.yaml`, re-run `jvagent examples/jvagent_app --update` to
> sync the new actions (`code_execution`, `file_interface`, `skill_hub`) into the
> graph before serving.

## Notes

- Skills live under `skills/` (app-local) and `jvagent/skills/` (library); configure via `skills_source` and `skills` in `agent.yaml`.
- `jvagent/pageindex_action` provides the internal knowledge base (`pageindex__search/assimilate/list/delete`). Ingest documents first (`pageindex__assimilate`), then the `answer` skill searches them before falling back to the web. PageIndex auto-installs its pip deps (litellm, pdf libs) on first load.
- The signup interview (`actions/jvagent/interview_action/skills/signup_interview/` + `jvagent/interview_action`) is the demo multi-turn flow: frontmatter `interview:` contract, custom rules in `SKILL.md` body, standard procedure composed via extends. Copy the skill folder pattern for other structured interviews.
- This agent occupies weight `-200` as the single pattern orchestrator; it runs fine beside other agents in the same app.
