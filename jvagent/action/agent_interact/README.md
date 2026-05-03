# `agent_interact`

Unified interact action (**`AgentInteractAction`**, package `jvagent/agent_interact_action`): Phase-1 routing in `router/`, fast conversational replies (`converse.py`), and skill execution via `skill_handler/` (local scaffolding vs `action/skill`).

**Documentation:** [AgentInteract guide](../../../docs/agent-interact.md) — architecture, routing (`skills` + `interact_actions`), canned lead-ins, prompt overrides, and links to examples.

## Layout

| Path | Role |
|------|------|
| [`router/`](router/README.md) | `AgentInteractRouter`, default prompts (`prompts.py`), clarification wiring |
| [`skill_handler/`](skill_handler/README.md) | Agentic skill loop, catalog shim, hot reload |
| [`agent_interact_action.py`](agent_interact_action.py) | Action class, YAML-configurable `routing_*` and router/skill fields |

## Platform bugfixes (do not revert for AgentInteract)

AgentInteract must **not** add features by patching these shared modules:

| Location | Fix |
|----------|-----|
| `jvagent/action/model/language/base.py` | `_QUERY_KWARGS_BLOCKLIST` — avoids duplicate kwargs forwarded into `query_messages`. |
| `jvagent/action/model/ollama_endpoint.py` | `ollama_host_root()` — normalizes `api_endpoint` when callers use a docs-style `…/api` base so paths are not doubled to `/api/api/...`. |
| Ollama LM / embedding actions | Use `ollama_host_root` when building request URLs. |
| `tests/action/model/test_ollama_actions.py` | Regression coverage for `ollama_host_root`. |

All other AgentInteract-specific behavior lives under this package (`router/`, `skill_handler/`, etc.).
