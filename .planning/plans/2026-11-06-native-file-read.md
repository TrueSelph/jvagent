# Native bounded File Interface read — 2026-11-06

Parent `9f387cb6` has all nine remote checks passing. This increment qualifies
one existing modular tool name through the public SDK adapter. It adds no
execution loop, storage backend, provider translation, dependency or frontend
change. Existing direct/transitional file tools retain their signatures and
behavior; this does not qualify their writes, scripts or directory operations.

## Engineering boundary

`FileInterfaceAction.get_native_tools()` publishes only
`file_interface__read_file`, using its existing argument schema/name and the
native adapter's server-injected visitor (`file_interface_action.py:38`). The
model cannot supply caller identity or a byte-budget override. Host access and
skill checks remain in the existing SDK tool wrapper (`jvagent/harness/native.py:95`).
The read revalidates host scope before dispatch and before returning content;
provider errors return a safe failure without leaking bucket or host paths.
Native publication uses the unchanged SDK/event/accounting path.

The Action declares `native_read_max_bytes` with `attribute(...)`, default
262144 (`file_interface_action.py:36`). Configuration is host-owned and positive;
the [configuration reference](../reference/configuration-keys.md) describes it.
Other native file tools are omitted explicitly; there is no legacy fallback.
Existing skills which require omitted tools must migrate or remain blocked.

The bounded domain helper uses public `stream_file`/`aclose`, not full-file
`get_file` or a metadata-size guess (`jvagent/action/file_interface/_core.py:162`).
It never provisions storage. A head may return once complete requested lines
arrive. Full and tail reads above the budget fail, never silently returning an
incomplete file. Positive line limits are validated rather than coerced into a
full read. Incremental UTF-8 decoding preserves split multibyte and CRLF lines.
Storage consumption is bounded by the allowance plus one chunk of at most 8192
bytes needed to detect overflow, assuming the provider's incremental contract.
The extra chunk is consumed but is not included beyond the configured source-byte selection.

For local storage, the helper re-scopes the public LocalFileInterface to the
physical caller directory with `create_root=False`, rejecting a linked caller
root and paths resolving outside it. This avoids the shared application root
being mistaken for the user boundary. The previous unbounded helper remains for
its existing domain/transitional callers; its behavior is not declared repaired
or native-qualified by this adapter. See the
[published dependency prerequisite](2026-11-06-spatial024-qualification.md).

## Deterministic and adversarial evidence

The initial nine helper cases fail before the implementation; the final focused
suite passes **24 cases on each Python 3.11 and 3.12**. Tests cover exact-limit
and oversize files, early head on a large file, no false partial tail, real
cross-user symlink rejection, unsafe paths, no directory creation, cleanup on
early return and cancellation, a never-ending growing stream, split UTF-8/CRLF,
invalid line limits, linked user-root rejection, concurrent same-name caller reads, native read-only admission, unchanged legacy write surface,
official SDK permission denial, forged model visitor, changed visitor during
read and host-path error sanitization. The initial forged-argument test expected
a retry; the existing HarnessContractError correctly refuses it before dispatch,
and the assertion was corrected without weakening runtime enforcement.

Sources: `tests/action/file_interface/test_bounded_file_reads.py:1`,
`tests/harness/native/test_file_read_adapter.py:1`. The growth test reads 16384
bytes for a 9000-byte budget then closes; it proves bounded overflow detection,
not an exact 9001-byte physical-read limit. Real published storage incremental
behavior is separately qualified by the preceding dependency milestone.

Native/host/real PostgreSQL suites pass **464 cases per Python 3.11/3.12**
(83.34/87.56 seconds). These include the six native adapter cases and overlap
the focused suite; counts must not be added. The affected transitional
file/PageIndex slice passes **22, one skipped** in 0.76 seconds; the native
module is skipped in that profile and executed in both native environments.
All-files hooks pass after explicit source/test formatting; final artifact hooks,
full pre-push and new-head CI remain publication gates.

## Actual-provider browser and graph readback

The fresh disposable PostgreSQL app on backend 8149 hosts NativeHarnessAction,
FileInterfaceAction and ReplyAction. It has a simple file-reading skill and no
Orchestrator, pilot or model-service Action. The reference client on 3140 uses
isolated browser context/page 47. File byte budget is explicitly 4096 for this
fixture, persisted on the Action. No runtime function is patched.

| Actual GPT-4.1 scenario | Visible result | Interaction seconds |
|---|---|---:|
| Read first two lines of 180025-byte schedule | Maple / 16:45, exact head argument and successful tool result | 4.195126 |
| Read the whole schedule | Tool returns safe failure; agent reports refusal | 3.620520 |
| Read link to another user's private file | Tool returns safe failure; agent reports failure without trying other paths | 4.451050 |
| After restart/cache-loss, read first two lines again | Correct Maple / 16:45 with a fresh tool call and usage | 4.793144 |

The initial UI automation fill left Send disabled. One keyboard input triggered
normal submission; no request/Interaction occurred for the earlier failed click.
This setup issue is retained rather than counted as a model success or timeout.
After the third turn the owned backend exits on SIGTERM. A fresh process loads
formatted final source. Deleting only the browser transcript cache and selecting
the existing conversation restores all three replies/tools/statistics. SQL
proves restoration changes no Interactions, registrations, tasks or spending.
The fourth actual model/tool turn confirms continuation under that fresh process.

All four Interactions are delivered, closed/emitted and usage-complete, with
one SDK registration each. Eight provider requests reconcile to **4664 reported
tokens and USD 0.010276 estimated**. Unknown-cost requests are zero for this
cohort. Invoice costs and browser end-to-end timings are unmeasured; SDK attempt
times and individual receipts are retained. All prior Interaction hashes remain
unchanged after continuation. Foreign secret content and large-file filler are
absent from all persisted Interaction records, including SDK state. Module probes
show no old executor/Orchestrator loop/pilot runtime/HarnessRuntime loaded.

See [persisted receipts/source blobs](2026-11-06-native-file-read-smoke.json) and
[browser tool/result/statistics evidence](2026-11-06-native-file-read-browser.json).
The browser proves real local reads and safe refusals; physical byte counters
are measured in deterministic tests, not instrumented in the browser process.

## Remaining release limitations

This is a local-storage read cohort, not complete File Interface compatibility.
Live S3, directory discovery, writes, artifacts/scripts and their skill migrations
still need qualification. The public local provider does not establish an atomic
defense against concurrent hostile OS-level symlink replacement; that filesystem
boundary remains an upstream/deployment security gate. Blocked worker I/O still
has no hard cancellation deadline. Provider failures are intentionally safe but
coarse; richer operational error classification remains open. No overall
performance, enterprise, migration or legacy-engine deletion gate is closed here.

Final expanded formatted Python 3.11 file/native/PageIndex slice: 30 passed
in 1.02 seconds; focused Python 3.12: 24 passed in 0.72 seconds. All-files
hooks passed for the staged source/artifacts before the two final isolation
cases; rerun the full hook set with those cases and final receipts before commit.

Final staged source, new isolation cases and all receipt artifacts pass the
complete all-files hook set without modifications. The expanded affected suite
is green as above. A normal full-suite push and exact-head remote checks are
required separately for the containing commit.
