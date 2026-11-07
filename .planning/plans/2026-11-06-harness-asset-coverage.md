# Source asset inventory reconciliation

## Confirmed coverage gap

The original name inventory listed 58 package Action manifests and 22 package
skill/SOP assets at baseline `7d689e4d`. Git's tracked baseline actually contains
59 Action manifests and 36 SKILL.md files. The omissions are the app-local
Storefront mock Action, twelve app-local skills and two interview test fixtures.
They are named, with proposed dispositions, in
[the reconciliation](2026-11-06-harness-asset-reconciliation.json).
The old arrays remain historical records; they no longer stand in for full
repository coverage. Source: the original inventory's `actions`/`skills` arrays,
compared with `git ls-tree -r --name-only 7d689e4d`.

The current [tracked snapshot](2026-11-06-harness-tracked-assets.json) covers:

| Scope | SKILL.md | Action manifests | Agent declarations |
|---|---:|---:|---:|
| Package, including Action SOPs/examples | 24 | 60 | 0 |
| App examples | 12 | 1 | 2 |
| Test fixtures | 2 | 0 | 0 |
| Total | 38 | 61 | 2 |

All baseline skill paths are accounted for after the three documented path
migrations. Native interview and delegation skills are new tracked assets. The
Action delta includes native_harness/pageindex_read and the current HandoffAction;
the baseline HandoffInteractAction path is absent. This audit does not infer that
the two handoff contracts are equivalent, approve that earlier change, or revert
pre-existing work. Handoff consumer/state compatibility remains a named review
obligation. Source delta: `git diff --name-status -M 7d689e4d HEAD -- '**/SKILL.md' '**/info.yaml'`.

## Implemented guard

`scripts/harness_asset_inventory.py:91` discovers tracked declarations with Git,
including paths hidden by ignore-filtered searches. It records source names,
literal policy spelling, declaration setting names and content digests without
resolving values or importing discovered code. Agent credential values are not
copied into the report. Tracked directory digests bind companion scripts,
resources and extension modules. Symlink escapes/oversized metadata refuse the
scan; malformed frontmatter remains a named record requiring review.
Sources: `scripts/harness_asset_inventory.py:42`, `:63`, `:91`.

The new local hook and source-conformance test refuse inventory drift. A source
change requires regeneration and review rather than silently inheriting an old
qualification label. See [maintainer commands](../runbooks/harness-asset-coverage.md)
and `tests/test_harness_asset_inventory.py:1`. No app execution, provider protocol,
tool surface, persistence schema or runtime dependency changes in this increment.

## Verification and remaining work

Eleven Git/source regressions cover ignored-but-tracked app assets,
companion drift without module execution, BOM/underscore preservation, invalid
policy shape, root-level skills, credential-value omission, malformed frontmatter/action names,
symlink refusal, full-root scanning from a subdirectory, and changed/new asset
drift. The final test also compares the actual repository to the reviewed
snapshot. Source: `tests/test_harness_asset_inventory.py:1`.

The read-only checker runs against the live source. Required all-files and
repository push gates remain publication checks. This is developer/source-ledger
work, not a runtime implementation round: no new browser/model behavior is
claimed or requalified. The prior real-provider DOCX/restart cohort remains bound
to its tested runtime blobs at parent `f0c2e222`; all nine remote checks on that
parent pass. See [that acceptance record](2026-11-06-native-docx.md).

Complete tracked coverage does not close runtime compatibility, import/dependency
ownership or deletion gates. App-local skill extensions, Storefront/LeadGen
consumers, the handoff delta and remaining reference settings must now be reviewed
explicitly in the cutover ledger. Untracked/private/external deployment assets
remain outside this snapshot. The reference configuration still has ten known
migration blockers; this audit neither removes them nor authorizes a subset
cutover. The full goal remains active.
