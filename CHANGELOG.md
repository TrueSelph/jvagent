# Changelog

All notable changes to **jvagent** (this package) are documented here. Indexing and database-adapter behavior that lives in **jvspatial** is recorded in the [jvspatial changelog](../jvspatial/CHANGELOG.md).

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added

- **Drop-in `jvagent.embed.Server` subclass mounts jvagent's HTTP routes on the host automatically.** Hosts swap `from jvspatial.api import Server` for `from jvagent.embed import Server`; the subclass overrides `get_app()` so jvagent's `@endpoint`-decorated modules (admin agents CRUD, status, app/update_mode, graph repair, memory admin, action endpoints, logging, optional google OAuth) sync onto the server *before* FastAPI snapshots the endpoint router — guaranteeing routes appear on the live ASGI app. No timing-sensitive call between `Server(...)` and `get_app()` required. Suppress with `JVAGENT_EMBED_ENDPOINTS_DISABLED=true` (or `1`/`true`/`yes`/`on`, case-insensitive); the programmatic surface (`embed.interact`, `interact_stream`, etc.) stays available. The lower-level `embed.register_jvagent_endpoints_on_host(server)` function remains available for hosts that prefer explicit control. Shared importer lives in `jvagent.core.embed_endpoints`; the standalone CLI now delegates to it for parity.
- Startup index migration: `jvagent.core.index_bootstrap.run_index_migration()` runs before application graph bootstrap — drops deprecated MongoDB index names, then eagerly calls `ensure_indexes` for core entity types. Wired from `pre_startup_bootstrap` and `bootstrap_only`. See [docs/database-indexing.md](docs/database-indexing.md).
- Exception taxonomy in `jvagent.core.errors`: `TransientError`, `IntegrationError`, `ConfigError`, `LogicError`, plus `classify_exception()` / `is_transient()` helpers covering common httpx and stdlib exceptions. Foundation for replacing broad `except Exception:` clauses with classified handling.
- `jvagent.core.jvspatial_compat`: single chokepoint for jvspatial private-API access (`find_raw_node_records`). Used by `AgentLoader` ghost-node detection so the coupling lives in one place.
- `jvagent.tooling.tool_executor.ToolDispatchContext`: immutable dataclass exposing `agent_id`, `user_id`, `session_id`, `interaction_id`, `channel` to tool closures. `MCPAction` now reads it instead of the live walker.
- `jvagent.skills._action_helpers.resolve_action()`: shared resolver-and-error helper for skill scripts that delegate to actions; adopted by gmail / outlook_mail / calendar / outlook_calendar bundles.

### Changed

- **Cockpit replaces experimental stacks.** `jvagent/action/agent_interact/` and `jvagent/action/skill/` (plus the `agentic` scaffold profile and `docs/agent-interact.md`) are removed. `jvagent/cockpit_interact_action` is the unified routing + skill-loop action. Production importers (`action/endpoints.py`, `model/language/anthropic.py`, `skills/skill_hub/install_skill.py` and `remove_skill.py`) migrated to cockpit equivalents. Message-format helpers moved to `jvagent.action.model.utils.message_format`.
- **`action/cockpit/` reorganized** into role-based subpackages: `routing/` (router + types), `delivery/` (helpers + delegation + gates), `registry/` (assembler + access + shim), `catalog/` (skill_catalog + skill_discovery + action_resolver), `tools/` (artifact, conversation, memory, response, search, skill, task). Public API (`from jvagent.action.cockpit import …`) is unchanged.
- **`core/endpoints.py` split** into `core/endpoints/` package: `agents.py`, `status.py`, `conversation.py`, `app.py`, `graph_repair.py`. Public re-exports preserved via `__init__.py`.
- **Model credential resolution is env-only.** `BaseModelAction.api_key` and the Ollama `api_key` attribute are removed. `api_key_from_context()` scans environment variables (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `HUGGINGFACE_API_KEY`, `OLLAMA_API_KEY`, `HF_API_KEY`). Generic embedding action exposes `api_key_env` (default `GENERIC_EMBEDDING_API_KEY`). See [docs/environment-keys-reference.md](docs/environment-keys-reference.md).
- **Lazy event-loop locks** for `App` singleton (`App._get_lock()`), `CacheManager` (per-loop dict), and `MemoryLockManager._global_lock_for_loop()`. Module-import locks were unsafe on serverless warm starts.
- **Memory counter recount runs under a Memory-scoped lock** (`refresh_memory_counters_from_graph`) so concurrent `add_user` / `purge_user_memory` cannot interleave between the recount and corrective save. Orphan-interaction cleanup now warns instead of swallowing delete failures.
- **CLI flag tightening**: `--source` / `--merge` without `--update` exits 2 (was a silent warning); `--source` and `--merge` together also exit 2.
- **Tool-error logs sanitized when `sanitize_errors=True`**: `tooling/tool_executor.py` now logs only the exception class name (no `exc_info=True`) so provider response bodies, auth headers, or partial credentials stay out of the operator log. Raw exception still recorded on the `ToolExecutionEnvelope`.
- **Webhook posts pinned to a validated IP** (`core/callback.py`): `_resolve_and_validate()` returns the safe IPs and `_post_webhook_pinned_async()` issues the request against `scheme://<ip>:port/...` with the original `Host` header (and SNI hostname forwarded for HTTPS). Closes the DNS-rebinding TOCTOU between SSRF validation and httpx's own resolve.
- **Distributed conversation lock** (`memory/distributed_conversation_lock.py`): release-failure logs no longer use `exc_info=True`. Redis client and AWS error reprs can echo the connection URL or partial credentials; only the exception class name (Redis) or AWS error code (DynamoDB) is logged now.
- **Strict `..` reject across sandbox I/O** (`skills/fileinterface/scripts/_core.py`): `validate_relative_path` (renamed from `validate_relative_write_path`) is applied to every read and write; the `PathSanitizer` fallback no longer falls through to `rel.replace("..", "")`. The `*_with_local_fallback` helpers that wrote sandbox paths to the host filesystem were removed.

### Removed

- `jvagent/action/agent_interact/` and `jvagent/action/skill/` packages plus their tests, documentation page, and the `agentic` builtin profile.
- `BaseModelAction.api_key` and `OllamaLanguageModelAction.api_key` attributes.
- `Action.export()` redaction override and `_secret_attrs` ClassVar plumbing — there are no remaining secret attributes; reintroduce when the next one arrives.
- `jvagent.skills.fileinterface.scripts._core.write_text_file_with_local_fallback`, `create_directory_with_local_fallback`, and `copy_binary_file_with_local_fallback`. They had no internal callers and would silently write user content to the process cwd if storage failed.
