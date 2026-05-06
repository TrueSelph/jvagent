# Codebase Structure

**Analysis Date:** 2026-05-06

## Directory Layout

```
jvagent/
├── cli/                              # CLI entry point and commands
│   ├── main.py                       # Main CLI dispatcher
│   ├── bootstrap.py                  # Bootstrap initialization logic
│   ├── commands.py                   # CLI subcommand handlers
│   ├── app_commands.py               # App-specific CLI commands
│   └── server_config.py              # Server configuration
│
├── core/                             # Core domain entities and bootstrap
│   ├── app.py                        # App node (root application)
│   ├── agent.py                      # Agent node (individual agent)
│   ├── agents.py                     # Agents node (structural branchpoint)
│   ├── app_loader.py                 # Bootstrap App from app.yaml
│   ├── agent_loader.py               # Bootstrap Agent from agent.yaml
│   ├── app_yaml_validator.py         # Validate app.yaml schema
│   ├── agent_yaml_validator.py       # Validate agent.yaml schema
│   ├── cache.py                      # Application-level caching (agents, actions)
│   ├── config.py                     # Configuration resolution (env vars, defaults)
│   ├── env_resolver.py               # Environment variable resolution for actions
│   ├── endpoints.py                  # Agent CRUD REST endpoints
│   ├── graph_repair.py               # Graph consistency repair logic
│   ├── graph_repair_job.py           # Graph repair job execution
│   ├── graph_repair_handlers.py      # Graph repair repair phases
│   ├── repair_scratch.py             # Temporary storage for repair state
│   ├── repair_state.py               # Repair state tracking
│   ├── callback.py                   # Event callbacks for lifecycle hooks
│   ├── channel.py                    # Channel definitions
│   ├── startup.py                    # App startup hooks
│   ├── bootstrap_logger.py           # Bootstrap-time logging
│   ├── bootstrap_update_mode.py      # Bootstrap update mode logic
│   ├── app_context.py                # App context storage
│   ├── observability.py              # Observability hook system
│   ├── graph_traversal.py            # Graph traversal helpers
│   ├── profiling.py                  # Performance profiling utilities
│   ├── index_bootstrap.py            # Index creation during bootstrap
│   ├── dependency_installer.py       # Install action dependencies
│   ├── benchmark.py                  # Benchmarking utilities
│   ├── public_url.py                 # Public URL resolution
│   └── repair_phases/                # Graph repair phases
│       └── *.py                      # Individual repair phase implementations
│
├── action/                           # Action framework and built-in actions
│   ├── base.py                       # Base Action class (all actions extend)
│   ├── actions.py                    # Actions node (action manager per agent)
│   ├── endpoints.py                  # Action CRUD endpoints
│   ├── plugin_contracts.py           # Plugin interface contracts
│   ├── streaming.py                  # Streaming helpers
│   │
│   ├── interact/                     # Interact subsystem (user interaction pipeline)
│   │   ├── base.py                   # InteractAction base class
│   │   ├── interact_walker.py        # InteractWalker (main execution engine)
│   │   ├── endpoints.py              # Interact HTTP endpoints (POST /interact)
│   │   ├── response_builder.py       # Response formatting and building
│   │   ├── rate_limiter.py           # Rate limiting per user/agent
│   │   └── utils/                    # Interact utilities
│   │
│   ├── agent_interact/               # Unified skill-routing interact action
│   │   ├── agent_interact_action.py  # AgentInteractAction (router + skill loop)
│   │   ├── router/                   # Routing logic (intent classification)
│   │   │   ├── gates.py              # Routing decision gates
│   │   │   └── prompts.py            # Routing prompts/templates
│   │   └── skill/                    # Skill execution within agent interact
│   │       ├── agentic_loop.py       # Think-act-observe loop
│   │       ├── context.py            # Skill run context
│   │       ├── contracts.py          # Skill contracts
│   │       ├── converse_delivery.py  # Conversational response delivery
│   │       ├── hot_reload.py         # Dynamic skill (re)loading
│   │       ├── run_config.py         # Skill run configuration
│   │       └── shim.py               # Walker visitor adapter
│   │
│   ├── router/                       # Legacy routing action
│   │   └── *.py                      # Legacy router implementation
│   │
│   ├── skill/                        # Skill loop action (agentic think-act-observe)
│   │   ├── skill_action.py           # SkillAction main execution
│   │   ├── skill_action_contracts.py # Skill action contracts
│   │   ├── skill_catalog.py          # Skill discovery and catalog
│   │   ├── skill_interact_action.py  # Legacy skill-only interact action
│   │   ├── tool_executor.py          # Execute tool calls from LM
│   │   ├── tool_registry.py          # Register skills as tools
│   │   ├── loop_context.py           # Skill loop execution context
│   │   ├── loop_checkpoint.py        # Loop state checkpoint
│   │   ├── stuck_detector.py         # Detect infinite loops
│   │   ├── recovery_policy.py        # Loop recovery strategies
│   │   ├── context_compactor.py      # Compress loop context for LM
│   │   ├── prompts.py                # Skill loop system prompts
│   │   ├── README.md                 # Skill system documentation
│   │   └── version_utils.py          # Version checking utilities
│   │
│   ├── converse/                     # Conversational (persona-based) action
│   │   └── *.py
│   │
│   ├── persona/                      # Persona action (format responses per persona)
│   │   └── persona_action.py
│   │
│   ├── model/                        # Language models and embeddings
│   │   ├── base.py                   # Base model action class
│   │   ├── language/                 # Language model actions
│   │   │   ├── anthropic/            # Anthropic Claude
│   │   │   ├── openai/               # OpenAI GPT
│   │   │   ├── ollama/               # Ollama local models
│   │   │   ├── openrouter/           # OpenRouter multi-provider
│   │   │   └── base.py               # LanguageModelAction base class
│   │   ├── embedding/                # Embedding model actions
│   │   │   ├── openai/               # OpenAI embeddings
│   │   │   ├── huggingface/          # HuggingFace embeddings
│   │   │   ├── ollama/               # Ollama embeddings
│   │   │   ├── openrouter/           # OpenRouter embeddings
│   │   │   └── generic/              # Generic HTTP embedding service
│   │   └── utils/                    # Model utilities
│   │
│   ├── retrieval/                    # Retrieval and context building
│   │   ├── base.py
│   │   ├── endpoints.py
│   │   └── utils/
│   │
│   ├── vectorstore/                  # Vector database abstraction
│   │   ├── base.py                   # VectorStore base class
│   │   └── typesense/                # Typesense vector store
│   │
│   ├── long_memory/                  # Long-term memory storage
│   ├── long_memory_store/            # Long-term memory persistence
│   ├── long_memory_retrieval/        # Long-term memory retrieval
│   │
│   ├── pageindex/                    # Document indexing and retrieval
│   │   ├── core/                     # Core pageindex logic
│   │   ├── pageindex_action/         # Main pageindex action
│   │   ├── pageindex_google_drive_sync_action/  # Google Drive sync
│   │   └── pageindex_retrieval_interact_action/ # Retrieval interact
│   │
│   ├── response/                     # Response bus and publishing
│   │   └── response_bus.py
│   │
│   ├── google/                       # Google Workspace integrations
│   │   ├── google_calendar_action/
│   │   ├── google_docs_action/
│   │   ├── google_drive_action/
│   │   ├── google_gmail_action/
│   │   └── google_sheets_action/
│   │
│   ├── microsoft/                    # Microsoft 365 integrations
│   │   ├── microsoft_excel_action/
│   │   ├── microsoft_onedrive_action/
│   │   ├── microsoft_outlook_calendar_action/
│   │   └── microsoft_outlook_mail_action/
│   │
│   ├── email_action/                 # Email handling
│   │   ├── inbound/                  # Inbound email processing
│   │   ├── modules/                  # Email modules
│   │   └── utils/                    # Email utilities
│   │
│   ├── web_search/                   # Web search integrations
│   │   ├── brave/                    # Brave Search
│   │   ├── serper/                   # Serper
│   │   └── serpapi/                  # SerpAPI
│   │
│   ├── web_search_retrieval/         # Web search + retrieval integration
│   ├── interview/                    # Interview/questionnaire action
│   │   ├── core/                     # Core interview logic
│   │   │   ├── foundation/
│   │   │   ├── session/
│   │   │   ├── classification/
│   │   │   ├── processing/
│   │   │   ├── graph/
│   │   │   └── utils/
│   │   └── docs/
│   │
│   ├── stt_action/                   # Speech-to-text
│   │   ├── deepgram/
│   │   └── modules/
│   │
│   ├── tts_action/                   # Text-to-speech
│   │   ├── elevenlabs/
│   │   └── modules/
│   │
│   ├── task_creation_interact_action/  # Task creation within interaction
│   ├── task_trigger_interact_action/   # Task triggering
│   ├── task_dispatcher/                # Task distribution
│   ├── handoff_interact_action/        # Handoff to external service
│   ├── access_control/                 # Access control checks
│   ├── loader/                         # Action loading
│   ├── avatar_action/                  # User avatar/profile action
│   ├── intro/                          # Intro/onboarding action
│   ├── cockpit/                        # Agent cockpit/dashboard
│   ├── postiz_action/                  # Postiz social media integration
│   ├── facebook_action/                # Facebook integration
│   ├── whatsapp/                       # WhatsApp integration
│   ├── video_generation/               # Video generation
│   ├── mcp/                            # Model Context Protocol
│   ├── agent_utils/                    # Agent utility actions
│   └── utils/                          # Action utilities
│
├── memory/                           # Memory system (users, conversations, interactions)
│   ├── manager.py                    # Memory node (root for user/conversation tree)
│   ├── user.py                       # User node
│   ├── conversation.py               # Conversation node (session)
│   ├── interaction.py                # Interaction node (single exchange)
│   ├── endpoints.py                  # Memory REST endpoints
│   ├── evidence_log.py               # Evidence/audit logging
│   ├── task_store.py                 # Task tracking within conversation
│   ├── user_long_memory.py           # User long-term memory
│   ├── services/                     # Memory services
│   ├── lock_manager.py               # Distributed locking for concurrent operations
│   ├── distributed_conversation_lock.py # Conversation-level locks
│   └── long_memory_retrieval_utils.py # Long-term memory retrieval
│
├── scaffold/                         # Project scaffolding and initialization
│   ├── operations.py                 # Scaffold operations
│   ├── profile_resolve.py            # Resolve agent profiles
│   ├── skill_resolve.py              # Resolve skills
│   ├── resource_io.py                # Resource file I/O
│   ├── yaml_io.py                    # YAML file handling
│   ├── builtin_profiles/             # Built-in agent profiles
│   └── static/                       # Static scaffold templates
│
├── skills/                           # Built-in skills (used by skill loop)
│   ├── answer/                       # Answer/knowledge skill
│   ├── calendar/                     # Calendar skill
│   ├── code_review/                  # Code review skill
│   ├── fileinterface/                # File interface skill
│   ├── gmail/                        # Gmail skill
│   ├── google_drive/                 # Google Drive skill
│   ├── google_sheets/                # Google Sheets skill
│   ├── microsoft_excel/              # Excel skill
│   ├── microsoft_onedrive/           # OneDrive skill
│   ├── outlook_calendar/             # Outlook Calendar skill
│   ├── outlook_mail/                 # Outlook Mail skill
│   ├── pageindex_docs/               # Document retrieval skill
│   ├── pageindex_search/             # Document search skill
│   ├── pdf_generation/               # PDF generation skill
│   ├── research/                     # Research skill
│   ├── skill_hub/                    # Skill hub (discover/list skills)
│   ├── triage/                       # Issue triage skill
│   └── web_search/                   # Web search skill
│
├── tooling/                          # Tool registry and execution (low-level)
│   ├── tool.py                       # Tool definition
│   ├── tool_registry.py              # Register tools
│   ├── tool_executor.py              # Execute tool calls
│   ├── tool_result.py                # Tool result handling
│   ├── tool_schema_validator.py      # Validate tool schemas
│   ├── tool_serializer.py            # Serialize/deserialize tools
│   └── tool_observability.py         # Tool observability hooks
│
├── logging/                          # Logging system
│   ├── service.py                    # Logging service
│   └── endpoints.py                  # Logging endpoints
│
├── bundle/                           # Docker bundle generation
│   ├── bundler.py                    # Bundle creator
│   └── dockerfile_generator.py       # Dockerfile generation
│
├── utils/                            # Shared utilities
│   └── *.py                          # Various utility modules
│
├── env.py                            # Environment configuration
├── version.py                        # Version information
├── __init__.py                       # Package initialization
├── __main__.py                       # Module entry point
└── stress_seed_graph.py              # Stress testing utilities
```

## Directory Purposes

**cli/**: Command-line interface entry point; handles bootstrap, server startup, agent/skill management, and admin commands. Entry point: `cli/main.py::main()`

**core/**: Core domain entities (App, Agent, Agents) and bootstrap system. Handles app/agent initialization from YAML, caching, configuration, graph repair, and REST endpoints.

**action/**: Action framework and all built-in actions. BaseAction defines plugin interface; InteractAction extends it for interact subsystem. Organized by capability (model providers, integrations, interact components).

**memory/**: User/conversation/interaction management. Implements bidirectional chaining of interactions, cascade delete behavior, task tracking, and distributed locking for concurrent access.

**scaffold/**: Project creation and agent profile scaffolding. Resolves built-in profiles, skill resolution, YAML template generation.

**skills/**: Built-in skills (registered in SkillCatalog) that can be invoked as tools during agentic loops. Each skill defines tool schema and handler.

**tooling/**: Low-level tool definition, registration, and execution. Used by SkillAction to invoke tools. Separate from action.skill to decouple tool execution from skill loop logic.

**logging/**: Centralized logging service; integrates with jvspatial logging framework.

**bundle/**: Docker containerization utilities; generates Dockerfile and bundle artifacts for deployment.

**utils/**: Miscellaneous shared utilities.

## Key File Locations

**Entry Points:**
- `cli/main.py`: CLI dispatcher (entry point for `jvagent` command)
- `__main__.py`: Module entry point (python -m jvagent)
- `action/interact/endpoints.py::interact()`: HTTP POST /interact endpoint

**Configuration:**
- `core/app.yaml` (loaded by AppLoader)
- `core/agent.yaml` (loaded by AgentLoader)
- `action/{namespace}/{action_name}/info.yaml` (action metadata)
- `skills/{skill_name}/SKILL.md` (skill definition)

**Core Logic:**
- `core/app_loader.py`: Bootstrap App from app.yaml
- `core/agent_loader.py`: Bootstrap Agent from agent.yaml and discover/register actions
- `action/interact/interact_walker.py`: Main interaction execution engine
- `action/agent_interact/agent_interact_action.py`: Unified router + skill loop
- `action/skill/skill_action.py`: Agentic loop (think-act-observe)
- `action/skill/tool_executor.py`: Execute tool calls
- `action/skill/skill_catalog.py`: Skill discovery and registry

**Testing:**
- `tests/`: Mirror structure of `jvagent/`; test files co-located with implementations

## Naming Conventions

**Files:**
- `*_action.py`: Action class implementation (e.g., `persona_action.py`)
- `*_walker.py`: Walker class implementation (e.g., `interact_walker.py`)
- `endpoints.py`: REST API endpoints module
- `base.py`: Base class definitions
- `*.py`: Most other modules use snake_case with no suffix

**Directories:**
- `{action_name}/`: Action packages (e.g., `persona/`, `skill/`, `google/`)
- `{namespace}/`: Namespace groupings (e.g., `google/`, `microsoft/`)
- `{module_name}/`: Feature modules (e.g., `memory/`, `core/`, `action/`)
- Singular/plural: Use singular for conceptual groupings (`action/`, `skill/`), plural for collections when semantically correct

**Classes:**
- `{ActionName}Action`: All Action subclasses (e.g., `PersonaAction`, `AgentInteractAction`, `SkillAction`)
- `{EntityName}`: Domain entities (e.g., `User`, `Conversation`, `Interaction`)
- `{OperationName}Walker`: Walker subclasses (e.g., `InteractWalker`)

**Variables/Functions:**
- `snake_case`: Functions, variables, module names
- `UPPER_CASE`: Module-level constants
- `_private_name`: Private module-level or method-level items

## Where to Add New Code

**New Feature (end-to-end):**
- Primary code: `action/{namespace}/{feature_name}/` (new Action subclass)
- REST endpoints: `action/{namespace}/{feature_name}/endpoints.py`
- Metadata: `action/{namespace}/{feature_name}/info.yaml`
- Tests: `tests/action/{namespace}/{feature_name}/`
- Export: Update `action/{namespace}/{feature_name}/__init__.py` to export Action class

**New Interact Action (user-facing):**
- Implementation: `action/interact/{action_name}/base.py` or `action/{namespace}/{action_name}/interact_action.py`
- Extend: `InteractAction` base class
- Implement: `execute(visitor)` method to handle interaction
- Register: Declare in `agent.yaml` under `actions:`
- Weight: Set `weight` attribute for top-tier execution ordering

**New Skill (for skill loop):**
- Definition: `skills/{skill_name}/SKILL.md` (declare tool schema, handler, metadata)
- Handler: `action/skill/` (or action-specific skill module)
- Registration: Discovered automatically by SkillCatalog at startup
- Tool schema: Define via Pydantic model or dict in `SKILL.md`

**New Domain Entity:**
- Class definition: `core/{entity_name}.py` or `memory/{entity_name}.py`
- Extend: `jvspatial.core.Node`
- Attributes: Use `attribute()` descriptors
- CRUD: Implement static methods (`get()`, `find_one()`, `delete()`)
- Relationships: Connect via edges using `connect()`, `node()`, `nodes()`

**New Utility Module:**
- Location: `utils/{feature_name}.py` (for shared utilities)
- Or: Create within feature package if only used there
- Import: Export from `utils/__init__.py`

**New Configuration:**
- App-level: Add to `app.yaml` under app context or top-level
- Agent-level: Add to `agent.yaml` under agent context or action context
- Validation: Update `core/app_yaml_validator.py` or `core/agent_yaml_validator.py`

## Special Directories

**tests/**: Test suite mirroring main codebase structure. Run via `pytest tests/`. Tests are unit (mocked dependencies) and integration (real database).

**.planning/**: Planning artifacts (generated by GSD commands)
- `codebase/`: Codebase analysis documents (ARCHITECTURE.md, STRUCTURE.md, etc.)

**.files/**: Default file storage root (configurable via app.yaml `file_storage_root_dir`). Not committed; local data only.

**docs/**: Documentation (user guides, API reference, architecture deep-dives).

**examples/**: Example applications and configurations.

---

*Structure analysis: 2026-05-06*
