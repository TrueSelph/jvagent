# Native attachment input

NativeHarnessAction accepts attachment envelopes through the existing interaction
API. Configure `input_media_types` explicitly on that Action; the default empty
list admits no attachments. This host policy cannot be expanded through client
`data`, skill prose or tool arguments. Unknown/duplicate MIME declarations refuse
startup. Sources: `jvagent/action/native_harness/native_harness_action.py:114`,
`jvagent/harness/inputs.py:37`.

Example native Action context:

```yaml
input_media_types:
  - image/png
  - image/jpeg
  - application/pdf
  - text/plain
```

The current profile supports inline PNG, JPEG, WebP, GIF, PDF, UTF-8 plain text,
CSV and Markdown. Existing `image_urls`, `whatsapp_media`, `files`, `attachments`
and `documents` envelope keys are recognized. Each key must contain a list of
`{base64, mime_type, filename}` entries. Data URLs must have the same declared
MIME type. The full request refuses malformed members, unsupported types,
invalid UTF-8/base64 and bound violations before any model request. Limits are
20 attachments, 5 MiB per file and 10 MiB total. It never silently drops files or
truncates their contents. Source: `jvagent/harness/inputs.py:51`.

The public SDK receives images/PDFs as BinaryContent and text as user content;
attachment names/content are not system instructions. Native SDK checkpoint and
history serialization retain typed content, with no separate VisionAction call,
local OCR, extractor or provider message translator. The model receives the host
MIME policy and explicit image/PDF enablement facts. Sources:
`jvagent/action/native_harness/native_harness_action.py:346`,
`jvagent/action/native_harness/native_harness_action.py:456`,
`jvagent/harness/native.py:592`.

Remote URL attachments and other formats are explicitly refused; a referenced
URL is never fetched by this adapter. User content remains untrusted and cannot
change caller identity, permissions or budgets. A rejected input receives a
persisted `invalid_input` host notice and requires a new conversation; it is not
turned into synthetic SDK model history. Same-conversation rejected-input
recovery, remote/channel-specific authenticated downloads, aggregate media capacity and
large-document/long-session behavior remain qualification work. Configured model
and provider must support the selected formats; the allowlist is admission policy,
not proof of model capability. No full legacy `vision` flag migration is authorized
by this local profile. See [qualification](../plans/2026-11-06-native-inputs.md).

Payloads now use the [official SDK media adapter](native-media.md) on the existing
graph. Large image/PDF/TextContent payloads are referenced rather than copied
into each new snapshot; old inline data remains readable.
