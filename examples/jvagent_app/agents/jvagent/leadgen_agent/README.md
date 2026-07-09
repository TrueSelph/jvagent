# leadgen_agent — Storefront demo

Reference agent for `jvagent/leadgen`, shaped as a small **storefront sales
assistant**. It shows the leadgen capability coexisting with other skills on one
orchestrator surface, recalling returning visitors, and syncing captured leads
through the standard MCP interface — all without external credentials.

## What it demonstrates

| Capability | Where |
|---|---|
| **Conversational lead capture** (proactive, value-tied contact gap-fill) | `jvagent/leadgen` + skill `product_inquiry_leads` |
| **FAQ answering** (pricing, trial, onboarding, integrations, security) | skill `product_faq` → `storefront__faq` |
| **Product search / recommendation** | skill `product_search` → `storefront__search_products` |
| **First-message intro** woven into the reply (no double-greeting) | `jvagent/intro_interact_action` |
| **Return-visit recall** (same user, new session) | `leadgen__retrieve` over the per-user `LeadRecord` |
| **Destination-agnostic sync** to a creds-free flat file | `jvagent/mcp` `leadfile` server + action-level `sync_destinations` |

The FAQ and catalog data are mock, served by an app-local action:
`actions/contrib/storefront/` (dummy `FAQ_ENTRIES` + `PRODUCTS`).

## Layout

```
leadgen_agent/
├── agent.yaml                      # orchestrator (gpt-4.1), leadgen + sync config,
│                                   #   intro, storefront action, leadfile MCP server
├── actions/contrib/storefront/     # mock FAQ + product-catalog tools (dummy data)
└── skills/
    ├── product_inquiry_leads/      # leadgen skill (fields, aliases, gap-fill)
    ├── product_faq/                # SOP → storefront__faq
    └── product_search/             # SOP → storefront__search_products
```

## Model floor

The orchestrator runs **gpt-4.1**. Routing across three coexisting skills (and
compound multi-intent turns) needs the reasoning floor; a weaker completion model
(gpt-4o-mini) misroutes or answers without firing the right tool. See
`docs/ORCHESTRATOR.md` "Model floor".

## Sync

Sync is configured on the `jvagent/leadgen` action in `agent.yaml`
(`sync_mode` / `sync_min_fields` / `sync_require_any` / `sync_destinations`) — it
is deployment/infra, not skill semantics. The demo targets `leadfile`, a
sandboxed `jvspatial_fs` MCP server that writes each captured profile to
`<files_root>/<agent>/<user>/lead.json`. Point `sync_destinations` at any other
MCP server (spreadsheet, email, CRM, DB) with no code change.

## Run

```bash
jvagent examples/jvagent_app --debug
# chat UI: jvagent chat  (http://127.0.0.1:3000)
```
