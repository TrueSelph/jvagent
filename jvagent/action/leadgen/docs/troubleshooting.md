# Leadgen troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Sync never runs | Thresholds not met | Check `sync_min_fields` / `sync_require_any` and captured values |
| `no destinations` | No sync destinations | Set `sync_destinations` on the `jvagent/leadgen` action (agent.yaml), or a `sync:` block in the skill |
| `skipped: connector not configured` | Destination names an MCP server that isn't registered/enabled | Add the server under `jvagent/mcp`; leadgen skips gracefully until then (never blocks the conversation) |
| `MCP error … Invalid arguments` | Tool arg type mismatch (e.g. a bare `{profile_json}` re-parsed into an object where a string is required) | Wrap so the value stays a string, e.g. `content: "Lead profile: {profile_json}"` |
| Wrong field key stored | Alias mismatch | Add `aliases` on the field def |
| Duplicate captures | Same fields within 30s | Expected dedup — status `deduplicated` |
| Sync fires on every capture | (fixed) digest previously hashed internal keys | Update leadgen; the digest now excludes `_`-prefixed keys |
| Multiple skills | Ambiguous spec | Pass `skill` arg or use one leadgen skill per agent |
