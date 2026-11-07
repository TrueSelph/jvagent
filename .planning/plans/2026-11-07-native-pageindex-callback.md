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

## Separate host-selected callback model

The enabled PageIndex profile can set `native_callback_model`. Empty
uses the chat model. A nonempty value selects the SDK model for the
authenticated callback while retaining the native owner's settings and
limits. Request model text only verifies the host selection. Usage
settlement uses the selected operation model. Sources:
`pageindex_action.py:117`, `endpoints.py:2250`, `native_callback.py:30`,
and `native_search.py:108`.

Browser smoke at parent `19686027`, runtime diff SHA-256
`dcf3e8ce687e7df386cdda0b556057904b04363a9e0d81c3a3ed8f4d657b3435`.
A loopback browser fixture forwarded its fixed payload to the production
callback with the disposable Action-bound key and trusted proxy scheme.
The fresh PostgreSQL app kept GPT-4.1 configured for chat. OpenAI
rejected the reference Nano alias during generation, and independent
SDK controls reproduced the rejection for the alias and dated snapshot.
The browser showed HTTP 503 in 2.147 seconds. Interaction
`n.Interaction.850f18feeb9049a39a342a73` recorded `ModelNotFoundError`,
one failed attempt, no usage receipt and one unknown-cost event.
A chat-model override returned HTTP 422 in 50 ms with no Interaction
or model request.

After applying GPT-4.1 Mini to the disposable YAML and persisted Action
and restarting, the browser displayed HTTP 200 in 1.588 seconds and
`MINI-CALLBACK-VERIFIED`. PostgreSQL Interaction
`n.Interaction.6955b9d7b9714d2c83df8399` completed with one Mini call,
26 provider-reported tokens, USD 0.000021 estimated and no unknown
cost. Neither callback emitted tool events. The fixture exercised the
callback through a browser page. It verified the chat model setting
by graph readback without making a chat turn. HTTP tree search,
ingestion, and in-run ranking still need separate model selection
before the full reference migration.
