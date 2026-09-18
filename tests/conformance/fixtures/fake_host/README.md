# Fake host fixture (HP-08)

Independent sample host for `HostCapabilityProvider` contract tests.

- Supplies per-session dynamic tools and skills.
- Has its own private "scope" map that **must not** leak into jvagent types.
- Not Integral. No workspaces, organizations, Apps, or domain schemas.

Wired in HP-08. HP-00 only reserves this directory so later packages do not
embed a product host in `tests/conformance/`.
