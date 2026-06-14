# jvagent

[![GitHub release (latest by date)](https://img.shields.io/github/v/release/TrueSelph/jvagent)](https://github.com/TrueSelph/jvagent/releases)
[![GitHub Workflow Status](https://img.shields.io/github/actions/workflow/status/TrueSelph/jvagent/test-jvagent.yaml)](https://github.com/TrueSelph/jvagent/actions)
[![PyPI version](https://img.shields.io/pypi/v/jvagent)](https://pypi.org/project/jvagent/)
[![GitHub issues](https://img.shields.io/github/issues/TrueSelph/jvagent)](https://github.com/TrueSelph/jvagent/issues)
[![GitHub pull requests](https://img.shields.io/github/issues-pr/TrueSelph/jvagent)](https://github.com/TrueSelph/jvagent/pulls)
[![GitHub](https://img.shields.io/github/license/TrueSelph/jvagent)](https://github.com/TrueSelph/jvagent/blob/main/LICENSE)

A modular, production-shaped platform for building AI agents on a graph. Declare your app in YAML, and jvagent gives each agent a persistent memory graph, a load-on-demand plugin system, and a single-loop **Orchestrator** that turns every incoming message into tool calls — routing, turn-locking, and replying without a hand-written state machine.

## Table of Contents

- [Overview](#overview)
- [Why jvagent](#why-jvagent)
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

The result is a runtime that favors real deployments — channel adapters, rolling-window memory, distributed locks, a separate logs database, serverless-friendly warm starts — rather than a notebook demo.

**Use cases:** turn-based chatbots, channel adapters (WhatsApp / Messenger / email / web), document-grounded assistants, and long-running autonomous agents.

## Why jvagent

### 🧠 One-loop Orchestrator, not a state machine
A single action (weight `-200`) runs each turn in one `execute()`: resume an active flow if one is locked, otherwise let the model think-act-observe over every available tool. Adding a capability means adding a tool — there is no separate router, intent classifier, or capability registry to maintain. See [`docs/ORCHESTRATOR.md`](docs/ORCHESTRATOR.md).

### 🪶 Thin harness
The server stays out of the model's way. Steering, extraction, and orchestration live in **skills** (Markdown SOPs) and the model's own tool calls — not in server-side special-casing. This keeps behavior predictable and the codebase small. See [`docs/thin-harness.md`](docs/thin-harness.md).

### 🔌 Load-on-demand actions
Actions are namespaced plugins discovered from `info.yaml`. Only what an agent lists (and its transitive dependencies) is imported; everything else stays dormant and its endpoints stay closed. Deep lifecycle hooks (`on_register`, `on_enable`, `on_startup`, `on_disable`, `on_deregister`, `pulse`) make enable/disable dynamic and safe.

### 🗂️ Skills (Markdown-first)
Skills add procedures as Markdown with optional Python tool scripts — Claude-compatible bundles you can drop in without touching Python. The interview skill drives multi-field, validated data collection on top of the same tool surface. See [`jvagent/skills/README.md`](jvagent/skills/README.md).

### 💾 Persistent, self-pruning memory
`User → Conversation → Interaction` is a bidirectional chain. Rolling-window pruning keeps latency predictable (capped per call), and a separate logs database keeps interaction/error trails out of the hot path. See [`.planning/reference/memory-and-pruning.md`](.planning/reference/memory-and-pruning.md).

### 📣 Channels & proactive messaging
A response bus with channel adapters delivers replies to WhatsApp, Messenger, email, or the web, and `Agent.send_proactive_message` lets an agent reach out between turns. See [`docs/proactive-messages.md`](docs/proactive-messages.md).

### 🛠️ Production-shaped
Distributed conversation locks (Redis / DynamoDB), per-event-loop locking for serverless warm starts, model HTTP retries with backoff, MCP tool gateway, and light/heavy model gearing — all configurable from YAML.

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
>     --extra-index-url https://pypi.org/simple/ jvagent==0.1.0rc1
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

This writes `app.yaml`, `agents/`, `profiles/`, and `.env.example`. Built-in agent profiles include `minimal`, `conversational`, `whatsapp_voice`, and `research`. Full CLI reference: [`docs/scaffolding.md`](docs/scaffolding.md).

### 2. Configure the environment

```bash
cd my_app
cp .env.example .env
```

Set at minimum:

- `JVAGENT_ADMIN_PASSWORD` — the initial admin user's password.
- `JVSPATIAL_JWT_SECRET_KEY` — JWT signing secret (change from the default for any non-local use).

Add a model provider key (e.g. `OPENAI_API_KEY`) for the agent to reason. See the [configuration reference](docs/configuration.md) and [environment keys](docs/environment-keys-reference.md).

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

A complete worked example ships in [`examples/jvagent_app/`](examples/jvagent_app/README.md) — an Orchestrator-pattern agent with a skill bundle. See its [STRUCTURE](examples/jvagent_app/STRUCTURE.md) for the file-by-file tour, and the [local dev runbook](.planning/runbooks/local-dev.md) to run it end-to-end.

## Core Concepts

### Graph hierarchy

```
Root → App → Agents → Agent ─┬─ Actions → Action(s) → [InteractAction subclass]
                             └─ Memory → User → Conversation → Interaction*
```

The graph is the source of truth. Top-level `InteractAction`s are visited by the `InteractWalker` in ascending `weight` order; the Orchestrator sits at the front (weight `-200`) and runs the turn.

### Actions

An **Action** is a namespaced plugin (`namespace/action_name`) declared by an `info.yaml`. **Persisted fields use `attribute(...)`** so they live on the graph; plain class attributes do not persist. Actions expose tools via `get_tools()`, discover peers with `get_action()`, and honor lifecycle hooks. **InteractActions** additionally participate in a turn and can be dispatched as tools by the Orchestrator. To build one, start with the [action authoring contract](.planning/reference/action-authoring.md).

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

`ReplyAction` (`jvagent/reply`) is jvagent's **single output contract**: producers queue directives; `ReplyAction` gathers them and delivers exactly one emission per turn. Identity (`alias` + `role`) lives on the Agent node. See [ADR-0024](.planning/adr/0024-single-per-turn-egress.md) / [ADR-0025](.planning/adr/0025-replyaction-single-output-contract.md).

### Memory

Each user gets a `Conversation` holding a bidirectional chain of `Interaction`s. `Conversation.interaction_limit` sets the rolling window (`0` disables); pruning is capped per call for predictable latency. Full API in [`jvagent/memory/README.md`](jvagent/memory/README.md).

### Skills

A **skill** is a Markdown SOP (optionally plus Python tool scripts) that extends an action's behavior without new Python wiring. The interview skill, for example, layers validated multi-field collection onto the orchestrator's tool surface. See [`jvagent/skills/README.md`](jvagent/skills/README.md) and the [thin-harness guide](docs/thin-harness.md).

## Configuration

jvagent resolves configuration by precedence (highest first):

1. CLI flag (`--update`, `--source`, `--merge`, `--debug`, `--serverless`)
2. Environment variable (via `jvspatial.env.env`)
3. `app.yaml` (app root)
4. `agent.yaml` (under `agents/`)
5. Action `attribute(default=...)`

`app.yaml` stays lean; per-agent and per-action settings live on the agent and action nodes. See the [configuration reference](docs/configuration.md), the full [configuration keys](.planning/reference/configuration-keys.md), and [integration env vars](docs/integrations-environment.md).

## Documentation

### Getting started & operating

- [Configuration reference](docs/configuration.md) — `app.yaml` ↔ env mapping, prefix rules, jvspatial alignment
- [Environment keys reference](docs/environment-keys-reference.md) — every `JVAGENT_*` / `JVSPATIAL_*` / vendor key
- [App scaffolding CLI](docs/scaffolding.md) — `jvagent app create`, `agent create`, `app profile new`
- [Language models](docs/language-models.md) — provider actions, retries, model gearing
- [Database indexing](docs/database-indexing.md) · [Security review](docs/security-review.md)
- [Logging](docs/logging.md) · [Interaction logging](docs/interaction-logging.md) · [Error logging](docs/error-logging.md)
- [Task tracking](docs/task-tracking.md) · [Proactive messages](docs/proactive-messages.md)

### Architecture & internals

- [Orchestrator](docs/ORCHESTRATOR.md) — the turn model and source layout
- [Thin harness](docs/thin-harness.md) — platform-wide design principle
- [Memory System](jvagent/memory/README.md) — Conversation, Interaction, User APIs
- [InteractAction API](jvagent/action/interact/README.md)
- [Skill Bundles Standard Guide](jvagent/skills/README.md)

### Action modules

- [Orchestrator source layout](docs/ORCHESTRATOR.md) · [InteractRouter](jvagent/action/router/README.md)
- [RetrievalInteractAction](jvagent/action/retrieval/README.md) · [IntroInteractAction](jvagent/action/intro/README.md) · [Converse](jvagent/action/converse/README.md)
- [InterviewAction](jvagent/action/interview/README.md) · [ReplyAction](jvagent/action/reply/reply_action.py) · [Response](jvagent/action/response/README.md)
- [Model Actions](jvagent/action/model/README.md) · [MCPAction](jvagent/action/mcp/README.md) · [PageIndex](jvagent/action/pageindex/README.md)
- [WhatsApp](jvagent/action/whatsapp/README.md) · [TTS](jvagent/action/tts_action/README.md) · [STT](jvagent/action/stt_action/README.md)
- [Access Control](jvagent/action/access_control/README.md) · [Agent Utils](jvagent/action/agent_utils/README.md)
- Full inventory: [actions catalog](.planning/reference/actions-catalog.md)

### Deployment & tooling

- [Dockerfile generator](jvagent/bundle/README.md)
- [jvchat](jvchat/README.md) — React reference chat UI
- [Releasing](RELEASING.md) — version bump → tag → PyPI Trusted Publishing

### Example application

- [jvagent_app](examples/jvagent_app/README.md) · [App structure](examples/jvagent_app/STRUCTURE.md) · [Orchestrator agent](examples/jvagent_app/agents/jvagent/orchestrator_agent/README.md)

### For AI agents & contributors

Agent-facing design docs live under [`.planning/`](.planning/README.md); the root [`CLAUDE.md`](CLAUDE.md) is the entry point (also surfaced as [`AGENTS.md`](AGENTS.md)).

- [Project vision](.planning/PROJECT.md) · [SPEC](.planning/SPEC.md) · [Patterns](.planning/PATTERNS.md) · [Architecture diagrams](.planning/architecture.md) · [Glossary](.planning/GLOSSARY.md)
- [Action authoring](.planning/reference/action-authoring.md) · [Memory & pruning](.planning/reference/memory-and-pruning.md) · [Observability](.planning/reference/observability.md) · [jvspatial integration](.planning/reference/jvspatial-integration.md)
- [Decision records (ADRs)](.planning/adr/) · [Specs](.planning/specs/) · [Plans](.planning/plans/)
- Runbooks: [local dev](.planning/runbooks/local-dev.md) · [add an action](.planning/runbooks/add-action.md)
- Per-subsystem guides: [`core`](jvagent/core/CLAUDE.md) · [`memory`](jvagent/memory/CLAUDE.md) · [`action`](jvagent/action/CLAUDE.md) · [`interact`](jvagent/action/interact/CLAUDE.md) · [`cli`](jvagent/cli/CLAUDE.md) · [`logging`](jvagent/logging/CLAUDE.md) · [`tests`](tests/CLAUDE.md)
- [Changelog](CHANGELOG.md)

## Authors & maintainers

jvagent — an agent harness built on jvspatial — was created by **Eldon Marks** ([@eldonm](https://github.com/eldonm)), who serves as its lead maintainer.

See [AUTHORS](AUTHORS) for the full list of authors and contributors. Copyright and licensing terms are set out in the [LICENSE](LICENSE) file.

## Contributors

<p align="center">
    <a href="https://github.com/TrueSelph/jvagent/graphs/contributors">
        <img src="https://contrib.rocks/image?repo=TrueSelph/jvagent" />
    </a>
</p>

## Contributing

Contributions are welcome. Please read the [Contributing Guide](CONTRIBUTING.md) for the dev loop, conventions, and the CI gates, and the [Code of Conduct](CODE_OF_CONDUCT.md) before opening a pull request. Security issues: see the [Security Policy](SECURITY.md).

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
