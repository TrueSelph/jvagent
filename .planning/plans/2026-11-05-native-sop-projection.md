# Native SOP inheritance through official Skills

Parent `a63373d1`; branch `feat/pydantic-harness-rebuild`; author `eldonm`.
Governing [execution plan](2026-11-04-harness-rebuild-execution.md),
[prior interview adapter course](2026-11-05-interview-platform-state.md),
[SQL/source receipt](2026-11-05-native-sop-projection-smoke.json), and
[sanitized browser receipt](2026-11-05-native-sop-projection-browser.json).

## Implemented boundary

The native factory now composes JV `extends` declarations before official Skills
catalog/load. A public read-only Workspace facade substitutes only the selected
SKILL.md, leaving authored files intact. The official Skills capability still
validates its catalog, materializes the selected capability and owns loading;
JV supplies declaration translation, not a second interpreter or execution loop
(`jvagent/harness/skill_assets.py:34`, `:120`, `jvagent/harness/native.py:68`, `:156`).
See the [official Skills workspace interface](https://pydantic.dev/docs/ai/harness/skills/).

Sibling `skill:name` references resolve inside the configured skill root, including
nested chains. Action bases require an explicit host-owned map passed to the
factory; ambient package scanning is not an authorization mechanism. NativeHarnessAction
does not yet supply an interview base map because native interview activation and
write effects remain unqualified (`jvagent/harness/native.py:27`,
`jvagent/harness/skill_assets.py:83`). No old discovery or SOP execution module is imported.

Inherited tool declarations and prerequisites accumulate; disabled tools subtract.
Channel allowlists intersect and denials accumulate. A child cannot erase an
inherited output contract, unsupported task/access policy, or malformed list by
overwriting it. Missing bases, cycles, root escapes, chains beyond twelve documents
and documents over 256,000 bytes fail admission. Existing runtime permission and
read-effect checks still apply to every selected tool
(`jvagent/harness/skill_assets.py:48`, `:94`, `:110`, `jvagent/harness/native.py:146`).

This projection has a deletion condition: replace it if official Skills gains
compatible declarative inheritance. It contains no routing, intent classification,
activation, tool scheduler, provider translation, state store or task engine.

## Browser and persistence evidence

A fresh jvchat conversation in the isolated `native-sop-inheritance` context calls
the public authenticated HTTP entrypoint on the disposable 8136/3136 fixture.
The child SOP declares no tool permissions. Its base supplies the required MCP
Action/tool, rehearsal reference and marker SOP-INHERITED-29. GPT-4.1 invokes
mcp_records__lookup_record and returns the marker, Cedar and 14:30. The UI shows
one tool call and 775 tokens; HTTP returns 200. Fresh PostgreSQL readback has a
delivered Interaction, native tool/run events and persisted usage. The module
observer reports no legacy execution engines. The receipt names the exact source
blobs loaded after formatting and retains both fixture SOPs for reproduction.

One browser workflow, two provider requests, 775 provider-reported tokens,
USD 0.001832 estimated; no unknown-cost calls. Provider-invoiced dollars were not
returned. Backend Interaction duration is 5.874521 seconds; browser request duration
is 6055.8 ms. Login headers and session capabilities are omitted from evidence.
No backend or provider execution function was patched. Initial browser fill did
not update the React composer state; native input events corrected the test driver
before any model request was sent.

## Validation and limits

The new asset boundary suite has eighteen cases. A separate mutation process
removes inherited procedure text while leaving admission intact: the model-wire
assertion fails as expected, proving the test observes the official prompt rather
than only the resolver return value. Final native/PG cohorts and all-file hooks
are recorded in the SQL/source receipt. The normal full-suite push hook and
exact-head remote CI remain independent gates. Earlier pushes of parent a63373d1
were rejected by GitHub after all hooks passed; do not confuse local commit state
with remote publication.

This qualifies inherited procedure text and its declared read-tool prerequisites,
not full interview execution. Child resources remain in their original directory;
base-relative resource mounting, packaged script execution and custom provider
classes require separate compatibility work. Read-only workspace APIs are not OS
isolation. The factory action-base map is a trusted host input, not a model argument.
No existing production configurations or conversations were migrated.

Next implement a versioned native interview base SOP and explicit domain activation,
then qualify built-in mutations using official tool effects before lifting the
current read-only gate. Preserve field validation, branching, review/confirmation,
reset/cancel and durable domain sessions. Follow the linked interview adapter
course for hooks, task continuation, failure recovery, migration and legacy deletion.
The main goal remains active and enterprise qualification remains unproven.
