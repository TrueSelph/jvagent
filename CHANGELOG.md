# Changelog

All notable changes to **jvagent** (this package) are documented here. Indexing and database-adapter behavior that lives in **jvspatial** is recorded in the [jvspatial changelog](../jvspatial/CHANGELOG.md).

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [PEP 440](https://peps.python.org/pep-0440/) /
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- **MCP OAuth tokens encrypted at rest.** `MCPOAuthAction` uses
  `encrypt_token_for_storage`; `client_secret` is no longer persisted (resolved
  from env at use time). Legacy plaintext JSON still decrypts. Extra routes
  declared via `additional_endpoint_path_prefixes` so deregister cleans `/mcp/`.

### Fixed

- **TaskMonitor tick endpoint** raises `ResourceNotFoundError` when the monitor
  is missing (was HTTP 200 + error body).
- **Orchestrator fail-open validation (Wave 2).** Precondition startup validation
  logs WARNING when skills declare unregistered preconditions (runtime fail-open
  unchanged); `apply_abandon` promoted to public API; coupling guard allowlists
  intentional ADR-0034 touchpoints; AC fail-open / fail-closed behavior documented.
- **Hygiene and docs coverage (Wave 3).** Async bootstrap helpers (`yaml_io.py`,
  `install_action_dependencies_async`, webhook DNS in `asyncio.to_thread`); logger
  import cleanup; User docstring; `re` import; `_graph_context` helper. Docs:
  orchestrator.yaml profile name, examples/jvagent_app references orchestrator/leadgen
  agents, tests/CLAUDE.md layout, MCP README fileinterface pointer. Consolidated
  boolean-env parsers into `env_resolver.parse_bool_env`.
- **Dead code and scaffolding removal (Wave 4).** Reverted `repair_phases/` parallel
  M1 scaffolding — moved `RepairLimits`, `STATE_VERSION`, phase constants, and memory
  repair functions inline into `graph_repair_job.py`; removed `_repair_checkpoint`
  alias. Collapsed callback duplication — removed dead `try: pass` block and
  `_validate_webhook_url` wrapper. Deleted unused symbols: `get_conversation_history`,
  `get_event_history`, `get_interpretation_history`, `get_context_history` (use
  `get_interaction_history` with flags); `ToolSerializer`; `SkillActivationEnvelope`;
  `chunk_text_by_words`, `chunk_text_by_chars` (keep `chunk_text_by_lm_tokens`);
  `with_hint` from interview hooks; `is_development_mode`; `assert_parameters_schema_clean`.
  Updated memory/README.md and docs/security-review.md to point to current functions.

### Changed

- **Lint gate honesty.** Pre-commit flake8 plugins and mypy type-stub deps are
  pinned; `[tool.mypy]` matches the hook (`strict_optional = false`, etc.) so
  `mypy jvagent/` agrees with CI; `.flake8` sets `max-complexity = 90`; CLAUDE.md
  documents the real (non-strict) typing bar. Fixed flake8-bugbear B042/B043 in
  `manifest.py` / `llm_bridge.py`. Hot-path None guards in MCP tool selection,
  TaskMonitor tick, and interview set_fields.

## [0.1.2] - 2026-07-17

### Added

- **Interview `for_each` staging (#80).** Parent fields can stage remaining
  subpart items in one turn: `for_each_staged` is applied after the parent
  store + post-processors, staged items auto-advance, and matching records are
  preserved on parent re-entry. Static `prompt_prefix` is replaced by
  singularized/ordinal defaults with an optional `for_each_prefix`; tool
  responses surface `for_each` metadata + model nudges. Interview-scoped
  `parameters` (and `INTERVIEW_CORE_PARAMETERS`) inject only while a session is
  active.
- **Google Drive shared-drive support (#80).** `supportsAllDrives` on Drive/
  Sheets calls, shared-drive listing, `drive_id`, and
  `get_shared_drive_metadata`.
- **PageIndex jvforge resilience (#80).** Retry transient jvforge assimilate
  POSTs; shared-drive root handling + client invalidation on Drive sync errors.

### Security

- **Egress authz (once-over S1).** `POST .../email/send` requires `roles=["admin"]` (was any authenticated caller). SendGrid inbound drops messages unless SPF and DKIM pass before minting a User from `From`. `reply/publish` binds `user_id` to the authenticated caller (rejects foreign `user_id`). WhatsApp `/whatsapp/{action_id}/qr` and connection page require admin (were `auth=False`).

### Fixed

- **Log retention + PII bloat (once-over S2).** `App.log_retention_days` is enforced on `TaskMonitor.tick` via `jvagent.logging.retention` (`0` disables). `sanitize_visitor_data_for_log` redacts `image_urls` / attachments / data-URIs. Task `_events` capped at 200; `terminal_ttl_days` default is now **30** (was 0 = unbounded).
- **Orchestrator resilience (once-over S3).** Action-enum failure skips orphan-flow cancel; locked-flow dispatch honors `tool_call_timeout`; observation replay capped; missing/erroring interview branch hooks fail the turn without pruning answers; MCP `connect` is serialized + cleans up on failure; directive trust treats `contrib_*` / unknown `ns__tool` as untrusted.
- **ResponseBus lifecycle (once-over S4).** Process-level bus registry keyed by agent id (survives Agent cache TTL); SSE dedup uses `ResponseMessage.id`; accumulator eviction is idle-based; idle session queues are evicted.
- **Multi-worker + ops (once-over S5).** User usage/`last_seen` reload under lock; proactive claim CAS under conversation lock; Messenger inbound mid dedup; `interaction_limit=0` syncs and disables prune; `agent uninstall` requires `--yes`/confirm; `--update --source` removes agents dropped from app.yaml; pip deps reject option injection; enable/disable invalidates action cache; DynamoDB log creds no longer clobber process `AWS_*`.
- **`ReplyAction.gather()` ignored intro-style parameters.** When the interaction
  carried response-shaping parameters but no directives (first-engagement intro via
  `IntroInteractAction`), `gather()` returned early without composing and
  `respond()` treated parameters-only shaping as silent — so orchestrator
  `_egress()` dropped the greeting. `gather()` now routes parameters-only (and
  channel-format-only) shaping through `respond()`, which falls back to the user's
  utterance when only shaping is present; `reply()` behavior is unchanged.
  Orchestrator `_send_reply` also falls back to `respond()` when `gather()` does
  not emit. Covered by `tests/action/reply/test_reply_action.py` and
  `tests/action/intro/test_intro_orchestrator_egress.py`.

### Changed
- **Orchestrator skill grounding (#80).** PROCEDURE stays in the system
  `skills_section` (not in `use_skill` observations); duplicate task-lock prep
  catalog is skipped on the same activation turn; the field catalog is
  re-grounded on resumed task-lock turns.
- **Reply defaults to markdown (#80).** Default markdown channel format and the
  current utterance is included in compose; the PageIndex modal defaults jvforge
  on.
- **Action modularization refactor** — shared channel helpers moved to `interact/webhook_pipeline.py`; OAuth to `action/oauth/`; spreadsheet range utils to `action/spreadsheet/`; `MediaManager` to `action/channels/media.py`; Meta verify/dedup canonical in `whatsapp/utils/`; `webhook_system_user_factory` collapses duplicate `webhook_auth` modules; Facebook gets own `webhook_auth`; handoff resolves notify channel via `handoff_notify_action_type`; `TaskLockPrep` in `skill_spec/task_lock.py`; public `mcp_action.normalize_call_result`. See `.planning/reference/action-modularization-audit.md`.
- **Docling no longer in default test/dev deps.** Native PDF→Markdown still uses Docling when `convert_to_markdown` is on and ingest runs locally; install `jvagent[pageindex]` (or set `JVAGENT_JVFORGE_BASE_URL` to delegate conversion). Removed `docling`/`tabulate` from `[test]` and `requirements-dev.txt` — they pulled torch into every dev/CI install without being required for the core test suite.

## [0.1.1] - 2026-07-08

### Removed

- **Rails orchestration** — `InteractRouter`, `ConverseInteractAction`, `RetrievalInteractAction`, `WebSearchRetrievalInteractAction`, `PageIndexRetrievalInteractAction`, `UserLongMemoryRetrievalInteractAction`, and related packages.
- **UserLongMemory subsystem** — `UserLongMemory` / `UserLongMemoryNode` graph nodes, `UserLongMemoryInteractAction`, `UserLongMemoryStoreInteractAction`, `LongMemoryService`. Per-user durable memory is **`User.memory`** only.
- **`User.user_model`** — use `User.memory`; one-time read migration from persisted context on user load.
- **`get_dispatch_visitor()`** — use `get_tool_visitor()` or `get_dispatch_context()`.
- **`skills_source='registry'`** (and `local` / `builtin` aliases) — use `app`, `library`, or `both`.
- **`include_legacy_agent_skills`** — standard skill paths only.
- **`enable_interact_router_cache`** / **`interact_router_cache_ttl`** app config keys.

See [ADR-0029](.planning/adr/0029-rails-orchestration-removal.md) and [docs/deprecated-api-migration.md](docs/deprecated-api-migration.md).

### Changed

- **Builtin profiles** — `minimal` and `research` use Orchestrator + ReplyAction (Rails stack removed).
- **Memory HTTP API** — `/memory/me` and admin content endpoints return `User.memory` dict.
- **Orchestration docs** — [`docs/orchestration-modes.md`](docs/orchestration-modes.md) documents Orchestrator-only deployment.

## [0.1.0rc15] - (prior)

### Added

- **Orchestration mode guide** — [`docs/orchestration-modes.md`](docs/orchestration-modes.md)
  documents Orchestrator vs Rails (legacy-compat) deployment patterns.

- **Deprecated API migration guide** — [`docs/deprecated-api-migration.md`](docs/deprecated-api-migration.md)
  with removal target **jvagent 0.1.1** for `User.user_model`,
  `get_dispatch_visitor()`, `skills_source='registry'`, and
  `include_legacy_agent_skills`.

### Deprecated

- **`User.user_model`** — use `User.memory`. `migrate_user_model_to_memory()`
  helper copies legacy data. Removed in **0.1.1**.
- **`get_dispatch_visitor()`** — use `get_tool_visitor()` or `get_dispatch_context()`. Removed in **0.1.1**.
- **`skills_source='registry'`** (Orchestrator) — use default `both`/`app`/`library`.
  Removed in **0.1.1**.
- **`include_legacy_agent_skills=True`** — migrate to standard skill paths.
  Removed in **0.1.1**.
- **Rails orchestration** (`InteractRouter` + retrieval/converse IAs) —
  legacy-compat; Orchestrator-only is the default for new agents. See
  ADR-0028 and `docs/orchestration-modes.md`.

### Changed

- **PersonaAction references** in docstrings and READMEs updated to
  ReplyAction/Orchestrator terminology (PersonaAction code was removed
  previously; docs lagged).

- **Dev tooling** — black `target-version` aligned to Python 3.10+;
  `pytest-xdist` added to `[dev]` optional deps; consolidated test PDF
  deps to `PyPDF2` only.

- **LeadGenAction (`jvagent/leadgen`).** Unified conversational lead capture with
  spec-driven `leadgen:` frontmatter, `leadgen__capture` / `retrieve` / `status` /
  `sync` tools, server-side auto-sync to MCP destinations on capture, and
  `jvagent skill create-leadgen` scaffolding. Reference agent:
  `examples/jvagent_app/agents/jvagent/leadgen_agent/`.

- **Storefront demo (`leadgen_agent`).** The leadgen reference agent is now a
  multi-skill storefront: leadgen coexists with a FAQ skill (`product_faq`) and a
  product-search skill (`product_search`) over a mock `contrib/storefront` action
  (dummy FAQ + catalog), plus a first-message intro. Demonstrates skill
  coexistence, return-visit retrieval, and end-to-end sync to a creds-free
  flat-file MCP server.

- **Interview declarative activation seeding (I-INT-SEED-01).** Fields may declare
  `validator_args.seed_from_activation` (canonical value → trigger phrases) and use
  built-in pre_processor `seed_field_from_activation`. Matching engine in
  `activation_seed.py`; downstream hooks call `infer_field_from_activation()`.
  `HookExecutionContext.field_def` set for pre_processor runs.

- **Interview `for_each` per-item subparts.** Parent fields may declare nested
  `for_each.fields` templates in frontmatter. After the parent stores, a
  post-processor returns `for_each_expand` (via `ctx.expand_for_each`) to walk
  subpart questions once per item. The engine owns iteration, review grouping,
  and `session.context["for_each"][parent]["records"]`. Reference skill:
  `interview/examples/example_for_each_interview/`.

- **`ctx` helpers so interview skills import nothing from the interview package.**
  `HookExecutionContext` gains `get_for_each_records(parent_key)` (read completed
  per-item records without touching `session.context` internals),
  `start_for_each(parent_key, items=, skip=)` (launch a parent's subparts from a
  *different* field's post_processor), `activation_utterance` (the original
  activating request, no `ACTIVATION_UTTERANCE_KEY` import), and
  `infer_field_from_activation(field_key)` (declarative seed inference). `ctx.field_def`
  is now also set for validator and post_processor runs (previously pre_processor only),
  so hooks use `ctx.field_def.key` instead of hard-coding a field name.

- **Per-turn model credential override (BYOK).** `per_turn_model_override`
  ContextVar in `jvagent.action.model.context` with `bind_model_override()`.
  `api_key_from_context()` consults the override before environment variables;
  `bind_model_gear()` selects `light_api_key` vs `api_key` per orchestrator gear.
  Orchestrator `_resolve_model_action()` / `_gear_model()` honor per-turn
  `provider` / `light_provider` and model IDs for multi-tenant embed hosts.
  `_gearing_on()` and gear selection honor BYOK `light_model` even when the
  agent YAML leaves `light_model` empty.

- **Conversation Use Case Specification (CUCS).** Framework-level YAML schema
  (`jvagent.use-case/v1`) for documenting multi-turn conversational scenarios
  that inform orchestrator E2E test suites. Normative reference:
  `.planning/reference/conversation-use-cases.md`; JSON Schema:
  `jvagent/schemas/use-case-v1.schema.json`; ADR-0027. Domain-neutral witness
  scenarios under `jvagent/action/interview/examples/example_account_gating/use-cases/`.

- **Interview `activation_utterance` session context.** `handle_start` stashes the
  activating `user_message` on a fresh session (or a resumed session with no stored
  fields yet) as `session.context["activation_utterance"]` so activation
  `pre_processor` hooks can seed the first field from the user's original request
  on gated task-lock resume without relying on the model re-extracting from an
  aged observation. Key constant: `ACTIVATION_UTTERANCE_KEY` in `session.py`.

### Removed

- **`jvagent/lead_profile_action` and `jvagent/lead_sync_action`.** Superseded by
  `jvagent/leadgen`. Use `leadgen__capture`, `leadgen__retrieve`, and auto-sync
  (or `leadgen__sync` in manual mode). Library skills `lead_profile` and `lead_sync`
  also removed.

### Changed

- **Require `jvspatial==0.0.10`.** Pins the substrate floor for rc14 (orchestrator
  perf, embed hosts on integral's identity-map / single-hop neighbor paths).

- **Interview directive composition extracted from `engine.py`.** Batch-failure
  and multi-directive merge helpers moved to `directive_compose.py` to shrink the
  tool-handler module.

- **Leadgen sync is destination-agnostic and configured on the action.** Sync now
  goes through the standard MCP interface (server + tool + arguments) with no
  Google-Sheets-specific coupling — a flat file, spreadsheet, email, CRM, or DB
  are the same shape. Sync config moved to the `jvagent/leadgen` action
  (`sync_mode` / `sync_min_fields` / `sync_require_any` / `sync_destinations`) in
  `agent.yaml`; a skill may still self-contain sync via its own `sync:` block
  (skill wins when it declares destinations).

- **Leadgen captures contact details as a standing goal.** The `leadgen__capture`
  / `retrieve` tool descriptions carry a STANDING GOAL and the tool results add a
  `next_ask` hint, so the agent proactively asks for the next missing field
  (name → email/phone) tied to value each turn, easing off once required fields
  are in.

- **`IntroInteractAction` contributes a parameter, not a directive.** The
  first-message greeting is now a response-shaping parameter so `ReplyAction`
  weaves it into the same reply as the substantive answer, instead of emitting a
  second, disjoint paragraph — the intro and the executive coexist in one reply.

### Fixed

- **`for_each` expansion no longer wiped on a failed parent correction.** The engine
  cleared a parent's expansion *before* validating its new value, so a rejected
  re-submission left the old value stored with no expansion — permanently blocking
  the subparts. Wipe now happens only after the new value validates.
- **`for_each` child fields surfaced in `awaiting_fields`.** `build_awaiting_fields`
  used `spec.get_field`, which returns `None` for subpart keys, silently dropping
  them; it now resolves via the active expansion.
- **Custom review handlers can hide `for_each` records.** `omit_fields` from a review
  handler now also suppresses the matching parent's per-item records in the summary.
- **`field_sort_order` no longer raises** when a `for_each` parent key is absent from
  the top-level field list (defensive `next()` lookup instead of `list.index`).
- **Leadgen sync no longer re-syncs unchanged profiles.** `compute_digest` hashed
  the whole profile including the stored `_leadgen_sync_digest`, so the digest
  changed the moment it was written back and the unchanged-data check never
  matched — every capture re-synced (duplicate rows on append-style
  destinations). The digest now excludes underscore-prefixed internal keys.
- **Leadgen sync no longer leaks internal keys.** `{profile_json}` included
  `_`-prefixed bookkeeping keys (unlike `{profile_row}` / `{profile_keys}`); all
  templates now exclude them, so internal state never reaches a destination.

### Removed

- **Orphaned `interview/responses.py`.** Dead module left from an unmerged conflict
  (referenced a nonexistent `get_hook_execution_context` and the obsolete
  `Tell the user:` directive prefix). Its surface was fully superseded by `hooks.py`
  and `directive_compose.py`; no live imports.

## [0.1.0rc15] - 2026-07-02

### Changed

- Pin **jvspatial 0.0.11** (was 0.0.10). 0.0.11 fixes two single-hop
  `find_connected_nodes` regressions that broke the embedded agent's memory
  traversal — `Agent.get_memory()` and `Conversation.node(direction="in",
  node=User)` returned `None` because of a limit-before-filter bug and a
  base-`Node` subtype-resolution bug under app/agent `User` entity-name
  collisions. No jvagent code change beyond the dependency pin.

## [0.1.0rc9] - 2026-06-22

Ninth release candidate (TestPyPI). Fixes PageIndex access control: having
`AccessControlAction` in an agent no longer silently gates document retrieval.

### Fixed

- **PageIndex access control is now opt-in via the metadata filter.** Previously
  any agent that hosted `AccessControlAction` had every PageIndex search gated by
  `user_groups`, so documents tagged with their own scheme (e.g.
  `access: "public"` / `access: "private"`) matched the injected group filter
  nothing and the knowledge base returned no results. `resolved_metadata_filter`
  now engages access control only when a metadata filter is in effect (passed per
  call or configured as `PageIndexAction.metadata_filter`); with no filter set,
  retrieval is decoupled from `AccessControlAction` ("in the same agent, but not
  together"). When a filter is set, matched `user_groups["PageIndexAction"]`
  groups are merged alongside the configured baseline, an unmatched visitor keeps
  the public baseline, and a filter without an `access` baseline is scoped to
  `access=[]` so restricted documents are never leaked. Untagged/empty `access`
  is treated as public in both filter layers (`_build_metadata_query`,
  `_root_matches_metadata`).

## [0.1.0rc7] - 2026-06-18

Seventh release candidate (TestPyPI). Maintenance: the bundled base image now
comes from a public registry, so `jvagent bundle` Dockerfiles build without
private-registry credentials.

### Changed

- **Bundle base image → public ECR.** `jvagent/bundle/Dockerfile.base` now
  `FROM public.ecr.aws/s1x1t0a3/jvagent:latest` (was the private
  `registry.v75inc.dev/jvagent/jvagent-base:latest`), so generated Dockerfiles
  build without Harbor access.

## [0.1.0rc6] - 2026-06-17

Sixth release candidate (TestPyPI). Headline: a `@tool` decorator makes Action
tools declarative (no more hand-written JSON Schema), and the Orchestrator now
reliably drives multi-step tasks to completion (plan-drain guard, plan-aware
tool surfacing, friction-free tool discovery) instead of stalling mid-task on a
weaker model.

### Added

- **`@tool` decorator for Action tools.** Decorate an `async def` method with
  `@tool` and the base `get_tools()` auto-publishes it — name
  (`{action_name}__{method}`, override via `@tool(name=…)` or a class-level
  `tool_namespace`), description (method docstring), and `parameters_schema`
  (derived from the signature; `Annotated[T, "desc"]` for per-arg docs) all
  come from the function. New `jvagent/tooling/signature_schema.py` (Python type
  → portable JSON Schema) + `tool_decorator.py` (`tool`, `collect_tools`). Every
  capability action migrated (Google/Microsoft/file_interface/pageindex/
  skill_hub/vision/web_fetch/web_search/code_execution), removing ~1500 lines of
  hand-written `Tool()`/schema. Manual `Tool()` and `get_tools()` overrides
  still work. See [`action-authoring.md`](.planning/reference/action-authoring.md) §10.
- **Resumable plans carry work across the resume (ADR-0019).** `update_plan`
  steps accept an optional `result`/note (e.g. an artifact path) persisted on the
  step; `plan_resume_note` surfaces it so a resumed turn reuses saved work
  (read the file) instead of regenerating it.

### Changed

- **The Orchestrator finishes multi-step tasks instead of stalling.**
  - **Plan-drain completion guard**: while an active plan has open steps, a
    turn-ending decision (`final`/`reply`/`respond`) is deflected with an
    actionable nudge (do the next step; use `find_tool` if needed; produced text
    can go straight to the tool) — bounded by `plan_completion_max_deflections`
    (default 6).
  - **Plan-aware lean pre-surfacing**: the relevance signal folds in the active
    plan's checklist, so a resumed/low-signal turn still surfaces the next
    step's tools.
  - **Prompt hardening**: act-don't-announce; finish multi-step work before
    replying; `find_tool` first if the exact tool isn't visible, don't
    substitute a look-alike.
- **`block_raw_tool_invocation` no longer hard-gates hidden tools.** A real tool
  the model names directly is **auto-promoted and run** (implicit `load_tool`);
  only an unknown/hallucinated name is bounced to `find_tool`. The flag still
  stops the *user* from dictating tool selection. (Previously this dead-looped
  when a weak model repeatedly named a correct-but-hidden tool.)
- **`update_plan` tolerates the shapes models emit**: the step list under many
  key aliases, a dict-of-steps, a bare string, or a single inline step; and the
  loop normalizer folds a **flattened** call (args at the decision top level
  instead of under `args`) into the tool args.
- **Model-floor guidance**: the Orchestrator is model-mediated — documented a
  gpt-4.1 / Claude Opus-Sonnet floor for the heavy gear (the class default
  `gpt-4o-mini` is for cheap single-step agents). See
  [`docs/ORCHESTRATOR.md`](docs/ORCHESTRATOR.md) "Model floor".
- **Example orchestrator agent**: disabled the redundant sandboxed-filesystem
  MCP server (it duplicated `file_interface`'s per-user slice and was the
  surface a weak model kept mis-calling); `pinned_tools` left available but off.

### Fixed

- **PageIndex assimilate no longer loses document content.** A path-like `doc`
  (e.g. a file written by `code_execution__bash`) is resolved from the caller's
  per-user sandbox and its **content** ingested; an unresolvable path now fails
  loud instead of silently storing the filename string as the document body.

## [0.1.0rc5] - 2026-06-16

Fifth release candidate (TestPyPI). Headline: the interview hook authoring
interface is now a single `ctx` object (**BREAKING for skill authors**). Also
fixes a turn-lock re-grounding regression from rc4 and applies `country_code` in
the builtin phone validator.

### Changed

- **Interview hooks take a single `ctx` (BREAKING for skill authors).** Every
  `custom_tools` hook — validator, pre/post processor, skill tool, handler, branch
  condition — now takes exactly one argument, `ctx` (`HookExecutionContext`), and
  imports nothing from the interview package.
  - **Inputs** are attributes: `ctx.value` (validators), `ctx.session`,
    `ctx.visitor`, `ctx.interview` (the action), `ctx.config` (the spec),
    `ctx.extracted_values`, `ctx.args` (validator_args / skill-tool args),
    `ctx.phase`. `ctx` is always injected and never `None` (no null-guard).
  - **Output** is methods: `ctx.say(msg | [msgs], *, continue_=False, hint="")` is
    the single channel for user-facing text — one string is one question, a list is
    sequential statements (statement-then-followup), `continue_` appends the
    branch-aware next prompt, `hint` is model-only guidance. `ctx.tool_response(...)`
    is the control envelope (status / next_tool / interview_complete / value /
    retain_context_keys / review keys / a deferred `note`). `ctx.call_tool(tool)`,
    `ctx.no_session()`, and `ctx.valid(...)` / `ctx.invalid(...)` (validators —
    `invalid` auto-frames the error as the re-ask) round out the surface.
  - `ctx.say` records onto the context; `call_hook` folds it into the result's
    `response_directive` in one place, so it flows the existing, proven delivery
    path (no double-emit). It is **inert outside reply-producing phases** (the
    pre-processor store re-run, branch eval), so a prompt-builder that re-runs while
    the answer is stored can't bleed the previous prompt onto the next turn — call
    it unconditionally. This also resolves the rc4 regression where user-facing
    content placed in a `tell_user` `note=` was stripped at egress.
  - The standalone `responses.py` directive builders (`tell_user`,
    `tell_user_with_followup`, `interview_tool_response`, `call_tool_directive`,
    `no_session_directive`, …) and the `InterviewDirectives` sink (`directives.py`)
    are **removed** — both modules are deleted; the framing primitives now live
    inside `hooks.py` (internal; used by the engine and by `ctx`). The
    `directives`/`session`/`visitor`/… back-compat kwarg injection is gone — `ctx`
    is the only injected argument.

### Added

- **Field-level `hint`.** Interview fields take an optional `hint` alongside
  `prompt` / `guidance` — plain **answer-guidance for the user** (how to answer the
  question, e.g. "Enter your first, last, and any other names"; an accepted format;
  that a field is optional). It is woven into the prompt's user-facing text so the
  agent instructs the user on the intended answer, and surfaced in `field_reference`
  / `next_field` so the model can answer the user's per-question clarifications.
  Distinct from `guidance` (model-facing, judges the answer). Phrase it as what to
  tell the user and keep it non-redundant with `prompt`.

### Fixed

- **Locked-interview re-grounding lost the field catalog (regression).** Under
  task-driven turn-lock (ADR-0026) a skill entered as a pushed prerequisite or
  resumed via the drain is delivered terminally, so the model never runs the
  activation turn where the full `field_reference` is surfaced — and the per-turn
  re-ground (`interview_turn_status`) only sent the slim key list. The re-ground
  now re-asserts the **full** `field_reference` (key, prompt, guidance, required,
  optional `hint`) on every locked turn. Covered by
  `tests/action/interview/test_get_status_reference.py`.
- **Builtin `phone` validator now applies `country_code`.** `validator_args` were
  relayed to the validator but the `country_code` arg was ignored, so a bare local
  number was never normalized (a 7-digit number with `country_code: 592` was
  rejected by the 10-digit check instead of becoming `592…`). It now prepends
  `country_code` to a bare **local-length** number — exactly `full_length −
  code_length` digits (7 for `592`, full 10) — leaving full-length numbers and
  numbers already carrying the code untouched. Acceptance is therefore strictly a
  local number (→ full) or an already-full number, nothing in between (6/8/9/11-digit
  inputs are rejected). No `country_code` → unchanged. Covered by
  `tests/action/interview/test_phone_validator_country_code.py`.

## [0.1.0rc4] - 2026-06-16

Fourth release candidate (TestPyPI). Task-driven turn-lock: the orchestrator's
work graph becomes a standard, drainable mechanism (ADR-0026).

### Added

- **Task-driven turn-lock — work-stack orchestration (ADR-0026).** The
  `TaskStore` is now a work graph the orchestrator drains. Prerequisites push,
  completion pops and re-resolves, and resume is orchestrator-selected (not
  model-mediated). Generic, domain-agnostic primitives in `memory/task_graph.py`
  (`prerequisites_met`, `is_runnable`, `has_outstanding_work`,
  `pick_top_runnable`) over new `Task` fields (`resumes`/`blocked_on`/`order`/
  `seed`/`snapshot`).
- **Declarative `requires-tasks` + precondition registry
  (`action/orchestrator/preconditions.py`, `skills.py`).** A skill declares
  `{when: <precondition>, push: <skill>, seed_from: [...]}` in frontmatter; a
  consumer binds precondition names to predicates at bootstrap. The first unmet
  precondition is pushed as a blocking prerequisite, seeded with the original
  request, and resumed deterministically on completion. The detour's first
  question and the resume are server-delivered (terminal), never model-fabricated.
- **Task-runner registry + standing store drain
  (`action/orchestrator/task_runners.py`, §2.4/§3, invariant 7).** A
  `task_type → runner` registry; `SKILL`/`PROACTIVE` are advanced by the
  orchestrator loop, other types by registered runners. The orchestrator drains
  runnable work every turn and never finalizes idle while runnable work remains —
  independent of any skill turn-lock.
- **Proactive scheduler folded into the work graph (ADR-0022 unification).** A
  `PROACTIVE` task is runnable for the generic resolver only once the scheduler
  claims it (`pending` queued → `active` due); the orchestrator resolves a claimed
  proactive task from the store, not only via a side channel.
- **Snapshot/rehydrate hooks (§2.3)** so a flow torn down for a detour rebuilds
  from its task snapshot on resume.
- **Full work graph in the debug `tasks` payload** (every status + a derived
  `blocked` flag), instead of a this-turn window. Production payloads stay
  redacted.
- **Framework-agnostic CI guard** (`tests/test_framework_domain_agnostic.py`):
  no consumer domain vocabulary may appear in `jvagent/`. Plus a non-zoon example
  consumer under `action/interview/examples/example_account_gating/`.

### Fixed

- **Dead-prerequisite deadlock.** A blocker that ends `cancelled`/`failed` now
  cascade-abandons its dependents (it would otherwise leave a non-terminal but
  unrunnable zombie that kept the engagement state True forever).
- **Directive guidance no longer leaks to the user.** Model-only composition
  guidance after the `U+2063` marker is stripped before egress (a weak compose
  model could echo it), and an entry directive that auto-resolves to a tool-call
  chain (`Call interview__next_field()`) advances server-side to the first real
  question instead of leaking the chain.
- **User-facing copy never calls the flow an "interview"** (cancel/reset/missing
  messages reworded).

## [0.1.0rc3] - 2026-06-14

Third release candidate (TestPyPI). Adds the bundled jvchat web UI + CLI and an
interview re-ask fix.

### Added

- **`jvagent chat` — bundled jvchat web UI.** The built jvchat SPA now ships
  inside the wheel and is served by `jvagent chat` on its own port (a separate
  process/origin from the agent server, by design — see
  [`docs/jvchat.md`](docs/jvchat.md) for the security rationale). `--url`
  injects the target agent URL at runtime (`window.__JVCHAT_RUNTIME_CONFIG__`),
  so one pre-built bundle targets any agent without a rebuild. The static server
  adds SPA fallback, a path-traversal guard, and security headers (`no-store`
  HTML, `immutable` assets, `nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy`). Built/staged by `scripts/build_jvchat.py` and shipped as
  wheel package-data (the publish workflow builds the UI before
  `python -m build`). Covered by `tests/webui/`.

### Fixed

- **Interview validation re-ask no longer duplicates the question.** A
  validator returning a complete-sentence error already re-asks for the value;
  `validation_guidance_directive` previously also appended the field question,
  producing a doubled ask. The field question is now appended only for a terse
  error fragment (an error is treated as self-contained when it is prefixed with
  `Tell the user:` / `Ask:` or ends with `.`/`!`/`?`). Covered by
  `tests/action/interview/test_validation_reask.py`.

## [0.1.0rc2] - 2026-06-14

Second release candidate (TestPyPI). Fixes the standalone `jvagent bootstrap`
path on jvspatial 0.0.9 and refreshes CI / dependency tooling.

### Fixed

- **Standalone `jvagent bootstrap` creates the admin user again.** jvspatial
  ≥0.0.9 resolves the auth service from the `Server` in context. The serve
  path builds the `Server` before `ensure_admin_user()`, but the standalone
  `bootstrap_only` path did not — so `jvagent bootstrap` (which the scaffolder
  tells users to run) failed with `get_auth_service() requires a Server to be
  set in context` and never created the admin. `bootstrap_only` now
  instantiates the `Server` (without starting uvicorn) before
  `ensure_admin_user()`. Covered by
  `test_bootstrap_only_creates_admin_without_preexisting_server`.

### Changed

- **CI GitHub Actions moved to Node 24.** `actions/checkout@v5`,
  `actions/setup-python@v6`, `actions/setup-node@v6`,
  `actions/{upload,download}-artifact@v5`, plus Dependabot bumps of the Docker
  build actions (`setup-buildx@v4`, `login@v4`, `metadata@v6`). Clears the
  Node 20 deprecation warnings.
- **jvchat dependency bumps** via Dependabot (`@assistant-ui/react`,
  `@uiw/react-codemirror`, `@uiw/codemirror-theme-github`).

## [0.1.0rc1] - 2026-06-14

First public release candidate. Consolidates the `dev-executive` line: the
Orchestrator turn model, the thin-harness interview v2, single-egress
`ReplyAction`, and the skills-v2 surface. See the entries below for the full
set of changes rolled into this candidate.

### Changed

- **`jvagent/vision` made self-contained + configurable prompt.** All vision prompts and model operations moved out of `interact/utils/vision_prompt.py` into the action's own package: prompt constants in `jvagent/action/vision/prompts.py` (`IMAGE_INTERPRETATION_PROMPT`, now multiline) and the builders/model call in `jvagent/action/vision/multimodal.py` (`build_prompt_for_vision`, `generate_image_interpretation`). `VisionAction` gains an **`interpretation_prompt`** attribute (default = `IMAGE_INTERPRETATION_PROMPT`) overridable in `agent.yaml`; precedence is per-call prompt → `interpretation_prompt` → constant. Fixes a latent bug where `describe()` passed `prompt=None`, clobbering the default and sending a `{"type":"text","text":null}` part to the model. The old `interact/utils/vision_prompt.py` and its `interact/utils/__init__` re-exports are removed (no other callers). Example `orchestrator_agent` and `zoon` agent configs document the model + `interpretation_prompt` knobs (and note that a non-vision model returns HTTP 400 `image_url is only supported by certain models`). Covered by `tests/action/test_vision_action.py`, `tests/action/test_vision_multimodal.py`.

### Dependencies

- **Require `jvspatial>=0.0.9`.** The test/runtime surface now depends on
  `jvspatial.core.context._default_context_var` (added upstream), so the floor
  is raised across `pyproject.toml`, `requirements.txt`, and
  `requirements-all.txt` (previously an inconsistent `0.0.6` / `0.0.7`).

### Removed

- **`PersonaAction` retired (ADR-0025).** The `jvagent/action/persona/` package and its dedicated tests are deleted. `ReplyAction` is now jvagent's single output contract: identity lives on the Agent node (`alias` + `role`), parameters/format/history live on `ReplyAction`. `Action.get_responder()` resolves `ReplyAction` only (no fallback — returns `None` if absent). The `minimal` / `orchestrator` / `research` scaffold profiles enable `jvagent/reply`, not `jvagent/persona`. A grep-guard test (`test_no_persona_imports.py`) proves no source references `jvagent.action.persona`.

- **v1 `InterviewInteractAction` (`jvagent/action/interview/`) removed.** The legacy v1 interview package (Rails-style `InterviewInteractAction` walker) has been deleted. All interview functionality now uses the v2 `InterviewAction` (`jvagent/action/interview/`) with skills-v2 (`extends: action:jvagent/interview` + `interview:` frontmatter). Removed: `jvagent/action/interview/` package, `tests/action/interview/` test suite, `signup_interview_interact_action` example, and all import/reference sites updated. The `jvagent.action.interview.*` package entry was removed from `pyproject.toml`.

### Changed

- **Single per-turn egress + `ReplyAction` as sole output conduit (ADR-0024/0025).** `interaction.directives` is the one output queue: producers (the orchestrator, rails IAs) queue directives; `ReplyAction.gather()` is the conduit that delivers them. The orchestrator is the author for model/skill turns — its egress `reply`/`respond` tools queue the model's reply as an `interaction.directive` (attributed to `OrchestratorInteractAction`) then call `gather`; a lone relay directive (`Tell the user: …`) is slim-published (N=1, no model call), anything else composes once in the Agent identity. `ReplyAction` never adds its own directives (stays a pure conduit). A per-turn `interaction.emitted` latch (set at the bus/no-bus/streaming delivery choke points) enforces exactly one emission per turn, fixing the duplicate-response class on channel adapters. "voice"-as-respond terminology renamed to reply/render across `ReplyAction`, the orchestrator, and docs (TTS `voice` channel unchanged); `apply_voice_rules` → `apply_reply_rules`. Covered by `test_single_egress.py`, `test_emitted_latch.py`, `test_interaction_emitted.py`, `test_adapter_no_double_send.py`, `test_reply_action.py`.

- **Interview context contract split (breaking).** `use_skill` activation remains the rich context surface (`awaiting_fields`, `field_keys`, `active_path_keys`, paged `field_hints`), while `interview__set_fields` responses are now compact and result/directive-oriented. Re-fetch context via `interview__next_field` or `interview__get_status` instead of expecting repeated hint dumps on every store response.

- **Interview thin harness refactor (breaking).** Canonical foundation surface is eight `interview__*` tools: `set_fields`, `skip_field`, `next_field`, `get_status`, `review`, `complete`, `cancel`, `reset`. Renamed `interview__next_question` → `interview__next_field`; responses use singular `next_field` object instead of `next_questions[]`. Removed deprecated read/write aliases (`interview__get_fields`, `interview__get_field`, `interview__set_field`). Reset no longer internally calls `next_field` — returns `next_tool` hint only. `on_skill_activate` returns structured session JSON (no procedural chaining prose). `get_status` and validation-failure payloads omit embedded next field. Added `interview_path_snapshot()` helper; moved validator dispatch to `runtime/validation.py`; removed no-op `finalize_store_continuation`. Covered by `test_thin_harness_guards.py`, updated interview_action and orchestrator interview tests.

- **Interview base session gate (`use_skill` before field collection).** Base [`interview_action/SKILL.md`](jvagent/action/interview/SKILL.md) adds **Activation (session gate)**: mandatory `use_skill` before asking interview questions, activation-turn chaining, late-activation grounding, and **Start interview** intent routing. Thin runtime envelopes: stronger `no_session_directive`, `on_skill_activate` returns activation JSON, session requirement on `interview__set_fields` / `interview__next_field` tool descriptions. Docs: interview thin-harness invariant #9, multi-turn anti-pattern, troubleshooting entry. Covered by `test_interview_procedure.py`, `test_interview_responses.py`, `test_interview_skill_activate.py`.

- **Interview `interview:` frontmatter schema (breaking).** Renamed keys: `questions`→`fields`, `name`/`question`/`description`→`key`/`prompt`/`guidance`, `pre_tools`/`post_tools`→`pre_processor`/`post_processor`, top-level `review`/`completion`/`reset`/`cancel`→`handlers.*` (function name strings), `tools`→`skill_tools`, `description`→`summary`. Validators are flat `validator` + `validator_args` (no nested `{ function: … }`). Removed `extractors` — model extracts via `interview__set_fields`. Added `confirm: manual|auto` for review→complete chaining without user yes. Legacy keys fail at parse. See [`jvagent/action/interview/docs/frontmatter-schema.md`](jvagent/action/interview/docs/frontmatter-schema.md). In-repo skills migrated; external skills (e.g. zoon-ai) must update separately.

- **Interview harness strip-down (raw tools + SOP).** Removed server-side turn steering: `message_evaluation` prep, auto-injected `interview__next_field` observations, `merge_auto_next_field` / `merge_auto_review` inlining, and orchestrator post-store tool-call guards. Base `SKILL.md` rewritten as intent-first procedure with first-class **Correct / update** via `interview__set_fields`. New batch primitives: `interview__set_fields`, `interview__get_status`; `interview__reset` replaces `interview__reset_interview` (`interview__set_fields` / `interview__get_status` remain deprecated aliases). Model chains `next_field` / `review` per SOP. Covered by `test_set_fields.py`, `test_signup_activation_inline.py`, `test_use_skill_task_lock_prep.py`.

### Fixed

- **Interview terminal-sequence stability (skip → review → complete).** Three foundation fixes for a model that skipped the last optional field and then bypassed the review confirmation — either thrashing on `skip_field`/`next_field` or completing the task outright (dropping the turn-lock without the user confirming). (1) `interview__skip_field`'s tool description no longer hardcodes "call `interview__next_field`" — it defers to the per-call `response_directive`, which routes to `next_field` while fields remain or `review` once the queue empties (invariant #4). (2) A bare skip on an empty queue is terminal, not an error — it routes cleanly to `review`. (3) `handle_complete` enforces the review gate under `confirm: manual`: completion is refused (routed back to `interview__review`) until `interview__review` has run (invariant #5); `confirm: auto` is exempt. Covered by `test_review_gate.py`.

- **Interview `skip_field` unknown-key guard.** `interview__skip_field` now rejects a `field_key` the spec does not define (e.g. the model guessing `training_availability_slot` from prompt text when the key is `available_times`) instead of silently recording a phantom skip that bypassed the required-field guard. Returns `error_code: UNKNOWN_FIELD`, re-anchors on the real pending field, and leaves `skipped_fields` untouched. Covered by `test_review_gate.py`.

- **Interview redundant re-submit no-op (idempotency guard).** `handle_set_fields` now short-circuits a field whose submitted value exactly matches the already-stored value: it skips the `pre_processor`, validator, and `post_processor` and returns `results[].idempotent: true` (still `stored: true`, still chains `next_field`/`review`). Prevents API-calling `post_processor`s (e.g. customer lookups) from re-firing when the model redundantly re-submits a previously collected field across turns. A genuine value change still flows through normally, so review corrections are unaffected. Covered by `test_idempotent_resubmit.py`.

- **Interview review corrections and branch pivots.** `set_fields` on an already-stored field now accepts validator-passing updates with `validated_from: correction` even when the latest utterance does not repeat the new value — fixes repeated failed stores when a slot change spans turns (e.g. review pivot to Saturday, then "yes, virtual is fine"). Base and signup SOP: store corrections immediately; no inline "please confirm the change" before `set_fields`. Covered by `test_review_field_update.py`.

- **`interview__set_fields` args shape.** Canonical tool args are `{"fields": {"field_key": "value"}}` only (`additionalProperties: false` on the tool schema). Handler coerces legacy flat field keys and deprecated `field`/`value` kwargs for compatibility; SOP and skill docs updated with explicit JSON examples. Covered by `test_set_fields.py`.

- **Interview validation message clarity.** Grounded-but-invalid `set_fields` values now return validator-specific errors (`validated_from: supplied_grounded`) instead of the generic ungrounded message. `rejected_ungrounded` is reserved for true model bypass (valid value not in the utterance). Multi-field batch failures slim the envelope: `results[]` is authoritative per field; top-level omits duplicated `error` / `next_fields` and carries one `response_directive` from the first failure. Covered by `test_validation_message_clarity.py`.

- **Interview inception multi-extraction.** New `forward_storable_fields` contract (`compute_forward_storable_field_names`) lists fields legal for forward `set_fields` this turn — e.g. signup activation `[user_name, available_times]` while `missing_required` stays `[user_name]`. Store gate rejects fields behind unresolved branch parents (`BRANCH_UNRESOLVED`); batch `set_fields` processes in spec order and continues on per-field failure. Exposed on start/status/next_field/set_fields and activation notes. Covered by `test_forward_storable_inception.py`.

- **Interview path regression remediation.** Split collectible prefix path (`compute_collectible_path_names`) from active projection for prune (`compute_active_path_for_prune`): `missing_required`, store gate, and `next_field` stop at the first gap; prune alone walks the full projection. Branch points without a matching `when`/`else` no longer linear-fallback to the next spec field (fixes premature `existing_email` on unanswered `has_account`). Idempotent `already_stored` stores still return `next_tool` / `response_directive`; activation note aligns with base SOP (`set_fields` before `next_field` when the utterance is extractable). Covered by `test_path_regression_remediation.py`; `test_branch_path_invalidation.py` remains green for prune preservation.

- **`interview__set_fields` utterance grounding.** When the visitor carries the user's latest message, values must be extractable from that turn (or match a `pre_processor` `suggested_value` stored in session) — ungrounded model-supplied values from older chat history are rejected (`validated_from: rejected_ungrounded`). Suggestions are persisted on `interview__next_field` via `session.context.field_suggestion`.

- **Skill lifecycle binding vs `requires-actions` gate.** `action_for_skill()` previously bound lifecycle hooks (`on_skill_activate`, `prepare_task_lock_turn`, `resolve_task_lock_skill`, etc.) to the first enabled Action whose class name appeared in `requires-actions`, using **`agent.yaml` action list order**. Multi-action skills (e.g. interview + API dependency) could bind to the wrong Action, skip interview bootstrap, strip `interview__*` tools, and hide tool-call SSE in jvchat. Binding now resolves: (1) `extends: action:<namespace>/<action>` ref, (2) sole lifecycle-protocol implementor among required actions, (3) `requires-actions` declaration order. The hard gate (`_enforce_required_actions`) is unchanged — all declared actions must still be enabled.

- **Locked-skill skill-name-as-tool loop.** During turn-lock, `use_skill` is removed from the callable surface; the model often still emits the skill name as a tool (e.g. `onboarding_interview`), wasting ticks on `(no such tool)`. The orchestrator now steers back to declared interview tools or `reply`/`respond` instead.

- **Duplicate interview prep after `Tell the user:` store.** When `interview__set_fields` returns a `Tell the user:` `response_directive` (post-tool auto-advance already inlined the next question), the orchestrator no longer re-runs `refresh_locked_skill_prep` — which re-injected conflicting `interview__next_field` observations and encouraged extra tool calls before `reply`.

- **Tool-call visibility during streaming.** Internal progress lines (`Using interview__set_fields…`) no longer duplicate substantive tool activity in the Reasoning panel when structured `tool_call`/`tool_result` thoughts are emitted for the TOOL CALLS section. jvchat auto-expands the tool group while the turn is running.

- **Task-lock turn prep after mid-loop `use_skill`.** When the model activates a `task-lock` skill via `use_skill` on tick 1 (not new-user auto-start), the orchestrator now runs `apply_task_lock_turn` immediately so `interview__message_evaluation` / `interview__next_field` prep reaches the same turn. Fixes signup activation with inline name (`"Hello my name is Eldon Marks…"`) skipping extraction and re-asking for full name. Covered by `tests/action/orchestrator/test_use_skill_task_lock_prep.py`.

- **Interview tools after mid-loop `use_skill`.** `InterviewAction.prune_task_lock_tools` could remove `interview__*` tools before the session was ready; mid-loop activation then left the model with `(no such tool: interview__set_fields)`. `ensure_skill_tools_materialized` re-adds bound-action tools when turn-lock applies with a ready runtime.

- **Duplicate `set_field` on activation.** After a successful store, the orchestrator refreshes locked-skill prep (drops stale `interview__message_evaluation`, injects updated `interview__next_field`) and idempotent `set_field` only short-circuits when the same normalized value is already stored — not on review corrections or branching updates.

- **jvchat tool-call panel for server prep.** Server-injected prep observations (`interview__message_evaluation`, `interview__next_field`) now emit `tool_call` / `tool_result` thoughts so the TOOL CALLS section appears alongside Reasoning.

### Added

- **Thin harness documentation (jvagent-wide).** Platform principle at [`docs/thin-harness.md`](docs/thin-harness.md) — thin Orchestrator/Action harness, thick SOP + skill extensions. Interview-specific profile at [`jvagent/action/interview/docs/thin-harness.md`](jvagent/action/interview/docs/thin-harness.md). Cross-linked from root `CLAUDE.md`, `docs/ORCHESTRATOR.md`, `.planning/GLOSSARY.md`, `action-authoring.md`, `jvagent/action/CLAUDE.md`, `jvagent/skills/README.md`, and interview docs.

- **Proactive Task Monitor (ADR-0022).** Unified proactive execution: `ProactiveTaskSpec` (`spec_version: 2`) on `TaskStore`, eligibility engine (`task_eligibility.py`), `TaskMonitor` action (replaces `TaskDispatcher`) dispatching one eligible `PROACTIVE` task per conversation through the full Orchestrator; `TaskTriggerInteractAction` event bridge; `queue_task` orchestrator tool; `Agent.enqueue_proactive_task()` and `embed.enqueue_proactive_task()`. Covered by `tests/memory/test_task_proactive.py`, `test_task_eligibility.py`, `test_task_store_proactive.py`, `tests/action/task_monitor/`, and updated task creation/trigger tests.

### Removed

- **Interview server-side control-intent regex.** Deleted `runtime/control_intent.py` and the `interview__control_intent` prep observation. Cancel/stop and start-over/restart intent is classified by the model via base `SKILL.md` Intent routing — turn prep always runs message evaluation or seeds `interview__next_field` like any other utterance.

- **`jvagent/task_dispatcher`.** Replaced by `jvagent/task_monitor` (forward-only; no migration shims).

- **InterviewAction deprecation shims.** Removed standalone `interview.yaml` loader, `@interview_tool` decorator auto-discovery, `input_context_provider` alias (use `pre_tools`), `set_field` `name` parameter alias, `contract_name` / `_ensure_contracts_loaded` back-compat APIs, `is_interview_skill_bundle`, and root-level `custom_tools.py` path fallback. Spec discovery is SKILL.md frontmatter only.

### Changed

- **InterviewAction unified per-message entity evaluation.** Every user message (including skill activation) is evaluated via `evaluate_message_for_extraction` in `runtime/message_evaluation.py`. Turn prep injects `interview__message_evaluation` when applicable candidates are found, or `interview__next_field` otherwise — never text-only directives. The model extracts via `interview__set_fields`; validators accept/reject. Init-time `_seed_fields_from_user_message` auto-store removed. `field_extractors.py` expanded for `validate_full_name` and `validate_available_times` intro/slot candidates. Covered by `test_message_evaluation.py` and `test_signup_activation_inline.py`.

- **InterviewAction set_field auto-advance (cascade fix).** After a successful `interview__set_fields`, the server now inlines `interview__next_field` via `merge_auto_next_field` (same pattern as `merge_auto_review`) and returns a `Tell the user:` directive with `next_fields` — no `next_tool` chain for the model to follow in the same turn. Fixes activation-turn tool cascades (`set_field` → `next_field` → spurious `set_field` loops). Signup `get_available_training_times` pre-tool no longer embeds a `Call interview__set_fields` hint inside the user-facing directive.

- **Interview base SOP owns message evaluation.** Prep observations (`interview__message_evaluation`, `interview__next_field`) documented in base `SKILL.md`; per-skill custom instructions no longer restate field-specific evaluation rules (signup, zoon onboarding/pre-alert).

- **Proactive task documentation.** `docs/task-tracking.md`, `docs/proactive-messages.md`, `docs/configuration.md`, and `docs/environment-keys-reference.md` document `TaskMonitor`, scheduler bootstrap (`JVSPATIAL_SCHEDULER_*`), serverless HTTP tick, and enqueue APIs. Planning refs updated in `configuration-keys.md` and `action-authoring.md`.

- **Skill placement standard (ADR-0023).** Agent skills default to `agents/.../skills/<name>/`. Exceptions: base action SOP at `<action_dir>/SKILL.md` (extends only); skills bundled with a custom/core action under that action's `skills/`. Documented in [`jvagent/skills/README.md`](jvagent/skills/README.md). Deprecation warning for `requires-actions` in app-local folders removed. Example `signup_interview` and Zoon interview skills migrated to app `skills/`.

- **Action-backed skill scan paths.** `Action.resolve_skill_scan_dirs()` and `skill_resolve.resolve_action_skill_scan_dirs()` derive overlay paths from loader metadata (`info.yaml` `package.name`) — no per-action hardcoded refs. Fixes `signup_interview` discovery after ADR-0020 overlay migration.

- **`interview_action` package layout.** Root holds only `SKILL.md`, `interview_action.py`, `info.yaml`, `README.md`, `CLAUDE.md`, `AGENTS.md`. Core modules under `core/`; reference packages under `examples/` (not skill-discovered); empty `api/` removed; `sop/` retired (authoring template → `docs/skill_custom_instructions.md`).

- **Skill SOP inheritance (`extends`) + action-backed placement (ADR-0020).** JV skills may declare `extends: action:<namespace>/<action>` or `extends: skill:<name>` to compose base SOP markdown at discovery (`jvagent/scaffold/sop_extend.py`). Action base SOPs live at `<action_dir>/SKILL.md`; action-backed skills live under `<action_dir>/skills/<name>/` (app overlays: `agents/.../actions/.../skills/`). Agent `agents/.../skills/` is reserved for pure JV SOPs and `spec: claude` bundles; legacy action-backed paths log a deprecation warning. Interview implicit injection removed — skills declare `extends: action:jvagent/interview`. `signup_interview` and `example_interview` relocated. Covered by `tests/scaffold/test_sop_extend.py`, `tests/scaffold/test_action_skill_discovery.py`.

- **Scalable interview SOP (superseded by ADR-0020).** Standard tool-loop procedure now lives in `interview_action/SKILL.md` and is composed via explicit `extends` rather than implicit `requires-actions` detection.

- **InterviewAction docs aligned to scalable SOP.** `README.md`, `docs/` (index, extending, multi-turn-flow, troubleshooting), `CLAUDE.md`, legacy `interview/README.md` banner, zoon-ai `docs/interviews.md`, and planning `actions-catalog.md` updated for frontmatter `interview:` contract, procedure injection, and custom-only `SKILL.md` bodies.

- **Interview SOP assets moved to `sop/`.** Runtime procedure (`standard_procedure.md`) and authoring template (`skill_custom_instructions.md`) relocated from `docs/` so they are not confused with how-to documentation.

- **Interview spec in SKILL.md frontmatter.** `InterviewRegistry` now loads the machine contract from the `interview:` key in `SKILL.md` frontmatter (`parse_interview_spec`, `load_interview_spec_from_skill`). Standalone `interview.yaml` remains as a deprecated fallback (warning logged). Migrated reference/example/fixture skills and signup_interview; covered by `tests/action/interview/test_interview_frontmatter_load.py`.

- **InterviewAction documentation pass.** `README.md`, `docs/extending.md`, `docs/multi-turn-flow.md`, and `docs/troubleshooting.md` now document `interview.yaml` (not `contract.yaml`), turn-prep seeding, `retain_context_keys` / `clear_interview_context()`, auto-chained `next_tool`, and review/complete confirmation patterns.

### Added

- **Base `interview__reset_interview` tool.** All interview skills inherit a standard reset tool that clears progress and restarts from the first question (start-over intent only; cancel uses `interview__cancel`). Skills may override via `interview.reset.function` in frontmatter (same pattern as `review` / `completion`) — implement the handler in `scripts/custom_tools.py`; the model still calls `interview__reset_interview()`. Handler: `_handle_reset_interview` / `_handle_custom_reset` in `interview_action.py`. Covered by `tests/action/interview/test_signup_reset.py`, `test_reset_handler.py`.

- **Interview `allowed-tools` frontmatter merge.** Skills extending `action:jvagent/interview` inherit base `allowed-tools` from the action's `SKILL.md`; skill frontmatter `allowed-tools` is additive and `disabled-tools` removes base entries (e.g. `interview__cancel` when cancel is handled via `interview.reset`). Merged at discovery via `merge_extends_allowed_tools` in `jvagent/scaffold/sop_extend.py`. Covered by `tests/scaffold/test_sop_extend.py`. Zoon `onboarding_interview` and `pre_alert_interview` migrated.

- **InterviewAction skill-based refactor.** `interview.yaml` replaces `contract.yaml`; `InterviewRegistry`/`InterviewSpec` replace `ContractRegistry`/`InterviewContract`. New `runtime/` package (`hooks`, `pipeline`, `path_resolver`, `branch_eval`) implements input handlers, branch-aware paths, auto-chained `next_tool` directives, and `cancel` handler support. Covered by `tests/action/interview/`.

- **Orchestrator loose-coupling + `InterviewAction` tool bundle.** Interview-specific branches removed from `OrchestratorInteractAction`; generic skill lifecycle lives in `skill_tasks.py` (`resolve_active_task_lock_skill`, `compose_skill_activate_hooks`, `requires-actions` binding). `jvagent/interview` relocated from zoon-ai (successor to legacy `InterviewInteractAction`); skills clone `example/example_interview/` with `contract.yaml` + frontmatter (`task-lock`, `requires-actions`). Covered by `tests/action/orchestrator/test_skill_tasks.py`, `tests/action/interview/`.
- **Common parameter subsystem + response hardening (weak-model-safe).** Behavioural rules are now **parameters** — persona-shaped `{condition?, response}` plus a **`scope`** — declared on the `Action` base, so every action shares one subsystem (`jvagent/action/parameters.py`). Scope routes where a rule is injected: **`orchestration`** rules render in the LOOP PROTOCOL of the executive's system prompt; **`response`** rules (the default when scope is unspecified) render in the ReplyAction compose. Each turn the Orchestrator **accumulates** every enabled action's scoped params onto `interaction.parameters` (queued like directives — deduped, persisted, observable in the Debug view); each injection site renders only its scope. The **Orchestrator** natively owns the orchestration core, the **ReplyAction** the response core (no AI/model/provider disclosure, no knowledge cutoff, no internal-architecture reveal, no invitation closers, grounding); any action contributes more. Hardening is enforced at three layers: **(1) authorship** — the OPERATING RULES section renders the orchestration rules *plus the core response params* (so a reply the executive writes itself, on the fast `reply` path, is hardened too) and a peak-attention `SAFEGUARDS_REMINDER` rides in the user prompt each step (the technique that got the model to comply with directives); **(2) compose** — `ReplyAction.respond()` renders the response params (PersonaAction-style MANDATORY directives + a COMPLIANCE-CHECK tail + a directive reminder); **(3) deterministic egress scrub** — `vet_egress()` runs at the single `_pipe_response` choke point (fast literal *and* composed) and drops, sentence-level, self-identification as an AI/model/provider, knowledge-cutoff statements, and trailing invitation closers (conservative + self-referential, so topical mentions and specific asks survive). The fast literal-publish path is preserved — core params carry an internal `ambient` flag so pooling them doesn't trip the slim-vs-compose gate. Fixes the orchestrator under-claiming/over-disclosing ("I can't sign you up…", "I have a variety of tools such as…") and the cutoff/closer leaks ("trained on data up to October 2023", "…just let me know"); verified live on a weak model (gpt-4o-mini). The `parameters` attribute moved from `InteractAction` to `Action` (`Agent.collect_capabilities`/`get_capabilities` symmetry retained). Covered by `tests/action/test_parameters.py`, `tests/action/orchestrator/test_parameters_hardening.py`, `tests/action/reply/test_reply_action.py`.
- **LOOP PROTOCOL reorg + generalized memory protocol.** The orchestrator system prompt's step-selection block is now a labelled **LOOP PROTOCOL** section, and planning, the tool-use policy, and memory render *inside* it (via a `{loop_protocol_extra}` slot) instead of trailing after the rules. The vision-gated `artifact_recall_prompt` ("MEMORY OF UPLOADS") is generalized into a standing **`memory_prompt`** — a memory-access protocol covering both sources (the conversation in context + saved artifacts), with artifact-tool use phrased conditionally so it's safe whether or not those tools are surfaced.

- **Media-aware payload size limit on the public `interact` endpoint.** The endpoint validated the *entire* `data` JSON against a hardcoded 256 KB cap, base64 image included — so any photo over ~190 KB was rejected outright (`data exceeds maximum size of 262144 bytes`) before vision/ingestion ran. `validate_data_payload` is now **media-aware**: the known upload keys (`image_urls`, `whatsapp_media`, `files`, `attachments`, `documents`) are validated against a separate, generous **media** cap (`JVAGENT_INTERACT_MAX_MEDIA_BYTES`, default 20 MB), while the rest of `data` — control fields — stays bounded by the small **control** cap (`JVAGENT_INTERACT_MAX_DATA_JSON_BYTES`, default 256 KB) for abuse protection. Both are now env/`app.yaml`-configurable (previously neither was) and either may be `none` to disable. Per-item upload size is independently capped (`uploads.py`, 5 MB/item). Covered by `tests/action/interact/test_rate_limiter_payload.py`. *(The longer-term fix — uploading media to storage and sending a reference/URL instead of inline base64 — is tracked separately.)*
- **Session-token authentication for the public `interact` endpoint** (ADR-0020). `POST /agents/{id}/interact` is intentionally unauthenticated to serve embeddable, anonymous chat — but `user_id`/`session_id` were **client-asserted strings the server never issued and could not verify**, making `session_id` a forgeable bearer credential (conversation hijack / IDOR / a `session_id`→`user_id` enumeration oracle). A new **`jvagent/action/interact/session_token.py`** restores integrity through two doors, reusing jvspatial's HS256 signer + `JVSPATIAL_JWT_SECRET_KEY` (no bespoke crypto): **Mode A** trusts a real login JWT (`Authorization: Bearer`) and derives `user_id` from it; **Mode B** mints a short-lived anonymous **session capability token** bound to one conversation — claims carry `agent_id`/`session_id`/`user_id`, a per-`Conversation` `token_secret` (`cs`), and a `web` channel scope — required on every resume. The endpoint runs a **pre-spawn identity guard** (`resolve_interact_identity`) before any LLM cost, threads the *verified* `user_id` into the walker, and mints/refreshes the Mode B token into the non-stream response body and the streaming `start` chunk. Revocation is free (rotate `Conversation.token_secret`); web tokens **cannot** resume a provider-channel (WhatsApp/Messenger/email) session. **Staged rollout** via `JVAGENT_INTERACT_PUBLIC_AUTH ∈ {off (default, legacy), log (observe-only — never rejects), required (enforce 401)}` so deploys don't break existing clients; token TTL via `JVAGENT_INTERACT_TOKEN_TTL_SECONDS` (default 7d). `Conversation` gains `token_secret` + `ensure_token_secret`/`rotate_token_secret` (lazy backfill on resume). The `InteractWalker` and channel webhooks are out of scope (they establish identity at their own edge). Covered by `tests/action/interact/test_session_token.py`.
- **Vision input + conversation-scoped artifact memory** (ADR-0021). Image uploads are first-class for the orchestrator again (lost when PersonaAction was replaced). A new **`jvagent/vision` `VisionAction`** owns its own multimodal model (independent of the reasoning model) and interprets images in the canonical `visitor.data["image_urls"]`; a gated (**`vision`**, default off) **pre-loop reflex** runs it when a turn carries images, persists the description as a conversation **artifact**, and seeds it into the turn so the reply uses the image context — plus an on-demand **`interpret_images`** tool. Artifacts live in a new **`Artifacts` branch node** under the `Conversation` (queryable in one traversal) and associate to the producing `Interaction(s)` via a `PRODUCED` edge; lifecycle is a **refcounted cascade** hooked into the existing `interaction_limit` pruning (`prune_artifacts_with_interaction`, default on; `pinned` exempts) — so the registry stays bounded with no separate artifact-pruning system. **Removed remnants:** the legacy `Interaction.image_interpretation` field, the dormant `Interaction.artifacts` dict (defined but never used), and PersonaAction's behind-the-scenes interpretation storage/read-back + `vision_model_*` config. Covered by `tests/memory/test_artifacts.py`, `tests/action/test_vision_action.py`, `tests/action/orchestrator/test_vision_reflex.py`; wired (off by default in code, on in the example agent) via the `jvagent/vision` action.
  - **All uploaded files become artifacts (S4).** Previously only images produced an artifact (the vision interpretation); uploaded documents/text/binaries were dropped. A new orchestrator pre-loop reflex `_ingest_uploads` (attribute `ingest_uploads`, default on) scans every upload key in `visitor.data` (`image_urls`, `whatsapp_media`, `files`, `attachments`, `documents` — configurable via `upload_data_keys`) and records one `source="upload"` artifact per file. Bytes are persisted to the caller's **per-user file storage** and referenced by a new `Artifact.path` (plus `filename`/`mime`/`size`) — **never stored inline on the node**, keeping the graph lean (base64-on-node would bloat every conversation read/backup). Text files are decoded into the queryable `data`; an uploaded **image** is enriched in place with a **per-image** VisionAction interpretation so it is **one consolidated artifact** (file reference + its own interpretation, tagged `interpreted`/`vision`) rather than two — the single-artifact-per-file shape is the extension point for document interpreters later. The standalone vision reflex (separate `source="vision"` artifact) remains as the fallback only when `ingest_uploads` is off. The refcounted prune was extended (`_delete_artifact_file`) so reaping a file-backed artifact also deletes its stored bytes — no orphans. Pure helpers in `jvagent/action/interact/utils/uploads.py`. Covered by `tests/action/interact/test_uploads.py`, `tests/action/orchestrator/test_ingest_uploads.py`, and a file-cleanup case in `tests/memory/test_artifacts.py`.
  - **Recall on back-reference (S3).** Storing the interpretation wasn't enough — when a later turn referred back to an earlier image ("which house is more luxurious?") without re-uploading it, a weak model would say it "can't recall previous images" rather than reach for the (already-surfaced, working) `list_artifacts`/`get_artifact` tools. Two fixes: a vision-gated **affordance line** in the system prompt (`artifact_recall_prompt`) telling the model that earlier uploads persist as artifacts to consult before claiming it can't recall; and a **deterministic recall seed** — when vision is on, the turn carries no new image, the conversation holds image artifacts, and the utterance reads like a back-reference, the most-recent interpretation(s) are seeded straight into the loop (bounded to 2 artifacts × 1200 chars) so recall doesn't depend on the model choosing a tool. The `list_artifacts`/`get_artifact` tools were already pinned visible (not subject to lean surfacing) whenever vision is on. Covered by `tests/action/orchestrator/test_artifact_recall.py`.
- **Always-visible tool pins under lean surfacing** (ADR-0018 §5). Above the lean threshold the action+MCP long tail is gated purely by lexical relevance, so a capability that must fire turn-1 regardless of phrasing could fall behind a `find_tool` round-trip — and the only lever was `lean_tool_threshold: 0`, which un-leans the whole surface. Two opt-in pins fill that middle, applied *after* the lean policy so they survive it: a new **`pinned_tools`** orchestrator attribute (tool-name globs, e.g. `["filing__*"]`) and **`always-active: true`** on a skill, which now pins that skill's `allowed-tools` into the visible set every turn. Both default off/empty (no behaviour change) and preserve lean for the rest of the surface. **Bug fix:** `always-active` was parsed but never read by the orchestrator — it only let a skill bypass the `skills:` selector (a no-op under `skills: "-all"`), so it silently did nothing for tool visibility; it now pins. Covered by `tests/action/orchestrator/test_lean_surfacing.py`; lean knobs + `pinned_tools` now surfaced in the example agent + scaffold profile.
- **Orchestrator-owned resumable plan** (ADR-0019) — an opt-in `update_plan` tool lets the model record a multi-step plan as a checklist that **persists across turns** on the conversation `TaskStore` (as an `AGENTIC_LOOP` control-task owned by the orchestrator), so an interrupted multi-step turn resumes instead of re-planning. Gated by a new `planning` attribute, **off by default** (zero cost when unused; when on, cost is incurred only when the model calls `update_plan`). Full-state overwrite (TodoWrite-style) keeps a single active plan; an unfinished plan is re-surfaced next turn via a soft `plan_resume_note` (not a hard lock — consistent with `lock_active_flow=False`); the loop's `finally` completes-and-clears a done plan or leaves a pending one active to resume (so budget/"continue" resume falls out for free). Activates the previously-dead `AGENTIC_LOOP` task type. New `TaskHandle.sync_plan` + `normalize_step_status` on the `TaskStore`. Side-effect idempotency on resume is out of scope. Covered by `tests/action/orchestrator/test_plan_persistence.py`.
- **Lean tool surfacing** (ADR-0018) — progressive tool disclosure that keeps the orchestrator prompt slim. The `find_tool`/`load_tool` catalog was decorative because `_assemble_tools` marked every action + MCP tool `visible`, dumping the whole surface (~40 tools) into the prompt every tick. Now the hideable long tail is listed only when its count is at/under `lean_tool_threshold` (default 15); above it, the prompt carries the always-on core (egress, meta-tools, core, active-flow) plus a per-turn **relevance pre-surface** of the `lean_presurface_k` (default 6) tools most relevant to the user's message (cheap token overlap, no model call), and the rest stay reachable via `find_tool`. `find_tool` output is grouped by namespace, and a one-line hint tells the model the list is partial when lean. `lean_tool_threshold: 0` disables (always full). Dispatch and `block_raw_tool_invocation` already supported hidden tools, so only what the prompt *lists* changed. Covered by `tests/action/orchestrator/test_lean_surfacing.py`.
- **Host skill providers (embedded deployments).** [`skill_providers.py`](jvagent/action/orchestrator/skill_providers.py) lets embed hosts register sync callables via `register_host_skill_provider()` that return extra `SkillDoc` entries merged into `discover_skill_docs()` after filesystem resolution. Filesystem/app-local skills win on name collision; host providers run regardless of `skills_source`. Integral uses this for per-workspace App-bundled skill overlays. Covered by `tests/action/orchestrator/test_host_skill_providers.py`. See [`docs/ORCHESTRATOR.md`](docs/ORCHESTRATOR.md) § Host skill providers.
- **Two skill specs + a multitenant code-execution substrate** (ADR-0017). Skills now carry a `spec` frontmatter key with exactly two values: `jv` (default — an SOP that references action/IA tools via `allowed-tools`/`requires-actions`) and `claude` (a standard Anthropic Agent Skills folder whose bundled scripts the model runs). The earlier "skill `scripts/` as typed tools" idea (a third variation) is gone.
  - **`jvagent/core/sandbox.py`** — the per-user/agent filesystem sandbox service, promoted from `action/mcp/sandbox.py` into core so `MCPAction`, the `file_interface` action, and `code_execution` share one service (no more reaching into a sibling action's submodule). `resolve_mcp_sandbox_relpath` → `resolve_user_sandbox_relpath`; new `provision_user_sandbox(agent, user, fi)` returns a ready per-user cwd; the generic path guards (`validate_relative_path`, `normalize_sandbox_dir_prefix`, `resolve_agent_user`) moved here too.
  - **`jvagent/code_execution`** — a `CodeExecutionAction` exposing `code_execution__bash`, whose cwd is the caller's own `<agent_id>/<user_id>/` slice (per-user data isolation inherited from `core.sandbox`). Per-execution OS limits come from a pluggable `Executor` (default `SubprocessExecutor`: no network, CPU/memory/time/output caps, scrubbed env, confined cwd — a pragmatic default, **not** a hard jail; swap in a container/jail backend for untrusted skills). **Off by default**, enabled per agent.
  - **Claude-skill activation** stages the skill folder at `staged_skills/<name>/` in the caller's slice (`use_skill` `activate_hook`) so its scripts are runnable; JV skills still just surface their referenced tools.
  - **`pdf_generation` and `triage` are now Claude skills** (bundled CLI scripts run via the substrate; `pdf_generation` renders a PDF into the user's own slice as the canonical end-to-end proof).
  - **`fileinterface` → `jvagent/file_interface` action** (8 file-I/O tools) and **`skill_hub` → `jvagent/skill_hub` action** (4 management tools): both were dead "skills" whose tools nothing loaded; their capabilities are now first-class action tools.

### Fixed

- **Interview answer quality (prompt-first).** The orchestrator could call `interview__set_fields` with acknowledgements or filler (e.g. "Ok Ok" as a full name) because only structural validators ran after the tool call. The base interview procedure now includes an **Answer quality gate** — the model must not call `set_field` unless the latest message substantively answers the active question. Per-question `description` is surfaced in `next_fields` tool observations; `interview__set_fields` tool description and locked-turn `pending_directive` reinforce the gate. `signup_interview` `user_name` description and custom instructions updated with acceptance criteria. Covered by `tests/action/interview/test_interview_procedure.py` and `test_build_next_field.py`.
- **Interview intent routing (cancel vs start over).** Cancel/stop messages could invoke a skill-specific reset and chain `interview__next_field`, restarting instead of closing the session. Base procedure adds **Intent routing** and **`interview__reset_interview`** as the standard reset tool (cancel → `interview__cancel` only; start over → `interview__reset_interview` or `interview.reset` handler). Reset handlers return `Tell the user:` directives (no same-turn `next_field` chain). Covered by `test_interview_procedure.py`, `test_signup_reset.py`, `test_reset_handler.py`.
- **InterviewAction contract discovery.** Discovery now resolves `agents/<ns>/<agent>/skills` (matching the orchestrator) instead of walking up to built-in `jvagent/skills/` (no agent `contract.yaml` files). Covered by `tests/action/interview/test_contract_discovery.py`.
- **Skill turn-lock runtime bootstrap (generic).** Turn-lock prep lives in `skill_tasks.apply_task_lock_turn` with generic orchestrator-owned lifecycle handling. Orchestrator `_apply_active_task_lock_skill` delegates only. `use_skill` surfaces declared tools after activation hooks; `binds_tools_to_visitor` gates visitor-bound tool wrap. Fixes turn-1/turn-2 `NO_SESSION` loops. Covered by `tests/action/orchestrator/test_interview_no_session_turn1.py`, `tests/action/orchestrator/test_interview_turn_lock_e2e.py`, `tests/action/orchestrator/test_apply_task_lock_turn.py`, `tests/action/interview/test_session_contract_reload.py`.
- **`get_session` new-user race** — ``_check_is_new_user`` consults the
  ``(memory_id, user_id)`` compound index, not only live Memory edges, and
  resuming an existing conversation always returns ``new_user=False`` so
  first-time intro does not re-fire after restart when the User row survived
  but the Memory edge was temporarily missing.
- **Restored interact debug/observability detail in local dev.** A public-endpoint hardening pass had `build_interact_response(public_endpoint=True)` redact the full `interaction` payload (events, `observability_metrics`, tasks) **and** the `report` unconditionally — so the jvchat Debug view went blank and lost observability metrics even in local dev. Redaction is now gated: production always redacts (unchanged); the public endpoint additionally redacts outside production only when `JVAGENT_INTERACT_REDACT_DEBUG` is set (for non-prod internet deploys). Local dev keeps full detail by default. Covered by `tests/action/interact/test_response_redaction.py`.
- **Interview no longer leaks a field's internal `description` into the question.** The signup interview asked "What's your full name? (The user's full name)" — the field `description` was rendered inline via `QUESTION_DIRECTIVE` (`{question} ({description})`) and the model echoed the parenthetical. The default directive drops inline `{description}` (folded into non-echoed `Note:` guidance by `question_node`; custom templates that place `{description}` keep ownership), and the example signup agent's `question_directive` override — which still hardcoded `({description})` — was corrected. Covered by `tests/action/interview/test_question_directive_leak.py`.

- **Adversarial review remediation (security + concurrency).** PageIndex assimilate now reuses the shared SSRF guard and rejects filesystem paths outside the action work directory ([`pageindex/url_guard.py`](jvagent/action/pageindex/url_guard.py)). Public interact defaults to **`required` auth in production** when `JVAGENT_INTERACT_PUBLIC_AUTH` is unset; session-token `cs` claim uses `hmac.compare_digest`; debug fields are redacted on the public route even in dev. Optional `data` payloads are capped at 256KB serialized JSON; inline base64 uploads reject items over 5MB before decode. The conversation **turn lock** now spans interaction create + full walker traversal (reentrant `conversation_mutation_lock`); serverless boot warns when neither Redis nor DynamoDB distributed lock is configured. Orchestrator locked-turn path calls `_finalize_plan`; stale flow tasks whose owner is no longer routable are cancelled. Streaming interact **awaits** background actions (matching non-streaming Lambda behavior). Covered by new tests under `tests/action/interact/` and `tests/action/orchestrator/`.
- **`requires-actions` is now enforced (hard gate) with inline version constraints.** A JV skill's `requires-actions` frontmatter — the Action types the SOP depends on — was parsed onto the bundle (and printed by the CLI) but never checked, so a skill whose Actions were absent or disabled still surfaced and activated, only to fail mid-procedure. `_assemble_tools` now resolves each declared Action type once (enabled-only, O(1) cached) and **hides any skill whose requirements aren't all met** for that turn: dropped from the surfaced skill list, `find_skill`, `use_skill`, and `always-active` pinning, so the model never sees a skill it can't run. Each entry may now carry an **optional inline version constraint** in PEP 508 style — the comparison operator is the delimiter (`PageIndexAction>=2.0`, `WebFetchAction==1.4.0`, `GmailAction>=1.0,<2.0`); the resolved Action's `get_version()` is checked against it (fails closed when constrained but the action reports no/uncomparable version; an unparseable constraint degrades to presence-only). This **replaces the separate `requires-action-versions` map**, which is removed (it was keyed by `namespace/label` and was itself never enforced). Skills with no `requires-actions` are unaffected; `requires_actions` now rides on `SkillDoc`. Covered by `tests/action/orchestrator/test_requires_actions.py`.
- **Restored the orchestrator's typed streaming emission for chat UIs** (orchestrator stream-emission spec). After the SkillExecutive→Orchestrator migration the producer stopped sending the thought envelopes a chat UI/translator (jvchat, integral's assistant-ui translator) renders, so reasoning + tool sections vanished and acks/greetings concatenated into the answer bubble. Now: (a) **acks are channel-conditional** — `thought`/`status` (ephemeral activity strip) on a streamed UI, but a whole `category="user"` message on a non-streamed channel so WhatsApp/Messenger users actually see "working on it" (the channel adapter delivers it; `transient` ⇒ not persisted); (b) reasoning/progress thoughts carry `thought_type="reasoning"` and fire on **both** gears (single-step light turns show reasoning too, not only multi-step heavy ones); (c) tool dispatch emits structured `thought_type="tool_call"` (before) + `"tool_result"` (after) sharing one `segment_id`, with `tool_name`/`tool_args`/`tool_result`/`is_error` metadata, for substantive tools only. jvchat's existing consumer already reads this taxonomy; no client change needed. Covered by `tests/action/orchestrator/test_stream_emission.py`; contract documented in `docs/ORCHESTRATOR.md`.
- **Locked flow no longer echoes the IA-as-tool status sentinel as a reply.** With `lock_active_flow: true`, the orchestrator dispatched the active flow's IA tool and decided whether it had spoken by checking only `interaction.response`. But rails IAs (the interview) publish via **queued directives** (`visitor.add_directive`), not by setting `interaction.response` — so the check false-negatived and the orchestrator voiced the loop-internal sentinel `(ran <Action>)` through `ReplyAction`, surfacing it as a stray reply/directive and muddying directive composition (erratic off-topic behaviour mid-interview). The locked path now detects emission via `_ia_emitted` (response **or** queued directive) and **never** echoes the IA status sentinels (`(ran X)` / `(no visitor available)` / `(flow error: …)`) — falling back to the clean `clarify_text` instead. Covered by `tests/action/orchestrator/test_flow_lock.py`.
- **Orchestrator egress terminology renamed off "voice".** The orchestrator's internal `_voice`/`_maybe_voice_final`/`voiced` (a metaphor that collided with the real TTS/voice-channel plumbing) are now `_emit_reply`/`_maybe_emit_final`/`emitted`, aligning with the `reply` egress and `_emit_thought`. Internal only — no config or behaviour change.
- **Markdown (`.md`) file saves no longer rejected as `application/octet-stream`.** jvspatial's storage validator allow-lists `text/markdown`, but on hosts without `libmagic` it falls back to stdlib `mimetypes`, which has no `.md` entry on some OSes (e.g. macOS) — so every `.md` an agent wrote (research reports, notes) failed validation. `jvagent/_mimetypes_compat.py` now registers `.md`/`.markdown` → `text/markdown` in `mimetypes` at package import (before any save path, mirroring the `_logging_compat` shim), making detection deterministic without depending on `libmagic` or the host mime database. Covered by `tests/test_mimetypes_compat.py`.

### Changed

- **Interview stays on-script for off-topic asides (example).** Off-topic input that extracts no field re-emits the question node's `question_directive`, which the responder composes its reply from — so the example signup agent now overrides `question_directive` to answer an off-topic aside in one short sentence and then re-ask the pending field (ending the message with it), instead of answering unrelated questions at length. Also adds a configurable `active_task_description` attribute on the interview action (placeholders `{action_title}`/`{action_description}`) that overrides the control-task description used for divergence tracking; default unchanged.

### Removed

- **Skill frontmatter `plan-steps` removed** (dead under the Orchestrator pattern). It was parsed and shown by `skill show` but the runtime `SkillDoc` never carried it and `use_skill` never surfaced it — the SOP body is the plan. Dropped from the parser, CLI, the `answer` skill, and the skills README.
- **Skill frontmatter `response-mode` removed** (dead under the Orchestrator pattern). It was parsed and shown by `skill show` but never consumed — egress is the `reply`/`respond` tools, and `ReplyAction` adapts (slim publish vs. composed respond) from queued directives/parameters. Dropped from the parser, CLI, the `answer` skill, the skills README, and its tests.
- **Redundant skill bundles that duplicated action operations removed.** The library skills that re-wrapped an action's operations as `scripts/` stubs are deleted: `gmail`, `calendar`, `google_drive`, `google_sheets`, `microsoft_excel`, `microsoft_onedrive`, `outlook_mail`, `outlook_calendar`, `pageindex_docs`, `pageindex_search`, `web_search`, plus the orphaned `jvagent/skills/_action_helpers.py`. Those operations are now first-class **action tools** (see Added). Remaining library skills settled into the two specs (see Added): JV skills `research`, `answer`; Claude skills `pdf_generation`, `triage`. (`fileinterface` and `skill_hub` became actions.) `jvagent/skills/README.md` rewritten around the two specs.
- **Bridge, Helm, Cockpit, and Executive + Centers patterns removed.** Replaced by the **Orchestrator** single-orchestrator pattern (`jvagent/action/orchestrator/`). The old `jvagent/action/executive/` package (ExecutiveInteractAction + Skills/IA/Persona centers), bridge/helm/cockpit packages, and related tests/examples are deleted. The scaffolder default profile is `executive` (installs `jvagent/orchestrator` + `jvagent/reply`). Removed legacy `Manifest.turn_lock`, `can_interrupt`, and `interrupt_phrases` fields (flow continuation is now TaskStore + `lock_active_flow`). See [`docs/ORCHESTRATOR.md`](docs/ORCHESTRATOR.md) and [`.planning/adr/0012-skill-executive-architecture.md`](.planning/adr/0012-skill-executive-architecture.md).

### Added

- **Orchestrator prompt surface is fully overridable from `agent.yaml`.** Every sub-prompt is now a config attribute defaulting to its constant in `jvagent/action/orchestrator/prompts.py` (same pattern as other actions): `system_prompt`, `user_prompt`, `tool_use_policy_prompt`, `flow_in_progress_prompt`, `length_limit_prompt`, `finalize_prompt`, `no_skills_text`, plus an additive `system_prompt_extra` appended to the base body. Overrides are `str.format` templates (preserve placeholders; double literal braces); a malformed override falls back to the built-in for that piece and logs, so a bad string never breaks a turn. The agent's identity still comes from the Agent's `alias` + `role` (ADR-0014). See [`.planning/reference/configuration-keys.md`](.planning/reference/configuration-keys.md) §6.
- **Core integration actions expose their full tool surface via `get_tools()`** (ADR-0012: actions are first-class tools). Retrofitted to full operation parity so the Orchestrator uses them directly (replacing the deleted skill stub bundles): `GoogleGmailAction` (5 tools), `GoogleCalendarAction` (3), `GoogleDriveAction` (6), `GoogleSheetsAction` (14), `MicrosoftOutlookMailAction` (6), `MicrosoftOutlookCalendarAction` (3, added from scratch), `MicrosoftOneDriveAction` (4), `MicrosoftExcelAction` (10). Each tool delegates to the action's existing method and returns JSON. Locked in by `tests/action/test_action_tool_surfaces.py` (skips gracefully when optional integration deps are absent).
- **Orchestrator v1 capability surface.** The single-orchestrator pattern reaches v1 with: **model gearing** (a light completion model handles single-dimensional turns, escalating to the heavy reasoning model on multi-step/skill work — `light_model`/`light_model_action_type`/`escalate_after_tool_calls`/`escalate_on_skill`; a light model with no main model becomes the sole model) — ADR-0016; **MCP `tool_servers`** integration (tools from `jvagent/mcp` surface as `mcp_<server>__<tool>` with per-user dispatch); **`jvagent/web_fetch`** (SSRF-guarded page fetch → markdown, tool `web_fetch__fetch`); the full **configuration surface** (reasoning passthrough, budgets, tool tier/timeout, `block_raw_tool_invocation`) — ADR-0015; and **egress via `jvagent/reply`** (identity on the Agent's `alias`+`role`) — ADR-0014. Progress/reasoning streaming and the transient ack are gated to the **heavy** gear (light-only turns stay quiet). See [`docs/ORCHESTRATOR.md`](docs/ORCHESTRATOR.md) and [`.planning/reference/configuration-keys.md`](.planning/reference/configuration-keys.md) §6.
- **Drop-in `jvagent.embed.Server` subclass mounts jvagent's HTTP routes on the host automatically.** Hosts swap `from jvspatial.api import Server` for `from jvagent.embed import Server`; the subclass overrides `get_app()` so jvagent's `@endpoint`-decorated modules (admin agents CRUD, status, app/update_mode, graph repair, memory admin, action endpoints, logging, optional google OAuth) sync onto the server *before* FastAPI snapshots the endpoint router — guaranteeing routes appear on the live ASGI app. No timing-sensitive call between `Server(...)` and `get_app()` required. Suppress with `JVAGENT_EMBED_ENDPOINTS_DISABLED=true` (or `1`/`true`/`yes`/`on`, case-insensitive); the programmatic surface (`embed.interact`, `interact_stream`, etc.) stays available. The lower-level `embed.register_jvagent_endpoints_on_host(server)` function remains available for hosts that prefer explicit control. Shared importer lives in `jvagent.core.embed_endpoints`; the standalone CLI now delegates to it for parity.
- Startup index migration: `jvagent.core.index_bootstrap.run_index_migration()` runs before application graph bootstrap — drops deprecated MongoDB index names, then eagerly calls `ensure_indexes` for core entity types. Wired from `pre_startup_bootstrap` and `bootstrap_only`. See [docs/database-indexing.md](docs/database-indexing.md).
- Exception taxonomy in `jvagent.core.errors`: `TransientError`, `IntegrationError`, `ConfigError`, `LogicError`, plus `classify_exception()` / `is_transient()` helpers covering common httpx and stdlib exceptions. Foundation for replacing broad `except Exception:` clauses with classified handling.
- `jvagent.core.jvspatial_compat`: single chokepoint for jvspatial private-API access (`find_raw_node_records`). Used by `AgentLoader` ghost-node detection so the coupling lives in one place.
- `jvagent.tooling.tool_executor.ToolDispatchContext`: immutable dataclass exposing `agent_id`, `user_id`, `session_id`, `interaction_id`, `channel` to tool closures. `MCPAction` now reads it instead of the live walker.
- `jvagent.skills._action_helpers.resolve_action()`: shared resolver-and-error helper for skill scripts that delegate to actions; adopted by gmail / outlook_mail / calendar / outlook_calendar bundles.

### Changed

- **Renamed the SkillExecutive pattern → Orchestrator (forward-only, no alias).** The `skill` prefix misdescribed a component that coordinates the whole tool surface, not just skills. Renames: action `jvagent/skill_executive` → **`jvagent/orchestrator`**; class `SkillExecutiveInteractAction` → **`OrchestratorInteractAction`**; package `jvagent/action/skill_executive/` → `action/orchestrator/`; scaffold profile `executive` → **`orchestrator`** (the default profile); observability event `executive_activation` → `orchestrator_activation`; example agent `executive_agent` → `orchestrator_agent`; `docs/EXECUTIVE.md` → `docs/ORCHESTRATOR.md`; prompt constants `SKILL_EXECUTIVE_*` → `ORCHESTRATOR_*`. **Breaking:** existing `agent.yaml` files must change `action: jvagent/skill_executive` → `jvagent/orchestrator` and re-run `--update`. ADRs 0011–0016 retain the historical name (immutable); ADR-0012 carries a rename errata.
- **Orchestrator harness remediation.** Proactive task pipeline aligned on TaskStore `data`/`id` schema (`task_payload` helpers; `TaskTriggerInteractAction`, `TaskDispatcher`, `TaskCreationInteractAction`). TaskTrigger runs at weight `-250` (before Orchestrator) so directives inject on the same turn. Multi-active-flow precedence prefers the most recently updated task. Agent YAML validator warns when both `orchestrator` and `interact_router` are installed. Executive reference agent interview settings moved to `context`. Docs/SPEC updated (removed stale `ModelBudget`, dead module links). `jvagent/action/agent_interact/` and `jvagent/action/skill/` (plus the `agentic` scaffold profile and `docs/agent-interact.md`) are removed. `jvagent/cockpit_interact_action` is the unified routing + skill-loop action. Production importers (`action/endpoints.py`, `model/language/anthropic.py`, `skills/skill_hub/install_skill.py` and `remove_skill.py`) migrated to cockpit equivalents. Message-format helpers moved to `jvagent.action.model.utils.message_format`.
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
