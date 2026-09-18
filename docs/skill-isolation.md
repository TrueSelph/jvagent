# Skill isolation (HP-09)

jvagent accepts two SKILL.md forms only: `spec: jv` and `spec: claude` (ADR-0017).
A third format is refused at manifest register.

## Subprocess is not a sandbox

`SubprocessExecutor` is **development-only containment**. It is not an approved
isolation backend. Untrusted script-bearing skills refuse unless one of these
backends is configured on `HarnessRuntime(isolation_backend=...)`:

- `gvisor`
- `firecracker`
- `nsjail`

Trusted SOP skills (no script) activate from digest under the admitted snapshot.
With `skill_signing_key` set, `register_manifest` / `publish_manifest` require an
HMAC-SHA256 signature (`hmac.compare_digest`). `revoke_manifest` drops the digest
from the in-process registry so the next activate refuses.

## Isolation executor

`jvagent.harness.isolation.IsolatedExecutor` prefixes the command with `runsc` /
`firecracker` / `nsjail` **only when that binary is on PATH**. Absence is a
`SkillIsolationRefused`, never a subprocess fallback. `CodeExecutionAction.executor()`
uses `executor_for_backend(get_runtime().isolation_backend, SubprocessExecutor())`.

## Staging

Runtime stage record is `stage/{session_id}/{snapshot_id}/{digest}`. Filesystem copy
for `CodeExecutionAction.stage_skill` is `staged_skills/{snapshot_id[:12]}/{digest}/{name}`
when a turn snapshot is in cache; otherwise the legacy `staged_skills/{name}` dest
is kept for tests and offline staging. Cleanup is `cleanup_stage(path)`. A revoked
or expired snapshot cannot activate. Claude (`spec: claude`) activations pass
`trust_tier=untrusted` and refuse without an approved isolation backend.

## Audit

Stage / activate / refuse are recorded as harness spans (`skill_activate`) on
the snapshot id. Correlate with the turn via NativeCaller, not a host scope.
