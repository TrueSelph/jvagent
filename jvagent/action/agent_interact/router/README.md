# `router`

Phase-1 routing for **`AgentInteractAction`**: posture (RESPOND / SUPPRESS / DEFER), intent, canned lead-in, and selection of catalog routes (skill names) that map to interact actions on the walk path.

- **`prompts.py`** — default routing / clarification templates; override via `AgentInteractAction` `routing_*` attributes in `agent.yaml`. The default user template includes `interact_actions_json` alongside `skills_json` so the model can return either catalog’s keys (or both).
- **`service.py`** — `AgentInteractRouter` (LLM call, cache, walk-path finalization).
