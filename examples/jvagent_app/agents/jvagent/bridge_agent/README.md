# Bridge Agent

Demonstrates the **Bridge + Helm** architecture (ADR-0007): a multi-helm
orchestrator (`BridgeInteractAction`) at weight `-200` that composes one
or more helms — `ReasoningHelm` by default. Capability-parity with
[`cockpit_agent`](../cockpit_agent); structural difference is the
composition.

See:

- [`.planning/BRIDGE-ROADMAP.md`](../../../../../.planning/BRIDGE-ROADMAP.md) — Bridge milestone plan.
- [`.planning/adr/0007-bridge-helm-architecture.md`](../../../../../.planning/adr/0007-bridge-helm-architecture.md) — verb set, state shape, manifest schema.
- [`.planning/PATTERNS.md`](../../../../../.planning/PATTERNS.md) — pattern catalog (Rails / Cockpit / Bridge).
- [`docs/COCKPIT.md`](../../../../../docs/COCKPIT.md) — the cockpit-style think/act/observe pattern that `ReasoningHelm` duplicates.

## Architecture

```
BridgeInteractAction (weight: -200)
  └─ ReasoningHelm.step()                       [one model call per Bridge visit]
        ├─ Phase 1 — CockpitRouter              (fast posture + skill selection)
        └─ Phase 2 — CockpitEngine (think-act-observe loop)
              ├─ Harness service tools: memory_*, response_*, task_*,
              │     conversation_*, skill_*, artifact_*, cockpit_search
              ├─ Action tools: pageindex__*, web_search__*, mcp_filesystem__*
              └─ Skill bundle tools: pageindex_search, pageindex_docs, web_search
```

`ReasoningHelm` is **duplicated** from `jvagent/action/cockpit/` under
`jvagent/action/helm/reasoning/` — zero source-level imports between the
two patterns. Per-file source attribution lives in
[`jvagent/action/helm/reasoning/DUPLICATION_NOTICE.md`](../../../../../jvagent/action/helm/reasoning/DUPLICATION_NOTICE.md).

## Verb dispatch summary

ReasoningHelm signals Bridge via the helm verb set:

| Engine result | Helm verb | Bridge behaviour |
|---|---|---|
| `tool_calls`         | `CONTINUE` | re-enqueue helm (one more walker visit) |
| `final_response`     | `YIELD`    | clear Bridge state (persona already published the answer) |
| `timeout` / `stuck` / `budget_exhausted` | `YIELD` | clear state after fallback response |
| SUPPRESS posture     | `YIELD`    | exit cleanly without publishing |

## Structure

```
bridge_agent/
├── agent.yaml    # BridgeInteractAction + ReasoningHelm + supporting actions
└── README.md     # this file
```

No `actions/` directory — `jvagent/bridge` and `jvagent/reasoning_helm`
are core packages auto-discovered from the jvagent install.

## Components (from `agent.yaml`)

| Action | Role |
|---|---|
| `jvagent/bridge` | Multi-helm orchestrator (weight: -200) |
| `jvagent/reasoning_helm` | Deliberate-class reasoning helm (default helm) |
| `jvagent/openai_lm` | GPT-4o-mini for routing, reasoning loop, persona |
| `jvagent/ollama_lm` | Kept for parity with `cockpit_agent` (not the default) |
| `jvagent/persona` | Persona renders the final response |
| `jvagent/mcp` | Filesystem MCP server |
| `jvagent/intro_interact_action` | Intro IA |
| `jvagent/handoff_interact_action` | Handoff IA |
| `jvagent/pageindex_action` | Backing APIs for pageindex skills |
| `jvagent/serper_web_search` | Web search provider |

Pre-loaded skills (mirrors `cockpit_agent`):
`pageindex_search`, `pageindex_docs`, `web_search`.

## Running

Same as `cockpit_agent`:

```bash
# From the repo root, with .env or shell env populated with:
#   OPENAI_API_KEY, OLLAMA_API_KEY (optional), SERPER_API_KEY
jvagent examples/jvagent_app
```

The agent is exposed at `POST /agents/jvagent/bridge_agent/interact`.

## Parity baseline (C-7 smoke gate)

The 6-utterance smoke harness at `tests/action/bridge/smoke_bridge.py`
runs this agent and compares results to the cockpit baseline at commit
`7d95904`:

| Utterance | dur(s) | model_calls | prompt_tok | resp_chars |
|---|---|---|---|---|
| "Hi"             | 2.93 | 2 | 2014 | 34 |
| "What is 2+2?"   | 2.79 | 2 | 4956 | 5 |
| Web search       | 5.89 | 3 | 8342 | 167 |
| Remember pref    | 9.29 | 3 | 8260 | 139 |
| Recall pref      | 8.70 | 3 | 8342 | 183 |
| "Thanks!"        | 3.55 | 2 | 2180 | 79 |
| **TOTAL**        | **33.15** | **15** | **34094** | |

Parity gate: ≤5% drift on every metric. JSON dumps archived under
`tests/action/bridge/baselines/`.
