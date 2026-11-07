# App-local Storefront qualification

The existing mock Storefront Action's two fixed-data reads use the generic native
Action adapter. They now declare `effect_class="read"`; the description uses a
persisted attribute. Tool names, argument aliases and JSON result shapes remain
unchanged. Source: `examples/jvagent_app/agents/jvagent/leadgen_agent/actions/contrib/storefront/storefront_action.py:345`.

Its two skills move from `product_faq`/`product_search` to official canonical
directory/frontmatter names `product-faq`/`product-search`. The example's finite
skill list and README follow the rename. The SOPs clarify handling a mixed FAQ
and product request and distinguish subscription tiers from catalog prices.
Sources: the two `SKILL.md:2` and `:24` files under the example's `skills/`;
`examples/jvagent_app/agents/jvagent/leadgen_agent/agent.yaml:34`.
This does not switch that example's existing Orchestrator deployment or qualify
its LeadGen capture, MCP file synchronization, UI rendering or other skills.

## Provider gap and regression

Three fresh ordinary GPT-4.1 browser requests repeatedly loaded capabilities but
never invoked either Action tool. Observational tracing found the correct native
tools after activation; the SDK marked them `via_history` and supplied their
definitions through Responses `additional_tools` instead of ordinary `tools`.
No Action body ran. Each request ended at SDK retry exhaustion.

A narrow override for GPT-4.1 and dated GPT-4.1 string declarations uses the public
`OpenAIModelProfile(tool_addition_mode=None)`. The SDK then owns fallback message
projection and tool declarations. There is no provider wire implementation or
additional execution engine. Other model names and explicit model instances keep
their existing behavior. Sources: `jvagent/harness/native_model.py:6`,
`jvagent/harness/native.py:590`. The public profile contract is described in the
[SDK model-profile API](https://ai.pydantic.dev/api/profiles/).
Remove the override when the same ordinary success/denial/restart cohort passes
with the upstream default. This evidence does not establish every model's support
for incremental tool discovery or qualify mini/nano variants.

The real SDK/OpenAI-adapter HTTP regression asserts the outgoing tool declaration
after capability activation. Replacing the profile with the unmodified upstream
default makes it fail because `storefront__faq` is absent from ordinary tools.
Additional regressions cover official multi-skill activation with/without SDK
persistence, real Action results, host permission denial before the body, malformed
schema inputs, forbidden model caller authority and narrow profile selection.
Sources: `tests/harness/native/test_storefront.py:1`,
`tests/harness/native/test_native_model.py:1`, `tests/examples/test_storefront_action.py:1`.

## Browser and persisted-state evidence

[Machine evidence](2026-11-06-native-storefront-smoke.json) retains all eight
turns, source blobs, worker identities, actual Action body observations, SDK
records, request timings and reconciled usage. [Browser observations](2026-11-06-native-storefront-browser.json)
preserve the final success, denial, allowed sibling tool, restart and reload UI.
Fresh disposable PostgreSQL/backend 8166 and source jvchat 3140 use actual OpenAI
GPT-4.1. Runtime functions are not patched. No personal graph is modified.

After the provider override, one reply still misused FAQ plan prices for a
catalog recommendation. That semantic failure remains recorded. The domain SOP
clarification then makes the unchanged compound request call both tools and
return the 14-day/no-card trial and Customer Insights at $249/month. Live admin
permission replacement denies an unseen onboarding FAQ: the reply withholds the
fact and the observed FAQ body count is zero. Catalog reads remain usable.
Restoring the policy and cold restarting preserves all existing records and
spending. A follow-up freshly reads onboarding (3–5 business days; Enterprise
about two weeks) and retains the earlier recommendation. Reload restores visible
tools/replies/statistics with byte-equivalent interaction/conversation records.

All eight turns reconcile 22 provider requests, 25,750 provider-reported tokens
and USD 0.043412 estimated from SDK pricing, with zero unknown usage. These are
not provider-billed dollar totals. The final compound request takes 10.240 seconds
end to end; UI provider-attempt time is 5.3 seconds. No performance improvement is
claimed from this small cohort. Conversation task arrays remain empty; native
results and execution records live in the existing graph.

Python 3.12 native/host/real-PostgreSQL passes 688 cases with the Docker profile.
Python 3.11 passes 682 with six optional profile cases skipped, followed by a
configured script/affected slice of 55 passes including those Docker cases.
Final affected tests pass 34 on each Python version. The first lint run sorts an
import; publication requires a clean staged rerun. Full pre-push and exact-head
remote checks remain separate gates.

Four pre-fix duplicate capability-load receipts remain `started` in failed
sessions. They are retained without inferring rollback or rewriting SDK facts;
failed-session reconciliation needs separate qualification. Final success,
denial, sibling read and restarted follow-up have no unfinished effects. Broader
accuracy, application migration, external effects, handoff contract equivalence,
legacy deletion, deployment and enterprise release gates remain open. The data
is explicitly mock; its synthetic security claims are not real certification.
