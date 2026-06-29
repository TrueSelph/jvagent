# ADR 0028 — Defer Meta WhatsApp Groups API

**Status**: Accepted
**Date**: 2026-06-26
**Supersedes**: N/A
**Related**: Meta Cloud API provider (`provider: meta`), wwebjs bridge groups

---

## 1. Context

jvagent supports WhatsApp groups via bridge providers (wwebjs, wppconnect, etc.): `@g.us` IDs, `is_directed_message` @-mention routing, `group_members()`, and outbound `is_group=True`.

Meta’s **Official Groups API** (2026) is a different product surface:

- Requires **Official Business Account (OBA)** eligibility
- Invite-only groups, **max 8 participants**
- `group_id` identifiers (not `@g.us`)
- Separate webhook fields (`group_lifecycle_update`, `group_participants_update`, etc.)
- Outbound uses `recipient_type: group` + `group_id`

1:1 Meta Cloud API parity (text, media, voice, typing, webhook hardening with wamid dedup) is implemented. Groups would multiply webhook traffic and retry surface before 1:1 is validated in production.

---

## 2. Decision

**Defer Meta Groups support** until all of the following are true:

1. **1:1 production stable** — desk8800 (or target deployment) validated for ~1 week: media, voice/STT/TTS, single reply per message, duplicate webhook ignored.
2. **WABA eligibility confirmed** — OBA status and Meta Groups API access on the target WABA.
3. **Dedicated implementation phase** — not treated as wwebjs parity; requires its own design and tests.

Until then, `meta_api` **rejects** `is_group=True` on all send paths and inbound parse sets `isGroup=False`. Dashboard should subscribe only the **`messages`** field on the agent callback URL.

---

## 3. Consequences

### Positive

- Avoids building a half-working groups layer on incompatible IDs and webhooks
- Webhook idempotency and 1:1 paths can stabilize first
- Clear product boundary for persona capabilities (`get_capabilities()` omits groups for meta)

### Negative

- Meta provider cannot replace wwebjs for group bots until a future phase
- Users on Cloud API only cannot use existing group-oriented agent skills

---

## 4. Revisit criteria

When revisiting, produce a follow-up ADR or amend via new ADR covering:

| Area | Work |
|------|------|
| Inbound | Parse group webhooks; `isGroup=True`; Meta mention model |
| Routing | `is_directed_message` without `group_members()` bridge call |
| Outbound | `recipient_type: group`, `group_id` on Messages API |
| Webhooks | Subscribe group lifecycle/participant fields (separate handler or extended parser) |
| Dedup | Group message wamids in same dedup cache |

---

## 5. References

- [`jvagent/action/whatsapp/modules/meta_api.py`](../../jvagent/action/whatsapp/modules/meta_api.py) — group send blocks
- [`jvagent/action/whatsapp/utils/endpoint_helpers.py`](../../jvagent/action/whatsapp/utils/endpoint_helpers.py) — `is_directed_message`
- [`jvagent/action/whatsapp/README.md`](../../jvagent/action/whatsapp/README.md) — Meta webhook subscription guidance
