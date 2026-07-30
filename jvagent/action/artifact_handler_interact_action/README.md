# Artifact Handler Interact Action (`jvagent/artifact_handler_interact_action`)

Auto-detects media attachments on `visitor.data`, queues URL/file ingest, and
exposes LLM tools (`artifact_handler__*`) for list/delete/status. Async ingest
goes through **jvforge**; when a job finishes, jvforge POSTs to
`/api/artifact_handler_action/notify/{agent_id}` so the action can import the
pageindex graph into PageIndex and notify the user. Without
`JVAGENT_JVFORGE_BASE_URL`, ingest falls back to synchronous PageIndex assimilate.

Registered package id (namespace/action): `jvagent/artifact_handler_interact_action`.

## How to use

1. Enable this action on the agent (`agent.yaml`):

   ```yaml
   - action: jvagent/artifact_handler_interact_action
     context:
       enabled: true
   ```

2. Pair with the **`artifact_handler`** library skill under the orchestrator
   (`jvagent/skills/artifact_handler`). With `skills_source: library` or
   `both`, list it explicitly:

   ```yaml
   - action: jvagent/orchestrator
     context:
       skills_source: both
       skills:
         - artifact_handler
   ```

3. Ensure **PageIndexAction** and **AccessControlAction** are on the agent
   (skill `requires-actions`). For async ingest + proactive ready messages,
   set `JVAGENT_JVFORGE_BASE_URL` so jobs can callback the notify webhook.

Media attachments are ingested automatically on the interact walk; call
`artifact_handler__ingest_document` only for explicit URL ingest. Use
`artifact_handler__check_ingest_status` for ready/queued checks (no
`doc_name`/`url` args).

## License

See the application-level [LICENSE](../../../../LICENSE).

## Author

**Tharick Jairam** · jvagent/artifact_handler_interact_action / V75 Inc.
