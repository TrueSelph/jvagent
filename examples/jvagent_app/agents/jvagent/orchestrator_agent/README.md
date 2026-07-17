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
| `Send me the signup template` (**WhatsApp only**) | `whatsapp_templates` skill → `whatsapp__send_template` → Meta HSM to **the same** inbound sender via jvconnect. Web/chat is refused (`whatsapp_templates_require_inbound_whatsapp`). Requires APPROVED Meta template `signup` + allowlist in `agent.yaml`. |
| `Send me the signup flow` (**WhatsApp text or voice-call**) | `whatsapp_flows` skill → `whatsapp__send_flow` → interactive Flow to the inbound sender/caller. Web/chat refused (`whatsapp_flows_require_inbound_whatsapp`). Build/copy the Graph payload from jvconnect **Flows → Send**. |
| `I want to sign in` / `I want to sign up` (**WhatsApp text or voice-call**) | `whatsapp_service_flows` skill → `whatsapp__send_flow` with published `signin_signup` Flow. On a voice call, spoken reply stays on the call; the Flow card appears in WhatsApp chat. |
| `I want to book an appointment` (**WhatsApp text or voice-call**) | `whatsapp_service_flows` skill → `whatsapp__send_flow` with published `appointment_booking` Flow. Same voice-call behavior as sign-in. |
| `Hello my name is Jane Doe` (activation) | Model calls `interview__set_fields`, chains `interview__next_field`, then replies — no server prep steering |
| your answers on following turns | Model classifies intent per SKILL SOP; `interview__set_fields` → `interview__next_field` → reply |
| `change my email to X` mid-interview | `interview__set_fields` correction — works on any stored field, not only the active question |
| `stop` / `cancel` mid-interview | `interview__cancel` |
| `start over` mid-interview | `interview__reset` |
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

- **Skill placement (ADR-0023):** all agent skills live under `skills/` — JV SOPs (`web_lookup`), interviews (`signup_interview`), and Claude bundles (`docx`). Library skills come from `jvagent/skills/`. Base action SOPs (e.g. `interview/SKILL.md`) are extends targets only, not skill folders. Configure exposure via `skills_source` and `skills` in `agent.yaml`.
- `jvagent/pageindex_action` provides the internal knowledge base (`pageindex__search/assimilate/list/delete`). Ingest documents first (`pageindex__assimilate`), then the `answer` skill searches them before falling back to the web. PageIndex auto-installs its pip deps (litellm, pdf libs) on first load.
- The signup interview (`skills/signup_interview/` + `jvagent/interview`) is the demo multi-turn flow: frontmatter `interview:` contract, custom rules in `SKILL.md` body, standard procedure composed via `extends: action:jvagent/interview`. Copy the skill folder pattern for other structured interviews.
- This agent occupies weight `-200` as the single pattern orchestrator; it runs fine beside other agents in the same app.
