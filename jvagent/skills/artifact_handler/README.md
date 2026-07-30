# artifact_handler — library JV skill

Reusable private-document ingest / list / delete / status SOP. Discovered when
`skills_source` includes `library`/`both` and the agent's `skills` list
includes `artifact_handler` or `-all`. Requires `ArtifactHandlerInteractAction`
(`jvagent/artifact_handler_interact_action`), `PageIndexAction`, and
`AccessControlAction` on the agent.

Async ingest is backed by **jvforge** via the interact action (notify webhook
imports completed graphs into PageIndex); without jvforge, the action uses
PageIndex sync assimilate.
