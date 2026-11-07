# Native PDF compatibility qualification

## Change

The shipped skill's declared name `pdf-generation` did not match its
`pdf_generation` directory, and its declaration lacked the required native
script tool prerequisite. The folder now matches the existing declared name;
the skill explicitly requires bash and permits the independently authorized
download tool. The renderer and engine cascade are unchanged. Existing discovery
by declared skill name remains; explicit old paths require the documented minor
adjustment. Sources: `jvagent/skills/pdf-generation/SKILL.md:1`,
`jvagent/harness/skill_admission.py:65`, `jvagent/harness/skill_catalog.py:60`.

The public Docker image setting now accepts a full local image content ID as
well as a repository digest. Both are immutable identifiers; mutable tags and
short IDs remain refused. This permits reviewed custom images without a registry
service. No execution engine, provider, staging mapper or dependency was added
to the harness. Optional image dependencies remain in
`examples/native_pdf/Dockerfile:1`; policy is `jvagent/harness/script_sandbox.py:23`.
See [operator configuration](../runbooks/native-pdf.md).

## Actual-provider browser evidence

Parent revision: `675a5ba3`; the exact tested runtime Git blobs are recorded in
[the smoke record](2026-11-06-native-pdf-smoke.json). An isolated fresh PostgreSQL
app on port 8158, bounded 64 MiB filesystem and jvchat on 3140 used
`openai:gpt-4.1`. The optional image was 254,186,115 bytes and contained
WeasyPrint 70.0, Markdown 3.10.3 and pydyf 0.13.0; its full identity and package
versions are retained. The SDK owns the Docker workspace and shell execution.

1. An ordinary request for a one-page status memo used two SDK bash calls
   (author content, run the shipped renderer), then FileInterface publication.
   Anonymous browser download returned a real 7,677-byte PDF. Text extraction
   verified the requested facts and table, including the explicit unqualified
   enterprise-release caveat. The rendered single page was visually inspected;
   it is basic default typography, not publication-design qualification.
2. After backend shutdown/restart, restored history and spending were unchanged.
   Follow-up requested publication without rendering: the persisted SDK effects
   show one share call, no bash call, and identical PDF bytes/hash.
3. Reload restored both completed turns, tool groups and model usage without
   registering execution, changing the persisted Interactions or increasing spend.

Both requests delivered. Six provider requests used 7,865 provider-reported
tokens and USD 0.012622 estimated cost; no unknown-cost calls were observed.
Per-turn durations, receipts, effects, workspace constraints, anonymous HTTP
headers, PDF content, source blobs and persisted spend reconciliation are in the
record. [Browser observations](2026-11-06-native-pdf-browser.json) retain visible
responses/tools/usage. Capability credentials are masked in committed evidence.
No overall optimization or enterprise qualification is claimed.

## Regression and packaging evidence

The three new tests fail on the parent and pass after migration: canonical asset
loading/script namespace, full local image identity, and missing-engine failure
without a fabricated file. The parent missing-engine test fails locating the
new asset path; only the passing migrated test qualifies the engine failure.
The native/host/real-PostgreSQL lanes pass 561 on Python 3.11 (134.42 seconds)
and 3.12 (131.62 seconds). The affected legacy skill tests pass five cases; the
legacy host-rendering test skips for an absent host engine. Actual native
container rendering above is the positive proof. Source:
`tests/harness/native/test_pdf_skill.py:1`.

A dirty local wheel build retained the removed folder from an old build tree.
A fresh export of staged sources builds a wheel containing the canonical skill,
requirements and Python script, with no old-folder duplicate. The record binds
the wheel hash and each packaged asset Git blob. Build release artifacts from
clean sources. This is asset inspection, not a fresh installed-wheel runtime test.
Staged all-files checks and normal full-suite push remain publication gates.

## Remaining gates

Only the reviewed local WeasyPrint route and basic one-page Markdown/table are
qualified. LaTeX/pandoc, multilingual/font coverage, long and hostile documents,
other formats, actual missing-engine model behavior, independent capacity,
interrupted rendering/publication reconciliation, image lifecycle, remote hosts
and production supply-chain/deployment remain open. Artifact retention/revocation
limits from the prior download profile still apply. Full migration, legacy engine
deletion and enterprise release remain open.
