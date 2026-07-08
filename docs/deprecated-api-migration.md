# Removed APIs (jvagent 0.1.1)

The following APIs were removed in **jvagent 0.1.1**. See [CHANGELOG.md](../CHANGELOG.md) for the full list.

## Rails orchestration

Removed action refs (do not use in `agent.yaml`):

- `jvagent/interact_router`
- `jvagent/converse_interact_action`
- `jvagent/retrieval_interact_action`
- `jvagent/web_search_retrieval_interact_action`
- `jvagent/pageindex_retrieval_interact_action`
- `jvagent/long_memory_retrieval_interact_action`
- `jvagent/long_memory_interact_action`
- `jvagent/long_memory_store_interact_action`

**Migration:** Use `jvagent/orchestrator` + `jvagent/reply` and tool-based RAG (`pageindex__search`, Serper, MCP). See [orchestration-modes.md](orchestration-modes.md).

## User.user_model

**Use:** `User.memory` dict.

## get_dispatch_visitor()

**Use:** `get_tool_visitor()` for live walker access, or `get_dispatch_context()` for identity fields only.

## skills_source aliases

Only `app`, `library`, and `both` are valid. Removed aliases: `registry`, `local`, `builtin`.

## include_legacy_agent_skills

Removed. Skill discovery always scans standard `agents/{ns}/{agent}/skills/` and action overlay dirs.
