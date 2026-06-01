# ADR 0021 — Conversation-scoped artifact memory (branch node) + Orchestrator vision input

**Status**: Proposed (design approved; implementation pending)
**Date**: 2026-06-01
**Relation**: Restores vision (lost when PersonaAction was replaced — ADR-0012/0014) for the orchestrator turn, and establishes a **general conversation-scoped artifact memory** as the substrate. Extends the deterministic pre-loop stage (ADR-0013 continuation check) with a vision reflex. Removes legacy remnants (`Interaction.image_interpretation`, the dormant `Interaction.artifacts` dict, PersonaAction's vision-storage code).

---

## 1. Context

The orchestrator has **no vision path** (`orchestrator_interact_action.py` never
reads images), so an image upload is silently dropped. The reusable machinery
survives the PersonaAction removal — `interact/utils/vision_prompt.py`
(`build_prompt_for_vision`, `generate_image_interpretation`), the canonical
ingress `visitor.data["image_urls"]` (`interact/README.md:282` → `endpoints.py:609`
→ walker), and the suppression key `visitor.data["image_interpretation"] = False`
(set by the interview classifier, `classification_handler.py:242` — a **data
key**, unrelated to the Interaction field below).

But the storage side is **legacy bloat that must not be inherited**:

- `Interaction.image_interpretation` (`interaction.py:108`) — a single per-turn
  string written only by **PersonaAction** (`persona_action.py:407,1280`) and
  read back for follow-ups (`919–931`). PersonaAction is **unwired** in the live
  orchestrator path (only a docstring example references it, `agent.py:155`).
- `Interaction.artifacts` (`interaction.py:166`) — a dict "Orchestrator
  artifacts" that is **defined but never read or written anywhere**. A
  speculative remnant.

Per the cleanup mandate: don't carry either forward. Design the artifact memory
properly, graph-idiomatically, and queryable.

## 2. Decision

Two co-designed parts: **(A) a conversation-scoped artifact memory as a branch
node**, and **(B) vision as its first producer** via an orchestrator reflex +
tool.

### 2.A Artifact memory — Conversation registry + interaction association

Artifacts are **not** a dict on `Interaction`. A dedicated `Artifacts` branch/
registry node hangs off the `Conversation` (single, queryable collection), and
each `Artifact` is **associated** to the Interaction(s) that produced/referenced
it by a separate edge — separating *where it is stored & queried* from *what
produced it*:

```
Conversation ──CONTAINS──▶ Artifacts (branch node, one per conversation, lazy)
                              └─CONTAINS──▶ Artifact*        (registry membership; root-reachable, I-GRAPH-01)
Interaction  ──PRODUCED───▶ Artifact*                       (associative edge: provenance + lifecycle hook; many-to-many)
```

`Artifact` node fields: `name` (handle), `data` (full text/payload), `summary`
(short, for the index), `tags: List[str]`, `source` (e.g. `"vision"`),
`kind`/`mime`, `pinned: bool` (durability opt-out, default `False`),
`created_at`/`updated_at`. Provenance lives on the `PRODUCED` **edge** (which
interaction, when), not as a scalar field — the object-spatial idiom (semantics
on edges).

Why this shape (registry + association, not a dict, not interaction-owned):

- **Queryable in one traversal.** `Artifacts.nodes(edge=[CONTAINS], node=["Artifact"])`
  filters by `source`/`tags`/`name` without scanning interaction history — branch
  node for organization.
- **Lifecycle bound to interactions (bounded by default).** The `PRODUCED` edge
  is the pruning hook: pruning is **refcounted cascade** (§2.A.1), so artifacts
  are reaped with their interactions — no separate artifact-pruning system, no
  unbounded registry.
- **Flexible.** Many-to-many association (a re-referenced image's artifact links
  several interactions without duplication); `pinned` artifacts survive pruning
  for the rare must-keep.
- **General.** Vision is the first producer; future producers (file analyses,
  web-fetch extracts, computed summaries) write the same way.

#### 2.A.1 Pruning — refcounted cascade (default on)

When the rolling window prunes interaction *X* (`interaction_limit`):
for each `Artifact` *X* `PRODUCED`, drop the `PRODUCED` edge; then **delete the
`Artifact`** (and its `CONTAINS` membership) **iff no other live interaction
still `PRODUCED` it and it is not `pinned`**. So:

- single-interaction artifacts are reaped exactly when their interaction is
  (same effective bound as owning them outright);
- shared artifacts survive until their **last** associating interaction is
  pruned (refcount = 0);
- `pinned: true` exempts an artifact (durability opt-out).

This runs inside the existing pruning path (`conversation.py`), gated by a
`prune_artifacts_with_interaction` flag (**default True**).

### 2.B Access / query mechanism (the "queryable in history" provision)

1. **Artifact index in assembled context.** The orchestrator surfaces a compact
   index of the conversation's artifacts — `{name, source, tags, summary,
   created_at}` only, **not** the full `data` — so the model knows what exists
   and can reference it.
2. **Tools** (core surface): `list_artifacts(filter?)` (search by source/tag/
   text → index rows) and `get_artifact(name)` (fetch full `data`) so the model
   can **back-reference** on demand without the payload bloating every prompt.
3. **Write helper:** `Conversation.add_artifact(interaction, …)` lazily creates
   the `Artifacts` branch, creates the `Artifact` (membership `CONTAINS`), and
   wires the `Interaction ──PRODUCED──▶ Artifact` association — returning the
   `Artifact`. Re-referencing adds a `PRODUCED` edge from another interaction
   rather than duplicating.

### 2.C Vision producer — reflex + tool, VisionAction-owned model

- **`jvagent/vision` `VisionAction`** is a standard model-bearing action with its
  **own** multimodal model config (`model_action_type`/`model`/`model_temperature`/
  `model_max_tokens`) — decoupled from the reasoning model. Exposes
  `describe(visitor) -> str` (runs `generate_image_interpretation` on its own
  model) and `get_tools() → interpret_images`.
- **Pre-loop reflex (automatic).** When `visitor.data["image_urls"]` is present
  and not suppressed and the turn has no vision artifact yet: run
  `VisionAction.describe`, **write an `Artifact`** via
  `Conversation.add_artifact(interaction, source="vision", …)` (registry node +
  `PRODUCED` edge from the current interaction), and seed a compact note into the
  loop so this turn's response composes with the image context.
- **`interpret_images` tool (on-demand).** Re-interpret current images, or images
  referenced by a prior artifact handle.
- **Follow-up turns.** The artifact index (in context) shows the vision
  artifact; the model `get_artifact`s it (or the orchestrator auto-injects the
  most recent vision summary) — no re-upload.
- **Gate:** `vision: bool` on the orchestrator, default **False** (no VisionAction
  assembly, no reflex, zero cost). Wired off in example agent + scaffold.

## 3. Remnants removed (do not inherit)

- **Delete** `Interaction.image_interpretation` (`interaction.py:108`).
- **Delete** the dormant `Interaction.artifacts` dict (`interaction.py:166`) —
  replaced by the `Artifacts` branch node.
- **Excise** PersonaAction's vision-storage paths (`persona_action.py:389–407`,
  `919–931`, `1262–1280`) and its `vision_model_*` config (dead once storage is
  gone); update/remove `tests/action/test_persona_vision_model.py`.
- The reusable `vision_prompt.py` helpers **stay** (VisionAction reuses them);
  `generate_image_interpretation`'s `calling_action_name` label updated.
- The suppression **data key** (`visitor.data["image_interpretation"]=False`)
  **stays** — it's the interview's vision opt-out, unrelated to the deleted field.
- **Flag (separate cleanup):** PersonaAction itself appears fully unwired; full
  removal is a candidate for its own follow-up, out of scope here.

## 4. Flow (end to end)

```
client → data.image_urls → interact_endpoint → InteractWalker(data) → visitor.data
orchestrator turn ─ pre-loop: image_urls present & not suppressed? ─▶ VisionAction.describe()  (own model)
        └─▶ Conversation.add_artifact(interaction, source="vision", data=…, summary=…)
                 ├─ Artifacts ──CONTAINS──▶ Artifact          (registry, queryable)
                 └─ Interaction ──PRODUCED──▶ Artifact         (provenance + lifecycle)
        └─▶ seed compact note ─▶ think-act-observe loop ─▶ response uses interpretation
later turn ─ artifact index in context ─▶ model calls get_artifact(name) ─▶ back-reference (no re-upload)
on-demand ─ model calls interpret_images / list_artifacts
pruning ─ interaction_limit reaps interaction X ─▶ drop PRODUCED edges ─▶ delete Artifact iff refcount 0 & not pinned
```

## 5. Implementation surface (for the build)

1. `memory`: new `Artifact` node + `Artifacts` branch node + `PRODUCED` edge;
   `Conversation.add_artifact(interaction, …)` / `get_artifacts(filter)`
   (traversal); refcounted cascade prune in the existing `conversation.py`
   pruning path (`prune_artifacts_with_interaction`, default True), respecting
   `pinned`. **Remove** `Interaction.image_interpretation` + `Interaction.artifacts`.
2. `jvagent/vision/vision_action.py`: `VisionAction` (own model), `describe()`,
   `get_tools()→interpret_images`; reuses `vision_prompt`.
3. Orchestrator: gated pre-loop vision hook (detect → describe → add_artifact →
   seed note); artifact-index surfacing in context; `vision` attribute.
4. Core tools: `list_artifacts` / `get_artifact` (model back-reference).
5. PersonaAction excision + test updates (§3).
6. Config + example/scaffold (gated off); client check that `data.image_urls` is
   populated (jvchat attachment mapping).
7. Tests: artifact registry CRUD + traversal/filter; **refcounted cascade**
   (single-interaction artifact reaped with its interaction; shared artifact
   survives until last producer pruned; `pinned` survives); reflex
   fires/suppresses; vision Artifact written (no `image_interpretation`); index
   surfaced; `get_artifact`/`list_artifacts`/`interpret_images`; gate off = inert.
8. Docs: `actions-catalog.md`, orchestrator artifact+vision section, memory
   README rewrite, CHANGELOG; scrub `image_interpretation` field mentions.

## 6. Consequences

**Positive**

- Artifacts become a first-class, **queryable** conversation memory (single
  registry branch node) the model fetches on demand instead of bloating every
  prompt — **bounded by default** (refcounted cascade), with a `pinned` opt-out
  for the rare must-keep.
- The `PRODUCED` edge cleanly separates storage/query from provenance/lifecycle
  and supports many-to-many association (re-reference without duplication).
- Vision is orchestrator-native, reusing audited helpers; the vision model is
  independently configurable.
- Two legacy remnants and an orphaned persona code path are deleted, not carried.

**Negative / risks**

- The refcounted cascade is **not** an automatic subgraph delete (the registry
  holds the node) — it is explicit pruning logic in `conversation.py`. This is
  the deliberate cost of the registry's queryability over interaction-owned
  artifacts; covered by tests.
- New nodes + edges + a memory migration (drop two Interaction fields) — needs a
  dev/staging DB reset or a light migration; acceptable pre-1.0.
- The pre-loop reflex adds a model call on image turns (bounded by the gate +
  "only when images present & unseen").
- Index-in-context must stay compact (summaries only) to avoid prompt growth;
  full data only via `get_artifact`.
- Large base64 images inflate the request; size/format limits are a follow-up.

## 7. Alternatives considered

- **Dict on `Interaction` / `Conversation`** (the dormant field): not
  traversal-queryable, no per-artifact identity or edges, not the object-spatial
  idiom. Rejected.
- **Branch node under each `Interaction`** (`Interaction ──CONTAINS──▶ Artifacts ──▶ Artifact`):
  pruning is free (subgraph cascade), but there is no single registry to query
  (must scan every windowed interaction) and no many-to-many. Rejected for the
  registry's queryability.
- **`Interaction ──▶ Artifact` direct (no registry)**: simplest, free cascade —
  but same loss of a queryable collection and shared artifacts. Rejected; its
  cascade behavior is preserved as the *default* of the chosen model.
- **Registry with no pruning (durable forever)**: unbounded growth — the bloat
  we are avoiding. Rejected in favor of refcounted cascade default + `pinned`.
- **Keep `image_interpretation`**: redundant with artifacts; legacy. Removed.
- **Model-invoked tool only / heavy model for vision**: kept reflex+tool and
  VisionAction-owned model.

## 8. Out of scope

Image generation/editing; audio/video; channel media fetching (already populate
`image_urls`); full PersonaAction removal (separate); the public-endpoint auth
work (ADR-0020).
