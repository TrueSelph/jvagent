---
name: lead_sync
description: >-
  Sync the qualified customer lead profile to configured external systems (Google Sheets,
  SQL databases, Airtable, Notion, etc.) via MCP when the lead profile has been updated.
requires-actions:
  - LeadProfileAction
  - LeadSyncAction
allowed-tools:
  - sync_lead
always-active: true
version: 2
tags:
  - lead
  - sync
  - mcp
---

# Lead Profile Synchronization SOP

This skill defines the SOP for syncing the lead profile to external systems. All sync
destinations are configured via `sync_servers` in `agent.yaml` — no hardcoded fields or
destinations exist in this skill. The `sync_lead` tool handles everything.

## When to Trigger

Call `sync_lead` **immediately after** `lead_profile__save` returns `"updated"`.

- **Initial sync**: After the minimum fields (`name` + `phone` or `email`) are first populated.
- **Subsequent updates**: After every `lead_profile__save` that returns `"updated"`.
- **Do not call** `sync_lead` if `lead_profile__save` returned `"deduplicated"`, `"no-op"`, or
  was not called this turn.
- **Do not skip**: `lead_profile__save` only saves locally — it does NOT sync to external systems.
  You MUST call `sync_lead` to push changes to Google Sheets and other destinations.

## How It Works

`sync_lead` reads the **entire** lead profile and pushes it to each enabled MCP server
configured under `sync_servers` in `agent.yaml`. You do not choose fields — the full profile
is always synced. Duplicate detection (SHA-256 digest) is handled internally; calling
`sync_lead` is always safe even if nothing changed.

## Adding a New Sync Target

To add a new destination (e.g. HubSpot, Airtable, a SQL table), add an entry to `sync_servers`
in `agent.yaml`:

```yaml
sync_servers:
  - server: <mcp_server_name>     # must match a server name in jvagent/mcp servers list
    tool: <tool_name>             # exact tool name exposed by the MCP server
    arguments:                    # static + template args passed to the tool
      key: "{user_id}"            # {user_id}, {profile_json}, or any profile field key
```

Enable the corresponding server under `jvagent/mcp` and provide credentials. No code changes
are needed.

## Behavior Guidelines

- **Do not expose sync status** to the user. If it succeeds or fails, continue the conversation
  naturally.
- **Do not loop**: call `sync_lead` once per turn, not multiple times.
