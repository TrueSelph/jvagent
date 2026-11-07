# Published storage dependency qualification — 2026-11-06

Parent `9a6b2226` has all nine remote checks passing. This increment selects
published jvspatial 0.2.4 after fresh installation, deterministic storage checks,
full suites and an installed-wheel browser/graph upgrade rehearsal. It adds no
storage implementation, execution engine or provider adapter. The prior pinned
0.1.1 wheel buffered the entire stream before yielding its first chunk; see the
[previous dependency finding](2026-11-06-native-context-decoupling.md).

## Dependency and regression boundary

The three runtime declarations now agree on 0.2.4: `pyproject.toml:32`,
`requirements.txt:6`, `requirements-all.txt:9`. Native PostgreSQL CI uses the same
version (`.github/workflows/test-jvagent.yaml:98`). Root guidance and the current
integration reference are aligned; historical evidence remains unchanged.
The regenerated uv lock changes only jvspatial's package version, but also
normalizes platform markers for existing CUDA/httpcore dependencies. It is not
a one-line lock update. No new package version was added by this resolution.

The public LocalFileInterface contract test instruments physical reads without
replacing the streaming method. The first yielded 1024-byte chunk must consume
only 1024 bytes; early `aclose()` must close once without reading further. A
second case reconstructs exact binary bytes across chunk boundaries. Source:
`tests/core/test_storage_stream_consumption.py:15` and `:53`. The new first-chunk
case fails on installed 0.1.1 (1217 reads before the first yield) and both cases
pass on 0.2.4 under Python 3.11 and 3.12. This is an observed dependency defect,
not a synthetic implementation-mirroring test.

Installed 0.2.4 local/S3 implementations delegate to the incremental reader and
close it in `finally`. Only local storage is exercised here. There is no live
S3 qualification or hard cancellation deadline for an uncooperative I/O worker.
The JV File Interface still needs its own bounded-read adapter and native tool
qualification; its existing full-file helper is not repaired by this pin alone
(`jvagent/action/file_interface/_core.py:145`).

## Fresh environment checks

Fresh locked native/test/MCP/pilot environments on Python 3.11.15 and 3.12.15
install 114 packages and pass `uv pip check`. The independently resolved
transitional test/pilot/LiteLLM profile installs 126 and also passes. Its floating
transitives differ from the locked native profile; these are separate compatibility
profiles, not a controlled single-variable benchmark.

| Qualification | Result |
|---|---|
| Native/host/real PostgreSQL, Python 3.11 | 458 passed, 87.95 seconds |
| Native/host/real PostgreSQL, Python 3.12 | 458 passed, 89.72 seconds |
| Full native combined profile, Python 3.11 | 5136 passed, 129 skipped, 175.75 seconds |
| Full transitional profile, Python 3.11 | 4969 passed, 48 skipped, 119.27 seconds |
| Added storage cases, Python 3.11 / 3.12 | 2 passed on each |

The full suites began before the new storage cases were added; those cases are
reported separately. PostgreSQL is qualified by the explicit native lanes, not
by skipped cases in a full run. Logs remain under `/private/tmp/jv-spatial024-*`.
Final promoted-source hooks, focused suite and normal full push gate are recorded
below after completion. No speed or overall optimization claim follows from these
suite durations.

## Installed-wheel browser and retained graph

[Persisted receipts](2026-11-06-spatial024-smoke.json) identify source blobs,
wheel digest, actual package paths/versions, cloned graph hashes, original record
preservation, calls, SDK timing, cost provenance and spending. The noneditable
wheel runs outside checkout with PYTHONPATH unset: Pydantic AI core 2.66.0,
official Harness 0.66.0 and jvspatial 0.2.4. No runtime function is patched.

The disposable PostgreSQL database starts as an exact copy of the prior 0.1.1
fixture's 14 nodes, 13 edges and one auth User; refresh tokens are omitted and
the JWT secret rotated. Merge startup intentionally keeps stored Action settings.
After that warning, only the clone's declared alias, absolute existing skill path,
MCP interpreter and isolated MCP audit paths are explicitly applied. Run messages,
registrations, spend and SDK workspace identity are not rewritten. The obsolete
app storage-context warning is recorded separately from the successful final boot.
An initial reload during this configuration restart returned to login; form login
was repeated. No model request was made during that setup.

Browser page 46 uses an isolated context on source-built jvchat port 3140, backend
8148. Only a local conversation index is seeded under the verified owner; no
transcript is injected. Selecting it restores the old prompt/reply, tool and
691-token statistics. SQL proves restoration creates no execution or spend.
Actual GPT-4.1 then uses the reviewed MCP record integration again and returns
the correct **Cedar / 14:30**. The visible argument/result and statistics are in
the [browser record](2026-11-06-spatial024-browser.json).

The new delivered/closed/emitted turn reports two requests, **899 tokens**, and
**USD 0.002068 estimated**. Model time is 3.216 seconds and Interaction duration
8.363991 seconds; browser end-to-end timing and invoice costs are unmeasured.
Conversation totals reconcile old plus new to four requests, 1590 reported tokens,
USD 0.003720 estimated and zero unpriced requests. The new run points to the old
delivered frontier. One exact MCP invocation is audited; no old executor, loop,
pilot runtime or HarnessRuntime is loaded.

After a further backend restart and deletion of the browser transcript cache,
conversation selection restores both turns and their tool/statistics rows. SQL
again proves unchanged Interactions, registrations, tasks and spend. The original
source graph's node and edge hashes remain exact. Its live browser authentication
updates auth `last_accessed`, so the auth-object hash is explicitly not claimed
unchanged; source/clone User fields differ only in that timestamp.

## Remaining course

This closes the published dependency prerequisite for incremental local reads.
Next qualify a thin, caller-scoped bounded File Interface read adapter using that
public storage API, then continue remaining extension, migration and deletion
gates. Existing full-file helpers, scripts, writes, live S3, workspace relocation,
external channels, operator recovery and cross-platform deployment remain open.
The full goal remains active; this does not establish enterprise qualification.

Final promoted-source dependency/storage/install slice: 28 passed, one skipped
on Python 3.11; dependency/storage subset: eight passed, one skipped on 3.12.
The first all-files hook run reformatted the new test; it is a failed gate until
the formatted file is staged and the complete hook set reruns clean. The normal
full pre-push suite and exact-head remote checks remain separate publication gates.

After staging the formatted test, all-files hooks passed clean (YAML/JSON,
whitespace, black, isort, flake8, mypy and secrets). The formatted affected slice
again passed 28/one skip in 1.19 seconds. Full push and remote checks remain
required for the containing commit.
