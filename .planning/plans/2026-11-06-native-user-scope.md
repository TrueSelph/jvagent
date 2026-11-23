# Authenticated user file isolation through Messenger

Status: local authenticated embed profile qualified at `e1455848`;
no runtime source change. All nine exact-head remote checks pass. Extends the
[shared-file qualification](2026-11-06-native-user-files.md), with
[graph/resource/usage evidence](2026-11-06-native-user-scope-smoke.json) and
[browser evidence](2026-11-06-native-user-scope-browser.json).

## Test boundary

Fresh disposable PostgreSQL graph, backend 8156, Messenger host 3141, actual
configured OpenAI GPT-4.1 and a separate 64 MiB local filesystem. The same
app-local triage adaptation as the previous round enables existing file tools.
The tested runtime is unchanged; the module observer reports no legacy execution
modules and no patched runtime functions. The harness uses public SDK execution
and caller-scoped storage as described in the
[configuration runbook](../runbooks/native-script-sandbox.md).

The packaged Messenger iframe has no built-in bearer-login control. A disposable
loopback test host injects a bearer-fetch prelude before the unchanged app module
loads. Separate browser contexts select private admin/member fixture credentials.
This is host-supplied authentication, not a shipped login feature or an alternate
client/agent engine. Tokens, passwords and request authorization headers are
omitted from evidence. Actual `/api/auth/me` readback confirms the member has only
the `user` role. Graph registration and physical mount identity independently
confirm each executed caller. The normal jvchat admin-listing limitation remains.

Before testing, acceptance required exact file bytes, different caller mounts,
no foreign-file reads, a cross-user symlink refusal by FileInterface, canonical
verified identity despite a foreign client claim, visible live activity, usage
reconciliation and reload without new execution or spending.

## Results

- Admin's SDK command writes `AUTH-ADMIN-88`; the member's workspace does not
  contain that file. Member writes `AUTH-MEMBER-88`; the admin reads its original
  file and confirms the member file is absent. Docker inspection verifies one
  bind mount per container, each pointing at the corresponding user's leaf.
- The member creates a symlink toward the admin's physical host path. That path
  is unavailable inside the container. Reading the link through the existing
  native FileInterface returns its bounded unsafe/unavailable result, with no
  admin content. The link remains for inspection and both original files remain
  unchanged. Sources: `jvagent/harness/script_files.py:32`,
  `jvagent/action/file_interface/_core.py:165`.
- A browser-observed request supplies the admin's `user_id` alongside the
  member's valid bearer. The server executes as the verified member, with an
  unavailable file result. It does not reject before calling the model: the
  native contract overrides the client assertion. A first probe was incorrectly
  expected to reject; its actual model usage is retained. Sources:
  `jvagent/action/interact/session_token.py:391`,
  `jvagent/action/interact/endpoints.py:794`.
- During a ten-second SDK script, Messenger displays `bash…` and Stop generating,
  then returns the member's exact file bytes. Completed tool details and usage
  statistics are not displayed by this client and are not claimed as qualified.
  Current live-strip behavior: `jvmessenger/src/app/chat/Thread.tsx:116`.
- Reloading both authenticated embeds restores their separate transcripts.
  Eleven Interaction hashes, eleven SDK registrations, task/planning/import
  state and all spending remain unchanged. No model call is caused by reload.

There are eleven actual model turns, including nine authenticated turns and two
initial anonymous fixture attempts. The latter arose because a browser wrapper
missed the iframe; they remain separate identities and do not count as
authenticated-user proof. One additional idle anonymous session has no model
execution, script policy or spending. All records remain in the disposable graph.

Twenty-six provider requests report 33,713 tokens. USD 0.052192 is estimated
pricing, not provider-reported dollars; zero calls have unknown cost. Every receipt
reconciles to its turn and conversation counter. Tool refusals are explicit
results, not claimed successful foreign-file reads. Per-turn duration, command
arguments/results, effect status, workspace refs and resource inspection are in
the SQL evidence.

## Checks and remaining gates

The unchanged published runtime has nine green CI checks. Re-running the local
script/file-binding slice with actual Docker passes 29 cases. Documentation
publication still requires staged all-files hooks and normal pre-push checks.

This closes ordinary-member live local script/file isolation only for the
host-provided-bearer embed profile. Separate user capacity reservations, permission
revocation/delegation combinations, concurrent hostile filesystem changes,
distributed deployments, script-effect reconciliation, cleanup, destroyed-ref
continuation and artifact delivery remain open. The broader migration, legacy
removal, security, provider, evaluation and release gates remain active. No
performance improvement or general enterprise qualification is inferred.
