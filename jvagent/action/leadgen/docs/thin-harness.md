# Leadgen thin harness profile

## Thick (skill)

- When to capture vs retrieve
- Gap-fill tone and batching
- Domain decline values and qualification rules
- Custom hooks in `custom_tools.py`

## Thin (foundation)

- Field validation and alias canonicalization
- `LeadRecord` persistence
- Auto-sync threshold evaluation and MCP dispatch
- Digest dedup
- Never classify user intent in action code

## Invariants

1. Model owns extraction timing; server owns validation and sync.
2. Hook functions are not LLM tools unless listed in `skill_tools`.
3. Do not expose `sync_result` or raw YAML to users.
4. `on_capture` mode: model must not call `leadgen__sync`.

See also [platform thin harness](../../../docs/thin-harness.md).
