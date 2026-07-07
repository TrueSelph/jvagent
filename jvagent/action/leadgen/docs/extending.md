# Extending leadgen skills

## Two-file package

```
agents/<ns>/<agent>/skills/<skill_name>/
├── SKILL.md
└── scripts/
    └── custom_tools.py
```

## Hooks (not LLM tools)

Declare in `leadgen.handlers`:

| Handler | When | Signature |
|---------|------|-----------|
| `post_capture` | After validation, before save | `def fn(ctx) -> ctx` |
| `qualify` | Before save/sync | `def fn(ctx) -> ctx` — call `ctx.block_sync(reason)` to skip sync |
| `on_sync` | After auto-sync | `def fn(ctx) -> ctx` |

`ctx` provides: `spec`, `record`, `profile_data`, `fields`, `user`, `visitor`, `extra`.

## LLM-callable skill tools

Declare under `leadgen.skill_tools` and add to additive `allowed-tools`:

```yaml
skill_tools:
  - name: lookup_company
    function: lookup_company
    description: Enrich organization from domain
allowed-tools:
  - my_leads__lookup_company
```

## Scaffold

```bash
jvagent skill create-leadgen <namespace/agent> <skill_name>
```
