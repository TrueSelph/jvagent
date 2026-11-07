# Default vision reflex retirement

Parent revision: `3c16e433`. The [machine record](2026-11-06-native-vision-migration-smoke.json)
binds source epochs, a regenerated configuration candidate, original PNG hashes,
request results and spend. [Browser observations](2026-11-06-native-vision-migration-browser.json)
retain the upload, answers, cold readback and sanitized opt-out API result.

## Migration decision and boundaries

The reviewed default `vision: true` profile can now propose native image input
on the primary GPT-4.1 model instead of another interpreter. The candidate removes
VisionAction and its flag, declares the four admitted image MIME types, and
retains other declarations. It does not reproduce the independent model choice,
`interpret_images` tool, remote downloads or generated-description artifacts.
Original image content is retained through official SDK media history. These
retirements are explicit in the report, not silent compatibility claims. Sources:
`jvagent/cli/harness_migration_vision.py:37`,
`jvagent/cli/harness_migrate.py:634`, `jvagent/harness/inputs.py:61`.

The profile admits one default OpenAI VisionAction using gpt-4o/gpt-4.1 and a
primary GPT-4.1 OpenAI declaration. Unknown callers, duplicate/missing services,
disabled/invalid declarations, custom prompts, independent limits/credentials and
other models/providers remain blocked. Reviewed consumer names are a bounded
source-review list, not authority for unknown plugin code. Selected skills that
depend on VisionAction or its old tool, including wildcard matches, require
explicit contract changes. Malformed dependency declarations refuse rather than
being ignored. Sources: `harness_migration_vision.py:8`, `:55` and
`jvagent/cli/harness_migration_skills.py:195`.

Only legacy Orchestrator upload execution directly calls VisionAction in the
reviewed built-in source (`jvagent/action/orchestrator/uploads.py:59`, `:222`).
The on-demand tool is intentionally retired from the candidate; the package and
legacy callers remain until whole-app cutover/deletion. The migration function
does not import or execute VisionAction, connect to the graph or change source
configuration. Both isolated VisionAction removal and joint model-service removal
have candidate/count regressions. This does not authorize retained-state cutover.

The existing per-request `image_interpretation: false` opt-out refuses the complete
current image-bearing request before SDK registration/model work. It does not
silently drop an attachment. Text-only input remains usable, subject to its host
MIME policy. The host publishes a fixed notice with reason `invalid_input`; a new
conversation is required after rejection. This is current-input admission, not
erasure of previously admitted images/history. Sources:
`jvagent/harness/inputs.py:43`, `:100`,
`jvagent/action/native_harness/native_harness_action.py:240`.

No execution engine, provider protocol, schema field, dependency or database is
added. Native providers and official Harness still own model execution and
persistence. JV supplies envelope admission and an offline declaration migration.

## Actual browser and model evidence

A fresh PostgreSQL app on 8176 is generated from a legacy declaration with the
default vision reflex. Regenerating the candidate with final migration code gives
the exact running declaration. jvchat on 3140 submits a browser-created synthetic
PNG through its real file-input event and composer. The OS file picker is not
qualified by this test.

The actual GPT-4.1 reply identifies the yellow background and VECTOR-73 code in
one request. A separate authenticated browser-origin API request carries the same
PNG with interpretation disabled; its response and persisted notice confirm zero
model attempts, no SDK run and no spend. That negative probe is not presented as
a customer-facing composer control. Its session credential is omitted from the
committed evidence.

The first worker is confirmed terminal before restart. Both existing Interaction
and Conversation records remain exact. A follow-up asks for the smaller label,
absent from the first answer, and the model reads DOCK 28 in one request. Both SDK
histories retain the exact original 15,048-byte PNG by SHA-256. Final transcript
reload restores the attachment, replies and usage without changing any of the
three Interaction or Conversation records.

All three requests close: two delivered model replies and one expected blocked
input. Two SDK requests reconcile to two independent HTTP POST observations,
1,539 provider-reported tokens and USD 0.003192 estimated cost. Actual provider
dollar billing is unavailable. Conversation journals reconcile exactly. No tool
effect, domain task, VisionAction instance or legacy artifact is created. No
runtime function is patched; source observations exclude legacy execution modules.
These are functional observations, not a measured optimization comparison.

## Verification and remaining work

The affected native-input/Action/migration/state slice passes 272 cases per Python
3.11/3.12; the restored 3.11 run passes again after mutation checks. The positive
migration test fails before implementation. Five opt-out regressions fail before
input enforcement. Removing dependency checks makes all four selected-skill cases
fail; removing opt-out enforcement makes all five image-key cases fail. Production
source is restored. Required all-file hooks and normal full-suite push remain
publication gates.

The [reference assessment](2026-11-06-native-vision-reference-blockers.json) now has
four blockers: reviewed script image/resource policy, channel overrides,
active-flow policy and model-service callers. The reference itself still runs its
old declaration. Actual non-PNG/provider/deployment profiles, remote or authenticated
channel media downloads, custom interpreters, other channel opt-outs, historical
artifacts and whole-reference state migration remain unqualified. The legacy
VisionAction module is retained until cutover; this increment qualifies its
retirement proposal, not complete deletion or enterprise release.
