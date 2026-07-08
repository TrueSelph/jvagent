# Action modularization audit snapshot

Generated baseline for the action modularization refactor (2026-07). Not hand-maintained;
re-run `tests/action/test_action_import_guard.py` on CI.

## Resolved smells (post-refactor)

| Smell | Resolution |
|-------|------------|
| `meta_webhook_verify` / `meta_webhook_dedup` in `action/utils/` | Canonical home: `whatsapp/utils/` |
| `OAuthState` in `action/utils/oauth_state.py` | `action/oauth/state.py` |
| Facebook → `whatsapp.webhook_auth` | `facebook_action/webhook_auth.py` + shared factory |
| Email/Facebook → `whatsapp.utils.endpoint_helpers` | `interact/webhook_pipeline.py` |
| Private `_finalize_usage` / `_build_interaction_log_data` | Public in `interact/webhook_pipeline.py` |
| 5× duplicate `webhook_auth` bodies | `webhook_system_user_factory` in `utils/webhook_system_user.py` |
| Interview → orchestrator `TaskLockPrep` | `skill_spec/task_lock.py` |
| Excel → Google Sheets range helpers | `spreadsheet/range_utils.py` |
| Handoff → `WhatsAppAction` import | `handoff_notify_action_type` + `get_action_by_type` |
| Leadgen → `_normalize_call_result` | Public `mcp_action.normalize_call_result` |

## Correctly placed shared utils (unchanged)

| Module | Consumers |
|--------|-----------|
| `utils/endpoint_helpers.py` | 12+ endpoint handlers |
| `utils/meta_webhook.py` | WhatsApp + Facebook Messenger HMAC |
| `utils/webhook_system_user.py` | All channel webhook_auth modules |
| `utils/webhook_reconcile.py` | HeyGen + multi-consumer reconcile |

## Upload ownership

- `interact/utils/uploads.py` — canonical upload parsing.
- `orchestrator/uploads.py` — orchestrator ingestion reflex; imports interact helpers.
