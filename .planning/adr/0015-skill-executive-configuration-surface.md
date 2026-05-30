# ADR 0015 — SkillExecutive configuration surface (reasoning, stream, budgets, tooling, MCP)

**Status**: Accepted
**Date**: 2026-05-30
**Relation**: Extends [ADR-0012](0012-skill-executive-architecture.md) (SkillExecutive) and [ADR-0013](0013-togglable-deterministic-turn-lock.md) (turn-lock). Restores configuration knobs that did not survive the Bridge/Helm → SkillExecutive migration, and adds MCP tool-server integration via the existing `jvagent/mcp` `MCPAction`.

---

## 1. Context

The SkillExecutive (ADR-0012) shipped with a deliberately small config surface: model binding, `activation_budget`, `lock_active_flow`, `clarify_text`, and the skills selector. The retired Bridge/ReasoningHelm exposed a much richer surface (reasoning level, progress streaming, wall-clock budgets, tool tiers/timeouts, transient acks, MCP servers). Several of those are operationally important and were requested back.

Two facts shaped the design:

- The **model-action layer already supports reasoning** (`ReasoningModelConfig`; per-provider translation for OpenAI o-series, Anthropic thinking, OpenRouter). The OpenAI action honors a **per-call** `reasoning_effort`/`reasoning` over its own attribute. The executive simply wasn't threading it.
- The **response bus already supports `category="thought"`** transient messages, and `ModelActionResult` already carries `thinking_content`. Progress/reasoning streaming needs no new infrastructure.
- A fully built **`MCPAction` (`jvagent/mcp`)** already hosts MCP servers and furnishes `get_tools()` returning `mcp_<server>__<tool>` `Tool`s with per-user dispatch. Integration is *consumption*, not new plumbing.

## 2. Decision

Add the following attributes to `SkillExecutiveInteractAction`, all **off/neutral by default** (no behavior change for the `gpt-4o-mini` reference agent).

### 2.1 Reasoning passthrough

`reasoning_enabled` (tri-state), `reasoning_effort` (low/medium/high), `reasoning_budget_tokens`, `reasoning_extra`. Threaded into the loop's `query_messages` via `_reasoning_kwargs()`; the model action prefers per-call reasoning over its own attribute, so the executive profile owns its reasoning level independently of other model calls. No-op for non-reasoning models.

### 2.2 Thinking / progress stream

`stream_internal_progress` emits each loop tick as a transient `thought` bubble (`_emit_thought` → `response_bus.publish(category="thought", transient=True)`). `stream_reasoning_trace` surfaces `result.thinking_content` when the provider returns one. Both no-op without a live bus; thoughts are never persisted to the interaction response. The executive's control-JSON call stays `stream=False` (streaming it would leak control JSON to the user); the trace is read post-hoc off the result.

### 2.3 Budgets

`max_duration_seconds` — a wall-clock guard checked at the top of each loop tick, complementing the existing `activation_budget` tick cap. `max_statement_length` — a soft cap injected as a `LENGTH LIMIT` clause into the loop system prompt (no hard truncation, so replies stay coherent). The existing `history_limit` (loop working context) and agent-level `interaction_limit` (rolling memory window) are now surfaced in the example agent + scaffold profile.

### 2.4 Tooling / egress-UX

`tool_tier` (minimal/standard/full) gates the core-tool surface via `build_core_tools(action, tier)`. `tool_call_timeout` wraps each tool dispatch in `asyncio.wait_for`. `block_raw_tool_invocation` restricts dispatch to surfaced (visible) tools — hidden tools must be reached via `find_tool` or a skill. `enable_transient_ack` + `first_emit_timeout_ms` + `safety_net_ack_text` schedule a "working on it" transient over the bus if a turn is slow, cancelled when the loop completes.

### 2.5 MCP tool servers

`tool_servers` (`-all` or a list of action names) selects `MCPAction`(s); their `get_tools()` are pulled into the surface and bound via `wrap_action_tool` (AC-gated). Because the executive runs tools **directly** (not through `ToolExecutionEngine`), a new public `tool_executor.bind_dispatch_context(visitor)` binds the caller identity for the whole turn so per-user MCP sandboxing routes to the correct subprocess. `max_concurrent_tools` is reserved for bounding concurrent execution. The reference agent enables a sandboxed filesystem server (`jvagent/mcp`, stdio/npx) whose tools surface as `mcp_filesystem__<tool>`.

## 3. Consequences

- The executive profile is now the single place to tune reasoning, transparency, budgets, and tool exposure — no need to edit the underlying model action for reasoning.
- All additions are backward compatible: defaults reproduce prior behavior, and the existing test suite is unaffected.
- MCP requires the `mcp` pip extra (declared in `jvagent/action/mcp/info.yaml`) and, for the filesystem server, `npx`.
- `bind_dispatch_context` is a small public addition to the tooling layer, reusable by any caller that dispatches `Tool`s outside the engine.

## 4. Alternatives considered

- **True token-streaming of the control loop** for the reasoning trace — rejected: it would stream control JSON to the user; reading `thinking_content` post-hoc is sufficient and safe.
- **Hard truncation for `max_statement_length`** — rejected in favor of a prompt-level soft cap to avoid mid-sentence cuts; channel formatting (ADR-0014) already shapes length.
- **SE-owned MCP client** — rejected: `MCPAction` already exists and handles sandboxing, per-user routing, and tool filtering; the SE consumes it like any other action's tools.
