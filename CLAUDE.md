# CLAUDE.md — jvagent Master Agent Guide

> This file is the entry point for **AI agents** (Claude Code, Codex CLI, Gemini CLI, etc.) working on jvagent. Human contributors should start with [`README.md`](README.md). Both audiences are welcome here, but agent-targeted reference docs live under [`.planning/`](.planning/) and per-subsystem `CLAUDE.md` files are scattered through the source tree.
>
> **AGENTS.md** at the repo root is a one-line pointer to this file.

---

## 1. What jvagent is (60-second version)

A modular AI-agent platform built on [jvspatial](.planning/reference/jvspatial-integration.md)'s object-spatial graph framework.

- An *app* declares one or more *agents* in YAML.
- Each agent owns a graph of *actions* (plugins) plus a per-user memory subgraph (`User → Conversation → Interaction`).
- Incoming traffic at `POST /agents/{id}/interact` becomes an `Interaction`; an `InteractWalker` visits the agent's `InteractAction`s in weight order; the **Orchestrator** action (weight `-200`) runs the whole turn in one `execute()`: a deterministic **continuation check** (resume an active flow from the conversation `TaskStore`), then a bounded **think-act-observe loop** over a unified tool surface. Routing = tool selection; turn-lock = an active flow that hasn't returned `COMPLETE`/`YIELD`.
- Production-shaped: namespaced plugins, lifecycle hooks, response bus with channel adapters, rolling-window memory pruning, separate logs DB.

Use cases: turn-based chatbots, channel adapters (WhatsApp / Messenger / email / web), long-running autonomous agents.

---

## 2. Where to find things (the only table you need)

| You want to... | Read |
|---|---|
| **Navigate the design docs** | [`.planning/README.md`](.planning/README.md) (folder index) |
| **Get the big picture** | [`.planning/PROJECT.md`](.planning/PROJECT.md) |
| **Look up normative semantics** (invariants, contracts) | [`.planning/SPEC.md`](.planning/SPEC.md) |
| **Choose a deployment pattern** (Orchestrator) | [`.planning/PATTERNS.md`](.planning/PATTERNS.md) |
| **See diagrams** (boot, interact, executive, pruning) | [`.planning/architecture.md`](.planning/architecture.md) |
| **Define a term** | [`.planning/GLOSSARY.md`](.planning/GLOSSARY.md) |
| **Build a new action** | [`.planning/reference/action-authoring.md`](.planning/reference/action-authoring.md) |
| **Thin harness principle** (platform-wide) | [`docs/thin-harness.md`](docs/thin-harness.md) |
| **Build / extend an interview skill** | [`docs/thin-harness.md`](docs/thin-harness.md) + [`jvagent/action/interview/docs/thin-harness.md`](jvagent/action/interview/docs/thin-harness.md) (profile) + [`jvagent/action/interview/CLAUDE.md`](jvagent/action/interview/CLAUDE.md) |
| **See every existing action** | [`.planning/reference/actions-catalog.md`](.planning/reference/actions-catalog.md) |
| **Understand the jvspatial dependency** | [`.planning/reference/jvspatial-integration.md`](.planning/reference/jvspatial-integration.md) |
| **Understand memory pruning** | [`.planning/reference/memory-and-pruning.md`](.planning/reference/memory-and-pruning.md) |
| **Tune / query logging** | [`.planning/reference/observability.md`](.planning/reference/observability.md) + [`docs/logging.md`](docs/logging.md) |
| **Find a config key** | [`.planning/reference/configuration-keys.md`](.planning/reference/configuration-keys.md) + [`docs/environment-keys-reference.md`](docs/environment-keys-reference.md) |
| **Understand the Orchestrator pattern** | [`docs/ORCHESTRATOR.md`](docs/ORCHESTRATOR.md) + ADRs [0012](.planning/adr/0012-skill-executive-architecture.md) (architecture), [0013](.planning/adr/0013-togglable-deterministic-turn-lock.md) (turn-lock), [0014](.planning/adr/0014-identity-on-agent-replyaction-egress.md) (identity/egress), [0015](.planning/adr/0015-skill-executive-configuration-surface.md) (config surface), [0016](.planning/adr/0016-model-gearing-light-heavy.md) (model gearing), [0017](.planning/adr/0017-two-skill-specs-code-execution-substrate.md) (skill specs + code execution), [0018](.planning/adr/0018-lean-tool-surfacing.md) (lean surfacing), [0019](.planning/adr/0019-orchestrator-resumable-plan.md) (resumable plan) |
| **Document conversational test scenarios (CUCS)** | [`.planning/reference/conversation-use-cases.md`](.planning/reference/conversation-use-cases.md) + [ADR-0027](.planning/adr/0027-conversation-use-case-spec.md) |
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
- App: [`jvagent/core/app.py:21`](jvagent/core/app.py)
- Agent: [`jvagent/core/agent.py:30`](jvagent/core/agent.py)
- Action base: [`jvagent/action/base.py:49`](jvagent/action/base.py)
- InteractAction: [`jvagent/action/interact/base.py:27`](jvagent/action/interact/base.py)
- InteractWalker: `jvagent/action/interact/interact_walker.py:47`
- Orchestrator: [`jvagent/action/orchestrator/orchestrator_interact_action.py`](jvagent/action/orchestrator/orchestrator_interact_action.py) + supporting modules ([`continuation.py`](jvagent/action/orchestrator/continuation.py), [`tools.py`](jvagent/action/orchestrator/tools.py), [`core_tools.py`](jvagent/action/orchestrator/core_tools.py), [`catalog.py`](jvagent/action/orchestrator/catalog.py), [`skills.py`](jvagent/action/orchestrator/skills.py))
- Conversation + pruning: `jvagent/memory/conversation.py:235` (`add_interaction`) + `:490` (`_prune_old_interactions`)

---

## 4. Per-subsystem guides (drop into each one before editing)

When working inside a subdirectory, read its local `CLAUDE.md` first — it's stricter and more local than this file.

| Subdir | Local guide |
|---|---|
| `jvagent/core/` | [`jvagent/core/CLAUDE.md`](jvagent/core/CLAUDE.md) |
| `jvagent/memory/` | [`jvagent/memory/CLAUDE.md`](jvagent/memory/CLAUDE.md) |
| `jvagent/action/` | [`jvagent/action/CLAUDE.md`](jvagent/action/CLAUDE.md) |
| `jvagent/action/interact/` | [`jvagent/action/interact/CLAUDE.md`](jvagent/action/interact/CLAUDE.md) |
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

# Chat UI (bundled jvchat, served on its own port — see docs/jvchat.md)
jvagent chat                         # serve the bundled UI at http://127.0.0.1:3000
jvagent chat --url https://my-agent  # point the UI at a remote agent

# Scaffolding
jvagent app create --yes --dir ./my_app --app-id my_app --title "My App" \
    --author "You" --agent jvagent/main_bot@minimal --profile minimal

# Tests
pytest tests/                        # all
pytest tests/action/orchestrator/ -v  # one slice
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

### Commit gate (MANDATORY — run before every commit)

Before **any** `git commit`, both of these MUST pass — no exceptions, including
docs-touching commits (pre-commit still runs trailing-whitespace / YAML / secret
hooks):

```bash
pre-commit run --all-files     # black, isort, flake8, mypy, secrets, etc.
pytest tests/                  # or the affected slice(s) at minimum
```

- If a hook **reformats** files (black/isort), re-stage and re-run until the run is
  clean — a hook that "modifies files" is a FAILURE until a re-run passes with no
  changes. Never commit with a hook still reporting changes.
- If `pytest` has any failure, fix it or explicitly call it out; do not commit over
  red tests.
- `pre-commit install` also installs a pre-push pytest hook (full suite on push);
  the manual run above is still required before every commit.
- Do not use `git commit --no-verify` to bypass the gate.
- Applies on every branch, including hotfix/docs/chore branches.

### When editing source
- **Type-annotate everything.** Pydantic and jvspatial both rely on it.
- **Use `attribute(...)` for all persisted Node fields.** Plain class attributes are not persisted.
- **Add a test slice** in `tests/action/{name}/` or `tests/{subsystem}/` for any new behavior.
- **Run the commit gate above** (`pre-commit run --all-files` + `pytest`) before every commit and before claiming a change is done.
- **Cite file:line** in commit messages and PR descriptions when fixing bugs — `core/app.py:124` beats "fixed the App singleton".

### When editing docs
- **Reference, don't duplicate.** New docs link to the existing `docs/*.md` rather than rewriting them.
- **File:line refs for every claim** about runtime behavior.
- **Update [`.planning/GLOSSARY.md`](.planning/GLOSSARY.md)** when introducing a new term used in 2+ places.
- **ADRs are immutable** once accepted. To change a decision, write a new ADR that supersedes the old one.

### When adding a feature
- **Read [`.planning/reference/action-authoring.md`](.planning/reference/action-authoring.md)** first if it's a new Action.
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
| Mutating a `protected=True` field with `=` assignment | Silently dropped on some paths | Use `object.__setattr__` + `save()` (see `set_app_update_mode` at [`app.py:596`](jvagent/core/app.py)) |
| Top-level `InteractAction` not routing to children | Children never execute | Call `await visitor.visit(child)` in `execute()` |
| Setting `Agent.interaction_limit` very low after long history | Latency spike on next append | Pruning is capped per-call by `JVAGENT_MAX_INTERACTIONS_PRUNED_PER_CALL` (default 100); see [`adr/0003`](.planning/adr/0003-interaction-limit-pruning.md) |
| Caching jvspatial objects across event loops | `RuntimeError: attached to different loop` on serverless warm starts | Use the per-loop lock pattern from [`app.py:97-117`](jvagent/core/app.py) |
| Using `count()` on a jvspatial entity | Method may not exist | `len(await Entity.find(query))` |
| Long blocking work in `InteractAction.execute()` | Slow user-facing response | Use `run_in_background=True` or enqueue a `PROACTIVE` task (`TaskMonitor`) |
| Creating new App nodes | Singleton violation | Always use `await App.get()` |
| Fattening the harness (prep steering, extractors, auto-store, orchestrator special-casing) | Server fights the model; regressions to pre-refactor behavior | Follow [docs/thin-harness.md](docs/thin-harness.md); for interviews also [interview profile](jvagent/action/interview/docs/thin-harness.md); extend SOP + skill extensions instead |

---

## 9. Roadmap and in-flight work

- Orchestrator design + roadmap: [`.planning/adr/0012-skill-executive-architecture.md`](.planning/adr/0012-skill-executive-architecture.md), [`.planning/archive/EXECUTIVE-ROADMAP.md`](.planning/archive/EXECUTIVE-ROADMAP.md).
- ADRs: [`.planning/adr/`](.planning/adr/).

---

## 10. Out of scope for jvagent itself

- Database adapter internals → jvspatial.
- Auth / JWT / HTTP wire format → jvspatial.
- Model-provider API quirks → individual `LanguageModelAction` subclasses, not the core.
- Frontend chat UI internals → `jvchat/` reference client (built bundle is served by `jvagent chat`; the Python side is just a static server — see [`docs/jvchat.md`](docs/jvchat.md)).

---

## 11. If you only read 3 files...

1. [`.planning/SPEC.md`](.planning/SPEC.md) — what jvagent guarantees.
2. The local `CLAUDE.md` for the subsystem you're touching.
3. [`.planning/reference/action-authoring.md`](.planning/reference/action-authoring.md) — if you're adding behavior.

Everything else is reachable from those.
