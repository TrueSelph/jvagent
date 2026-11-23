# Native classic PDF ingestion

An enabled PageIndex Action can opt in with `native_pdf_ingestion: true`.
Local PDF uploads run the existing classic parser for the target agent's
collection. Docling conversion and Office ingestion retain separate admission
gates. The request's optional model name can identify the host-configured
provider profile.

The classic parser keeps its worker thread and document algorithm. The host
binds one explicit completion adapter for that worker. Sync and async parser
requests return to the owner loop for public Pydantic AI execution. They share
the operation deadline, request and spend policy, and SDK lineage. Continuation
history becomes typed SDK messages. Length-limited output is available only to
parser calls that explicitly request the finish reason. The worker serializes
requests across the thread boundary to avoid racing shared budget admission.
It latches a provider failure so a parser branch that catches an exception
cannot publish a partial tree. Owner cancellation drains the active SDK request
before closing its receipts.

The worker adapter reuses the existing PageIndex bridge interception. It is
needed while the bundled classic parser calls module-level synchronous model
helpers. Remove the interception when the parser exposes an explicit completion
and tokenizer interface. The adapter adds no provider client or model loop.
It uses the existing PDF reader with bounds of 256 pages, 2 MiB extracted text,
and 100,000 UTF-8 bytes per model prompt including continuation history.
Parser size checks use a conservative byte count. Provider usage comes from
the SDK response receipts.

## Qualification

See the [browser and graph evidence](2026-11-07-native-pageindex-pdf-smoke.json)
for source epochs, request durations, per-call usage and persisted results.
Actual GPT-4.1 classic parsing and summaries produced a two-page index in the
disposable PostgreSQL app and isolated JSON PageIndex index. The browser
upload reported two chunks, which independent graph readback confirmed.
jvchat loaded the PageIndex skill and returned Quartz Room, ORBIT-42, page 1
and a clickable document source.

A browser-admin limit update tightened the configured request ceiling to one.
The refused upload made one provider request and published no root. The
initial HTTP error surfaced as a generic browser fetch failure. The final
source maps known SDK request and spend refusal to an upload validation error.
The final browser replay returned HTTP 422 after one provider call and again
left no root. Across the source epochs, 26 requests, 11,275 provider-reported
tokens and USD 0.029216 estimated cost reconcile with the graph. Reload
preserved the compared application graph records.
Focused tests cover typed continuation, cancellation draining, and failure
propagation even when the parser returns a partial result.

## Remaining gates

The browser fixture is a small text PDF. Scanned documents, Office conversion,
live jvforge, Drive sync, large PDF behavior, and duplicate or interrupted
publication recovery still require qualification. The reference converter
continues to block model-service retirement.
