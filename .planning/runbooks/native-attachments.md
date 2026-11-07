# Native attachment input

NativeHarnessAction accepts attachment envelopes through the existing interaction
API. Configure `input_media_types` explicitly on that Action; the default empty
list admits no attachments. This host policy cannot be expanded through client
`data`, skill prose or tool arguments. Unknown/duplicate MIME declarations refuse
startup. Sources: `jvagent/action/native_harness/native_harness_action.py:181`,
`jvagent/harness/inputs.py:47`.

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
truncates their contents. Source: `jvagent/harness/inputs.py:61`.

An explicit `data.image_interpretation: false` refuses a current request containing
an image before model work; it never silently drops that image. Text-only input
still follows the configured MIME policy. This does not erase previously admitted
media or prevent reasoning about earlier history. Rejection receives the fixed
opt-out notice and requires a new conversation. Sources:
`jvagent/harness/inputs.py:100`,
`jvagent/action/native_harness/native_harness_action.py:240`.

The public SDK receives images/PDFs as BinaryContent and text as user content;
attachment names/content are not system instructions. Native SDK checkpoint and
history serialization retain typed content, with no separate VisionAction call,
local OCR, extractor or provider message translator. The model receives the host
MIME policy and explicit image/PDF enablement facts. Sources:
`jvagent/action/native_harness/native_harness_action.py:389`,
`jvagent/action/native_harness/native_harness_action.py:521`,
`jvagent/harness/native.py:638`.

Remote URL attachments and other formats are explicitly refused; a referenced
URL is never fetched by this adapter. User content remains untrusted and cannot
change caller identity, permissions or budgets. A rejected input receives a
persisted `invalid_input` host notice and requires a new conversation; it is not
turned into synthetic SDK model history. Same-conversation rejected-input
recovery, remote/channel-specific authenticated downloads, aggregate media capacity and
large-document/long-session behavior remain qualification work. Configured model
and provider must support the selected formats; the allowlist is admission policy,
not proof of model capability. The original [input qualification](../plans/2026-11-06-native-inputs.md)
does not itself retire the legacy vision flag. A bounded default-profile
[migration qualification](../plans/2026-11-06-native-vision-migration.md) now covers
the explicit replacement; custom interpreters/callers and stored artifacts remain open.

Payloads now use the [official SDK media adapter](native-media.md) on the existing
graph. Large image/PDF/TextContent payloads are referenced rather than copied
into each new snapshot; old inline data remains readable.
