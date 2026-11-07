# Native local Markdown ingestion

The PageIndex upload endpoint can use the enabled native harness for local
Markdown and text ingestion when its PageIndex Action declares
`native_markdown_ingestion: true`. Native uploads are limited to the target
agent's collection. PDF and Office files require a separately qualified route.
The local native branch refuses an unqualified profile instead of calling the
legacy model transport. Existing explicit jvforge delegation remains separate.

The Markdown parser and graph publication adapter retain their existing roles.
Node summaries and the document description use the shared accounted SDK
operation, with sequential model requests under the authenticated operator's
scope and configured model. The summary decision uses a conservative UTF-8
byte threshold instead of LiteLLM tokenization. Provider receipts record
measured token usage. Generated prompts have a 100,000-byte ceiling. The
standalone graph receipt records the published root, document name and
description without copying the generated tree into the result receipt.

## Qualification

The [browser and graph evidence](2026-11-07-native-pageindex-ingest-smoke.json)
records the source epochs, frozen jvchat artifact, disposable PostgreSQL
application graph and isolated PageIndex JSON index. An authenticated browser
upload made actual GPT-4.1 summary and description requests. The initial result
persisted one chunk but returned zero because the count ran against the
application graph. The fix scopes the count to PageIndex. The final browser
upload reported one chunk, which independent index readback confirmed.

A jvchat lookup then loaded the PageIndex skill and returned the uploaded
Maple Hall and FOUNDRY-29 details with the stored clickable citation. The
cross-collection and PDF uploads were refused before provider execution.
Across both source epochs, eight provider requests, 4,969 provider-reported
tokens and USD 0.009950 estimated cost reconcile with the graph. Reload
preserved the compared records.

Focused regressions cover summary and description ordering, budget refusal
before publication, absence of the legacy tokenizer on the native path,
and chunk counts from the intended index and collection.

## Remaining gates

This slice uses existing graph publication semantics. Interruptions during
publication, duplicate upload reconciliation, PDF and Office conversion,
live jvforge, and Drive sync still require qualification. The converter
continues to block reference model-service retirement.
