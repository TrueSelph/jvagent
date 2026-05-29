# Executive Agent (demo)

A runnable reference for the **Executive + Centers** pattern (ADR-0010). One
central executive (light model) engages conversation, recruits specialist
*centers*, integrates their results in working memory, and voices through a
single persona egress. See [`docs/EXECUTIVE.md`](../../../../../docs/EXECUTIVE.md)
and [`adr/0010`](../../../../../.planning/adr/0010-executive-centers-architecture.md).

```
ExecutiveInteractAction (-200)
  ├─ SkillsCenter   think-act-observe over the tool surface
  ├─ IACenter       hardened anchored rails InteractActions (signup interview here)
  └─ PersonaCenter  language/identity — voices ALL output
intro_interact_action runs BEFORE the executive (pipeline citizenship)
```

## Run it

1. Set `OPENAI_API_KEY` in `examples/jvagent_app/.env`.
2. Enable the agent: uncomment `- jvagent/executive_agent` under `agents:` in
   `examples/jvagent_app/app.yaml`.
3. Boot with YAML sync, then serve:

   ```bash
   jvagent examples/jvagent_app --update      # installs the agent + centers
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
| `hi there` | Executive `RESPOND` → Persona egress (one light decision, voiced) |
| `what is 19 x 23?` | Executive `ACTIVATE(SkillsCenter)` → think-act-observe → voiced |
| `sign up for jvagent training` | **Reflex** anchor hit → `IACenter` runs the signup interview directly (no executive model call); the interview `turn_lock`s → persisted as a sustained activation |
| your answers on following turns | Reflex resumes the sustained activation from `Conversation.context["executive_suspended"]` until the interview completes |
| `stop` / `cancel` mid-interview | Reflex treats it as an interrupt → falls through to the Executive instead of resuming |

## Notes / limits

- The SkillsCenter ships with an empty tool surface by default
  (`_resolve_tools` returns `{}`), so it answers directly until a skill-registry
  tool adapter is wired (documented TODO in `docs/EXECUTIVE.md`). Inject tools
  via `SkillsCenter.set_tools([...])` to see the tool loop.
- The signup interview (`signup_interview_interact_action`, copied into this
  agent's `actions/`) is the demo's anchored, turn-locking rails IA. Its
  `manifest.turn_lock=true` is what makes the IA center report a sustained
  activation — swap in your own anchored `InteractAction`s the same way.
- This agent occupies weight `-200` as the single pattern orchestrator for its
  agent; it owns the turn end-to-end *within the same agent*, but runs fine as a
  separate agent beside others in this app.
