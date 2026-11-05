# jvchat completed-stream transcript reset

Parent `ddebbde1`; branch `feat/pydantic-harness-rebuild`; author `eldonm`.
Governing [execution plan](2026-11-04-harness-rebuild-execution.md),
[browser checks](2026-11-05-jvchat-reset-browser.json) and
[source/build/SQL receipt](2026-11-05-jvchat-reset-smoke.json).

The [native interview round](2026-11-05-native-interview.md) confirmed that New
Conversation created a fresh backend session while retaining the previous visible
transcript. The stream-completion callback compared its captured sessionId after
the first-stream effect had already bound the real session. It could re-arm a
boolean preserve flag without a corresponding transition; reset/sidebar selection
then consumed that stale flag and retained the old messages
(`jvchat/src/components/ChatInterface.tsx:378`, `jvchat/src/hooks/useStreaming.ts:213`).

The completion callback now checks the current session ref. Preservation carries
the actual assigned session ID and applies only to undefined-to-that-ID binding.
Reset and real-session switching clear the transcript even if a late hint remains.
Both signal producers pass the assigned ID; no persistence format, backend session,
model execution, provider mapping or harness loop changed
(`jvchat/src/components/ChatInterface.tsx:283`, `:378`,
`jvchat/src/hooks/useStreaming.ts:169`, `:213`, `:1188`).

Two regressions fail on the parent with old messages retained. They pass with the
repair. A positive regression preserves the live first-turn transcript at initial
binding. All 124 jvchat tests pass (1.96 s), TypeScript/Vite production build passes
(4.44 s), and the eight Python static-server tests pass (2.94 s). Required all-file,
commit and full-suite push gates remain independent; publication/CI status is on
the live PR.

The new production UI build runs in the disposable fixture on 3136 against native
backend 8136 and its PostgreSQL graph. Vite preview serves this build so the shared
packaged UI used by other local servers was not overwritten. The receipt includes
source blobs and hashes of the built index and route bundle. Execution functions
were not patched; request observation clones responses and removes session
capabilities without recording authentication headers.

Actual OpenAI GPT-4.1 turns A and B each activate an interview through the public
HTTP endpoint, show the native tool event, response and usage. Between turns, New
Conversation shows an empty thread before the second request. B's thread contains
B and excludes A; selecting A and B restores only each selected transcript, tool
badge and usage. Reloading and selecting B restores B again. Sidebar labels still
list saved conversations; assertions inspect the thread rather than mistaking a
sidebar preview for transcript contamination. All six browser assertions pass.

Fresh SQL has two distinct sessions, two delivered Interactions and separate active
interview domain tasks with empty fields. The test did not complete or cancel those
interviews. Four provider requests used 4,616 provider-reported tokens and USD
0.006496 estimated, with no unknown-cost calls. Provider-invoiced dollars were not
returned. Backend durations were 3.907204 s and 3.005245 s; browser request timings
are in the receipt. These two samples do not prove a performance improvement.

This closes the confirmed completed-stream reset/restoration defect. In-flight
reset, switching while streaming, late callback ownership and cancellation races
remain separate gates. Packaging a fresh wheel, full client/provider/channel
qualification, complex interview extensions, operator recovery, migration, legacy
execution removal and enterprise qualification remain unfinished. No new execution
engine, state store, dependency or configuration option was added.
