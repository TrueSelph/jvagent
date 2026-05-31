# Configuration Keys

> Master index of all environment variables, `app.yaml` keys, and `agent.yaml` knobs. Cross-link: existing user-facing [`docs/configuration.md`](../../docs/configuration.md), [`docs/environment-keys-reference.md`](../../docs/environment-keys-reference.md), [`docs/integrations-environment.md`](../../docs/integrations-environment.md). This doc is the agent-targeted index — the user-facing references stay canonical for prose; here we surface the "what's the precedence + what affects what" answer.

---

## 1. Precedence (highest first)

1. **CLI flag** (`--update`, `--source`, `--merge`, `--debug`, `--serverless`, `--purge`)
2. **Environment variable** (resolved via `jvspatial.env.env`)
3. **`app.yaml`** at the app root
4. **`agent.yaml`** under `agents/{namespace}/{agent_name}/`
5. **Action `attribute(default=...)`** in Python

Code: [`jvagent/core/config.py:59-150`](../../jvagent/core/config.py) — `ConfigKey` / `ConfigSchema`. Env placeholders in YAML are expanded by [`jvagent/core/env_resolver.py`](../../jvagent/core/env_resolver.py).

---

## 2. Core environment variables

| Var | Default | Effect |
|---|---|---|
| `JVAGENT_ADMIN_PASSWORD` | (required for fresh installs) | Initial admin user password |
| `JVAGENT_BASE_PATH` | `.` | Base path for action package resolution ([`action/base.py:900`](../../jvagent/action/base.py)) |
| `JVAGENT_MAX_INTERACTIONS_PRUNED_PER_CALL` | `100` | Cap on per-call pruning ([`memory/conversation.py:320`](../../jvagent/memory/conversation.py)) |
| `JVAGENT_DISABLE_RUNTIME_PIP_INSTALL` | unset / `false` | If `true`, do not pip-install action dependencies at load time |
| `JVAGENT_ENVIRONMENT` | — | informational (`development` / `staging` / `production`) |
| `SERVERLESS_MODE` | unset | Set to `true` by `--serverless` ([`cli/main.py:144`](../../jvagent/cli/main.py)) |
| `AWS_LAMBDA_FUNCTION_NAME` | unset | Set by `--serverless` to a placeholder |

Full list, including per-integration: [`docs/environment-keys-reference.md`](../../docs/environment-keys-reference.md).

---

## 3. jvspatial environment variables (inherited)

| Var | Default | Effect |
|---|---|---|
| `JVSPATIAL_DB_TYPE` | `json` | `json` / `sqlite` / `mongodb` / `dynamodb` |
| `JVSPATIAL_JSONDB_PATH` | `./jvdb/dev` | JSON backend root |
| `JVSPATIAL_MONGODB_URI` | — | required if `DB_TYPE=mongodb` |
| `JVSPATIAL_MONGODB_DB_NAME` | — | required if `DB_TYPE=mongodb` |
| `JVSPATIAL_CACHE_BACKEND` | `memory` | `memory` / `redis` / `layered` |
| `JVSPATIAL_CACHE_SIZE` | `1000` | max cached entities (`0` disables) |
| `JVSPATIAL_FILE_STORAGE_PROVIDER` | `local` | `local` / `s3` |
| `JVSPATIAL_S3_BUCKET_NAME` | — | required for S3 |
| `JVSPATIAL_S3_REGION` | `us-east-1` | |
| `JVSPATIAL_S3_ACCESS_KEY` / `JVSPATIAL_S3_SECRET_KEY` | — | required for S3 unless IAM role |
| `JVSPATIAL_S3_ENDPOINT_URL` | — | for S3-compatible providers (MinIO, etc.) |
| `JVSPATIAL_LOG_LEVEL` | `INFO` | root log level |
| `JVSPATIAL_JWT_SECRET_KEY` | — | **required**; change from default in prod |
| `JVSPATIAL_ENVIRONMENT` | — | `development` / `production` (gates `--purge`) |

Full list: jvspatial's own docs + [`docs/environment-keys-reference.md`](../../docs/environment-keys-reference.md).

---

## 4. `app.yaml` keys

Top-level structure:

```yaml
app:                         # REQUIRED
  app_id: my_app             # stable identifier
  name: "My App"
  version: "0.1.0"
  description: "..."
  timezone: America/New_York # optional IANA TZ; affects App.now()

file_storage:                # optional
  provider: local            # or s3
  root_dir: ./.files         # local only
  enabled: true

logging:                     # optional
  enabled: true
  retention_days: 60

database:                    # optional — overrides JVSPATIAL_* env if set
  type: json                 # json | sqlite | mongodb | dynamodb
  json:
    path: ./jvdb
  mongodb:
    uri: ${MONGO_URI}
    db_name: my_app

server:                      # optional
  host: 127.0.0.1
  port: 8000
  cors:
    enabled: true
    allowed_origins: ["*"]
  auth:
    enabled: true
```

Detail: [`docs/configuration.md`](../../docs/configuration.md). Validator: [`jvagent/core/app_yaml_validator.py`](../../jvagent/core/app_yaml_validator.py).

---

## 5. `agent.yaml` keys

Top-level structure:

```yaml
agent:
  namespace: my_org
  name: support_bot
  alias: "Support Bot"                 # display name (identity, ADR-0014)
  role: "a friendly support assistant" # agent's role/purpose (identity, ADR-0014)
  description: "..."
  enabled: true
  interaction_limit: 100              # rolling-window cap (0 = disabled)
  max_statement_length: 2000          # truncation cap for history

actions:                               # ordered list
  - action: jvagent/persona            # namespace/action_name
    context:
      # override any `attribute(...)` on PersonaAction
      system_prompt: "You are a support agent."
      max_iterations: 25
  - action: jvagent/orchestrator
    context:
      model: gpt-4o-mini
      activation_budget: 16
      lock_active_flow: true
  - action: jvagent/model/language/anthropic
    context:
      api_key: ${ANTHROPIC_API_KEY}
      default_model: claude-sonnet-4-20250514
      max_retries: 4
```

Validator: [`jvagent/core/agent_yaml_validator.py`](../../jvagent/core/agent_yaml_validator.py).

### Universal `context:` keys (apply to any action)

| Key | Default | Effect |
|---|---|---|
| `enabled` | `true` | Set `false` to register but disable |
| `weight` (InteractAction only) | per-class default | Top-tier execution order |
| `run_in_background` (InteractAction) | `false` | Defer to post-response |
| `always_execute` (InteractAction) | `false` | Bypass routing exclusion |
| `description` | per-class | Overrides default description |

### LanguageModelAction retry keys

| Key | Default | Effect |
|---|---|---|
| `max_retries` | provider default | Total retry count for transient failures |
| `retry_initial_delay` | provider default | First retry wait (seconds) |
| `retry_max_delay` | provider default | Cap on retry wait |
| `retry_backoff_multiplier` | provider default | Exponential backoff base |
| `retry_jitter` | provider default | Randomization window |
| `retry_on_status_codes` | provider default | HTTP codes that count as transient |

Detail: [`docs/language-models.md`](../../docs/language-models.md).

---

## 6. OrchestratorInteractAction config (most-tuned action)

See [`docs/ORCHESTRATOR.md`](../../docs/ORCHESTRATOR.md) for the full pattern. Highlights:

| Key | Default | Effect |
|---|---|---|
| `model` | `gpt-4o-mini` | main/heavy orchestrator model (the reasoning tier when gearing is on) |
| `model_action_type` | `OpenAILanguageModelAction` | LM action binding for `model` |
| `activation_budget` | 24 | max think-act-observe iterations per turn |
| `history_limit` | 4 | prior turns fed into the loop prompt (working context). The rolling memory window is the agent-level `interaction_limit` |
| `lock_active_flow` | `true` | deterministic turn-lock to an active flow's IA; `false` = model-mediated continuation (ADR-0013) |
| `clarify_text` | (fallback prompt) | reply when a turn ends with nothing emitted |
| `skills_source` | `both` | skill discovery source (both specs): `app` (adjacent `skills/`), `library` (`jvagent/skills`), or `both`. Aliases: `local`→`app`, `builtin`→`library`; `registry` retired→`library` |
| `skills` | `-all` | which skills to load: `-all`, or a finite list of names/fnmatch patterns (e.g. `[research, web_lookup]`) |
| `denied_skills` | `[]` | skill names/patterns to exclude (subtracts from `skills`) |

### Prompt surface (overridable)

Every sub-prompt is a config key defaulting to the matching constant in
[`prompts.py`](../../jvagent/action/orchestrator/prompts.py); override any
from `agent.yaml`. These are `str.format` templates — preserve the placeholders
listed and double any literal `{`/`}` (write `{{` / `}}`). A malformed override
(unknown placeholder, unbalanced brace) falls back to the built-in for that
piece and logs a warning, so a bad string never breaks a turn.

| Key | Placeholders | Effect |
|---|---|---|
| `system_prompt` | `{identity_section}` `{tools_section}` `{skills_section}` | the main system-prompt body |
| `system_prompt_extra` | — | extra text appended after the base body (safe additive; no placeholders needed) |
| `user_prompt` | `{history_section}` `{utterance}` `{observations_section}` | the per-tick user prompt |
| `tool_use_policy_prompt` | — | appended when `block_raw_tool_invocation` is on |
| `flow_in_progress_prompt` | `{flow_note}` | appended while a flow is active |
| `length_limit_prompt` | `{max_chars}` | appended when `max_statement_length` is set |
| `finalize_prompt` | — | appended on the partial-compose finalize tick |
| `no_skills_text` | — | shown in the AVAILABLE SKILLS slot when none load |

(The agent's identity comes from the Agent's `alias` + `role` (ADR-0014), not
from these keys.)

### Model gearing (ADR-0016)

Set `light_model` to engage gearing; empty = single-model (current `model*` used everywhere).

| Key | Default | Effect |
|---|---|---|
| `light_model` | `""` | light/completion model id; empty disables gearing. Set with no main `model` → the light model becomes the **sole** model (fallback) |
| `light_model_action_type` | `""` | LM action for the light model; empty = heavy `model_action_type` |
| `light_model_temperature` | `0.2` | light gear temperature |
| `light_model_max_tokens` | `1024` | light gear max tokens |
| `escalate_after_tool_calls` | `2` | switch to heavy after this many substantive tool calls (egress/meta excluded) |
| `escalate_on_skill` | `true` | activating a skill escalates to heavy immediately |

The heavy gear uses the existing `model*` + `reasoning_*`; reasoning applies only on heavy. Escalation is sticky; the partial-compose finalize runs light.

### Reasoning, stream, budgets, tooling (ADR-0015)

Only bites with a reasoning-capable model; the `gpt-4o-mini` default ignores reasoning keys.

| Key | Default | Effect |
|---|---|---|
| `reasoning_enabled` | `None` | tri-state: `None` defers to the model action; `true`/`false` force on/off for the loop call |
| `reasoning_effort` | `None` | `low` \| `medium` \| `high` reasoning hint |
| `reasoning_budget_tokens` | `0` | explicit thinking-token budget (e.g. Anthropic); `0` maps from effort |
| `reasoning_extra` | `None` | provider-specific reasoning params, passed verbatim |
| `stream_internal_progress` | `false` | emit each loop tick as a transient `thought` bubble (needs a live bus). Fires only on **heavy**-gear ticks |
| `stream_reasoning_trace` | `false` | surface the model's thinking trace (`result.thinking_content`) as a thought. **Heavy**-gear only (the light model has no trace) |
| `max_duration_seconds` | `0` | wall-clock cap on the whole turn (alongside `activation_budget`); `0` disables |
| `max_statement_length` | `None` | soft reply-length cap (chars), injected as a prompt instruction |
| `tool_tier` | `standard` | core-tool tier: `minimal` \| `standard` \| `full` |
| `tool_call_timeout` | `0` | per-tool-call timeout (s); `0` disables |
| `block_raw_tool_invocation` | `false` | only surfaced (visible) tools are callable; hidden ones need `find_tool`/a skill |
| `lean_tool_threshold` | `15` | lean tool surfacing (ADR-0018): when the count of hideable capability tools (action + MCP) exceeds this, the long tail is kept off the prompt and reached via `find_tool`. `0` disables (always list every tool). Egress/meta/core/active-flow tools are always visible |
| `lean_presurface_k` | `6` | in lean mode, how many capability tools to pre-surface each turn by relevance to the user's message (token overlap, no model call), so common single-intent turns need no `find_tool` round-trip. **`0` = essentials-only** (see recipe below) |

**Lean tool surfacing — recipes.** Capability tools = action `get_tools()` tools
+ MCP tools. The hideable long tail is what's gated; egress (`reply`/`respond`),
the meta-tools (`find_tool`/`load_tool`/`find_skill`/`use_skill`), core tools, and
an active-flow tool are **always** visible, and **skills stay fully listed**
regardless (few in number; the "prefer a whole SOP" signal). Skills are not
gated by these knobs.

| Goal | Config |
|------|--------|
| **Default (recommended)** — small agents list everything; large agents auto-slim and pre-surface the relevant few | `lean_tool_threshold: 15`, `lean_presurface_k: 6` (defaults) |
| **Always list every tool** (back-compat / tiny surfaces) | `lean_tool_threshold: 0` |
| **Essentials-only on large surfaces** — show just egress/meta/core + skills; the model reaches every capability via `find_tool`. Smaller prompt, but costs a discovery round-trip on most turns and leans harder on weaker models — best for very large surfaces or strong models where prompt cost dominates | `lean_presurface_k: 0` (optionally `lean_tool_threshold: 1` to force it on for any surface) |
| **More/less aggressive pre-surface** | raise/lower `lean_presurface_k` (e.g. `10` surfaces more, fewer discovery ticks, bigger prompt) |
| `enable_transient_ack` | `false` | emit transient ack line(s) if the turn is slow. Armed only once the turn is **complex** — a skill is active, or it has made ≥ `escalate_after_tool_calls` substantive tool calls — so simple/reply-only turns never surface it |
| `first_emit_timeout_ms` | `1200` | delay before the first transient ack fires (from when the turn arms) |
| `ack_interval_ms` | `12000` | delay between subsequent acks |
| `ack_statements` | `["One moment…", "Still working on it…"]` | ordered ack bodies emitted while a slow turn runs |
| `tool_servers` | `-all` | MCP gateways to pull tools from: `-all` for every enabled `jvagent/mcp` action, or a list of action names. Tools surface as `mcp_<server>__<tool>` |
| `max_concurrent_tools` | `0` | reserved for future parallel tool batches; loop executes one tool per tick today |

### `jvagent/reply` (ReplyAction — Orchestrator egress, ADR-0014)

Voices the agent's output; identity comes from the Agent (`alias` + `role`), not from this action.

| Key | Default | Effect |
|---|---|---|
| `model` | `gpt-4o-mini` | model used to voice `respond` (identity-voiced single call) |
| `model_action_type` | `OpenAILanguageModelAction` | LM action binding |
| `model_temperature` | `0.4` | voicing temperature |

### `jvagent/web_fetch` (WebFetchAction)

SSRF-guarded page fetch → markdown; surfaces the `web_fetch__fetch` tool.

| Key | Default | Effect |
|---|---|---|
| `max_chars` | `8000` | truncation cap on extracted page text |
| `allow_private_hosts` | `false` | when `false`, loopback/private/link-local hosts are refused (validated before connect and on every redirect hop) |
| `max_redirects` | `5` | redirect hops before the fetch aborts |

### `jvagent/mcp` (MCPAction — tool servers)

Surfaces MCP server tools as `mcp_<server>__<tool>`; consumed by the Orchestrator via its `tool_servers` key (above).

| Key | Default | Effect |
|---|---|---|
| `sandbox_mode` | `false` | run servers in a sandbox |
| `sandbox_user_scoped` | `false` | scope the sandbox per agent + per user |
| `servers` | `[]` | list of server defs: `name`, `enabled`, `transport` (e.g. `stdio`), `command`, `args`, `mcp_connect_timeout`, `mcp_call_timeout`, `tools` (`-all` or list), `denied_tools` |

### `jvagent/code_execution` (CodeExecutionAction — Claude-skill substrate, ADR-0017)

The multitenant sandbox `spec: claude` skills run their bundled scripts in. Surfaces `code_execution__bash`, whose cwd is the caller's own `<agent>/<user>` slice. **Off by default.** The subprocess executor is a pragmatic default, **not a hard jail** — supply an isolating backend for untrusted skills. Requires local file storage in this version.

| Key | Default | Effect |
|---|---|---|
| `enabled` | `false` | master switch; no `bash` tool surfaced when off |
| `timeout` | `60` | wall-clock seconds per command |
| `memory_mb` | `2048` | `RLIMIT_AS` cap (MB); `0` = no limit (some toolchains, e.g. xelatex, mmap large) |
| `cpu_seconds` | `30` | `RLIMIT_CPU` cap |
| `max_procs` | `0` | `RLIMIT_NPROC`; **per-UID**, off by default (a small cap breaks on busy shared-UID servers) |
| `max_file_mb` | `256` | max single file size written (`RLIMIT_FSIZE`) |
| `max_output_bytes` | `64000` | cap on captured stdout+stderr |
| `network` | `false` | advisory; only an isolating executor backend actually enforces it |
| `sandbox_root` | `""` | override the filesystem root (else env/jvspatial default) |

### `jvagent/file_interface` (FileInterfaceAction) & `jvagent/skill_hub` (SkillHubAction)

`file_interface` exposes per-user sandboxed file-I/O tools (`file_interface__read_file`/`write_file`/`list_directory`/…) on the same per-user slice as `code_execution` and the filesystem MCP; `skill_hub` exposes skill-registry management tools (`skill_hub__search_registry`/`install_skill`/`list_installed`/`remove_skill`). Both just need `enabled: true`; no further config.

---

## 7. Update modes

| Mode | Source | Effect |
|---|---|---|
| `run` | default `App.update_mode` | Skip YAML re-sync on start |
| `merge` | `--update --merge` or `--update` (default) | Non-destructive merge from YAML |
| `source` | `--update --source` | Destructive; YAML wins |

After a successful bootstrap, `App.update_mode` is reset to `run`. Source: [`adr/0005-app-yaml-agent-yaml-split.md`](../adr/0005-app-yaml-agent-yaml-split.md).

---

## 8. Where the validator screams

- Unknown `app.yaml` key → `app_yaml_validator.py` raises with the offending key.
- Unknown `agent.yaml` action key → `agent_yaml_validator.py` raises.
- Unknown `context:` key on an action → Pydantic on `attribute(...)` fields rejects (uses `extra="forbid"` per Action subclass policy).
- Env placeholder `${ENV_VAR}` for a missing var → expanded to empty string (NOT an error); confirm with `jvagent /path validate`.

Run validation explicitly:

```bash
jvagent /path/to/app validate
```

Exit code 0 = clean. Non-zero = drift.

---

## 9. Webhook authentication

Channel-adapter and PageIndex webhooks use ``webhook_auth="api_key"``.
The API key can travel in **either** the query string (``?api_key=…``)
or an HTTP header (``X-API-Key`` by default; configurable per
deployment via ``auth.api_key_header`` in `app.yaml` or
``JVAGENT_API_KEY_HEADER`` env). **Prefer the header form** —
query-string credentials leak via:

- HTTP access logs (nginx, ALB, CloudFront).
- `Referer:` headers when a webhook URL is rendered in HTML.
- Browser history / address bar.

For self-generated webhook URLs (`Action.get_webhook_url()`), include
the key only when the caller cannot set headers (e.g. a third-party
provider that only supports static URLs). Otherwise return the bare
URL and provision the key out-of-band. AUDIT-actions XC-15.

## 10. Trusting proxy headers

`extract_client_ip` (`jvagent/action/interact/rate_limiter.py:177`)
consults `X-Forwarded-For` / `X-Real-IP` / `CF-Connecting-IP` before
falling back to `request.client.host`. Behind a trusted reverse proxy
this is correct; on a direct-internet listener it lets a client spoof
their IP and side-step per-IP rate limits.

Set ``JVAGENT_TRUST_PROXY_HEADERS=false`` to ignore the proxy headers
and always use ``request.client.host``. Default is ``true`` for
backward compatibility. AUDIT-interact MED-12.

## 11. Reading list

| For depth on... | Read |
|---|---|
| Configuration mechanics | [`docs/configuration.md`](../../docs/configuration.md) |
| Every env var jvagent + jvspatial reads | [`docs/environment-keys-reference.md`](../../docs/environment-keys-reference.md) |
| Integration-specific env keys (Google, Microsoft, Anthropic, etc.) | [`docs/integrations-environment.md`](../../docs/integrations-environment.md) |
| Scaffolding new app/profile/agent | [`docs/scaffolding.md`](../../docs/scaffolding.md) |
| Security review of secrets in config | [`docs/security-review.md`](../../docs/security-review.md) |
