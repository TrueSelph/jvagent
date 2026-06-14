# jvagent

[![GitHub release (latest by date)](https://img.shields.io/github/v/release/TrueSelph/jvagent)](https://github.com/TrueSelph/jvagent/releases)
[![GitHub Workflow Status](https://img.shields.io/github/actions/workflow/status/TrueSelph/jvagent/test-jvagent.yaml)](https://github.com/TrueSelph/jvagent/actions)
[![PyPI version](https://img.shields.io/pypi/v/jvagent)](https://pypi.org/project/jvagent/)
[![GitHub issues](https://img.shields.io/github/issues/TrueSelph/jvagent)](https://github.com/TrueSelph/jvagent/issues)
[![GitHub pull requests](https://img.shields.io/github/issues-pr/TrueSelph/jvagent)](https://github.com/TrueSelph/jvagent/pulls)
[![GitHub](https://img.shields.io/github/license/TrueSelph/jvagent)](https://github.com/TrueSelph/jvagent/blob/main/LICENSE)

**A modular AI-agent harness for your application.** Declare **multi-tenant, multi-agent, Claude-style agents** in YAML and serve them over HTTP — with persistent per-user memory, a load-on-demand plugin system, drop-in [Anthropic Agent Skills](https://www.anthropic.com/news/skills), built-in messaging-channel delivery, and a single-loop **Orchestrator** that turns every incoming message into tool calls. No hand-written state machine, no glue code to ship an agent.

> The model is the pilot. Tools are the controls. Skills are the flight plan. jvagent is the airframe — the production harness that holds it together.

## Table of Contents

- [Overview](#overview)
- [Why jvagent](#why-jvagent)
- [How jvagent compares](#how-jvagent-compares)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Core Concepts](#core-concepts)
- [Configuration](#configuration)
- [Documentation](#documentation)
- [Authors & maintainers](#authors--maintainers)
- [Contributors](#contributors)
- [Contributing](#contributing)
- [License](#license)

## Overview

jvagent is built on [jvspatial](https://github.com/TrueSelph/jvspatial)'s object-spatial graph framework. Everything an agent knows and does is a **Node** or **Edge**: the agent, its actions, and its per-user memory all live in one graph that persists across turns and processes.

An **app** declares one or more **agents** in `app.yaml` / `agent.yaml`. Each agent owns a graph of **actions** (namespaced plugins, loaded on demand) plus a per-user memory subgraph (`User → Conversation → Interaction`). Traffic at `POST /agents/{id}/interact` becomes an `Interaction`; the **Orchestrator** runs the whole turn in one `execute()` — a deterministic continuation check (resume an in-flight flow) followed by a bounded think-act-observe loop over a unified tool surface. **Routing is tool selection. Turn-lock is an active flow that hasn't returned `COMPLETE`.**

Most agent frameworks hand you a Python library and leave the production concerns — tenancy, persistence, auth, channels, deployment — as an exercise. jvagent ships them. It is the **harness** you wrap around the model: many agents per app, isolated state per user, the same agent voiced to WhatsApp / Messenger / email / web, and [Anthropic Agent Skills](https://www.anthropic.com/news/skills) dropped into a per-user sandbox without writing Python.

**Use cases:** multi-tenant SaaS assistants, customer-support and sales bots across messaging channels, document-grounded copilots, and long-running autonomous agents — embedded in *your* application, not a hosted black box.

## Why jvagent

### 🏢 Multi-tenant by construction
Every user's entire history is a connected subgraph (`User → Conversation → Interaction`), so isolation is a property of the data model, not a `WHERE user_id =` you have to remember. Claude/Anthropic skills run in a **per-user code-execution sandbox** ([ADR-0017](https://github.com/TrueSelph/jvagent/blob/main/.planning/adr/0017-two-skill-specs-code-execution-substrate.md)); deleting a tenant is one edge-walk.

### 👥 Many agents, one app
Declare any number of agents in `app.yaml` / `agent.yaml`, each with its own actions, identity, memory, and tool surface — one deployment, many distinct bots. Namespaced plugins (`jvagent/`, `contrib/`, `custom/`) keep third-party actions from colliding.

### 🧩 Drop-in Anthropic Agent Skills
A standard [Anthropic Agent Skill](https://www.anthropic.com/news/skills) folder (`SKILL.md` + `scripts/`, agentskills.io-compatible) activates as a tool: the Orchestrator stages it into the caller's sandbox and the model runs its scripts. Bring Claude's skill ecosystem into your own app — no rewrite. See [`docs/ORCHESTRATOR.md`](https://github.com/TrueSelph/jvagent/blob/main/docs/ORCHESTRATOR.md).

### 🧠 One-loop Orchestrator, not a state machine
A single action (weight `-200`) runs each turn in one `execute()`: resume an active flow if one is locked, otherwise let the model think-act-observe over every available tool. Adding a capability means adding a tool — there is no separate router, intent classifier, or capability registry to maintain. See [`docs/ORCHESTRATOR.md`](https://github.com/TrueSelph/jvagent/blob/main/docs/ORCHESTRATOR.md).

### 🪶 Thin harness
The server stays out of the model's way. Steering, extraction, and orchestration live in **skills** (Markdown SOPs) and the model's own tool calls — not in server-side special-casing. This keeps behavior predictable and the codebase small. See [`docs/thin-harness.md`](https://github.com/TrueSelph/jvagent/blob/main/docs/thin-harness.md).

### 🔌 Load-on-demand actions
Actions are namespaced plugins discovered from `info.yaml`. Only what an agent lists (and its transitive dependencies) is imported; everything else stays dormant and its endpoints stay closed. Deep lifecycle hooks (`on_register`, `on_enable`, `on_startup`, `on_disable`, `on_deregister`, `pulse`) make enable/disable dynamic and safe.

### 🗂️ Skills (Markdown-first)
Skills add procedures as Markdown with optional Python tool scripts — Claude-compatible bundles you can drop in without touching Python. The interview skill drives multi-field, validated data collection on top of the same tool surface. See [`jvagent/skills/README.md`](https://github.com/TrueSelph/jvagent/blob/main/jvagent/skills/README.md).

### 💾 Persistent, self-pruning memory
`User → Conversation → Interaction` is a bidirectional chain. Rolling-window pruning keeps latency predictable (capped per call), and a separate logs database keeps interaction/error trails out of the hot path. See [`.planning/reference/memory-and-pruning.md`](https://github.com/TrueSelph/jvagent/blob/main/.planning/reference/memory-and-pruning.md).

### 📣 Channels & proactive messaging
A response bus with channel adapters delivers replies to WhatsApp, Messenger, email, or the web, and `Agent.send_proactive_message` lets an agent reach out between turns. See [`docs/proactive-messages.md`](https://github.com/TrueSelph/jvagent/blob/main/docs/proactive-messages.md).

### 🛠️ Production-shaped
Distributed conversation locks (Redis / DynamoDB), per-event-loop locking for serverless warm starts, model HTTP retries with backoff, MCP tool gateway, and light/heavy model gearing — all configurable from YAML.

## How jvagent compares

Most agent frameworks are **libraries** that orchestrate model calls; you bring the server, the tenancy, the persistence, and the channels. jvagent is a **harness** — it ships those. The table below maps each project against its *primary, native* design as of mid-2026 (most gaps are bridgeable with extra code; this is about what you get out of the box).

| | **jvagent** | [LangGraph](https://www.langchain.com/langgraph) | [CrewAI](https://www.crewai.com/) | [AutoGen / AG2](https://microsoft.github.io/autogen/) | [Letta](https://www.letta.com/) | [Agno / AgentOS](https://www.agno.com/) |
|---|---|---|---|---|---|---|
| **Primary form** | Harness + HTTP server | Orchestration library | Orchestration library | Conversation library | Memory-agent runtime | Framework + runtime |
| **Define an agent in** | YAML (declarative) | Python | Python (+ some YAML) | Python | Python / API | Python |
| **Many agents per deploy** | ✅ first-class | ➖ you compose | ✅ crews | ✅ teams | ✅ | ✅ teams |
| **Per-tenant / per-user isolation** | ✅ by data model | ➖ DIY | ➖ DIY | ➖ DIY | ✅ memory-scoped | ✅ per-session |
| **Persistent memory model** | ✅ graph (`User→Conv→Interaction`) + pruning | ✅ checkpoints | ➖ task passing | ➖ in-context | ✅ OS-tiered memory | ✅ sessions + knowledge |
| **Built-in messaging channels** | ✅ WhatsApp / Messenger / email / web / SSE | ❌ | ❌ | ❌ | ❌ (API) | ➖ Slack / AG-UI |
| **Drop-in Anthropic Agent Skills** | ✅ `SKILL.md` → per-user sandbox | ❌ | ❌ | ❌ | ❌ | ➖ via Claude Agent SDK |
| **HTTP API + auth out of the box** | ✅ | ➖ via Platform | ❌ | ❌ | ✅ server | ✅ FastAPI |
| **Routing** | model picks tools (no router) | explicit graph edges | role/process | conversation | agent loop | model + teams |
| **License** | MIT | MIT | MIT | (Apache/MIT) | Apache-2.0 | MPL-2.0 |

✅ native · ➖ partial / with setup · ❌ not built in. Where to reach for each: **LangGraph** when you want to hand-draw a control graph with checkpoints; **CrewAI/AutoGen** for quick multi-agent prototypes; **Letta** when self-managing long-term memory is the whole point; **Agno** for a polished Python-first production runtime. Reach for **jvagent** when you want to *embed* multi-tenant, multi-agent, Claude-skill-capable agents into your own application and have the harness — memory, channels, auth, deployment — already solved.

> Comparisons reflect each project's primary documented design and are not exhaustive; all of these are capable frameworks. Corrections welcome via [issue](https://github.com/TrueSelph/jvagent/issues) or PR.

## Installation

```bash
# From PyPI
pip install jvagent
```

```bash
# From source (development)
git clone https://github.com/TrueSelph/jvagent.git
cd jvagent
pip install -e ".[dev]"
```

Requires Python 3.8+. Optional extras: `pageindex` (document ingestion/retrieval), `distributed-lock` (Redis / DynamoDB conversation locks), `test`, `dev`.

> Pre-1.0 release candidates publish to TestPyPI first:
> ```bash
> pip install -i https://test.pypi.org/simple/ \
>     --extra-index-url https://pypi.org/simple/ jvagent==0.1.0rc2
> ```

## Quick Start

### 1. Scaffold an app

```bash
jvagent app create --yes \
  --dir ./my_app \
  --app-id my_app \
  --title "My App" \
  --author "Your Name" \
  --agent jvagent/main_bot@minimal \
  --profile minimal
```

This writes `app.yaml`, `agents/`, `profiles/`, and `.env.example`. Built-in agent profiles include `minimal`, `conversational`, `whatsapp_voice`, and `research`. Full CLI reference: [`docs/scaffolding.md`](https://github.com/TrueSelph/jvagent/blob/main/docs/scaffolding.md).

### 2. Configure the environment

```bash
cd my_app
cp .env.example .env
```

Set at minimum:

- `JVAGENT_ADMIN_PASSWORD` — the initial admin user's password.
- `JVSPATIAL_JWT_SECRET_KEY` — JWT signing secret (change from the default for any non-local use).

Add a model provider key (e.g. `OPENAI_API_KEY`) for the agent to reason. See the [configuration reference](https://github.com/TrueSelph/jvagent/blob/main/docs/configuration.md) and [environment keys](https://github.com/TrueSelph/jvagent/blob/main/docs/environment-keys-reference.md).

### 3. Run

```bash
jvagent                 # uses the current directory as the app root
jvagent /path/to/my_app # or point at an app root explicitly
jvagent /path/to/my_app --update --debug
```

The server starts on `http://127.0.0.1:8000` (configurable). Interactive API docs are at `/docs` and `/redoc`.

### 4. Talk to the agent

```bash
# Log in with the admin credentials from .env
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@jvagent.example", "password": "your-admin-password"}'

# Send a turn (use the token from the login response)
curl -X POST http://localhost:8000/agents/{agent_id}/interact \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"utterance": "Hello"}'
```

### 5. Or use the chat UI

The bundled [jvchat](https://github.com/TrueSelph/jvagent/blob/main/docs/jvchat.md) web UI ships in the wheel and runs on its own port (a separate origin from the agent server, by design):

```bash
jvagent chat                                      # http://127.0.0.1:3000, opens a browser
jvagent chat --url https://my-agent.example.com   # point at a remote agent
```

A complete worked example ships in [`examples/jvagent_app/`](https://github.com/TrueSelph/jvagent/blob/main/examples/jvagent_app/README.md) — an Orchestrator-pattern agent with a skill bundle. See its [STRUCTURE](https://github.com/TrueSelph/jvagent/blob/main/examples/jvagent_app/STRUCTURE.md) for the file-by-file tour, and the [local dev runbook](https://github.com/TrueSelph/jvagent/blob/main/.planning/runbooks/local-dev.md) to run it end-to-end.

## Core Concepts

### Graph hierarchy

```
Root → App → Agents → Agent ─┬─ Actions → Action(s) → [InteractAction subclass]
                             └─ Memory → User → Conversation → Interaction*
```

The graph is the source of truth. Top-level `InteractAction`s are visited by the `InteractWalker` in ascending `weight` order; the Orchestrator sits at the front (weight `-200`) and runs the turn.

### Actions

An **Action** is a namespaced plugin (`namespace/action_name`) declared by an `info.yaml`. **Persisted fields use `attribute(...)`** so they live on the graph; plain class attributes do not persist. Actions expose tools via `get_tools()`, discover peers with `get_action()`, and honor lifecycle hooks. **InteractActions** additionally participate in a turn and can be dispatched as tools by the Orchestrator. To build one, start with the [action authoring contract](https://github.com/TrueSelph/jvagent/blob/main/.planning/reference/action-authoring.md).

### The turn

```
POST /agents/{id}/interact
        │
        ▼
  new Interaction ──> InteractWalker (weight order)
        │
        ▼
  Orchestrator.execute()
        ├─ continuation check ── resume a locked flow?
        └─ think → act (tools) → observe ──┐
                  ▲────────────────────────┘  (bounded loop)
        │
        ▼
  ReplyAction.gather() ── one unified reply per turn
```

`ReplyAction` (`jvagent/reply`) is jvagent's **single output contract**: producers queue directives; `ReplyAction` gathers them and delivers exactly one emission per turn. Identity (`alias` + `role`) lives on the Agent node. See [ADR-0024](https://github.com/TrueSelph/jvagent/blob/main/.planning/adr/0024-single-per-turn-egress.md) / [ADR-0025](https://github.com/TrueSelph/jvagent/blob/main/.planning/adr/0025-replyaction-single-output-contract.md).

### Memory

Each user gets a `Conversation` holding a bidirectional chain of `Interaction`s. `Conversation.interaction_limit` sets the rolling window (`0` disables); pruning is capped per call for predictable latency. Full API in [`jvagent/memory/README.md`](https://github.com/TrueSelph/jvagent/blob/main/jvagent/memory/README.md).

### Skills

A **skill** is a Markdown SOP (optionally plus Python tool scripts) that extends an action's behavior without new Python wiring. The interview skill, for example, layers validated multi-field collection onto the orchestrator's tool surface. See [`jvagent/skills/README.md`](https://github.com/TrueSelph/jvagent/blob/main/jvagent/skills/README.md) and the [thin-harness guide](https://github.com/TrueSelph/jvagent/blob/main/docs/thin-harness.md).

## Configuration

jvagent resolves configuration by precedence (highest first):

1. CLI flag (`--update`, `--source`, `--merge`, `--debug`, `--serverless`)
2. Environment variable (via `jvspatial.env.env`)
3. `app.yaml` (app root)
4. `agent.yaml` (under `agents/`)
5. Action `attribute(default=...)`

`app.yaml` stays lean; per-agent and per-action settings live on the agent and action nodes. See the [configuration reference](https://github.com/TrueSelph/jvagent/blob/main/docs/configuration.md), the full [configuration keys](https://github.com/TrueSelph/jvagent/blob/main/.planning/reference/configuration-keys.md), and [integration env vars](https://github.com/TrueSelph/jvagent/blob/main/docs/integrations-environment.md).

## Documentation

### Getting started & operating

- [Configuration reference](https://github.com/TrueSelph/jvagent/blob/main/docs/configuration.md) — `app.yaml` ↔ env mapping, prefix rules, jvspatial alignment
- [Environment keys reference](https://github.com/TrueSelph/jvagent/blob/main/docs/environment-keys-reference.md) — every `JVAGENT_*` / `JVSPATIAL_*` / vendor key
- [App scaffolding CLI](https://github.com/TrueSelph/jvagent/blob/main/docs/scaffolding.md) — `jvagent app create`, `agent create`, `app profile new`
- [Language models](https://github.com/TrueSelph/jvagent/blob/main/docs/language-models.md) — provider actions, retries, model gearing
- [Database indexing](https://github.com/TrueSelph/jvagent/blob/main/docs/database-indexing.md) · [Security review](https://github.com/TrueSelph/jvagent/blob/main/docs/security-review.md)
- [Logging](https://github.com/TrueSelph/jvagent/blob/main/docs/logging.md) · [Interaction logging](https://github.com/TrueSelph/jvagent/blob/main/docs/interaction-logging.md) · [Error logging](https://github.com/TrueSelph/jvagent/blob/main/docs/error-logging.md)
- [Task tracking](https://github.com/TrueSelph/jvagent/blob/main/docs/task-tracking.md) · [Proactive messages](https://github.com/TrueSelph/jvagent/blob/main/docs/proactive-messages.md)

### Architecture & internals

- [Orchestrator](https://github.com/TrueSelph/jvagent/blob/main/docs/ORCHESTRATOR.md) — the turn model and source layout
- [Thin harness](https://github.com/TrueSelph/jvagent/blob/main/docs/thin-harness.md) — platform-wide design principle
- [Memory System](https://github.com/TrueSelph/jvagent/blob/main/jvagent/memory/README.md) — Conversation, Interaction, User APIs
- [InteractAction API](https://github.com/TrueSelph/jvagent/blob/main/jvagent/action/interact/README.md)
- [Skill Bundles Standard Guide](https://github.com/TrueSelph/jvagent/blob/main/jvagent/skills/README.md)

### Action modules

- [Orchestrator source layout](https://github.com/TrueSelph/jvagent/blob/main/docs/ORCHESTRATOR.md) · [InteractRouter](https://github.com/TrueSelph/jvagent/blob/main/jvagent/action/router/README.md)
- [RetrievalInteractAction](https://github.com/TrueSelph/jvagent/blob/main/jvagent/action/retrieval/README.md) · [IntroInteractAction](https://github.com/TrueSelph/jvagent/blob/main/jvagent/action/intro/README.md) · [Converse](https://github.com/TrueSelph/jvagent/blob/main/jvagent/action/converse/README.md)
- [InterviewAction](https://github.com/TrueSelph/jvagent/blob/main/jvagent/action/interview/README.md) · [ReplyAction](https://github.com/TrueSelph/jvagent/blob/main/jvagent/action/reply/reply_action.py) · [Response](https://github.com/TrueSelph/jvagent/blob/main/jvagent/action/response/README.md)
- [Model Actions](https://github.com/TrueSelph/jvagent/blob/main/jvagent/action/model/README.md) · [MCPAction](https://github.com/TrueSelph/jvagent/blob/main/jvagent/action/mcp/README.md) · [PageIndex](https://github.com/TrueSelph/jvagent/blob/main/jvagent/action/pageindex/README.md)
- [WhatsApp](https://github.com/TrueSelph/jvagent/blob/main/jvagent/action/whatsapp/README.md) · [TTS](https://github.com/TrueSelph/jvagent/blob/main/jvagent/action/tts_action/README.md) · [STT](https://github.com/TrueSelph/jvagent/blob/main/jvagent/action/stt_action/README.md)
- [Access Control](https://github.com/TrueSelph/jvagent/blob/main/jvagent/action/access_control/README.md) · [Agent Utils](https://github.com/TrueSelph/jvagent/blob/main/jvagent/action/agent_utils/README.md)
- Full inventory: [actions catalog](https://github.com/TrueSelph/jvagent/blob/main/.planning/reference/actions-catalog.md)

### Deployment & tooling

- [Dockerfile generator](https://github.com/TrueSelph/jvagent/blob/main/jvagent/bundle/README.md)
- [jvchat web UI](https://github.com/TrueSelph/jvagent/blob/main/docs/jvchat.md) — `jvagent chat`, runtime config, and the separate-origin security rationale
- [jvchat source](https://github.com/TrueSelph/jvagent/blob/main/jvchat/README.md) — React reference chat UI
- [Releasing](https://github.com/TrueSelph/jvagent/blob/main/RELEASING.md) — version bump → tag → PyPI Trusted Publishing

### Example application

- [jvagent_app](https://github.com/TrueSelph/jvagent/blob/main/examples/jvagent_app/README.md) · [App structure](https://github.com/TrueSelph/jvagent/blob/main/examples/jvagent_app/STRUCTURE.md) · [Orchestrator agent](https://github.com/TrueSelph/jvagent/blob/main/examples/jvagent_app/agents/jvagent/orchestrator_agent/README.md)

### For AI agents & contributors

Agent-facing design docs live under [`.planning/`](https://github.com/TrueSelph/jvagent/blob/main/.planning/README.md); the root [`CLAUDE.md`](https://github.com/TrueSelph/jvagent/blob/main/CLAUDE.md) is the entry point (also surfaced as [`AGENTS.md`](https://github.com/TrueSelph/jvagent/blob/main/AGENTS.md)).

- [Project vision](https://github.com/TrueSelph/jvagent/blob/main/.planning/PROJECT.md) · [SPEC](https://github.com/TrueSelph/jvagent/blob/main/.planning/SPEC.md) · [Patterns](https://github.com/TrueSelph/jvagent/blob/main/.planning/PATTERNS.md) · [Architecture diagrams](https://github.com/TrueSelph/jvagent/blob/main/.planning/architecture.md) · [Glossary](https://github.com/TrueSelph/jvagent/blob/main/.planning/GLOSSARY.md)
- [Action authoring](https://github.com/TrueSelph/jvagent/blob/main/.planning/reference/action-authoring.md) · [Memory & pruning](https://github.com/TrueSelph/jvagent/blob/main/.planning/reference/memory-and-pruning.md) · [Observability](https://github.com/TrueSelph/jvagent/blob/main/.planning/reference/observability.md) · [jvspatial integration](https://github.com/TrueSelph/jvagent/blob/main/.planning/reference/jvspatial-integration.md)
- [Decision records (ADRs)](https://github.com/TrueSelph/jvagent/tree/main/.planning/adr/) · [Specs](https://github.com/TrueSelph/jvagent/tree/main/.planning/specs/) · [Plans](https://github.com/TrueSelph/jvagent/tree/main/.planning/plans/)
- Runbooks: [local dev](https://github.com/TrueSelph/jvagent/blob/main/.planning/runbooks/local-dev.md) · [add an action](https://github.com/TrueSelph/jvagent/blob/main/.planning/runbooks/add-action.md)
- Per-subsystem guides: [`core`](https://github.com/TrueSelph/jvagent/blob/main/jvagent/core/CLAUDE.md) · [`memory`](https://github.com/TrueSelph/jvagent/blob/main/jvagent/memory/CLAUDE.md) · [`action`](https://github.com/TrueSelph/jvagent/blob/main/jvagent/action/CLAUDE.md) · [`interact`](https://github.com/TrueSelph/jvagent/blob/main/jvagent/action/interact/CLAUDE.md) · [`cli`](https://github.com/TrueSelph/jvagent/blob/main/jvagent/cli/CLAUDE.md) · [`logging`](https://github.com/TrueSelph/jvagent/blob/main/jvagent/logging/CLAUDE.md) · [`tests`](https://github.com/TrueSelph/jvagent/blob/main/tests/CLAUDE.md)
- [Changelog](https://github.com/TrueSelph/jvagent/blob/main/CHANGELOG.md)

## Authors & maintainers

jvagent — an agent harness built on jvspatial — was created by **Eldon Marks** ([@eldonm](https://github.com/eldonm)), who serves as its lead maintainer.

See [AUTHORS](https://github.com/TrueSelph/jvagent/blob/main/AUTHORS) for the full list of authors and contributors. Copyright and licensing terms are set out in the [LICENSE](https://github.com/TrueSelph/jvagent/blob/main/LICENSE) file.

## Contributors

<p align="center">
    <a href="https://github.com/TrueSelph/jvagent/graphs/contributors">
        <img src="https://contrib.rocks/image?repo=TrueSelph/jvagent" />
    </a>
</p>

## Contributing

Contributions are welcome. Please read the [Contributing Guide](https://github.com/TrueSelph/jvagent/blob/main/CONTRIBUTING.md) for the dev loop, conventions, and the CI gates, and the [Code of Conduct](https://github.com/TrueSelph/jvagent/blob/main/CODE_OF_CONDUCT.md) before opening a pull request. Security issues: see the [Security Policy](https://github.com/TrueSelph/jvagent/blob/main/SECURITY.md).

## License

This project is licensed under the MIT License — see the [LICENSE](https://github.com/TrueSelph/jvagent/blob/main/LICENSE) file for details.
