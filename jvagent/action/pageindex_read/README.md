# Direct PageIndex retrieval for native skills

`jvagent/pageindex_read` subclasses the existing service and publishes only
`pageindex__search` ([implementation](pageindex_read_action.py:10)). It retains
`PageIndexAction` ancestry, so skills declaring that prerequisite can use this
implementation without changing their SKILL.md. Native dependency admission uses
actual Python ancestry, not a model-supplied alias (see
[the native interaction adapter](../native_harness/native_harness_action.py)).

The inherited service qualifies only explicitly configured `direct` retrieval as
a read tool ([classification](../pageindex/pageindex_action/pageindex_action.py:613)).
Its model-based strategies and write tools remain outside this profile. This is a
model-tool exposure profile, not an HTTP authorization boundary: inherited service
methods and administrative routing need their own access policy and qualification.

The manifest lists retrieval dependencies without the legacy LiteLLM transport
([manifest](info.yaml)). The original full PageIndex Action remains available;
indexing, tree search and nested model budget accounting must be migrated before
claiming its complete native compatibility. No dependency prerequisite was removed
from the existing `answer` skill to qualify this integration.

A disposable GPT-4.1 browser smoke exercised the existing answer skill, an empty
PageIndex query, then real Serper/fetch and cited text. This does not qualify
populated or private document retrieval. See the
[acceptance record](../../../.planning/plans/2026-11-04-harness-rebuild-progress.md).
