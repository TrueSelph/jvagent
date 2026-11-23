# Native artifact downloads: local development profile

Use the existing FileInterface Action to share files created by SDK skills.
This profile adds `file_interface__share_file(path, expires_in=600)`, not a new
execution engine. It creates a bounded snapshot through existing file storage
and an expiring jvspatial URLProxy in the same graph database. Source:
`jvagent/action/file_interface/sharing.py:39`.

Configure the Action in `agent.yaml`:

```yaml
- action: jvagent/file_interface
  context:
    enabled: true
    native_share_max_bytes: 8388608
```

The limit must be from 1 through 8388608 bytes. Per-call `expires_in` must be
from 1 through 3600 seconds. The skill must allow `file_interface__share_file`,
and the host must explicitly grant that named tool under
`permissions.<channel>.tools.file_interface__share_file`. General Action access
and skill instructions do not grant publication. An issued link is a bearer
capability: anyone holding it can download until it expires. Sources:
`jvagent/action/file_interface/file_interface_action.py:108`, `:244`.

Set the existing `JVAGENT_PUBLIC_BASE_URL` to the trusted externally reachable
origin (for example `http://127.0.0.1:8157` locally). Request headers and model
arguments do not choose the origin. Keep `JVSPATIAL_FILE_STORAGE_ENABLED=false`
(the default for generic storage HTTP routes). Application local file storage
must still be available through App. The profile refuses S3, enabled generic
storage HTTP routes, missing proxy service, invalid origin, unsafe source paths
and oversized files; it never falls back to a raw file URL. Sources:
`jvagent/core/config.py:556`, `jvagent/action/file_interface/sharing.py:54`.

SDK scripts can produce the original using the
[bounded script profile](native-script-sandbox.md). Sharing copies bytes under
the storage service's private `native_exports/<action>/...` namespace, outside
the script/user mount. Later edits to the original do not change the download.
The tool returns path, filename, byte count, SHA-256, timezone-qualified expiry
and URL; it does not send file bytes through the model. Sources:
`jvagent/action/file_interface/sharing.py:80`, `:114`.

The public Action-scoped GET endpoint requires the issued code rather than a
login JWT. It verifies proxy Action/agent/path scope, expiry and snapshot digest,
then returns those verified bytes as an attachment with no-store and nosniff.
The endpoint requests a sandbox CSP, but the current jvspatial middleware replaces
it with the server CSP; the browser qualification records the effective header.
Invalid, expired, wrong-Action and altered snapshots are refused.
Sources: `jvagent/action/file_interface/endpoints.py:12`,
`jvagent/action/file_interface/sharing.py:119`.

Sharing is a non-retryable SDK mutation. Failure after snapshot/proxy creation
requires reconciliation; it is not proof of rollback. Existing links are not
revoked by removing permission to mint new ones. Disabling the Action refuses
its downloads. Proxy access counts record attempts, not acknowledged delivery.
Sources: `jvagent/action/file_interface/file_interface_action.py:35`,
`jvagent/action/file_interface/sharing.py:125`.

Download URLs remain in canonical SDK results for the intended recipient.
Operational interaction-log projections and URL-proxy/access log messages mask
their credentials. The dependency's naked-code logs use a narrow public logging
filter; remove that filter after upstream stops logging bearer codes and the
regressions pass. Sources: `jvagent/logging/capabilities.py:11`,
`jvagent/action/interact/webhook_pipeline.py:322`.

See [qualification and open gates](../plans/2026-11-06-native-artifact-downloads.md).
Cleanup/retention of expired snapshots, aggregate capacity, operator reconciliation,
individual revocation UX, remote/S3 storage and non-web channels remain open.
The profile does not qualify PDF/DOCX generation engines or the enterprise release.
