# CLAUDE.md — jvagent Master Agent Guide

> This file is the entry point for **AI agents** (Claude Code, Codex CLI, Gemini CLI, etc.) working on jvagent. Human contributors should start with [`README.md`](README.md). Both audiences are welcome here, but agent-targeted reference docs live under [`.planning/`](.planning/) and per-subsystem `CLAUDE.md` files are scattered through the source tree.
>
> **AGENTS.md** at the repo root is a one-line pointer to this file.

---

## 1. What jvagent is (60-second version)

A modular AI-agent platform built on [jvspatial](.planning/jvspatial-integration.md)'s object-spatial graph framework.

- An *app* declares one or more *agents* in YAML.
- Each agent owns a graph of *actions* (plugins) plus a per-user memory subgraph (`User → Conversation → Interaction`).
- Incoming traffic at `POST /agents/{id}/interact` becomes an `Interaction`; an `InteractWalker` visits the agent's `InteractAction`s in weight order; the **Cockpit** action runs a walker-revisit model loop with full agency over harness services and action tools.
- Production-shaped: namespaced plugins, lifecycle hooks, response bus with channel adapters, rolling-window memory pruning, separate logs DB.

Use cases: turn-based chatbots, channel adapters (WhatsApp / Messenger / email / web), long-running autonomous agents.

---

## 2. Where to find things (the only table you need)

| You want to... | Read |
|---|---|
| **Get the big picture** | [`.planning/PROJECT.md`](.planning/PROJECT.md) |
| **Look up normative semantics** (invariants, contracts) | [`.planning/SPEC.md`](.planning/SPEC.md) |
| **Choose a deployment pattern** (Rails / Cockpit / Bridge) | [`.planning/PATTERNS.md`](.planning/PATTERNS.md) |
| **See diagrams** (boot, interact, cockpit, pruning) | [`.planning/architecture.md`](.planning/architecture.md) |
| **Define a term** | [`.planning/GLOSSARY.md`](.planning/GLOSSARY.md) |
| **Build a new action** | [`.planning/action-authoring.md`](.planning/action-authoring.md) |
| **See every existing action** | [`.planning/actions-catalog.md`](.planning/actions-catalog.md) |
| **Understand the jvspatial dependency** | [`.planning/jvspatial-integration.md`](.planning/jvspatial-integration.md) |
| **Understand memory pruning** | [`.planning/memory-and-pruning.md`](.planning/memory-and-pruning.md) |
| **Tune / query logging** | [`.planning/observability.md`](.planning/observability.md) + [`docs/logging.md`](docs/logging.md) |
| **Find a config key** | [`.planning/configuration-keys.md`](.planning/configuration-keys.md) + [`docs/environment-keys-reference.md`](docs/environment-keys-reference.md) |
| **Understand the cockpit** | [`docs/COCKPIT.md`](docs/COCKPIT.md) + [`jvagent/action/cockpit/CLAUDE.md`](jvagent/action/cockpit/CLAUDE.md) |
| **Understand the bridge** | [`docs/BRIDGE.md`](docs/BRIDGE.md) + [`.planning/adr/0007-bridge-helm-architecture.md`](.planning/adr/0007-bridge-helm-architecture.md) |
| **Run jvagent locally** | [`.planning/runbooks/local-dev.md`](.planning/runbooks/local-dev.md) |
| **Add a new action end-to-end** | [`.planning/runbooks/add-action.md`](.planning/runbooks/add-action.md) |
| **Send a proactive (agent-initiated) message** | [`docs/proactive-messages.md`](docs/proactive-messages.md) |
| **See design rationale** | [`.planning/adr/`](.planning/adr/) |
| **User-facing onboarding** | [`README.md`](README.md) |

---

## 3. Graph hierarchy (memorize this)

```
Root → App → Agents → Agent ─┬─ Actions → Action(s) → [InteractAction subclass]
                             └─ Memory → User → Conversation → Interaction*
```

- Top-level `InteractAction`s are visited by `InteractWalker` in ascending `weight` order.
- Sub-`InteractAction`s connected as children require explicit `visitor.visit(child)` from the parent's `execute()`.
- `Interaction`s are bidirectionally chained after the second one is added.
- `Conversation.interaction_limit` controls rolling-window pruning; `0` disables.

Source anchors:
- App: [`jvagent/core/app.py:19`](jvagent/core/app.py)
- Agent: [`jvagent/core/agent.py:18`](jvagent/core/agent.py)
- Action base: [`jvagent/action/base.py:48`](jvagent/action/base.py)
- InteractAction: [`jvagent/action/interact/base.py:32`](jvagent/action/interact/base.py)
- InteractWalker: `jvagent/action/interact/interact_walker.py:50+`
- Cockpit: [`jvagent/action/cockpit/cockpit_interact_action.py:79`](jvagent/action/cockpit/cockpit_interact_action.py)
- Bridge: [`jvagent/action/bridge/bridge_interact_action.py`](jvagent/action/bridge/bridge_interact_action.py) + helms under [`jvagent/action/helm/`](jvagent/action/helm/)
- Conversation + pruning: `jvagent/memory/conversation.py:250-367`

---

## 4. Per-subsystem guides (drop into each one before editing)

When working inside a subdirectory, read its local `CLAUDE.md` first — it's stricter and more local than this file.

| Subdir | Local guide |
|---|---|
| `jvagent/core/` | [`jvagent/core/CLAUDE.md`](jvagent/core/CLAUDE.md) |
| `jvagent/memory/` | [`jvagent/memory/CLAUDE.md`](jvagent/memory/CLAUDE.md) |
| `jvagent/action/` | [`jvagent/action/CLAUDE.md`](jvagent/action/CLAUDE.md) |
| `jvagent/action/interact/` | [`jvagent/action/interact/CLAUDE.md`](jvagent/action/interact/CLAUDE.md) |
| `jvagent/action/cockpit/` | [`jvagent/action/cockpit/CLAUDE.md`](jvagent/action/cockpit/CLAUDE.md) |
| `jvagent/cli/` | [`jvagent/cli/CLAUDE.md`](jvagent/cli/CLAUDE.md) |
| `jvagent/logging/` | [`jvagent/logging/CLAUDE.md`](jvagent/logging/CLAUDE.md) |
| `tests/` | [`tests/CLAUDE.md`](tests/CLAUDE.md) |

Each local guide is ≤ 150 lines and self-contained for that directory.

---

## 5. Development commands

```bash
# Install
pip install -e ".[dev]"

# Run the server (defaults to ./examples/jvagent_app or arg path)
jvagent                              # uses cwd
jvagent examples/jvagent_app         # explicit app root
jvagent path/to/app --debug          # verbose
jvagent path/to/app --update         # apply merge YAML sync
jvagent path/to/app --update --source # destructive YAML sync
jvagent path/to/app --serverless     # serverless single-worker

# Subcommands
jvagent path/to/app bootstrap        # bootstrap graph without starting server
jvagent path/to/app status           # diagnostic snapshot
jvagent path/to/app validate         # validate app.yaml + agents
jvagent bundle path/to/app           # generate Dockerfile

# Scaffolding
jvagent app create --yes --dir ./my_app --app-id my_app --title "My App" \
    --author "You" --agent jvagent/main_bot@minimal --profile minimal

# Tests
pytest tests/                        # all
pytest tests/action/cockpit/ -v      # one slice
pre-commit run --all-files           # full lint pass

# Lint / type
black jvagent/
isort jvagent/ --profile black
flake8 jvagent/ --config=.flake8
mypy jvagent/
```

Full CLI reference in [`jvagent/cli/CLAUDE.md`](jvagent/cli/CLAUDE.md) and [`docs/scaffolding.md`](docs/scaffolding.md).

---

## 6. Conventions to follow

### When editing source
- **Type-annotate everything.** Pydantic and jvspatial both rely on it.
- **Use `attribute(...)` for all persisted Node fields.** Plain class attributes are not persisted.
- **Add a test slice** in `tests/action/{name}/` or `tests/{subsystem}/` for any new behavior.
- **Run `pre-commit run --all-files`** before claiming a change is done.
- **Cite file:line** in commit messages and PR descriptions when fixing bugs — `core/app.py:124` beats "fixed the App singleton".

### When editing docs
- **Reference, don't duplicate.** New docs link to the existing 12 `docs/*.md` rather than rewriting them.
- **File:line refs for every claim** about runtime behavior.
- **Update [`.planning/GLOSSARY.md`](.planning/GLOSSARY.md)** when introducing a new term used in 2+ places.
- **ADRs are immutable** once accepted. To change a decision, write a new ADR that supersedes the old one.

### When adding a feature
- **Read [`.planning/action-authoring.md`](.planning/action-authoring.md)** first if it's a new Action.
- **Stay within the action's directory** — cross-cutting changes should be unusual.
- **Honor lifecycle hooks**: `on_register`, `on_enable`, `on_startup`, `on_disable`, `on_deregister`.
- **Default to `run_in_background=True`** for analytics, model updates, follow-ups — anything not required for the user-facing response.

---

## 7. Configuration resolution (precedence)

1. CLI flag (`--update`, `--source`, `--merge`, `--debug`, `--serverless`)
2. Environment variable (resolved via `jvspatial.env.env`)
3. `app.yaml` at the app root
4. `agent.yaml` under `agents/`
5. Action `attribute(default=...)`

`Model HTTP retries`: `BaseModelAction` / `LanguageModelAction` expose `max_retries`, `retry_initial_delay`, `retry_max_delay`, `retry_backoff_multiplier`, `retry_jitter`, `retry_on_status_codes`. Tune per-action in `agent.yaml`. See [`docs/language-models.md`](docs/language-models.md).

---

## 8. Common traps

| Trap | What goes wrong | Fix |
|---|---|---|
| Forgetting `from . import endpoints` in `__init__.py` | HTTP routes don't register | Add the import |
| Mutating a `protected=True` field with `=` assignment | Silently dropped on some paths | Use `object.__setattr__` + `save()` (see [`app.py:537`](jvagent/core/app.py)) |
| Top-level `InteractAction` not routing to children | Children never execute | Call `await visitor.visit(child)` in `execute()` |
| Setting `Agent.interaction_limit` very low after long history | Latency spike on next append | Pruning is capped per-call by `JVAGENT_MAX_INTERACTIONS_PRUNED_PER_CALL` (default 100); see [`adr/0003`](.planning/adr/0003-interaction-limit-pruning.md) |
| Caching jvspatial objects across event loops | `RuntimeError: attached to different loop` on serverless warm starts | Use the per-loop lock pattern from [`app.py:97-117`](jvagent/core/app.py) |
| Using `count()` on a jvspatial entity | Method may not exist | `len(await Entity.find(query))` |
| Long blocking work in `InteractAction.execute()` | Slow user-facing response | Use `run_in_background=True` or push to `task_dispatcher` |
| Creating new App nodes | Singleton violation | Always use `await App.get()` |

---

## 9. Roadmap and in-flight work

- In-progress roadmap: [`.planning/COCKPIT-ROADMAP.md`](.planning/COCKPIT-ROADMAP.md).
- Per-subsystem codebase intel: [`.planning/codebase/`](.planning/codebase/).
- ADRs: [`.planning/adr/`](.planning/adr/).

---

## 10. Out of scope for jvagent itself

- Database adapter internals → jvspatial.
- Auth / JWT / HTTP wire format → jvspatial.
- Model-provider API quirks → individual `LanguageModelAction` subclasses, not the core.
- Frontend chat UI → `jvchat/` reference client only.

---

## 11. If you only read 3 files...

1. [`.planning/SPEC.md`](.planning/SPEC.md) — what jvagent guarantees.
2. The local `CLAUDE.md` for the subsystem you're touching.
3. [`.planning/action-authoring.md`](.planning/action-authoring.md) — if you're adding behavior.

Everything else is reachable from those.
