# Actions Catalog

> Inventory of every action shipped in `jvagent/action/`. Use this before authoring a new one — much of what you need may already exist.
>
> Companion: [`action-authoring.md`](action-authoring.md). Last surveyed: 2026-05-30.

Path convention: `jvagent/action/{dir}/`. Implementation file is `{dir}/{name}.py` or `{dir}/{name}_interact_action.py`.

> **Naming caveat**: the **canonical action name** is `info.yaml` →
> `package.name` (e.g. `jvagent/whatsapp_action`). The directory commonly
> drops the `_action` / `_interact_action` suffix for brevity. Below, the
> short forms (e.g. `jvagent/whatsapp`) are catalog shorthand; the
> authoritative spelling is in each action's `info.yaml`. `agent.yaml`
> writers must use the YAML spelling. AUDIT-actions XC-5 (resolved at the
> docs level; renaming directories is deferred to avoid breaking external
> consumers).
>
> Canonical names (`info.yaml.package.name`) for the most-aliased actions:
>
> | Catalog shorthand | `info.yaml` canonical |
> |---|---|
> | jvagent/whatsapp | jvagent/whatsapp_action |
> | jvagent/handoff | jvagent/handoff_interact_action |
> | jvagent/intro | jvagent/intro_interact_action |
> | jvagent/interview | jvagent/interview_interact_action |
> | jvagent/interview_action | jvagent/interview_action |
> | jvagent/long_memory | jvagent/long_memory_interact_action |
> | jvagent/long_memory_store | jvagent/long_memory_store_interact_action |
> | jvagent/long_memory_retrieval | jvagent/long_memory_retrieval_interact_action |
> | jvagent/retrieval | jvagent/retrieval_interact_action |
> | jvagent/web_search_retrieval | jvagent/web_search_retrieval_interact_action |
> | jvagent/converse | jvagent/converse_interact_action |
> | jvagent/access_control | jvagent/access_control_action |
> | jvagent/video_generation | jvagent/heygen_video_action |
> | jvagent/sentdm_broadcast | jvagent/sentdm_broadcast_action |
> | jvagent/interact_router (already canonical) | jvagent/interact_router |
> | jvagent/web_search/serper | jvagent/serper_web_search |
> | jvagent/web_search/brave | jvagent/brave_web_search |
> | jvagent/web_search/serpapi | jvagent/serpapi_web_search |
> | jvagent/vectorstore/typesense | jvagent/typesense_vectorstore |
> | jvagent/stt_action/deepgram | jvagent/deepgram_stt |
> | jvagent/tts_action/elevenlabs | jvagent/elevenlabs_tts |
> | jvagent/model/language/anthropic | jvagent/anthropic_lm |
> | jvagent/model/language/ollama | jvagent/ollama_lm |

---

## 1. By category

### 1.1 Language Model providers

| Action | Class | Base | Path |
|---|---|---|---|
| jvagent/model/language/anthropic | `AnthropicLanguageModelAction` | `LanguageModelAction` | `model/language/anthropic/` |
| jvagent/model/language/openai | `OpenAILanguageModelAction` | `LanguageModelAction` | `model/language/openai/` |
| jvagent/model/language/openrouter | `OpenRouterLanguageModelAction` | `LanguageModelAction` | `model/language/openrouter/` |
| jvagent/model/language/ollama | `OllamaLanguageModelAction` | `LanguageModelAction` | `model/language/ollama/` |
| jvagent/model/embedding | embedding actions | `BaseModelAction` | `model/embedding/` |

Bases:
- `BaseModelAction` — `model/base.py:26`
- `LanguageModelAction` — `model/language/base.py:24` (includes retry config: `max_retries`, `retry_backoff_multiplier`, `retry_on_status_codes`). See [`../docs/language-models.md`](../../docs/language-models.md).

### 1.2 Interaction routing / pipeline

| Action | Class | Base | Weight | Purpose |
|---|---|---|---:|---|
| jvagent/orchestrator | `OrchestratorInteractAction` | `InteractAction` | -200 | Single orchestrator (Orchestrator pattern). One `execute()` per turn: deterministic continuation check, then a bounded think-act-observe loop over a unified tool surface (routing = tool selection). ADR-0012/0013/0014/0015/0016. See [`../docs/ORCHESTRATOR.md`](../../docs/ORCHESTRATOR.md) |
| jvagent/interact_router | `InteractRouter` | `InteractAction` | -200 | Intent classifier → routes to InteractActions (Rails pattern; alternative to the Orchestrator) |
| jvagent/converse | `ConverseInteractAction` | `InteractAction` | (late) | Smalltalk fallback |
| jvagent/intro | `IntroInteractAction` | `InteractAction` | early | Initial greeting / first-interaction flow |
| jvagent/interview | `InterviewInteractAction` | `InteractAction` | mid | **Legacy** — structured Q&A state machine (Rails). Prefer `jvagent/interview_action` + skills-v2 |
| jvagent/interview_action | `InterviewAction` | `Action` | — | Interview tool bundle (`interview__*` tools). Skills clone `example/example_interview/` + `contract.yaml`; activate via `requires-actions` |
| jvagent/handoff | `HandoffInteractAction` | `InteractAction` | mid | Transfer to human (provides contact details) |
| jvagent/retrieval | `RetrievalInteractAction` | `InteractAction` | mid | Base retrieval orchestrator |

Bases: `InteractAction` — `interact/base.py:32`. See `.planning/architecture.md` §3 for traversal order.

### 1.3 Memory / knowledge

| Action | Class | Base | Purpose |
|---|---|---|---|
| jvagent/long_memory | `UserLongMemoryInteractAction` | `InteractAction` | Long-term per-user memory via PageIndex |
| jvagent/long_memory_retrieval | `UserLongMemoryRetrievalInteractAction` | `InteractAction` | Vectorless RAG (LLM tree search over PageIndex) |
| jvagent/long_memory_store | `UserLongMemoryStoreInteractAction` | `InteractAction` | Persist facts/prefs |
| jvagent/pageindex | `PageIndexAction` | `Action` | Document indexing + FTS + semantic search |
| jvagent/web_search_retrieval | `WebSearchRetrievalInteractAction` | `InteractAction` | Web search → context builder |

### 1.4 Messaging / broadcast

| Action | Class | Base | Purpose |
|---|---|---|---|
| jvagent/email_action | `EmailAction` | `Action` | Gmail / Outlook (OAuth) / SendGrid in/out |
| jvagent/facebook_action | `FacebookAction` | `Action` | Facebook Pages + Messenger Graph API |
| jvagent/whatsapp | `WhatsAppAction` | `Action` | WhatsApp Business API + webhooks |
| jvagent/sentdm_broadcast | `SentDMBroadcastAction` | `Action` | Sent.dm email-campaign broadcasts |
| jvagent/postiz_action | `PostizAction` | `Action` | Social media scheduling (X, LinkedIn, IG…) |

### 1.5 Productivity (Google Workspace)

Parent: `jvagent/google` (`GoogleAction`). Sub-actions under `google/`:

| Sub-action | Class | Purpose |
|---|---|---|
| google_gmail_action | `GoogleGmailAction` | Gmail send/search |
| google_calendar_action | `GoogleCalendarAction` | Calendar events |
| google_drive_action | `GoogleDriveAction` | Drive list/upload/share |
| google_sheets_action | `GoogleSheetsAction` | Sheets read/update/create |
| google_docs_action | `GoogleDocsAction` | Docs create/read/append |

### 1.6 Productivity (Microsoft 365)

Parent: `jvagent/microsoft` (`MicrosoftAction`). Sub-actions under `microsoft/`:

| Sub-action | Class | Purpose |
|---|---|---|
| microsoft_outlook_mail_action | `MicrosoftOutlookMailAction` | Outlook mail send/search via Graph API |
| microsoft_outlook_calendar_action | `MicrosoftOutlookCalendarAction` | Outlook Calendar via Graph API |
| microsoft_onedrive_action | `MicrosoftOneDriveAction` | OneDrive list/upload/share |
| microsoft_excel_action | `MicrosoftExcelAction` | Excel read/update/create |

### 1.7 Web search

Base: `BaseWebSearchAction` — `web_search/base.py`. Plus an InteractAction wrapper.

| Action | Class | Purpose |
|---|---|---|
| jvagent/web_search/serper | `SerperWebSearchAction` | Serper API |
| jvagent/web_search/brave | `BraveSearchAction` | Brave Search API |
| jvagent/web_search/serpapi | `SerpAPISearchAction` | SerpAPI (multi-engine) |
| jvagent/web_search_retrieval | `WebSearchRetrievalInteractAction` | Wraps search → context block |
| jvagent/web_fetch | `WebFetchAction` | SSRF-guarded page fetch → markdown (tool `web_fetch__fetch`); lets the Orchestrator read full pages after a search surfaces URLs |

### 1.8 Task automation

| Action | Class | Base | Purpose |
|---|---|---|---|
| jvagent/task_creation_interact_action | `TaskCreationInteractAction` | `InteractAction` | Convert model output → structured task plan |
| jvagent/task_trigger_interact_action | `TaskTriggerInteractAction` | `InteractAction` | Fire external tasks / webhooks |
| jvagent/task_dispatcher | `TaskDispatcher` | `Action` | Dispatch tasks to external systems |

See [`../docs/task-tracking.md`](../../docs/task-tracking.md).

### 1.9 Presentation / output

| Action | Class | Base | Purpose |
|---|---|---|---|
| jvagent/reply | `ReplyAction` | `Action` | Orchestrator-native egress (ADR-0014). Tools `reply` (slim publish), `respond` (identity-voiced single model call), `publish`. Identity comes from the Agent (`alias` + `role`) |
| jvagent/persona | `PersonaAction` | `Action` | Apply agent personality; aggregate capabilities; respond() entry-point. Legacy/Rails responder (Orchestrator uses `jvagent/reply`) |
| jvagent/vision | `VisionAction` | `Action` | Multimodal image interpretation with its **own** model (`model_action_type`/`model`); produces a text description and exposes an `interpret_images` tool. Gated by `vision: true`. With upload ingestion on (default), the Orchestrator calls it **per image** and consolidates the description into that image's `source="upload"` artifact (one artifact = file + interpretation, queryable via `list_artifacts`/`get_artifact` — no re-upload for follow-ups); a standalone pre-loop reflex storing a separate `source="vision"` artifact is the fallback when `ingest_uploads` is off. Suppress per-turn with `visitor.data["image_interpretation"] = False`. ADR-0021 (S2/S4) |
| jvagent/avatar_action | `AvatarAction` | `Action` | Store/retrieve base64 profile images |
| jvagent/video_generation | `HeygenVideoAction` | `Action` | Heygen AI video |

### 1.10 System / infrastructure

| Action | Class | Base | Purpose |
|---|---|---|---|
| jvagent/access_control | `AccessControlAction` | `Action` | Multi-channel RBAC (per-user, per-action) |
| jvagent/mcp | `MCPAction` | `Action` | Model Context Protocol server integration + sandbox. `get_tools()` surfaces each server's tools as `mcp_<server>__<tool>` with per-user dispatch; the Orchestrator consumes them via its `tool_servers` config |
| jvagent/agent_utils | `AgentUtils` | `Action` | Agent utilities (schema/metadata/discovery) |
| jvagent/file_interface | `FileInterfaceAction` | `Action` | Per-user sandboxed file I/O as first-class tools (`file_interface__read_file`/`write_file`/`list_directory`/…), same `<agent>/<user>` slice as the MCP filesystem + code_execution (ADR-0017) |
| jvagent/code_execution | `CodeExecutionAction` | `Action` | Multitenant sandboxed `bash` (`code_execution__bash`); the substrate Claude-spec skills run their bundled scripts in. cwd = caller's own per-user slice; pluggable executor (subprocess default). **Off by default** (ADR-0017) |
| jvagent/skill_hub | `SkillHubAction` | `Action` | Search / install / remove skill bundles from the skills.sh ecosystem (`skill_hub__search_registry`/`install_skill`/`list_installed`/`remove_skill`) |

### 1.11 Speech (STT / TTS)

| Action | Base | Path |
|---|---|---|
| Deepgram (and others) | `BaseSTTAction` | `stt_action/` |
| ElevenLabs (and others) | `BaseTTSAction` | `tts_action/` |

### 1.12 Vector stores

| Backend | Class | Base | Path |
|---|---|---|---|
| Typesense | `TypesenseVectorStore` | `VectorStore` | `vectorstore/typesense/` |

---

## 2. Infrastructure modules (not user-installable actions)

These ship inside `jvagent/action/` but are not pluggable on their own — they support the interact subsystem.

| Path | Purpose |
|---|---|
| `interact/` | `InteractWalker`, `InteractAction` base, endpoint, walker bootstrap |
| `response/` | `ResponseBus`, channel adapters, channel filters |
| `loader/` | Action loader, registry, plugin discovery |
| `utils/` | Shared utilities (webhook auth, system user mgmt) |
| `orchestrator/` | Orchestrator + supporting modules: `continuation.py` (active-flow resume), `tools.py` / `core_tools.py` (tool surface), `catalog.py` (find_tool/load_tool + lean surfacing, ADR-0018), `skills.py` (skill discovery — JV + Claude specs, ADR-0017), `prompts.py` |

---

## 3. Quick stats

| Metric | Value |
|---|---|
| Top-level action directories | 43 |
| Action packages with `info.yaml` | 30 |
| Main Action implementations (.py with class) | 23 |
| Total `@endpoint`-decorated routes across the library | ~183 |
| LanguageModelAction providers | 4 |
| Web search providers | 3 |

### Contract compliance status

The "4-file" pattern (`__init__.py`, `{name}.py`, `endpoints.py`, `info.yaml`)
in [`action-authoring.md`](action-authoring.md) §2 is **aspirational** —
many packages legitimately ship without an `endpoints.py` because they have
no HTTP surface (converse, intro, long_memory, retrieval, router,
task_creation_interact_action, task_trigger_interact_action,
handoff_interact_action, interview, web_search_retrieval, mcp,
vectorstore/typesense, web_search/*, stt_action/deepgram,
tts_action/elevenlabs, video_generation, pageindex sub-actions). For
packages that DO have an `endpoints.py`, registration is via one of two
paths:

1. `from . import endpoints` in `__init__.py` (standard).
2. Lazy import from `core/embed_endpoints.py` (`jvagent.action.google`)
   or from the package's main implementation file
   (`pageindex_google_drive_sync_action`).

Both paths are currently functional. AUDIT-actions XC-6 verified.

---

## 4. Cross-action dependencies (notable)

| Action | Depends on |
|---|---|
| `OrchestratorInteractAction` | `ReplyAction` (egress), a `LanguageModelAction` (heavy + optional light gear), all enabled actions' `get_tools()`, `MCPAction` (via `tool_servers`), `CodeExecutionAction` (stages/runs Claude skills), skills (JV + Claude specs) |
| `ReplyAction` | the Agent's identity (`alias` + `role`), a `LanguageModelAction` (voicing) |
| `WebFetchAction` | none (httpx + bs4 + markdownify; SSRF guard) |
| `HandoffInteractAction` | `PersonaAction` (polish), `WhatsAppAction` (contact routing) |
| `TaskCreationInteractAction` | `WhatsAppAction` (context), `PersonaAction` (formatting) |
| `UserLongMemoryRetrievalInteractAction` | `PageIndexAction` |
| `UserLongMemoryStoreInteractAction` | `PageIndexAction` |
| Any channel adapter | `ResponseBus` (per-agent, via `Agent.get_response_bus()`) |

Declared in `info.yaml` under `dependencies.actions`.

---

## 5. Where to look when adding to this catalog

When you ship a new action, update:

1. The relevant subsection in §1.
2. Stats in §3 if a category jumps.
3. Dependencies in §4 if you depend on or are depended on.
4. The "Reference walkthroughs" table in [`action-authoring.md`](action-authoring.md) if your action is a good template.

Last-resort discovery (if this doc is stale): `ls jvagent/action/*/info.yaml` and read each one.
