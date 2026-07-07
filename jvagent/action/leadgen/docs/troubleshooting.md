# Leadgen troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Sync never runs | Thresholds not met | Check `min_fields` / `require_any` and captured values |
| `no destinations` | Empty `sync.destinations` | Configure skill or `LeadGenAction.sync_destinations` |
| MCP errors | Server not enabled | Enable `jvagent/mcp` and matching server name |
| Wrong field key stored | Alias mismatch | Add `aliases` on field def |
| Duplicate captures | Same fields within 30s | Expected dedup — status `deduplicated` |
| Multiple skills | Ambiguous spec | Pass `skill` arg or use one leadgen skill per agent |
