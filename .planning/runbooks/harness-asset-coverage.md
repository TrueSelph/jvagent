# Tracked harness asset coverage

The historical inventory covered package declarations. The
[reconciliation](../plans/2026-11-06-harness-asset-reconciliation.json) adds the
omitted app-local and fixture scope; the
[current snapshot](../plans/2026-11-06-harness-tracked-assets.json) covers every
Git-tracked SKILL.md, info.yaml and agent.yaml in this repository.

Check source coverage before using the inventory for migration or deletion:

```bash
python3 scripts/harness_asset_inventory.py \
  --check .planning/plans/2026-11-06-harness-tracked-assets.json
```

After an intentional asset/companion change, stage the owned files first, then
regenerate and review the snapshot diff:

```bash
python3 scripts/harness_asset_inventory.py \
  > .planning/plans/2026-11-06-harness-tracked-assets.json
```

Git enumeration includes ignored-but-tracked examples and test fixtures. The
scanner reads bounded metadata, reports malformed assets instead of dropping
them, records BOM/legacy key spelling, refuses symlink escapes and binds tracked
directory companions with digests. It imports no discovered module, resolves no
credential placeholders and never starts an app, model, tool or database.
Sources: `scripts/harness_asset_inventory.py:42`, `:91`, `:141`.
The local pre-commit hook and repository test check drift; adding a declaration
without reviewing the snapshot fails the gate. Sources:
`.pre-commit-config.yaml:64`, `tests/test_harness_asset_inventory.py:1`.

This gate proves source coverage only. Metadata extraction is not SDK admission,
tool permission, import/caller tracing, compatibility qualification or approval to
delete anything. Directory digests can overlap for embedded Action SOPs and their
children; they are content bindings, not a dependency graph. Untracked/private
deployment roots, installed external plugins and generated assets need separate
inventory. See [qualification and remaining gates](../plans/2026-11-04-harness-qualification-matrix.md).
