# Cockpit Agent

Demonstrates **CockpitAction**: a model-cockpit InteractAction that grants the language model full agency over every harness service and action tool via a think-act-observe loop.

See the repository guide **[Cockpit Architecture](../../../../../docs/COCKPIT.md)** for the design rationale behind the cockpit pattern.

## Architecture

```
Phase 1 — CockpitRouter:
  Fast LLM call (GPT-4o-mini) for posture classification + skill selection.

Phase 2 — CockpitEngine (think-act-observe loop):
  The main model receives:
    - Harness service tools: memory_get_history, task_create_plan, response_publish,
      skill_search, skill_read, conversation_search, etc.
    - Artifact tools: artifact_add, artifact_get, artifact_update, artifact_delete,
      artifact_search — session-scoped structured storage on the active interaction.
    - Unified discovery: cockpit_search — find the most appropriate skill or tool
      for a job (engine context: skills + tools only; interact_actions is a
      router-only surface).
    - Action tools: pageindex__search, pageindex__assimilate, web_search__search, etc.
    - Skill bundle tools: automatically loaded from Claude-style SKILL.md directories
```

The model decides what tools to call, when to publish, when to create task plans, and when to discover new skills. Skills are Claude-style `SKILL.md` bundles — no special tool bundling required.

## Structure

```
cockpit_agent/
├── agent.yaml    # Agent configuration with CockpitAction
└── README.md     # This file
```

No `actions/` directory needed — `jvagent/cockpit_action` is a core action auto-discovered from the jvagent package.

## Components (from `agent.yaml`)

| Action | Role |
|--------|------|
| `jvagent/cockpit_action` | Cockpit router + engine (weight: -200) |
| `jvagent/anthropic_lm` | Claude for the cockpit loop |
| `jvagent/openai_lm` | GPT-4o-mini for routing and persona |
| `jvagent/persona` | Persona when cockpit delivers final response |
| `jvagent/pageindex_action` | Backing APIs for pageindex skills (search, assimilate, list, delete) |
| `jvagent/serper_web_search` | Web search for the `web_search` skill |

Pre-loaded skills: `pageindex_search`, `pageindex_docs`, `web_search`, `research`.

## Environment

Set at least:

```bash
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
SERPER_API_KEY=...
```

Use the app root `.env` or your deployment environment; see `SETUP.md` in the example app.
