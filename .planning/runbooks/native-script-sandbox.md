# Native script execution: development profile

The existing CodeExecution Action can declare a reviewed native container image:

```yaml
- action: jvagent/code_execution
  context:
    enabled: true
    native_image: python@sha256:<reviewed-image-digest>
    timeout: 60
    max_output_bytes: 64000
```

Use an actual reviewed repository digest or full local `sha256:...` image ID,
not the placeholder, a short ID or a mutable tag. The
image needs Python for the shipped triage script. This profile is optional and
does not qualify production script execution or existing artifact interoperability.
See the [qualification record and remaining gates](../plans/2026-11-06-native-script-sandbox.md).

The optional [PDF skill profile](native-pdf.md) qualifies one local rendering
and web-download workflow with its own reviewed image; it does not qualify all
script dependencies or artifact services.

`CodeExecutionAction.get_native_script_sandbox` declares the service; official
Harness `DockerSandbox` and `Shell` own execution. The public renamed toolset
preserves `code_execution__bash(command=...)`; SDK output replaces the old
executor's formatting. The SDK also exposes optional `timeout_seconds`, bounded
by host policy. There are no background shell tools. The only host bind mount is
the admitted caller's existing file-service directory.
Sources: `jvagent/action/code_execution/code_execution_action.py:84`,
`jvagent/harness/script_sandbox.py:104`.

A script skill must explicitly require `CodeExecutionAction` and the
`code_execution__bash` tool, and list that tool in `allowed-tools`. The shipped
`library:triage` asset does so. Host AccessControl must grant the named tool;
skill instructions and default Action access do not grant execution. The native
path rejects an absent image and unsupported legacy resource/network/storage
settings; it never invokes the legacy subprocess executor. Sources:
`jvagent/harness/skill_admission.py:65`,
`jvagent/action/native_harness/native_harness_action.py:234`,
`jvagent/harness/script_sandbox.py:32`.

The fixed development preset uses the non-root POSIX server's uid/gid, a read-only root filesystem,
dropped capabilities, no-new-privileges, no outbound network, 512 MiB memory,
one CPU, 128 processes, bounded temporary storage and an 8 MB SDK per-file
limit. It is deliberately separate from the old subprocess resource controls.
Custom old controls require explicit migration; they are not silently ignored.
Source: `jvagent/harness/script_sandbox.py:136`.

Only bounded ordinary script files under configured skill directories are
staged. Symlinks and oversized assets are refused; no command runs during
skill loading. Staging is lazy and serialized once per root run before SDK
parallel commands. The graph binds caller/agent/session, image, limits and
script hashes; changed policy needs a new conversation or an explicit migration
that is not yet implemented. SDK messages retain the workspace ref; the graph
policy is authorization state, not a second execution journal. Sources:
`jvagent/harness/script_sandbox.py:65`, `:112`, `:163`.

All shell calls are conservatively mutations, including shell commands intended
only to read. SDK effects must carry `jv:mutation:script:v1` before staging or
execution. Failed/uncertain mutation continuation requires reconciliation; this
profile has no approved script-effect reconciliation operation. Read-only
recovery and delegated children do not receive shell execution. Sources:
`jvagent/harness/native.py:294`, `:445`, `:529`.

**Storage boundary:** enable application local file storage on an actually
host-bounded filesystem of at most 64 MiB. An ordinary large disk with only a
configured upload limit does not qualify. This is a shared filesystem ceiling,
not a reserved quota for each user. Scripts and FileInterface share the existing
`<agent>/<user>` directory across conversations. The SDK mounts only that leaf;
the app, parent store and credentials are not mounted. The graph binds the
physical root identity; changed mounts, image or assets require explicit migration.
Sources: `jvagent/harness/script_files.py:32`,
`jvagent/core/sandbox.py:147`, `jvagent/harness/script_sandbox.py:170`.

The public Docker arguments explicitly mount the SDK process-control tmpfs with
mode 1777. With pinned Harness 0.66.0, its shorthand mount can reset to root-owned
0755 after container restart, preventing non-root execution. This is a narrow
upstream-gap configuration adapter; remove the explicit mount after an upstream
fix passes the stopped-container regression. It does not replace SDK execution,
timeout or cancellation. Source: `jvagent/harness/script_sandbox.py:151`.

Published V1 conversation policies are refused rather than silently rebound.
Do not purge old user files or rewrite SDK refs to force cutover. Container cleanup,
aggregate capacity, destroyed-ref operator recovery, artifact delivery, S3 and
deployment qualification remain open. File durability does not imply an uncertain
script is safe to repeat. Sources: `jvagent/harness/script_sandbox.py:209`,
`jvagent/harness/script_files.py:99`.

The converter reports missing image/resource policy as a blocker and preserves
existing declarations. A configuration candidate alone is not runtime approval.
Source: `jvagent/cli/harness_migrate.py:273`.

For the transitional executor's separate security posture, see
[skill isolation](../../docs/skill-isolation.md). That executor is not the native
path's fallback.
