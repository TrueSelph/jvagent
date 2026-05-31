# .planning — agent-facing design docs

Reference material for **AI agents and human contributors** working on jvagent.
The repo-root [`CLAUDE.md`](../CLAUDE.md) is the agent entry point; this folder
holds the deeper normative specs, reference guides, runbooks, and decision
records it links into. User-facing onboarding lives in the root
[`README.md`](../README.md).

## Layout

```
.planning/
  PROJECT.md        big-picture overview
  SPEC.md           normative semantics (invariants, contracts)
  PATTERNS.md       deployment patterns (Rails vs. Orchestrator)
  architecture.md   diagrams (boot, interact, executive, pruning)
  GLOSSARY.md       canonical terminology
  reference/        look-it-up guides (authoring, catalog, config, internals)
  runbooks/         step-by-step operator/dev procedures
  adr/              architecture decision records (immutable once accepted)
  archive/          superseded / shipped-and-historical docs
```

## Read first

| You want to… | Read |
|---|---|
| Get the big picture | [`PROJECT.md`](PROJECT.md) |
| Look up normative semantics | [`SPEC.md`](SPEC.md) |
| Choose a deployment pattern | [`PATTERNS.md`](PATTERNS.md) |
| See diagrams | [`architecture.md`](architecture.md) |
| Define a term | [`GLOSSARY.md`](GLOSSARY.md) |

## reference/ — guides

| Topic | Doc |
|---|---|
| Build a new action (the contract) | [`reference/action-authoring.md`](reference/action-authoring.md) |
| Inventory of every shipped action | [`reference/actions-catalog.md`](reference/actions-catalog.md) |
| Every config key + precedence | [`reference/configuration-keys.md`](reference/configuration-keys.md) |
| Logging, metrics, observability | [`reference/observability.md`](reference/observability.md) |
| Memory model + rolling-window pruning | [`reference/memory-and-pruning.md`](reference/memory-and-pruning.md) |
| The jvspatial dependency boundary | [`reference/jvspatial-integration.md`](reference/jvspatial-integration.md) |

## runbooks/ — procedures

| Task | Doc |
|---|---|
| Run jvagent locally | [`runbooks/local-dev.md`](runbooks/local-dev.md) |
| Add a new action end-to-end | [`runbooks/add-action.md`](runbooks/add-action.md) |

## adr/ — decision records

ADRs are immutable once accepted; to change a decision, write a new ADR that
supersedes the old one. Numbering gaps (0002, 0007–0009) are intentional —
those records covered patterns (bridge/helm/cockpit) that were removed.

| ADR | Decision | Status |
|---|---|---|
| [0001](adr/0001-graph-based-state.md) | Graph-based state over relational schema | Accepted |
| [0003](adr/0003-interaction-limit-pruning.md) | Rolling-window interaction pruning with per-call cap | Accepted |
| [0004](adr/0004-namespace-isolation.md) | Namespace-isolated action plugins | Accepted |
| [0005](adr/0005-app-yaml-agent-yaml-split.md) | `app.yaml` / `agent.yaml` split + update modes | Accepted |
| [0006](adr/0006-jvspatial-dependency.md) | Build jvagent on a separate jvspatial framework | Accepted |
| [0010](adr/0010-executive-centers-architecture.md) | Executive + Centers architecture | **Superseded by 0012** |
| [0011](adr/0011-skills-two-kinds.md) | Two skill kinds: native SOP overlays vs. Claude bundles | Accepted (extended by 0017) |
| [0012](adr/0012-skill-executive-architecture.md) | **Orchestrator architecture** (the v1 orchestrator) | Accepted |
| [0013](adr/0013-togglable-deterministic-turn-lock.md) | Togglable deterministic turn-lock (`lock_active_flow`) | Accepted |
| [0014](adr/0014-identity-on-agent-replyaction-egress.md) | Identity on the Agent + ReplyAction egress | Accepted |
| [0015](adr/0015-skill-executive-configuration-surface.md) | Orchestrator configuration surface | Accepted |
| [0016](adr/0016-model-gearing-light-heavy.md) | Model gearing: light completion + heavy reasoning | Accepted |
| [0017](adr/0017-two-skill-specs-code-execution-substrate.md) | Two skill specs (JV + Claude) + multitenant code-execution substrate | Accepted |
| [0018](adr/0018-lean-tool-surfacing.md) | Lean tool surfacing (threshold-auto progressive tool disclosure) | Accepted |

## archive/ — historical

Retained for context, not current guidance.

- [`archive/EXECUTIVE-ROADMAP.md`](archive/EXECUTIVE-ROADMAP.md) — the Orchestrator roadmap; its "Done" list shipped as v1.
- [`archive/executive-build-prompt.md`](archive/executive-build-prompt.md) — the ADR-0010 "Executive + Centers" build prompt, superseded by ADR-0012 / [`docs/ORCHESTRATOR.md`](../docs/ORCHESTRATOR.md).
