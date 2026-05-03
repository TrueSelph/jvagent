# `skill_handler`

Duplicated / extended scaffolding for **`AgentInteractAction`** so the unified interact stack does not depend on patches under `jvagent/action/skill/` for preload behavior.

- **`contracts.py`** — local copy of skill run types (`SkillRunContext.preloaded_skills`, etc.).
- **`agentic_loop.py`** — `AgentInteractSkillAction` subclasses `SkillAction` and overrides `prepare_run` only; uses `AgentInteractToolExecutor` for idempotent bundle registration.
- **`always_active.py`** — reads `always-active` from SKILL.md frontmatter when platform scaffold does not expose it.

Repository-wide **bugfixes** (LM kwargs blocklist, Ollama `ollama_host_root`) stay in `jvagent/action/model/` — see [../README.md](../README.md).
