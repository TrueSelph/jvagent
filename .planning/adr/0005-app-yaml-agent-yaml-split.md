# ADR 0005 — `app.yaml` / `agent.yaml` split with run/merge/source update modes

**Status**: Accepted
**Date**: pre-2026

## Context

A jvagent deployment has two scopes of declarative configuration:

1. **App-level**: identity (`app_id`, `name`), file storage backend, database backend, server host/port, logging retention.
2. **Agent-level**: per-agent action list, action-specific `context:` overrides, interaction limit, channel adapters.

Operators may want to:

- Add a new agent without redeploying — `agent.yaml` edit + `--update`.
- Reset an agent to YAML truth after manual experimentation — destructive sync.
- Cold-start fresh — no sync, just load persisted graph state.

## Decision

### File split

- `app.yaml` at the app root holds app-level config.
- `agent.yaml` under `agents/{namespace}/{agent_name}/agent.yaml` holds per-agent config.

The two are validated by separate validators (`core/app_yaml_validator.py`, `core/agent_yaml_validator.py`).

### Update modes

`App.update_mode` ([`core/app.py:74`](../../jvagent/core/app.py)) is a persisted string with three values:

| Mode | Meaning |
|---|---|
| `run` | Do **not** re-sync YAML on start. Use the persisted graph as-is. |
| `merge` | Apply a **narrow** merge from `app.yaml` (only `version` + `app_id` on the App node) on start. Per-agent action install/upgrade still runs. Existing App-level fields and non-listed graph state are untouched. ([`app_loader.py:308-317`](../../jvagent/core/app_loader.py).) |
| `source` | **Destructive**. YAML is the source of truth; conflicting graph nodes are reset. |

CLI flags **override** the persisted value for that process only:

- `--update` (without `--merge` / `--source`) → `merge`
- `--update --merge` → `merge`
- `--update --source` → `source`
- (no flag) → use persisted `App.update_mode`

`--source` and `--merge` REQUIRE `--update` and are **mutually exclusive** ([`cli/main.py:167-172`](../../jvagent/cli/main.py)).

After a successful sync (run or bootstrap), `App.update_mode` is reset to `run` so cold restarts do not repeat the operation. Source: implementation in `cli/commands.py`.

## Consequences

### Positive
- **Operator control without redeploy** — flip update_mode via `PUT /api/app/update_mode` (admin-only), then restart.
- **Safety by default** — `run` skips YAML, so a stale or removed `app.yaml` does not destroy a running graph.
- **One-shot semantics** — the reset-to-`run` policy prevents one-time operations from re-firing across cold starts.
- **Validators run before mutation** — a malformed `agent.yaml` is caught by `agent_yaml_validator.py` before any merge.

### Negative
- **Three modes is more surface than two.** Operators must learn the distinction between merge and source.
- **Reset behavior is implicit.** Easy to assume `App.update_mode` stays as set.
- **No dry-run for `source`.** Use `jvagent /path validate` first to catch schema issues.

## Precedence (config resolution)

Highest to lowest:

1. CLI flag (`--update`, `--source`, `--merge`)
2. Environment variable
3. `app.yaml`
4. `agent.yaml`
5. Action `attribute(default=...)` in Python

Source: [`core/config.py:59-150`](../../jvagent/core/config.py).

## Alternatives considered

1. **Single config file with sections.** Rejected: invites god-file growth; per-agent edits become coordination problems.
2. **Two modes only (run / sync)**. Rejected: destructive vs non-destructive is a load-bearing distinction.
3. **No persisted mode (CLI flag only)**. Rejected: operators wanted to flip mode without restarting from a controlled shell.

## References

- [`SPEC.md`](../SPEC.md) §6
- [`configuration-keys.md`](../reference/configuration-keys.md)
- [`docs/configuration.md`](../../docs/configuration.md)
- [`docs/scaffolding.md`](../../docs/scaffolding.md)
