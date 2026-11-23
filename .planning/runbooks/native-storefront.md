# Native mock Storefront profile

See the [qualification and limitations](../plans/2026-11-06-native-storefront.md).
This profile supplies two existing mock reads, not a production commerce service
or a complete migration of the LeadGen reference app.

1. Declare the existing app-local `contrib/storefront` Action with NativeHarnessAction
   as the only execution owner, plus the usual reply and AccessControl services.
2. Configure an explicit native `skill_directories` list pointing to
   `skills/product-faq` and `skills/product-search` under that agent. Preserve
   `requires-actions: [StorefrontAction]` and each skill's named `allowed-tools`.
3. Review host grants for `storefront__faq` and `storefront__search_products`.
   Skill activation never overrides host authorization.
4. Tell users the storefront is illustrative. Verify mixed FAQ/catalog requests,
   named permission refusal, restored access and restart using actual model calls.

The reference app already uses the canonical skill names in its transitional
Orchestrator list (`examples/jvagent_app/agents/jvagent/leadgen_agent/agent.yaml:34`).
Existing deployments must update old skill selectors explicitly. Do not change
their execution owner until their remaining capture/sync/extension contracts are
qualified. Tool names and result envelopes do not change
(`actions/contrib/storefront/storefront_action.py:352` under the same agent).

GPT-4.1 string declarations use a public SDK profile override for incremental
tool discovery (`jvagent/harness/native_model.py:6`). Explicit model instances
retain their caller's profile. The qualification record defines the removal
condition. Do not silently switch providers, bypass permission checks, auto-replay
unfinished effects or treat estimated dollar costs as provider-reported charges.
