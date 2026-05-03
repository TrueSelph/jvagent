# `agent_interact`

Unified interact action (`AgentInteractAction`): routing, fast conversational replies (`converse.py`), and skill execution via `skill_handler/` (duplicated scaffolding vs `action/skill`).

## Platform bugfixes (do not revert for AgentInteract)

AgentInteract must **not** add features by patching these shared modules:

| Location | Fix |
|----------|-----|
| `jvagent/action/model/language/base.py` | `_QUERY_KWARGS_BLOCKLIST` — avoids duplicate kwargs forwarded into `query_messages`. |
| `jvagent/action/model/ollama_endpoint.py` | `ollama_host_root()` — normalizes `api_endpoint` when callers use a docs-style `…/api` base so paths are not doubled to `/api/api/...`. |
| Ollama LM / embedding actions | Use `ollama_host_root` when building request URLs. |
| `tests/action/model/test_ollama_actions.py` | Regression coverage for `ollama_host_root`. |

All other AgentInteract-specific behavior lives under this package (especially `skill_handler/`).
