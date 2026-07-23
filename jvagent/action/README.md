# jvagent.action — package index

Action plugins live under `jvagent/action/{name}/`. Each package owns its Action class,
`info.yaml`, endpoints, and action-local `utils/`.

## Shared modules (import these across action families)

| Module | Purpose |
|--------|---------|
| [`utils/`](utils/) | Multi-consumer helpers: typed endpoint loader, Meta HMAC verify, webhook system user, reconcile |
| [`interact/`](interact/) | InteractAction base, walker, channel-neutral [`webhook_pipeline`](interact/webhook_pipeline.py) |
| [`oauth/`](oauth/) | OAuth state node, token crypto, audit logging |
| [`spreadsheet/`](spreadsheet/) | Vendor-neutral A1 range helpers (Sheets + Excel) |
| [`skill_spec/`](skill_spec/) | Skill contracts, registries, shared task-lock types |
| [`model/`](model/) | Language model actions and context |
| [`manifest`](manifest.py), [`parameters`](parameters.py) | App/action configuration |

## Modularization contract

See [`.planning/reference/action-authoring.md` §15](../../.planning/reference/action-authoring.md)
and [action-modularization-audit.md](../../.planning/reference/action-modularization-audit.md).

**Rules (summary):**

1. No imports from sibling action packages except through the shared modules above.
2. Code moves to a shared path only when **2+ unrelated action families** consume it.
3. No persisted `Node`/`Object` types under `action/utils/`.
4. One canonical import path — no re-export shims.
5. Channel webhook finalization uses `interact.webhook_pipeline` (public API).

## Upload ownership

- **`interact/utils/uploads.py`** — upload parsing constants and helpers (canonical).
- **`orchestrator/uploads.py`** — orchestrator ingestion reflex only; imports from interact.
