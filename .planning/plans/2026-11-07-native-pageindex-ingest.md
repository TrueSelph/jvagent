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

## Separate host-selected ingestion model

`native_ingestion_model` on the PageIndex profile selects the SDK model
for native Markdown and classic PDF uploads and Drive ingestion. An empty
setting uses the chat model. Request model values only validate the
configured model. The operation receipt records its model for interrupted
Drive publication settlement, with a historical fallback for older receipts.
Sources: `pageindex_action.py:124`, `endpoints.py:923`, `:947`,
`native_ingest.py:22`, `:141` and
`pageindex_google_drive_sync_action.py:908`.

At parent `e24cea24`, an authenticated browser fixture submitted a
multipart Markdown upload to the production route on a disposable
PostgreSQL app and JSON index. Selecting the chat model in the request
returned HTTP 422 in 45 ms before starting an operation. The admitted
GPT-4.1 Mini request returned HTTP 200 in 3.119 seconds, a description
containing ORCHID-81 and one published chunk under
`n.DocumentRootNode.5d0dbd6eb8fb4c90af3bd03e`. PostgreSQL Interaction
`n.Interaction.f6d2ca18ac594134a39ba406` recorded the configured model,
the matching root and two successful requests. It recorded 509
provider-reported tokens, USD 0.000272 estimated cost and zero
unknown-cost events. The browser showed the upload response without
chat tool events. Classic PDF and Drive use the shared selection
path. This round did not exercise those transports or a crash with
an overridden model in the browser.

Runtime diff SHA-256: `7b3180ab0b4824bc38788a73a918f8a773d00cbbe8fb3ce5a585bbcdf96d3859`.
