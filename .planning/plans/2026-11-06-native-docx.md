# Existing DOCX skill on the official execution path

## Implementation

The app-local DOCX skill keeps its authored API guidance, scripts, XML schemas and
license. It now declares CodeExecutionAction/bash prerequisites and optional
FileInterface sharing. The SOP distinguishes shipped validation tools from the
JavaScript program the model must write, and checks preinstalled docx-js before
attempting installation. These are domain instructions, not core prompt or engine
branches. Source: the skill's `SKILL.md:6`, `:21` and `:82` under
`examples/jvagent_app/agents/jvagent/orchestrator_agent/skills/docx/`.

An optional image supplies Node and XML validation dependencies without changing
host requirements. Bases are digest-pinned, docx-js and its transitive packages
use npm ci/lock, and Python validation packages are pinned. The tested Linux arm64
image is 319,599,398 logical uncompressed bytes. Node v22.23.4, docx-js 9.15.2,
Python 3.11.16, lxml 6.1.3 and defusedxml 0.7.1 are verified in the image. The
network-disabled non-root module probe passes. Image size is not incremental disk
or download footprint. Sources: `examples/native_docx/Dockerfile:1`,
`examples/native_docx/package-lock.json:1`; [operator profile](../runbooks/native-docx.md).

The actual browser run exposed a separate host-policy defect: a model-selected
120-second shell override against the configured 60-second allowance raised a
fatal ValueError before any effect. The host now projects its ceiling into a
cloned public SDK ToolDefinition schema and returns a public ModelRetry for an
invalid override before command staging/execution. The SDK owns the correction
turn. Commands are not clamped or silently executed. Sources:
`jvagent/harness/native.py:263`, `:316`; upstream-gap removal condition is in the
[script contract](../runbooks/native-script-sandbox.md).

No DOCX executor, additional state database, provider translation or host package
dependency is added. Existing SDK Docker/Shell, scoped user files, permissions,
checkpoints, accounting and FileInterface downloads supply the workflow.

## Verification

Three asset regressions prove explicit prerequisite refusal, official Skills
loading and byte-exact staging of all 59 scripts/schemas (1,099,623 bytes) within
existing bounds. A public-SDK regression injects an excessive timeout, verifies
the schema ceiling and correctable feedback, and proves no workspace reference or
execution effect was created. Sources: `tests/harness/native/test_docx_skill.py:1`,
`tests/harness/native/test_script_sandbox.py:239`.

Final native/host/real-PostgreSQL lanes pass 669 on each Python 3.11/3.12 with the
reviewed Docker/file-root test profile enabled. The affected CLI/asset/script
slice passes 157 with six optional Docker/profile cases skipped; the configured
669-case lanes include those cases. Earlier 668-case runs preceded the timeout
regression and are not substituted for final evidence. Initial black hooks
reformatted the new tests; clean reruns are required for publication. Normal
full-suite pre-push and exact-head remote CI remain separate gates. Parent
`21d85ad6` has nine green remote checks.

The [unchanged reference configuration preflight](2026-11-06-native-docx-reference-blockers.json)
now admits six selected skill formats and reports ten blockers, down from eleven.
Format admission is not complete skill-feature qualification or cutover approval.

## Actual-provider browser evidence

[Machine evidence](2026-11-06-native-docx-smoke.json) binds the parent revision,
runtime source blobs/processes, SOP epochs, script manifest, image, request
outcomes/durations, SDK records, usage, artifact inspection and restart/reload
audits. [Browser observations](2026-11-06-native-docx-browser.json) preserve the
failed requests and the successful tool/reply/statistics presentation. Download
capabilities are redacted from committed evidence.

A disposable PostgreSQL app on backend 8165, physically bounded 64 MiB local
storage and source jvchat 3140 uses actual OpenAI GPT-4.1:

1. The initial SOP guidance led the model to run an unwritten example generator.
   It honestly reported inability to create the file. The request failed its
   acceptance criterion although the framework delivered that reply. Its two
   completed shell calls, three provider requests and spend remain recorded.
2. After clarifying generator ownership, another fresh conversation attempted an
   offline npm installation with a 120-second override. The host rejected it
   before staging or any execution effect; the request ended with a generic
   configuration notice. This confirmed the timeout-admission defect above. The
   one provider request and its reported usage remain recorded.
3. With the corrected SOP and timeout policy, the same ordinary creation prompt
   succeeds. Four shell calls check the module, write the JavaScript program,
   generate the file and run the unchanged validator. The output reports
   `All validations PASSED!`. One file-sharing call publishes a real 9,944-byte
   DOCX. Browser GET returns 200; the bytes/hash match the source and immutable
   publication copy. ZIP/XML inspection verifies the requested title, marker and
   exactly three requested table rows. A Quick Look thumbnail visually shows
   those contents without clipping; it is not Word pagination qualification.
4. Cold restart preserves all earlier Interactions, SDK records and spending.
   Follow-up re-sharing calls only FileInterface, with no bash. The browser
   downloads identical bytes/hash. Reload and conversation selection restore both
   successful replies, tool groups and usage without additional execution/spend.

The full retained cohort has four model turns: two earlier failures and two
post-fix successes. Twelve provider requests reconcile 89,366 provider-reported
tokens and USD 0.091162 estimated cost, with zero unknown-cost calls. Three
conversations have no domain tasks. The failed timeout run retains its conservative
usage_complete flag as false; its observed model receipt is still counted.
This is an engineering cohort across revisions, not a statistical success-rate
estimate for the final implementation. No overall optimization is claimed.

## Remaining gates

This qualifies bounded local DOCX creation, authored XML validation and web
re-sharing. Uploaded/binary document ingress, arbitrary reads/edits, tracked
changes, comments, conversions and malicious/large documents need their own
evidence. LibreOffice and pandoc are not in the optional image. Other providers,
architectures, channels, image supply-chain/deployment, aggregate capacity and
interrupted-script/operator recovery remain open. Stored-state cutover, rollback,
remaining ten configuration migrations and legacy/pilot deletion remain required
by the full goal. This is not enterprise qualification.
