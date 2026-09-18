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

## Staging

Stage path is `stage/{session_id}/{snapshot_id}/{digest}`. Cleanup is
`cleanup_stage(path)`. A revoked or expired snapshot cannot activate.

## Audit

Stage / activate / refuse are recorded as harness spans (`skill_activate`) on
the snapshot id. Correlate with the turn via NativeCaller, not a host scope.
