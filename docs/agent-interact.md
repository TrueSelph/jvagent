# AgentInteract (`AgentInteractAction`)

**AgentInteract** is the unified interact stack implemented by **`jvagent/agent_interact_action`** (`AgentInteractAction`). It replaces the older **InteractRouter + SkillInteractAction** pair with a **single** `InteractAction` on the walker path: one visit runs Phase 1 (routing) and Phase 2 (conversation or agentic skill loop).

## When to use it

- You want **one** top-level interact action that **routes** the next step (posture, intent, targets) and then either **converses** (Persona) or runs the **skill / tool loop**.
- You are building a **skills-first** agent (Markdown skills + optional tools) with optional **direct routing** to other enabled `InteractAction` classes.
- For legacy setups that already chain **`jvagent/interact_router`** and **`jvagent/skill_interact_action`**, those remain supported; new apps often standardize on AgentInteract.

## Architecture (two phases)

| Phase | Purpose |
|-------|---------|
| **1 — Route** | Fast LLM (`router_model` / `router_model_action_type`) classifies **posture** (RESPOND / SUPPRESS / DEFER), **intent**, optional **canned** transient line, and selects **routes**: skill catalog keys (`skills`) and/or **`InteractAction` class names** (`interact_actions`). |
| **2 — Execute** | **Conversational** path (Persona, when intent is conversational or no routes), or **agentic skill loop** (`model_action_type`, skills, tools). |

Implementation lives under [`jvagent/action/agent_interact/`](../jvagent/action/agent_interact/README.md):

- **`router/`** — `AgentInteractRouter`, [`router/prompts.py`](../jvagent/action/agent_interact/router/prompts.py) defaults, [`router/gating.py`](../jvagent/action/agent_interact/router/gating.py) (canned + clarification), [`router/gates.py`](../jvagent/action/agent_interact/router/gates.py) (conversational vs processing gate).
- **`skill/`** — agentic loop ([`skill/agentic_loop.py`](../jvagent/action/agent_interact/skill/agentic_loop.py)), catalog shim, hot reload, [`skill/native_tools.py`](../jvagent/action/agent_interact/skill/native_tools.py) (`converse_skill`), [`skill/converse_delivery.py`](../jvagent/action/agent_interact/skill/converse_delivery.py) (Persona conversational path shared with the gate).

## Routing model

- **Skills catalog**: JSON built from `SkillCatalog` (same selector semantics as the skill loop: `skills`, `skills_source`, `denied_skills`).
- **Interact actions catalog**: enabled `InteractAction` instances on the agent **except** the current `AgentInteractAction` (by class name). Each entry exposes `kind`, `description`, and `weight` for the router prompt.
- The model returns JSON with **`skills`** and **`interact_actions`** arrays (exact keys only). The router **merges and validates** names, then curates the walker path so downstream actions match enabled interact actions.

Posture **SUPPRESS** / **DEFER** clears the walk path as before; **RESPOND** proceeds to Phase 2.

## Canned responses (`canned_response`)

When enabled (`enable_canned_response`), the router may emit a **short transient** line before the full reply. These are **lead-ins only**: hesitation or a tiny stall in the **user’s language**, not a standalone answer.

They **must not** be conclusive: no explanations, advice, refusals with workarounds (“… but you can …”), policy statements, or anything that could read as a finished message. Full rules and examples are in **`routing_canned_instructions_template`** (default in [`router/prompts.py`](../jvagent/action/agent_interact/router/prompts.py)); override via `routing_canned_instructions_template` on the action if needed.

Persona continues the same turn without treating a proper lead-in as a complete reply; see the canned lead-in section in the persona prompt bundle if you fork templates.

## Configuring prompts

Defaults live in [`router/prompts.py`](../jvagent/action/agent_interact/router/prompts.py). Override through **`AgentInteractAction`** attributes (same pattern as other actions—defaults point at module constants):

| Attribute | Role |
|-----------|------|
| `routing_system_prompt` | Router LLM system message |
| `routing_user_prompt_template` | User message; placeholders include `utterance`, `skills_json`, `interact_actions_json`, history sections, `optional_instructions`, `canned_field`, etc. |
| `routing_prior_fragments_section` | DEFER fragment block template |
| `routing_canned_instructions_template` | Extra routing rules for canned text (rule 6 in the default template) |
| `routing_clarification_user_prompt_template` | Primary clarification generation (low confidence) |
| `routing_clarification_paraphrase_prompt_template` | Paraphrase fallback strings |
| `routing_clarification_fallback_messages` | Rotating templates before paraphrase |

Custom **`routing_user_prompt_template`** must include every placeholder the default uses (especially **`{interact_actions_json}`**), or `.format` will fail at runtime.

## Example agent

The **`examples/jvagent_app/agents/jvagent/unified_agent/`** agent declares `jvagent/agent_interact_action` with router and skill-loop settings; see its [`agent.yaml`](../examples/jvagent_app/agents/jvagent/unified_agent/agent.yaml) and [README](../examples/jvagent_app/agents/jvagent/unified_agent/README.md).

## Related documentation

- [InteractAction API](../jvagent/action/interact/README.md) — walker, `respond`, directives
- [Legacy InteractRouter](../jvagent/action/router/README.md) — anchor-based routing (alternative composition)
- [Skill bundles](../jvagent/skills/README.md) — SKILL.md structure and tools
- [Persona](../jvagent/action/persona/README.md) — conversational delivery and `respond_slim`

## Dependencies

`jvagent/agent_interact_action` declares **`jvagent/persona`** in [`info.yaml`](../jvagent/action/agent_interact/info.yaml); the skill loop uses the platform skill stack and your declared LM actions.
