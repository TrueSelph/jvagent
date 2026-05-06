# Codebase Structure

**Analysis Date:** 2026-05-06

## Directory Layout

```
jvagent/                                    # Repository root
├── jvagent/                                # Main Python package (importable as `jvagent`)
│   ├── __init__.py                         # Exports __version__
│   ├── __main__.py                         # `python -m jvagent` shim
│   ├── env.py                              # App-id env helpers
│   ├── version.py                          # __version__ constant
│   ├── stress_seed_graph.py                # Synthetic memory-graph seeder for stress tests
│   ├── cli/                                # CLI subcommands + dispatch
│   │   ├── main.py                         # `jvagent` entry: argv parsing, subcommand dispatch
│   │   ├── commands.py                     # CLI handlers (run/status/agent/skill/action/...)
│   │   ├── app_commands.py                 # `jvagent app create` / `app profile new`
│   │   ├── bootstrap.py                    # Graph bootstrap orchestration + admin user
│   │   └── server_config.py                # Server build from app.yaml; endpoint module imports
│   ├── core/                               # Graph nodes + bootstrap + cross-cutting infra
│   │   ├── app.py                          # App node (singleton root)
│   │   ├── agents.py                       # Agents branchpoint + counters
│   │   ├── agent.py                        # Agent node + cached lookup
│   │   ├── app_loader.py / agent_loader.py # YAML descriptor loaders
│   │   ├── *_yaml_validator.py             # Validation issues for `jvagent validate`
│   │   ├── config.py                       # Centralized env/yaml/default resolution
│   │   ├── env_resolver.py                 # ${ENV} placeholder substitution in YAML
│   │   ├── cache.py                        # TTL caches (agent/actions/router)
│   │   ├── graph_repair*.py                # Distributed-lock graph repair
│   │   ├── repair_phases/                  # Repair pipeline engine
│   │   ├── observability.py                # Hookable event emitter
│   │   ├── public_url.py / channel.py      # URL building / channel normalization
│   │   ├── callback.py                     # SSRF-safe outbound webhooks
│   │   ├── startup.py                      # Lifecycle / scheduler bootstrap
│   │   ├── bootstrap_logger.py             # Phased status logger
│   │   ├── bootstrap_update_mode.py        # `App.update_mode` resolution + reset
│   │   ├── index_bootstrap.py              # MongoDB index creation
│   │   ├── dependency_installer.py         # info.yaml pip dependency installer
│   │   └── endpoints.py                    # Agent CRUD + admin repair endpoints
│   ├── memory/                             # User/Conversation/Interaction subgraph
│   │   ├── manager.py                      # Memory hub
│   │   ├── user.py / conversation.py / interaction.py
│   │   ├── task_store.py                   # Conversation-scoped Task/Step lifecycle
│   │   ├── evidence_log.py                 # Per-interaction evidence storage
│   │   ├── user_long_memory.py             # Long-memory node + retrieval helpers
│   │   ├── long_memory_retrieval_utils.py
│   │   ├── lock_manager.py / distributed_conversation_lock.py
│   │   ├── services/long_memory_service.py
│   │   └── endpoints.py                    # Memory admin endpoints
│   ├── action/                             # Pluggable action runtime (38 action packages)
│   │   ├── base.py                         # Action base class + lifecycle hooks
│   │   ├── actions.py                      # Actions manager node + register_action
│   │   ├── plugin_contracts.py             # Static plugin protocol shapes
│   │   ├── streaming.py                    # Stream helper utilities
│   │   ├── endpoints.py                    # Action CRUD admin endpoints
│   │   ├── loader/                         # Filesystem discovery + dynamic import
│   │   │   ├── action_loader.py            # ActionLoader (discover + instantiate)
│   │   │   ├── importer.py                 # `sys.meta_path` finder for `jvagent.actions.*`
│   │   │   ├── core_discovery.py           # Built-in action enumeration
│   │   │   ├── factory.py / metadata.py    # Metadata payload + ActionMetadata/ActionRegistry
│   │   │   ├── module_loading.py / info_yaml.py
│   │   ├── interact/                       # Walker + base + endpoints (interact subsystem)
│   │   │   ├── interact_walker.py          # InteractWalker (the execution engine)
│   │   │   ├── base.py                     # InteractAction base class
│   │   │   ├── endpoints.py                # POST /interact, /interact/stream
│   │   │   ├── rate_limiter.py / response_builder.py
│   │   │   └── utils/                      # Vision prompt builders + image interpretation
│   │   ├── cockpit/                        # CockpitInteractAction (current default)
│   │   │   ├── cockpit_interact_action.py  # Walker-revisit loop entry
│   │   │   ├── engine.py                   # Single-step think-act-observe iteration
│   │   │   ├── router.py / routing_types.py # Posture+skill classifier
│   │   │   ├── registry.py                 # Tool assembly (harness+action+skill)
│   │   │   ├── *_tools.py                  # Harness tool builders (memory/task/response/...)
│   │   │   ├── skill_catalog.py / skill_discovery.py
│   │   │   └── delivery.py / gates.py / shim.py / context.py / contracts.py
│   │   ├── agent_interact/                 # Legacy unified router+converse+skill
│   │   │   ├── agent_interact_action.py
│   │   │   ├── router/                     # Sub-router prompts + gates
│   │   │   └── skill/                      # Agentic loop helpers
│   │   ├── router/                         # InteractRouter (CoVe-prompted classifier)
│   │   ├── skill/                          # SkillAction + SkillInteractAction agentic loop
│   │   ├── persona/                        # PersonaAction (prompt + response generation)
│   │   ├── access_control/                 # Per-action gating + endpoints
│   │   ├── intro/                          # First-time-user intro action
│   │   ├── converse/                       # Conversational fallback
│   │   ├── interview/                      # Interview/branching action
│   │   ├── handoff_interact_action/        # Channel/agent handoff
│   │   ├── task_creation_interact_action/  # Task creation branch
│   │   ├── task_trigger_interact_action/   # Task trigger branch
│   │   ├── task_dispatcher/                # Background task dispatcher
│   │   ├── response/                       # ResponseBus + adapters + filters + chunking
│   │   ├── model/                          # Language + Embedding model providers
│   │   │   ├── base.py / context.py / cost_estimator.py / utils/
│   │   │   ├── language/                   # Anthropic, OpenAI, Ollama, OpenRouter
│   │   │   └── embedding/                  # Generic, HuggingFace, Ollama, OpenAI, OpenRouter
│   │   ├── google/                         # Google Calendar/Docs/Drive/Gmail/Sheets + OAuth
│   │   ├── microsoft/                      # Outlook Calendar/Mail, OneDrive, Excel + OAuth
│   │   ├── whatsapp/ facebook_action/      # IM channel adapters with webhooks
│   │   ├── postiz_action/ email_action/    # Outbound channels
│   │   ├── pageindex/                      # Vectorless tree-search RAG (Docling pipeline)
│   │   ├── vectorstore/                    # Vector store abstraction + Typesense
│   │   ├── retrieval/ web_search/ web_search_retrieval/
│   │   ├── long_memory/ long_memory_store/ long_memory_retrieval/
│   │   ├── mcp/                            # MCP client + sandboxed FS server
│   │   ├── stt_action/ tts_action/ avatar_action/ video_generation/
│   │   ├── agent_utils/ utils/             # Shared helpers
│   │   └── persona/, etc.
│   ├── skills/                             # Built-in Claude-style SKILL.md bundles (18)
│   │   ├── skill_hub/                      # Registry browser + installer
│   │   ├── research/ web_search/ answer/   # Reasoning + retrieval skills
│   │   ├── calendar/ gmail/ google_drive/ google_sheets/
│   │   ├── outlook_calendar/ outlook_mail/ microsoft_excel/ microsoft_onedrive/
│   │   ├── pageindex_docs/ pageindex_search/
│   │   ├── fileinterface/ pdf_generation/ triage/ code_review/
│   │   └── (each: SKILL.md + optional scripts/)
│   ├── tooling/                            # Provider-agnostic tool primitives
│   │   ├── tool.py                         # Tool dataclass
│   │   ├── tool_registry.py / tool_executor.py / tool_result.py / tool_serializer.py
│   │   ├── tool_observability.py / tool_schema_validator.py
│   ├── scaffold/                           # `jvagent app create` / `agent create` scaffolders
│   │   ├── operations.py / yaml_io.py / resource_io.py
│   │   ├── profile_resolve.py / skill_resolve.py / profile_stub.py
│   │   ├── builtin_profiles/               # minimal/conversational/agentic/research/whatsapp_voice
│   │   └── static/env.example.txt
│   ├── bundle/                             # Per-app Dockerfile generator
│   │   ├── bundler.py / dockerfile_generator.py / Dockerfile.base / README.md
│   ├── logging/                            # Custom INTERACTION level + log query endpoints
│   │   ├── service.py / endpoints.py
│   └── utils/                              # Internal misc helpers
├── jvchat/                                 # React + Vite + Tailwind frontend (separate package)
│   ├── src/{App.tsx,components,context,hooks,lib,types,utils,test}
│   ├── package.json / vite.config.ts / tsconfig.json / tailwind.config.js
│   └── README.md / config.yaml
├── jvdb/                                   # Auxiliary DB tooling (sibling project tree)
├── docs/                                   # Markdown documentation
│   ├── COCKPIT.md / agent-interact.md / configuration.md
│   ├── language-models.md / scaffolding.md / database-indexing.md
│   ├── interaction-logging.md / error-logging.md / logging.md
│   ├── task-tracking.md / security-review.md
│   ├── environment-keys-reference.md / integrations-environment.md
├── tests/                                  # Pytest suite (unit + integration)
│   ├── conftest.py
│   ├── action/                             # Per-action tests (sub-folders by action)
│   │   ├── access_control/ agent_interact/ email_action/ facebook_action/
│   │   ├── gating/ google/ interact/ interview/ long_memory/ mcp/ model/
│   │   ├── pageindex/ postiz_action/ response/ router/ skill/
│   │   ├── task_creation_interact_action/ task_dispatcher/ whatsapp/
│   │   └── test_action_endpoints.py / test_action_loader.py / test_persona_*.py / ...
│   ├── core/ memory/ scaffold/ skills/ cli/ bundle/ integration/
│   └── test_comprehensive_pruning.py / test_env_load.py / ...
├── examples/jvagent_app/                   # Reference application tree
│   ├── app.yaml
│   ├── agents/{jvagent,resolv}/.../agent.yaml
│   ├── README.md / SETUP.md / STRUCTURE.md / CHANGELOG.md
│   └── docs/ jvagent_db/ jvagent_logs/
├── pyproject.toml                          # Build, deps, pytest, mypy, black, isort
├── setup.py                                # Legacy setup hook
├── requirements*.txt                       # Pip lockfiles for full / dev / runtime
├── Dockerfile.base                         # Base image consumed by `jvagent bundle`
├── MANIFEST.in / .pre-commit-config.yaml / .flake8
├── README.md (76KB) / CHANGELOG.md / LICENSE / CLAUDE.md
├── cockpit_phaseA_smoke.py                 # Smoke test harness for cockpit phase A
├── .env.example                            # Documented env var template
└── .planning/codebase/                     # Generated codebase analysis (this directory)
```

## Directory Purposes

**`jvagent/cli/`:**
- Purpose: Console-script entry, argv parsing, subcommand dispatch, server bootstrap
- Contains: dispatcher (`main.py`), CLI handlers (`commands.py`, `app_commands.py`), server build (`server_config.py`), bootstrap orchestration (`bootstrap.py`)
- Key files: `main.py` (dispatcher), `commands.py` (~1257 lines: handlers for run/status/agent/skill/action/bundle/validate)

**`jvagent/core/`:**
- Purpose: Graph node definitions for the application root, plus all cross-cutting infrastructure
- Contains: `App`/`Agents`/`Agent` nodes; YAML loaders + validators; centralized config; cache; graph-repair; observability; channel + URL helpers; admin endpoints
- Key files: `app.py`, `agent.py`, `agents.py`, `app_loader.py`, `agent_loader.py`, `config.py`, `cache.py`, `graph_repair.py`, `endpoints.py`

**`jvagent/memory/`:**
- Purpose: Per-agent user/conversation/interaction state machine
- Contains: `Memory` hub, `User`/`Conversation`/`Interaction` nodes, `TaskStore`, `EvidenceLog`, long-memory primitives, conversation locking
- Key files: `manager.py`, `conversation.py`, `interaction.py`, `task_store.py`, `endpoints.py`

**`jvagent/action/`:**
- Purpose: All pluggable Action implementations + the loader that discovers them
- Contains: 38 action packages organized by domain (interact / model / persona / response / channels / retrieval / RAG / AV / tooling glue) plus a `loader/` subpackage and shared `base.py` / `actions.py` / `endpoints.py` / `streaming.py` / `plugin_contracts.py`
- Key files: `base.py` (Action base class), `actions.py` (registration manager), `endpoints.py` (action CRUD), `loader/action_loader.py`, `interact/interact_walker.py`, `cockpit/cockpit_interact_action.py`

**`jvagent/skills/`:**
- Purpose: Built-in Claude-style skill bundles shipped with jvagent
- Contains: 18 directories each with `SKILL.md` (frontmatter + workflow) and optional `scripts/` for tool implementations
- Key files: `skill_hub/SKILL.md` (registry browsing), `research/SKILL.md`, `web_search/SKILL.md`

**`jvagent/tooling/`:**
- Purpose: Provider-agnostic tool primitives consumed by cockpit / skill loops
- Contains: `Tool` dataclass, `ToolRegistry`, `ToolExecutionEngine`, `ToolResult`, schema validator, observability envelopes, serializer
- Key files: `tool.py`, `tool_registry.py`, `tool_executor.py`

**`jvagent/scaffold/`:**
- Purpose: Filesystem operations for `jvagent app create` / `agent create` / `skill add`
- Contains: profile resolution, YAML I/O, resource extraction, builtin profile templates, env example
- Key files: `operations.py`, `profile_resolve.py`, `skill_resolve.py`, `builtin_profiles/*.yaml`

**`jvagent/bundle/`:**
- Purpose: Generate per-app Dockerfile from discovered action `info.yaml` dependencies
- Contains: bundler, Dockerfile generator, base image template, README

**`jvagent/logging/`:**
- Purpose: Custom INTERACTION log level registration + log query API
- Contains: `service.py`, `endpoints.py`, `__init__.py` (registers level 22)

**`jvchat/`:**
- Purpose: TypeScript React frontend (chat UI + admin console)
- Contains: Vite + React + Tailwind project; consumes jvagent HTTP endpoints
- Generated build: `jvchat/dist/` (committed)

**`tests/`:**
- Purpose: Pytest suite (unit + integration); mirrors `jvagent/` package layout
- Contains: per-action subdirectories under `tests/action/`, plus `core/`, `memory/`, `scaffold/`, `skills/`, `cli/`, `bundle/`, `integration/`

**`examples/jvagent_app/`:**
- Purpose: Reference application tree usable as `jvagent /path/to/examples/jvagent_app`
- Contains: `app.yaml`, `agents/<ns>/<name>/agent.yaml`, sample DB / logs directories

**`docs/`:**
- Purpose: Long-form Markdown documentation referenced from README + agent.yaml
- Key files: `COCKPIT.md`, `agent-interact.md`, `language-models.md`, `scaffolding.md`, `task-tracking.md`

**`.planning/codebase/`:**
- Purpose: Auto-generated codebase analysis (this directory) consumed by GSD planning commands
- Contains: STACK.md, INTEGRATIONS.md, ARCHITECTURE.md, STRUCTURE.md, CONVENTIONS.md, TESTING.md, CONCERNS.md

## Key File Locations

**Entry Points:**
- `jvagent/__main__.py`: `python -m jvagent` shim → `jvagent.cli.main`
- `jvagent/cli/main.py`: console-script `main()` (declared in `pyproject.toml [project.scripts]`)
- `jvagent/cli/server_config.py:create_server_from_config`: HTTP server construction
- `jvagent/action/interact/endpoints.py`: `POST /interact` + streaming variant
- `jvagent/core/endpoints.py`: agent CRUD + graph repair
- `jvagent/action/endpoints.py`: action CRUD
- Per-channel webhooks: `jvagent/action/whatsapp/endpoints.py`, `jvagent/action/facebook_action/endpoints.py`, `jvagent/action/postiz_action/endpoints.py`, `jvagent/action/pageindex/endpoints.py`
- OAuth callbacks: `jvagent/action/google/endpoints.py`, `jvagent/action/microsoft/endpoints.py`

**Configuration:**
- `pyproject.toml`: build (setuptools), deps, pytest, mypy, black, isort
- `.pre-commit-config.yaml`: black + isort + flake8 + detect-secrets hooks
- `.flake8`: lint config (88-col line length, plugins for docstrings/comprehensions/bugbear/etc.)
- `requirements.txt` / `requirements-dev.txt` / `requirements-all.txt`: pinned deps
- `.env.example`: documented runtime env keys (~12.6KB)
- `Dockerfile.base`: base image consumed by `jvagent bundle`
- `examples/jvagent_app/app.yaml`: reference application descriptor
- `jvagent/scaffold/builtin_profiles/{minimal,conversational,agentic,research,whatsapp_voice}.yaml`: action profile templates

**Core Logic:**
- `jvagent/core/app.py`: App singleton node
- `jvagent/core/agent.py`, `agents.py`: Agent + Agents structural nodes
- `jvagent/action/base.py`: Action base class
- `jvagent/action/interact/interact_walker.py`: Walker that drives every interaction
- `jvagent/action/interact/base.py`: InteractAction base class
- `jvagent/action/cockpit/cockpit_interact_action.py`: current default interact action (model cockpit)
- `jvagent/action/cockpit/engine.py`: think-act-observe single-step engine
- `jvagent/action/agent_interact/agent_interact_action.py`: legacy unified router+converse+skill loop
- `jvagent/action/router/interact_router.py`: standalone CoVe routing classifier
- `jvagent/action/skill/skill_action.py`: programmatic agentic loop (used outside the interact subsystem)
- `jvagent/action/persona/persona_action.py`: persona prompt + response generation
- `jvagent/action/response/response_bus.py`: ResponseBus pub/sub for streaming
- `jvagent/memory/manager.py`: Memory hub
- `jvagent/memory/task_store.py`: conversation-scoped Task / Step lifecycle
- `jvagent/tooling/tool_executor.py`: tool dispatch engine

**Loader / bootstrap:**
- `jvagent/cli/bootstrap.py`: `bootstrap_application_graph` orchestration
- `jvagent/core/app_loader.py`: `AppLoader` (app.yaml → App + Agents)
- `jvagent/core/agent_loader.py`: `AgentLoader` (agent.yaml → Agent + Actions)
- `jvagent/action/loader/action_loader.py`: filesystem discovery + dynamic import
- `jvagent/action/loader/importer.py`: `sys.meta_path` finder for `jvagent.actions.*`

**Testing:**
- `tests/conftest.py`: shared fixtures
- `tests/action/<action>/`: per-action behavior tests
- `tests/core/`: validators, env resolver, graph repair, callbacks, secrets, startup
- `tests/memory/`: memory subgraph behavior
- `tests/integration/`: cross-component integration tests

## Naming Conventions

**Files:**
- Snake_case Python modules: `interact_walker.py`, `task_store.py`
- One archetype class per `*_action.py` file matching `info.yaml:package.archetype`
- Endpoint registries always named `endpoints.py` (auto-discovered by `_import_core_endpoint_modules`)
- Validators suffix: `*_yaml_validator.py`
- Tool builders suffix: `*_tools.py` (cockpit harness builders)

**Directories:**
- Action package = single directory under `jvagent/action/<action_dir>/` with `__init__.py` + `<action_name>.py` + `endpoints.py?` + `info.yaml`
- Provider sub-actions nest under their parent: `jvagent/action/google/google_calendar_action/`, `jvagent/action/model/language/anthropic/`
- Skill bundle directory contains `SKILL.md` and optional `scripts/`

**Action labels:** `<namespace>/<action_name>` (e.g., `jvagent/cockpit`, `jvagent/persona`, `contrib/<x>`, `custom/<x>`)
**Agent refs:** `<namespace>/<agent_name>` (e.g., `jvagent/cockpit_agent`, `acme/bot`)

**Class names:**
- Nodes: PascalCase singular (`App`, `Agent`, `Action`, `Memory`, `User`, `Conversation`, `Interaction`)
- Walkers: `<Subject>Walker` (`InteractWalker`)
- Actions: `<Name>Action` (`PersonaAction`, `CockpitInteractAction`, `OpenAILanguageModelAction`)
- InteractActions: `<Name>InteractAction` suffix when interact-only (`IntroInteractAction`, `ConverseInteractAction`)

## Where to Add New Code

**New built-in Action:**
- Primary code: `jvagent/action/<action_name>/`
  - `<action_name>.py` (subclass `Action`, `InteractAction`, `LanguageModelAction`, etc.)
  - `info.yaml` (declare `package.archetype` matching the class name)
  - `__init__.py` (export the class; import `endpoints.py` if any)
  - `endpoints.py` (optional `@endpoint`-decorated handlers)
- Tests: `tests/action/<action_name>/test_*.py`
- Profile reference (optional): add to `jvagent/scaffold/builtin_profiles/*.yaml`
- Documentation: `docs/<action_name>.md` if user-facing

**New custom (app-local) Action:**
- Primary code: `<app_root>/agents/<agent_ns>/<agent_name>/actions/<action_ns>/<action_name>/` (matches `JvagentActionsImporter` layout)
- Reference in `<app_root>/agents/<agent_ns>/<agent_name>/agent.yaml` under `actions:`
- Run `jvagent <app_root> --update` (merge) or `jvagent <app_root> bootstrap --update`

**New InteractAction (participates in walker pipeline):**
- Subclass `jvagent/action/interact/base.py:InteractAction`
- Implement `async def execute(walker)`; call `walker.visit([...])` or `walker.prepend([...])` to route
- Set `weight` (lower = earlier; negative for high precedence)
- Set `always_execute=True` for routing exceptions; `run_in_background=True` for post-response work
- Set `is_singleton=True` if only one instance per agent (enforced in `Actions.register_action()`)

**New cockpit harness tool:**
- Add a `_build_<area>_tools(ctx)` function in `jvagent/action/cockpit/<area>_tools.py` returning `List[Tool]`
- Register it in `jvagent/action/cockpit/registry.py:_register_harness_tools`

**New skill bundle (built-in):**
- Directory: `jvagent/skills/<skill_name>/`
- Files: `SKILL.md` (frontmatter `name`, `description`, `allowed-tools`, `tags`, `version` + workflow body), `scripts/<tool>.py` for executable tools
- Register tags as needed in `SkillCatalog`; available via `skills_source: builtin` or `both` in agent.yaml

**New skill bundle (per-agent):**
- Directory: `<app_root>/agents/<agent_ns>/<agent_name>/skills/<skill_name>/SKILL.md`
- Created via `jvagent skill add <agent_ref> <skill_name>`

**New endpoint:**
- Add inside the relevant module's `endpoints.py` using `@endpoint(path, methods=[...], auth=..., roles=[...], tags=[...], response=success_response(...))`
- Add module to `_import_core_endpoint_modules` in `jvagent/cli/server_config.py:51` if it's a new core module
- Action-local endpoints are auto-imported when the action's `__init__.py` imports them

**New CLI subcommand:**
- Add command name to `DISPATCH` in `jvagent/cli/main.py:42`
- Add handler dispatch branch in `main()` (`jvagent/cli/main.py:201`)
- Implement handler in `jvagent/cli/commands.py` (or a new module)
- Update `print_usage()` (`jvagent/cli/commands.py:236`)

**New Node type (graph entity):**
- File: `jvagent/<domain>/<node_name>.py`
- Subclass `jvspatial.core.Node`; declare typed fields with `attribute(...)`; add `@compound_index` for query patterns
- Wire creation in the appropriate manager node's helpers; ensure cascade-delete semantics via edge connections

**New language model provider:**
- Directory: `jvagent/action/model/language/<provider>/`
- Subclass `jvagent/action/model/language/base.py:LanguageModelAction`
- Add provider to `model_action_type` examples in profile YAMLs
- Tests: `tests/action/model/<provider>/`

**New tests:**
- Mirror the source tree under `tests/`
- Use `pytest-asyncio` (`asyncio_mode = "auto"` in `pyproject.toml`)
- Shared fixtures live in `tests/conftest.py`

**New documentation:**
- User-facing: `docs/<topic>.md` and link from `README.md`
- Developer-facing: docstrings at module + class + public method level

## Special Directories

**`jvagent/scaffold/builtin_profiles/`:**
- Purpose: Action profile templates used by `jvagent app create` / `jvagent agent create`
- Generated: No (committed source)
- Committed: Yes
- Includes: `minimal.yaml`, `conversational.yaml`, `agentic.yaml`, `research.yaml`, `whatsapp_voice.yaml`

**`jvagent/skills/`:**
- Purpose: Built-in Claude-style skill bundles
- Generated: No
- Committed: Yes
- Includes: 18 bundles; each has `SKILL.md` + optional `scripts/`

**`examples/jvagent_app/jvagent_db/` and `jvagent_logs/`:**
- Purpose: Example app's local JSON databases (created at first run)
- Generated: Yes (at runtime by jvspatial JSONDatabase)
- Committed: Partial (sample data may be checked in for examples)

**`jvchat/dist/`:**
- Purpose: Built frontend assets
- Generated: Yes (`npm run build`)
- Committed: Yes (per repo convention; check git history)

**`build/`, `jvagent.egg-info/`, `.mypy_cache/`, `.pytest_cache/`, `.pycache_sandbox/`, `__pycache__/`:**
- Purpose: Build / cache artifacts
- Generated: Yes
- Committed: No (in `.gitignore`)

**`.venv/`:**
- Purpose: Local Python virtualenv
- Committed: No

**`.files/`:**
- Purpose: Default file-storage root for local provider (configurable via `JVSPATIAL_FILES_ROOT_PATH`)
- Generated: Yes
- Committed: No

**`jvagent_demo_app_pageindex_db/`:**
- Purpose: PageIndex sample database for the demo app
- Generated: Yes (at runtime)
- Committed: Partial (test fixture data)

**`.planning/codebase/`:**
- Purpose: Auto-generated codebase analysis (this directory)
- Generated: Yes (by `/gsd-map-codebase`)
- Committed: Yes

**`agents/` (within an app root):**
- Purpose: Per-app agent + custom-action tree consumed by `AgentLoader`
- Layout: `agents/<agent_ns>/<agent_name>/{agent.yaml, actions/<action_ns>/<action_name>/...}`
- Generated: Created by `jvagent agent create` then hand-edited
- Committed: Yes (project-specific)

---

*Structure analysis: 2026-05-06*
