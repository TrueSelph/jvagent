# Native PageIndex completion callback

New PageIndex webhook credentials are restricted to the agent's exact callback
path. Completion and graph import both verify that the authenticated key ID
matches the target Action's persisted key ID and that it carries the PageIndex
webhook permission. Existing Action-bound keys can still be used on their
own agent. A general webhook permission or wildcard route grant alone cannot
authorize the Action.

An agent with a native harness owner can enable `native_webhook_completion`
on its PageIndex Action. The callback uses that owner's configured provider
and limits. Its optional `model` field may identify the configured model,
including its matching provider alias. An override is rejected before graph
operation admission. Text and enabled inline images retain the existing
response shape. Images pass through the native media policy and byte limits.
The same SDK operation adapter used by admin search persists text, usage,
spend and status in the application graph. Native completion rejects an
unqualified profile instead of calling the legacy bridge.

## Qualification

The [browser and graph evidence](2026-11-07-native-pageindex-callback-smoke.json)
records two agents in a disposable PostgreSQL app. Same-origin browser requests
through loopback trusted proxy headers made two actual GPT-4.1 calls. Text
returned the exact test marker and the PNG was correctly described as a green
circle on white. Both responses returned measured token usage. Two provider
requests, 315 provider-reported tokens and USD 0.000738 estimated cost
reconciled with the graph. Neither request used model tools. Browser reload
preserved the compared graph records exactly.

The other agent's exact-path request returned 403 at authentication. A separate
synthetic wildcard credential passed the middleware scope check but returned
403 at the Action binding. A model override returned 422. These refusals
created no operation receipt and made no provider request. Initial browser
requests over plain HTTP were refused by the webhook HTTPS policy before
execution. The successful local replay exercised trusted forwarded scheme
handling, while production TLS qualification remains open.

Focused regressions verify exact path minting, credential refusal before
payload access, model override rejection and the persisted text and image
completion contract. Graph artifact import was outside this browser cohort.

## Remaining retirement gates

The callback reuses the standalone operation budget and its current retention
and interrupted-recovery limits. Live jvforge deployment, credential rotation,
graph artifact import and end-to-end ingestion or Drive sync still require
qualification before the reference converter can retire legacy model services.
See the [retirement boundary](2026-11-07-native-model-service-boundary.md).
