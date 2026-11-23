# Native multimodal input qualification

## Confirmed gap and implementation

The native entrypoint previously forwarded only the utterance. Existing jvchat
image/document envelopes could therefore disappear from model input. A mutation
removing the new envelope projection reproduces five failing host-bound receipt
cases; the restored projection passes. Source:
`tests/harness/native/test_native_action.py:75`.

The thin input adapter now translates the existing envelope keys into public
SDK user content: BinaryContent for admitted images/PDFs and UTF-8 user text for
plain-text/CSV/Markdown files. No VisionAction, OCR, extraction service, extra model
round trip, provider wire translator or second execution engine is introduced.
A declared `input_media_types` list defaults to empty and is validated on startup.
Client payloads cannot widen it. Limits bound count, each decoded member and the
whole set; malformed or unsupported members refuse the complete request before
model work. Remote URLs are not fetched. See the
[configuration and limits](../runbooks/native-attachments.md). Sources:
`jvagent/harness/inputs.py:37`, `:51`,
`jvagent/action/native_harness/native_harness_action.py:114`, `:346`,
`jvagent/harness/native.py:592`.

The real browser negative setup also exposed misleading capability statements.
With image input disabled and no file attached, GPT-4.1 first claimed image input
was enabled. A generic MIME policy still produced an image-as-text suggestion.
The final trusted capability context includes explicit image/PDF enablement facts;
the same no-file query now correctly refuses image input. Both original replies
remain failures in the acceptance record. Source:
`jvagent/action/native_harness/native_harness_action.py:456`.

## Actual model and browser evidence

Parent cc02d8b9. [Machine evidence](2026-11-06-native-inputs-smoke.json) binds
runtime source blobs per process epoch, requests, outcomes, durations, provider
receipts, persisted binary hashes, SDK state, spend and graph readback.
[Browser observations](2026-11-06-native-inputs-browser.json) retain visible file
previews, responses and usage. Fresh isolated PostgreSQL, backend 8162 and source
jvchat 3140 use actual OpenAI GPT-4.1.

Synthetic PNG/PDF/text files are generated inside the browser and submitted
through the actual composer file-input change handler. The automation tool refused
OS fixture paths outside its configured roots; the test does not qualify the OS
file-picker path. The first negative setup also lost its transient file input and
submitted no file; that attempt is retained, not counted as an attachment refusal.

- Initial mixed input: actual model identifies the image code/color, PDF reference/
  amount and text warehouse/quantity. Prompts contain none of these answer values.
- Cold backend restart: the original Interaction, registration and spending remain
  exact. A follow-up asks for three details absent from the first answer and gets
  the image wording, PDF title and text heading. Native snapshots contain the exact
  original PNG/PDF bytes by SHA-256; fresh-connection tests independently prove
  serialization on PostgreSQL with deferred saves both enabled and disabled.
- A public authenticated configuration update permits only text/plain. A confirmed
  PNG upload receives the expected `invalid_input` notice, zero model attempts,
  zero SDK registrations and no spend. This admission control is deterministic;
  model instructions do not enforce it.
- Two no-file answers expose the capability communication issue described above.
  Explicit final host facts repair the same query, refusing image input honestly.
- Restore the original MIME policy through the admin endpoint. A new conversation
  repeats the complete three-file request on final runtime source and gets every
  requested value correctly. Earlier failed replies are not rewritten.
- Reload preserves all seven Interactions, six SDK registrations and spending,
  without model work. No tool or domain task is invoked by these read requests.

Seven interactions include six model replies and one expected pre-model refusal.
Five outcomes meet their full scenario and two retained model replies fail the
capability communication criterion. Six provider requests report 3,700 tokens;
USD 0.009452 is explicitly estimated, with zero unknown-cost calls. Every provider
receipt reconciles against conversation accounting. These are native observations,
not an optimization comparison against a measured legacy workload.

## Verification and remaining scope

Final native/host/real-PostgreSQL lanes: 623 passed per Python 3.11 and 3.12.
Focused admission/host tests pass 53; existing upload/ingestion tests pass 15.
Staged all-files hooks, clean native-only wheel/install probes and the normal full
pre-push are publication gates. No fixture image/PDF-generation libraries are added
to JV dependencies; browser-created bytes go directly through the SDK.

The actual-provider scope is local GPT-4.1 PNG/PDF/plain-text input. Other admitted
image and text MIME formats have deterministic envelope coverage only. Provider
format capability, corrupt media/provider failure, maximum sizes, large/hostile
and long documents, compaction of binary history, media offloading/retention,
channel-specific authenticated downloads, packaged/mobile clients and ordinary-
member upload UX remain unqualified. Typed binary data is currently inline in SDK
graph snapshots; the library's public media offloading interface is a follow-up
requirement before large-file/long-session qualification. No second state database
is introduced.

Rejected input requires a new conversation. Same-conversation rejected-input
recovery is not yet qualified. Legacy `vision` can also mean a separate interpreter
and stored artifact semantics; this local native profile does not authorize
silently retiring that active flag or removing VisionAction callers. The reference
profile retains 12 migration blockers. Full migration, distributed recovery,
security/deployment, evaluation and legacy/pilot execution deletion remain open.
