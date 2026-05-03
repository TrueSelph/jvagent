# Unified Agent

Demonstrates **AgentInteractAction**: unified skill routing (fast posture classification), native lightweight conversation, and an agentic skill loop with pre-loaded retrieval skills. Actions resolve from the installed **jvagent** package; this folder contains only `agent.yaml` and documentation (no bundled `actions/` subtree).

## Structure

```
unified_agent/
├── agent.yaml    # Agent configuration and core action assignments
└── README.md     # This file
```

Custom action packages live under `actions/<namespace>/<action_name>/` when you ship agent-specific code. This example uses built-in `jvagent/*` actions only, so an `actions/` directory is not required.

## Components (from `agent.yaml`)

| Action | Role |
|--------|------|
| `jvagent/agent_interact_action` | Router, native convo, and skill loop |
| `jvagent/anthropic_lm` | Claude for the agentic loop |
| `jvagent/openai_lm` | GPT-4o-mini for routing and persona-driven rewrite paths |
| `jvagent/pageindex_action` | Backing APIs for `pageindex_search` and `pageindex_docs` skills |
| `jvagent/serper_web_search` | Web search for the `web_search` skill |
| `jvagent/persona` | Persona when `response_mode` uses persona rewrite |

Pre-loaded skills: `pageindex_search`, `pageindex_docs`, `web_search`.

## Environment

Set at least:

```bash
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
SERPER_API_KEY=...
```

Use the app root `.env` or your deployment environment; see `SETUP.md` in the example app.
