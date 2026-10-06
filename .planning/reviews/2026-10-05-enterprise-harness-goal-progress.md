# Enterprise harness goal progress

Date: 2026-10-05
Goal status: Active
Branch: `codex/pydantic-inspired-skill-pilot`
Committed base: `82012aef`; working tree contains earlier uncommitted pilot changes and this goal's work. Nothing was staged or committed.

## Phase 1 — Action trust boundary and browser smoke

### Changes completed

- Replaced model-visible default parameters for the Action object and ACL label with a per-binding closure in `jvagent/action/orchestrator/pilot/tools.py:112`. The Action identity and permission label now come from server-created binding state.
- Added Draft 2020-12 JSON Schema validation before access checks, telemetry and Action dispatch in `jvagent/action/orchestrator/pilot/tools.py:120`. The pilot extra declares `jsonschema` in `pyproject.toml:47`; `uv.lock` was refreshed.
- Changed `jvagent/action/orchestrator/access.py:20` to distinguish a deliberately absent or non-applicable access policy from errors loading/evaluating a policy. Resolution and evaluation errors now deny access; an absent/non-applicable policy keeps the documented open-deployment behavior.
- Added regressions for forged wrapper keys, incorrect argument types, missing required arguments, policy resolution errors, policy evaluation errors, explicit denials and intentional policy absence in `tests/action/orchestrator/pilot/test_pilot_tools.py` and `tests/action/orchestrator/test_access.py`.

### Verification

- Pilot, access-control, materialization and delegated-tool tests passed. Three gated live evaluation tests were skipped; no failures remained.
- Black, isort, flake8, targeted mypy, `uv lock --check` and `git diff --check` passed.
- A real browser run on the updated source used an isolated app graph at `/private/tmp/jvagent-goal-smoke-f01-20261005/jvdb-clean`, served by the local backend at port 8103 and jvchat at port 3104. It used Ollama Cloud `glm-5.3:cloud` and the configured Serper search Action. The visible assistant turn showed a Reasoning panel, one `web_search__search` call, and a cited `https://serper.dev/` answer.
- The first browser turn took 27.1 seconds and displayed about 4.9k tokens. Its graph Interaction recorded three Ollama model calls with 4,910 provider-reported tokens total and `cost_usd` values of $0.0017234, $0.0024976 and $0.003586 (total $0.007807). One real Serper HTTP request returned 200. The Conversation has two completed `CAPABILITY_PILOT` tasks, each with a complete snapshot, five evidence references and a persisted cited result.
- A second post-restart browser turn on the final closure structure also completed, showing one tool call and the cited answer. The first turn's graph trace is the source for the detailed token/cost figures above.
- Provider calls and browser evidence establish these particular flows only. They do not close the grounding, failure, spending-limit or recovery findings from the code review.

## Phase 2 — Evidence receipt correctness (partial F04–F08)

### Changes completed

- `compose_skill_tools` now gives evidence observers the full Action result before clipping the separate model-visible result. This keeps valid search receipt extraction independent of the prompt-size limit.
- `PilotEvidenceCollector` only accepts fetch receipts that match the existing WebFetchAction successful text envelope. Refusal, transport error, unsupported-content and non-200 HTTP results do not become fetched-page evidence. Successful fetch receipts use the final `# Source:` URL (including validated redirect target) and bounded title/excerpt fields.
- Source IDs now use a stable SHA-256 URL identifier when the Action has no suitable short ID; the full URL remains a separate bounded field. New objectives start with an empty evidence set instead of inheriting the prior task's sources, preventing stale or unrelated prior-run receipts from authorizing citations.
- Added regressions for >4,000-character structured search payloads, failure/refusal/HTTP fetch envelopes, long URLs, and evidence carry-forward. The tool wrapper test confirms the observer receives full Action output while the model receives the clipped text.

### Verification

- Pilot/access slice: **76 passed, 3 skipped**.
- Full suite after both goal phases: **4,175 passed, 9 skipped, 31 warnings** in 136.64 seconds.
- Targeted pre-commit hooks passed: whitespace, Black, isort, flake8, mypy and detect-secrets. An initial flake8 run caught one unused local in the new collector; it was removed and the hooks rerun cleanly.
- Browser test used an isolated copied app/graph at `/private/tmp/jvagent-goal-smoke-evidence-f05-20261005/app`, backend port 8104, and jvchat port 3105. The first attempt selected the example's legacy OpenAI light-model configuration and failed with provider HTTP 401; the disposable YAML was then switched to the configured Ollama Cloud model and explicitly set to `skill_runtime: capability_pilot` using source-mode bootstrap **only in the disposable copy**. That second browser request entered Pydantic AI, emitted the Pydantic runtime banner, reached `https://ollama.com/api/chat`, and failed with HTTP 401. The UI showed the expected model-connectivity fallback. The persisted graph contains one failed capability-pilot task and no output; Serper was not called. This is a real negative-path model-call observation, not a successful provider/browser acceptance test. It indicates the copied environment's current provider credentials are unusable and needs fresh valid configured credentials before the next successful browser gate.
- The user app at port 3103 was not touched. The disposable backend and chat UI were stopped after evidence capture; its isolated files remain under `/private/tmp` for audit. No credentials were printed intentionally.

## Phase 3 — Typed claim citations and provider-error reply (F04; partial F09/F12)

### Changes completed

- Replaced freeform research brief prose with `ResearchFinding` records. Every finding has a bounded claim and one or more distinct observed source IDs. Research claims containing inline URLs/Markdown citations are rejected; citations are rendered by the host from the evidence ledger.
- The Pydantic AI output validator checks each finding's IDs against usable evidence and raises a repairable `ModelRetry`; output is checked again before publication. Pilot snapshot schema moved from v3 to v4; v3 snapshots are non-resumable by design but remain parkable during legacy rollback.
- Search results and successful fetch results now carry the deterministic source IDs visible to the model, including hashed IDs for long URLs. The JSON result clipper preserves valid JSON and trims descriptions before source IDs/links.
- Added regressions for typed output, forged/duplicate/inline citations, repairable missing references, long URL exposure, and bounded structured search output. Source membership still proves only that the model cited an observed source; it does not prove that the source supports the claim. `ConversationalReply` is also a separate factual-output escape path requiring adversarial evaluation.
- Scalar JSON clipping uses serialized-length budgeting too, so escape-heavy strings remain valid JSON and under the configured character cap. A dedicated regression covers quote, slash and newline expansion.
- Added a provider transport-error branch for `httpx.HTTPError`: it persists the failed pilot state and publishes a bounded user-facing response instead of leaving an empty assistant turn. Other generic internal exceptions continue to propagate after failure persistence.

### Verification

- Focused tool/evidence/rollback tests: **25 passed**; pilot/access slice before the final provider-error branch: **79 passed, 3 skipped**.
- Final full suite after bounded scalar JSON clipping: **4,179 passed, 9 skipped, 31 warnings** in 144.11 seconds.
- Final `pre-commit run --all-files`, `git diff --check` and `uv lock --check` passed. The tool/evidence focused tests passed **21 tests**.
- Browser run used the current source in a disposable app at `/private/tmp/jvagent-goal-smoke-claims-f04-20261005/app`, isolated JSON graph, backend port 8105, and jvchat port 3106. It made a real Pydantic AI call to Ollama Cloud (`kimi-k2.6:cloud`) and received HTTP 401 before any Serper search.
- The first live attempt (before the provider-error fix) completed in 1.09 seconds but left `Interaction.response=null`, `emitted=false`, and a failed pilot task with no evidence. After the fix, two in-browser attempts completed in 1.13 and 1.25 seconds and visibly displayed: “I couldn't complete that request because the model service returned an error. Please try again later.” The latest graph contains a failed pilot task with no output/evidence; the Interaction is closed and emitted. Provider token usage and cost are zero/unknown because the request was rejected before generation; the existing Interaction metrics report `model_call_count=0`, so request accounting still needs correction.
- This is a successful negative-path UI test, not a successful model/search acceptance test. The copied `OLLAMA_API_KEY` is currently rejected; Serper was not called. Your existing port 3103 app was untouched.

### Remaining evidence work

- F04: claim-to-source membership does not establish entailment or truth; add adversarial support, contradiction, uncertainty and calibration evaluation. Decide whether `ConversationalReply` must be gated for factual questions without introducing a brittle core intent classifier.
- F06: the collector recognizes the existing WebFetchAction text envelope; a generic typed Action result envelope remains preferable for new integrations.
- F07: no carry-forward is safe but limits genuine follow-up work; specify task-linked reuse, scope and freshness before enabling it.
- Provider errors now produce a reply for `httpx.HTTPError`; test other provider SDK/serialization exceptions and ensure usage/accounting tracks failed request attempts and elapsed time.

### Source integrity

These are the current SHA-256 digests of the implementation, tests and lock/config files changed across the recorded phases. The pilot files and Orchestrator also contain earlier working-tree changes that predate this goal; their digests identify the combined current content rather than an isolated goal-only patch. The progress document itself is omitted because it contains this table.

| File | SHA-256 |
|---|---|
| `jvagent/action/orchestrator/access.py` | `1822ed4dafd27bc55b22912830b21449c6d35904bc60eee0eb3d570691ec4665` |
| `jvagent/action/orchestrator/continuation.py` | `8923c80950e3dc80438f01b73c46e37018263513e523b73000a3495a41fc1fa7` |
| `jvagent/action/orchestrator/orchestrator_interact_action.py` | `7e0e0d3bca5be577dae57d777d33b74ce8dd93f3a2f48c179f0c0d6101dc4814` |
| `jvagent/action/orchestrator/pilot/contracts.py` | `b877f66af76f4dd5b1477eab86427aee04cd36bd9e2bfdb7773847a5d22be3c5` |
| `jvagent/action/orchestrator/pilot/runtime.py` | `e2c57fb4b9e9d90785347598bbd249c75f4cb83f5836a5044bd94a26881f29dd` |
| `jvagent/action/orchestrator/pilot/state.py` | `ea08d275cfbdce4a17a22449c5be1745b7aea3b222b1c2386d4fe6b7c30c968c` |
| `jvagent/action/orchestrator/pilot/tools.py` | `46254c4ba7561b14d12a8df04b426f49d00b80254ca6c782caf952610d02731f` |
| `docs/ORCHESTRATOR.md` | `c00f0d0ba99fe45367916fae26848b476d3000ee11af6293f24691eb6eb0cd25` |
| `tests/action/orchestrator/test_access.py` | `30c9aea0b62863057055b75cff3ea240b0a186b531cfd40703f970303f41206b` |
| `tests/action/orchestrator/pilot/test_orchestrator_pilot.py` | `5f9b539e3d646367b544dfdcb85da56e30663bfd0e5457ef6478cd1957e14713` |
| `tests/action/orchestrator/pilot/test_pilot_context.py` | `fe0756a93f8022cb0da0c9dabde47a11f828afe69cc3173c237de3c250f455a8` |
| `tests/action/orchestrator/pilot/test_pilot_contracts.py` | `54448dcb88dcf0411e805c668df3bc9f486bf8ade9bdfc8994d11004ffe688d3` |
| `tests/action/orchestrator/pilot/test_pilot_driver_selection.py` | `9a4b4ad1ee23853da9286a064f82440dbbd5ff32ea3b41ff2e3b7e146b6899e8` |
| `tests/action/orchestrator/pilot/test_pilot_evidence.py` | `58aef92ed5f9e8c7b685b84be5f5812102205ad952c8419e21d08a8eb840733b` |
| `tests/action/orchestrator/pilot/eval/blind_review.py` | `448f6159422320f782db7e5501df64238d3cfb1d059b7f37d733e34924b64483` |
| `tests/action/orchestrator/pilot/test_blind_review.py` | `87d4ad6fdc665ba16e47f055ecab404d1ae9dd0e77640637dd26822e24603ce8` |
| `tests/action/orchestrator/pilot/test_pilot_live_evaluation.py` | `c13745c6de4d5416c71aa4d059a6311a15765bcb916530ea8644b8c3b9029729` |
| `tests/action/orchestrator/pilot/test_pilot_live_model_smoke.py` | `a82100912b42b3296cbcb7a59efeccf63a2c199f8b01cbdc86c867bbd0cc30f7` |
| `tests/action/orchestrator/pilot/test_pilot_runtime.py` | `60d466af7e57dfa890a3ea87e8427fd93ce3b16adb04b35ad7330c4771c5da6d` |
| `tests/action/orchestrator/pilot/test_pilot_state.py` | `fce046845d92d151baf5fe8ecca083e0e9ee2f6f9337ad9eb3544ec7b400feca` |
| `tests/action/orchestrator/pilot/test_pilot_tools.py` | `e579709c72c10c75852c497ad782c8697328502ace1794bfadea50abb3022b63` |
| `tests/action/orchestrator/pilot/test_pydantic_ai_smoke.py` | `38e00bf8bc8a24c57f3af7f93c6c735ef22f3dc5560bcdbb98d18630a631b718` |
| `pyproject.toml` | `1ad455411f65e5b8c8626cb0556f9e7d291e135edbcbaf7c97da985ff429ff7d` |
| `uv.lock` | `a1db17cfeaaba8bf653020464a827aacedc3e6bb52b38a45acb74bab6cc7f5e1` |

## Phase 4 — Pilot dollar-budget enforcement and failure-safe settlement (F03)

### Changes completed

- The pilot now checks the existing `max_turn_cost_usd` and `max_conversation_cost_usd` before creating/resuming work, then checks again immediately before every Pydantic AI model request using the same Interaction usage/cost events as the legacy loop.
- If a completed request reaches a configured ceiling, the next request is blocked, the graph-backed pilot task is marked failed, and ReplyAction publishes a bounded budget message. A single call can cross a ceiling because provider cost is available only after the response; this is an explicit residual overshoot bound, constrained by model request/token/runtime limits.
- Conversation cost settlement now runs in `_execute_turn`'s `finally` path, so usage already recorded on Interaction is accumulated even when the driver raises or is cancelled. The read/update/save of the conversation total now occurs under the existing conversation mutation lock when a conversation ID exists.
- The pilot continues to consume provider-reported cost when present and the existing LiteLLM-backed estimate otherwise; no model price table or second accounting store was added. Unknown-pricing behavior remains governed by existing JV cost estimation.
- Added adapter coverage proving the dollar guard runs before provider invocation and driver-selection coverage proving settlement executes on a failing driver.

### Verification

- Pilot + resilience focused slice: **74 passed, 3 skipped**.
- `pre-commit run --all-files` passed after Black/isort normalization.
- Full repository suite: **4,181 passed, 9 skipped**, no failures. Skips are existing optional-dependency/PDF-engine/channel-gating gaps and opt-in live-model tests. `git diff --check` and `uv lock --check` passed.
- In-browser smoke used an isolated copied app and graph at `/private/tmp/jvagent-goal-smoke-cost-f03-20261005/app`, backend port 8106, and jvchat port 3107. The first launch revealed the shell's global jvagent lacked the optional Pydantic AI dependency; the backend was restarted with the repo `.venv` and the browser turn was repeated. A real request reached Ollama Cloud (`kimi-k2.6:cloud`) but returned HTTP 401 before generation. jvchat visibly displayed the bounded provider-error response; the graph persisted one failed pilot task and the Interaction response as closed/emitted. Request duration was about 1.05 seconds; provider usage/cost are zero because generation was rejected. Serper was not called. This verifies the real UI/provider failure path, but not a successful model response or live dollar-cap trip. Port 3103 was not touched; ports 8106 and 3107 were stopped after the smoke.
- No key material is copied into this report. The isolated app/graph remain available at the path above for inspection.

### Remaining F03 limits

- A single in-flight provider request can overshoot a dollar ceiling; there is no conservative per-request reservation because model pricing and final usage are provider-dependent.
- Rejected provider attempts with no response usage currently record neither an attempt duration nor a cost event. Their billed status cannot be inferred. This remains assigned to the provider-attempt telemetry work in the request/error lifecycle phase.
- The browser could not prove successful provider-reported or estimated cost, or call the Serper Action, because the configured provider credential was rejected.

## Phase 5 — Request fidelity and provider response semantics (F04, partial)

### Changes completed

- Removed the research prompt and proactive context's silent 2,000-character slices. Pilot request/snapshot/output contracts now admit up to 20,000 characters; longer user requests or scheduled context receive a visible ReplyAction limit notice before a task or model call is created.
- The JV adapter now maps Pydantic AI's standard sampling/token settings (`max_tokens`, `temperature`, `top_p`) to first-class `ModelRequest` fields and preserves other model settings in `ModelRequest.extra`; Pydantic `stop_sequences` maps to the provider-neutral `stop` argument. Configured orchestrator reasoning settings now reach the JV request contract and participate in the run configuration digest.
- `length`, `content_filter`, and `error` finish reasons are rejected before partial text or tool calls are consumed. The failure retains finish reason and token usage for the established user-facing fallback. Normal completion reason is carried into Pydantic AI's response object.
- Added regressions for explicit overlength handling (including no task creation), the 20,000-character contract boundary, sampling/stop setting mapping, host reasoning mapping, and partial text with a length finish reason.

### Verification

- Focused adapter/contracts/orchestrator tests: **20 passed** before formatting.
- Full repository suite: **4,184 passed, 9 skipped**, no failures. Skips remain optional dependencies/PDF engine/channel gating and opt-in live-model tests. `pre-commit run --all-files`, `git diff --check`, and `uv lock --check` passed after formatting.
- Tested source is based on commit `82012aef495b13b7f4bc638e734244974d031dd8` plus the current uncommitted worktree. SHA-256 for this round's primary files: Orchestrator `b403ea40d5822b6572ea4a1bd48509d9a04f95b3eee1d97c4463b44946884ee8`; pilot contracts `806ebd4a748cbc862ecc625a1549d645bd6b9ef4849bf0e05ce55e2c59fc40b9`; runtime adapter `c0288876b7ef6a93f5a75d92964c1f864817958aa9abe9f4fb45420b521aa089`.
- In-browser smoke used the freshly copied app and graph at `/private/tmp/jvagent-goal-smoke-model-f04-20261005/app`, backend port 8107, and jvchat port 3108. With `.venv` running, the actual configured Ollama Cloud model (`kimi-k2.6:cloud`) was called and returned HTTP 401. The visible jvchat reply was the bounded model-service error; the graph contains one failed capability-pilot task and a closed/emitted Interaction. Duration was **1.048 s**; recorded usage was **0 tokens / $0** and Serper was not reached. The request was rejected before generation, so no successful output or model-setting wire behavior was observable in-browser; unit tests cover those mappings. Ports 8107/3108 were stopped and the user app on 3103 remained untouched.

### Remaining F04 limits

- The 20,000-character cap is an explicit pilot limit, below the normal interaction ingress cap. Long requests are preserved in full within the pilot limit and rejected above it; no fallback silently processes a truncated prompt.
- Only a fixed research skill and read-only Serper/WebFetch Actions are currently admitted. Full Pydantic setting compatibility remains provider-specific; preserved extra settings may be rejected by adapters/providers that do not support them.
- A provider that returns an error without a normalized `ModelResponse` still cannot contribute response usage or billed-cost data; attempt duration/cost accounting remains open.
- Finish-reason normalization and malformed tool-argument repair across provider adapters still need broader cross-provider contract tests.

## Next phases

1. Expand the fact-support/calibration evaluation from lexical surface checks to repeated blind semantic review, and continue to constrain and assess `ConversationalReply` without adding a heuristic core intent classifier.
2. Replace WebFetchAction's string-result interpretation with a minimal typed success/refusal/error/final-URL envelope while preserving its SSRF checks and existing Action compatibility.
3. Close provider-attempt accounting gaps: request attempts, duration, failed-call usage/cost availability, and how provider transport retries consume the host dollar ceiling.
4. Complete cross-provider finish-reason and malformed-argument tests; clarify cancellation, timeout, tool error lifecycle, and live user-visible streaming/progress behavior.
5. Qualify graph-backed delivery attempts/acknowledgment and replay across crashes; multi-worker fencing, in-flight cancellation and the unavoidable pre-ack external side-effect window remain open.
6. Bind authenticated host context to server-established caller/run, expiry and replay controls; qualify skill/Action/provider and multi-worker behavior.
7. Once a configured provider credential is accepted, rerun successful model + Serper browser acceptance and the full browser failure matrix; add repeated matched evaluations, document interoperability limits, then identify redundant legacy responsibilities to retire without breaking SKILL.md or Action compatibility.

## Phase 7 — Fail-closed unknown pricing under dollar ceilings (F03)

### Changes completed

- Turn cost accounting now consumes explicit provider cost receipts first and the shared estimator otherwise. It retains a completeness flag alongside known dollars; unknown-provider pricing no longer silently counts as free when a dollar ceiling is configured.
- An incomplete cost receipt blocks further requests for that turn when `max_turn_cost_usd` is active. When `max_conversation_cost_usd` is active, settlement persists `_cost_accounting_incomplete` in the conversation context and the conversation is blocked on subsequent turns until an operator reconciles the provider usage and clears the marker. The graph remains the source of state; no new store was added.
- A valid provider-reported zero is still a complete receipt. Regression tests cover unpriced providers, valid zero costs, and conversation-level persistence/blocking. The operator recovery behavior is documented in `docs/ORCHESTRATOR.md`.

### Verification

- Focused resilience tests: **15 passed**.
- Full repository suite: **4,251 passed, 9 skipped, 31 warnings** in 142.44 seconds.
- `pre-commit run --all-files` passed after a first run caught and prompted removal of an unused import; the clean rerun passed Black, isort, flake8, mypy and secret scanning. Direct mypy on the Orchestrator passed. `uv lock --check` and `git diff --check` passed.
- Working tree is still based on `82012aef`; no files were staged or committed. SHA-256: Orchestrator `81be67e33f744dee84522708411dc0fae92bc7b3816ffc258aafa5ba627d872f`; resilience tests `a94de204816b037a5da842baadbb2191135040546d74ca2818d3dc3a338adba5`; Orchestrator guide `39cef7b4dcd34f9a0d0134b3193f02adec61930920cca94ecbe128954371c3c8`.
- At the user's request, the isolated app was restarted with the guarded development `--purge --yes` path. Only `/private/tmp/jvagent-goal-f01-repeat-20261005/app/jvdb` and its local `jvagent_logs` directory were purged. The CLI recreated the admin from that app's `.env` (`admin@jvagent.example`); the API is healthy on port 8122 and the existing isolated Messenger server remains on port 3122. The in-app browser is open at `/login`. The prior live Ollama credential returned HTTP 401; successful provider usage, cost, and Serper behavior remain unverified. A new live UI round awaits user sign-in. No credentials are stored in this report.

### Remaining F03 limits

- A provider request can still cross a configured dollar ceiling before its cost becomes available. Unknown pricing fails closed after the first unpriced call; preflight reservation is not yet implemented.
- Clearing `_cost_accounting_incomplete` is an operator reconciliation action, not an automatic retry. A supported admin workflow and audit evidence for that operation remain to be designed.
- The live UI smoke for this phase remains pending authentication; unit and repository checks are not browser qualification.

The evidence-backed review and its baseline dispositions remain at [2026-10-05-pydantic-ai-integration-review.md](2026-10-05-pydantic-ai-integration-review.md).

## Phase 27 — Treat unpriced transport retries as uncertain spend (F03, partial)

### Changes completed

- Extended the turn cost summary to consider `model_attempt` events as well as completed `model_call` / `embedding_call` usage events. Failed or cancelled provider transport attempts without a cost/usage receipt now make accounting incomplete instead of leaving a false `$0` budget result.
- Added a regression matching the real `BaseModelAction._emit_model_attempt` event shape, alongside the unknown-provider and valid reported-zero cases from Phase 7. Clarified the fail-closed rule in the Orchestrator guide.
- Rebuilt the isolated app graph at the user's request using the guarded dev-only `--purge --yes` command. It removed only that app's `jvdb` and `jvagent_logs`; fresh startup recreated `admin@jvagent.example` from that app's `.env`. Restarted the isolated API from the latest source and confirmed `/health` returned 200 with the graph connected. Messenger remains on port 3122.

### Verification and outstanding browser gate

- Focused resilience tests: **16 passed**.
- Full repository suite: **4,252 passed, 9 skipped, 31 warnings** in 143.47 seconds.
- Clean `pre-commit run --all-files` passed Black, isort, Flake8, mypy and secret scanning after the first run normalized the Orchestrator file. Direct Orchestrator mypy, `uv lock --check` and `git diff --check` passed.
- The Messenger browser is reopened at `http://127.0.0.1:3122/login`, but no user sign-in has occurred yet. This round's in-browser smoke is **pending**; repository checks are not UI evidence. The configured provider previously returned 401 in this isolated app, so a successful model-backed browser run remains an explicit qualification gate.
- Tested source is branch `codex/pydantic-inspired-skill-pilot`, base `82012aef`, plus the current combined uncommitted worktree. SHA-256: Orchestrator `1cb6aca991404f8d31d7ed2793fbde9d02cfcfa580369859196fc48e00ced803`; resilience tests `c419e4f3863bf70f628d79c17842618b912dd37351f9db6781a5f0b7004981af`; Orchestrator guide `398db974136cfab9138673b8c0a2ecab58cb4681ed9039290923bf4ef5df28f6`. Nothing was staged or committed.

### Remaining F03 limits

- Transport attempts that fail before a response are conservatively unpriced; there is still no provider reconciliation API or pre-request monetary reservation, so maximum billable overshoot is not proven.
- This round does not qualify the browser presentation of an unknown-cost block or successful live usage/cost receipts. Keep the implementation evidence provisional until the isolated login handoff permits a real UI turn and graph readback.

## Phase 6 — Malformed provider tool arguments and isolated browser smoke (F04)

### Changes completed

- The JV model contract preserves provider tool JSON in `ToolCall.raw_arguments`, but the Pydantic bridge previously forwarded the normalized fallback `{}` when that raw JSON was malformed. The bridge now parses the preserved raw payload again, rejects malformed JSON and non-object values with `PilotModelAdapterError`, and rejects disagreement between raw and normalized representations. A legitimate empty object remains valid.
- Added adversarial regressions for invalid JSON (`{oops`), valid non-object JSON (array, null and string), and empty object arguments.

### Verification

- Regression set: **5 passed**.
- Full pilot test slice: passed; three opt-in live-model/evaluation cases skipped.
- Full repository suite: passed with **9 skips** and no test failures. Existing skip reasons include optional PDF/deepgram dependencies, gated live-provider cases and unavailable checkout-local skills. Repository pre-commit checks, `git diff --check` and `uv lock --check` passed.
- Tested source is commit `82012aef495b13b7f4bc638e734244974d031dd8` plus the current uncommitted worktree. Runtime adapter SHA-256: `f465e26e53fe990ae257ad9f1ce8cd8fbd01cde2f919f82daeaae0bfa1deebc3`; regression test SHA-256: `9d7c3919af8fea3b0867f1bff50c853ef912c6bcd703dcb7af13a1c6422a0f4b`.
- Real Chrome/JV Messenger smoke used the isolated app and JSON graph at `/private/tmp/jvagent-goal-smoke-f04-malformed-20261005/app`, backend port **8111**, chat port **3110**. The copied app selected `glm-5.3:cloud` through its Ollama LanguageModelAction. A short official-documentation research request was submitted in the browser. Messenger visibly displayed: “I couldn't complete that request because the model service returned an error. Please try again later.” The server recorded an HTTP **401 Unauthorized** from `https://ollama.com/api/chat`; the call did not generate a model response or reach Serper.
- The disposable graph persisted one `CAPABILITY_PILOT` task with terminal `failed` status, `output: null`, and the provider rejection as failure reason. The Interaction was closed/emitted with the same bounded response. Task admission began at `17:54:36.136 UTC`; terminal run state was recorded at `17:54:37.268 UTC` (about **1.13 seconds**). Usage remained **0 tokens / $0**, `model_call_count=0`, and recorded duration `0`; provider-attempt telemetry therefore still fails to account for rejected requests. No model reasoning/tool events were expected because the provider rejected the request before generation.
- The user's browser smoke app on port 3103 was not touched. The isolated files are retained for audit; backend and chat processes are stopped after evidence capture. No provider keys are included in this record.

### Remaining F04 limits

- Raw provider argument behavior is unit-qualified at the JV/Pydantic boundary; no malformed tool-call payload could be induced from the live provider because its request was rejected before generation.
- Broader OpenAI/Ollama contract matrices still need real successful calls for refusals, finish reasons, multiple tool calls, reasoning, request settings and streaming/cancellation semantics.
- A successful model + live Serper browser run is still blocked on provider authorization. This negative-path run does not validate answer quality, reported token/cost usage or citation behavior.
- Request-attempt count, elapsed time and possible provider billing on request failures remain inconsistent in Interaction telemetry and are assigned to the attempt-accounting workstream.

## Phase 7 — Executable factual-evaluation checks and alternate provider smoke

### Changes completed

- Added test-only evaluation scoring in `tests/action/orchestrator/pilot/eval/scoring.py`. Live evaluation records now include explicit checks for required claim phrases, prohibited claim phrases, required source-ID coverage and use of `ResearchBrief` for factual evaluation cases. A conversational-output escape on a factual case is marked as a failure.
- The scorer explicitly returns `semantic_entailment_assessed: false`: phrase matching and source-ID presence are regression signals, not proof that a cited source entails a claim. Semantic support and calibration remain under the blinded human rubric. Tests cover a supported two-source conflict, a fabricated prohibited summary through `ConversationalReply`, and semantically plausible wording that fails exact required-phrase coverage.

### Verification

- Evaluation manifest/scorer tests: **3 passed**; the gated live evaluation remained skipped unless explicitly authorized.
- Full pilot test slice passed with the **3** opt-in live-provider/evaluation tests skipped. Full and explicit new-file pre-commit runs passed after Black/isort normalization; `git diff --check` and `uv lock --check` passed.
- Real Chrome/Messenger smoke used the separate app and JSON graph at `/private/tmp/jvagent-goal-smoke-factual-eval-20261005/app`, backend port **8112** and chat port **3112**. I selected the Orchestrator agent in the UI, configured the disposable copy to use `gpt-4.1` via the existing OpenAI LanguageModelAction, and submitted a web-research request. The Pydantic AI runtime was entered and `https://api.openai.com/v1/chat/completions` returned **401 `invalid_api_key`**. Messenger showed the bounded model-service error; Serper was not called and no assistant tool/reasoning events were produced.
- The isolated graph persisted one failed capability-pilot task with no output. Its task timestamps were `18:05:13.476280 UTC` to `18:05:14.205099 UTC` (about **0.73 seconds**); the Interaction was closed/emitted with the visible fallback response. Usage was **0 tokens / $0**, `model_call_count=0` and recorded duration `0`, so failed-request attempt telemetry remains open. This is an OpenAI rejection in addition to the previously recorded Ollama Cloud 401; both configured provider credentials currently fail authorization in this disposable environment.
- Startup succeeded and the API served the browser flow, but optional Action dependency installation logged errors because this `.venv` has no `pip` module and the optional Deepgram SDK is absent. These are smoke-environment setup warnings; the provider authentication result came from a real OpenAI request. The user's port 3103 app was untouched; ports 8112/3112 were stopped and disposable graph files retained for audit.

### Remaining evaluation limits

- Surface checks improve repeatability but cannot establish claim entailment, source quality, uncertainty calibration, or factuality of arbitrary free text. The adversarial factual-case runner must continue requiring blinded human review and the conversational-output failure flag; a semantic evaluator or separately bounded verification capability requires its own cost, accuracy and failure qualification before admission.
- The direct factual `ConversationalReply` output remains a runtime escape for non-evaluation turns. No brittle core intent classifier has been added. This remains unresolved as a safety/behavior contract; it needs a user-visible mode/scope decision or a verified alternative before broad research admission.
- Positive model responses, tool calls, answer scoring and live Serper behavior remain unverified because both provider keys returned 401.

## Phase 8 — Typed WebFetch receipts and browser/provider smoke (F06, partial)

### Changes completed

- Added `ToolResultText`, a `str`-compatible Action result that carries structured host metadata through the existing `Tool.call()` boundary without changing the Action tool name, schema, or model-visible text contract. `Tool.call()` preserves that metadata in `ToolResult`; the pilot observer receives it through the existing result-observer seam.
- `WebFetchAction.fetch()` still returns the existing bounded readable text, but now attaches a `web_fetch_result` receipt with outcome (`success`, `refused`, or `error`), requested URL, validated final redirect URL, content type, and HTTP status. A success receipt is emitted only for HTTP 200 accepted content; the existing per-hop DNS/IP validation, redirect policy, byte limit, content-type policy, and extraction remain in force.
- `PilotEvidenceCollector` creates a fetch citation only from a typed successful receipt whose requested URL matches the Action call and whose HTTP status is 200. It no longer treats `# Source:` text as proof of a fetch. Source IDs are attached using the stored requested-to-final URL alias, so redirects remain citable without parsing the rendered source header.
- Added regressions for readable-string compatibility, receipt propagation through `Tool.call()` and pilot tool composition, final redirect URL metadata, failed/forged/mismatched fetch receipts, and source-looking text without a typed receipt.

### Verification

- WebFetch, evidence collector, pilot tool-composition, and tool-decorator slice: **54 passed**. `git diff --check` and `uv lock --check` passed.
- Full repository suite: **passed at 100% with 9 skips** before the final observer-propagation regression was added; the later focused run includes that regression. Skips were optional PDF/Deepgram dependencies, gated live provider tests, unavailable checkout-local skills, and an empty parameter set. Existing deprecation/pytest warnings remain.
- `pre-commit run --all-files` passed after formatting and after the final regression, including Black, isort, Flake8, mypy and detect-secrets.
- Real Chrome/JV Messenger smoke ran on disposable app `/private/tmp/jvagent-goal-smoke-typed-fetch-20261005/app`, backend port **8113**, and chat port **3113**, with an isolated JSON graph under that app's `jvdb`. The browser visibly accepted the turn and showed: “I couldn't complete that request because the model service returned an error. Please try again later.” The app entered the Pydantic AI runtime and made a real Ollama Cloud request (`kimi-k2.6:cloud`); `https://ollama.com/api/chat` returned **401 Unauthorized** before generation. No Serper or WebFetch Action call occurred, so the typed fetch path is covered by unit/integration tests rather than a successful live browser turn.
- The graph persisted a terminal failed `CAPABILITY_PILOT` task with `output: null`, empty evidence, and a 401 failure reason. Its recorded task interval was **0.482 s**. The Interaction persisted the visible fallback response with `closed=true` and `emitted=true`; its start-to-completion interval was **1.575 s**. The provider rejected the request before returning usage; current Interaction metrics show **0 tokens, $0, 0 model calls, and 0 recorded model duration**, despite the observed network attempt. These counters do not yet represent rejected provider attempts and remain assigned to the request-attempt telemetry workstream.
- The user's Messenger on port 3103 was not touched. Disposable backend and chat processes were stopped after evidence capture; smoke graph and app files remain for audit. No provider key was recorded in this document.

### Remaining F06 limits

- `ToolResultText` metadata is a trusted Action-to-harness side channel, not a cryptographic receipt. Only server-loaded Actions can construct it; model arguments cannot supply or override it. Cross-process Action execution would need a separately authenticated envelope.
- The browser smoke did not generate a successful model response or invoke Serper/WebFetch because the provider credential was rejected. A new successful model + fetch + cited-response browser acceptance run is still required after valid provider authorization is available.
- Title text remains extracted from the backwards-compatible rendered header for display; receipt success and final URL no longer depend on that header. If titles become policy-bearing, move them into the typed receipt too.

## Phase 9 — Provider attempt telemetry (F03, partial)

### Changes completed

- The shared `BaseModelAction._execute_with_retry()` now emits one `model_attempt` event for each actual coroutine execution, including successful attempts, failed attempts, retry attempts, and cancellation. A malformed operation factory is not counted as a provider attempt.
- Attempt events record operation name, attempt number, provider/model, outcome, duration, safe error class, and HTTP status where available. They deliberately omit prompt/request content, credentials, token counts, and cost; failed requests with no provider usage response are labeled `usage_status=provider_unreported`.
- `Interaction.compute_usage()` now exposes `model_attempt_count`, `failed_model_attempt_count`, and `model_attempt_duration_seconds`, while leaving `model_call_count`, token/cost totals, and successful-call duration semantics unchanged. Failed provider calls therefore become visible without being mistaken for priced/zero-token successful work.
- Added regressions for a non-retryable 401, a transient failure followed by successful retry, and separate attempt versus successful usage aggregation.

### Verification

- Focused retry, observability, and usage tests: **31 passed**.
- Full repository suite: **passed at 100% with 9 skips**. Skips cover absent optional PDF/Deepgram dependencies, opt-in live-provider/evaluation tests, unavailable checkout-local skills, and an empty parameter set. Existing Python 3.10, PyPDF2 deprecation, pytest-mark, and JWT test-key warnings remain.
- Applicable pre-commit checks passed after the first pass applied Black formatting: Black, isort, Flake8, mypy, and detect-secrets. `git diff --check` and `uv lock --check` passed.
- Real Chrome/JV Messenger smoke ran on the disposable app at `/private/tmp/jvagent-goal-smoke-attempt-telemetry-20261005/app`, backend **8114**, chat **3114**, with a separate JSON graph and file store. The browser sent a real user prompt through Orchestrator and the configured Ollama Cloud model (`kimi-k2.6:cloud`). The provider returned HTTP **401**; Messenger displayed the bounded model-service error.
- Persisted Interaction `n.Interaction.f49f46408066460fb4935b6a` shows `model_attempt_count=1`, `failed_model_attempt_count=1`, `model_attempt_duration_seconds=0.259`, `model_call_count=0`, zero tokens and no cost. The attempt event reports outcome `failed`, `HTTPStatusError`, `status_code=401`, and `usage_status=provider_unreported`. The Conversation persisted one failed `CAPABILITY_PILOT` task; the Interaction response is closed and emitted. No Serper/WebFetch call occurred.
- Personal Messenger on port 3103 was left untouched. The isolated backend and chat processes were stopped after evidence capture; the disposable app and graph are retained for audit. Optional-action dependency setup logged warnings because this virtualenv lacks `pip`; the server nevertheless started and the exercised orchestration/model path ran.

### Remaining F03 limits

- Real provider retry costs remain unknowable when failed attempts provide no usage/cost receipt. The new counters expose attempt counts and latency, but do not settle unknown provider charges or reserve estimated cost against dollar ceilings. A conservative retry-reservation policy still needs provider/pricing policy decisions and tests.
- A successful model response with provider-reported usage/cost is not currently possible in this environment because the configured Ollama credential is rejected with 401. A successful-generation browser run remains a qualification gate.

## Phase 10 — Cancelled TurnRun consistency (F13, partial)

### Changes completed

- `OrchestratorInteractAction.execute()` now handles `asyncio.CancelledError` explicitly, transitions the active native TurnRun to `CANCELLED`, and re-raises cancellation so upstream task cancellation semantics are preserved. The existing `finally` path still writes the checkpoint to the Interaction.
- Added an end-to-end Orchestrator regression that injects cancellation into `_execute_turn()` and verifies both the in-memory journal and persisted Interaction checkpoint are terminal `CANCELLED`. This closes the mismatch where the pilot TaskStore was cancelled but the outer TurnRun remained `RUNNING`.

### Verification

- `tests/action/orchestrator/test_harness_recovery.py`: **2 passed**.
- Full repository suite: **passed at 100% with 9 skips**. Applicable pre-commit checks passed after isort normalized imports (Black, isort, Flake8, mypy, detect-secrets); `git diff --check` and `uv lock --check` passed.
- Real browser smoke ran on the disposable app at `/private/tmp/jvagent-goal-smoke-cancel-lifecycle-20261005/app`, backend **8115**, chat **3115**, with isolated graph and file store. Messenger submitted “Define a Python list in one sentence.” through Orchestrator to configured Ollama Cloud (`kimi-k2.6:cloud`); the actual request returned HTTP **401**, and the UI rendered the bounded model-service error.
- Persisted Interaction `n.Interaction.acc04ed1a6e1469ab348d344` is closed/emitted and records one failed model attempt, **0.629 s**, HTTP 401, provider usage unreported, and zero reported/estimated tokens/cost. The native TurnRun checkpoint is `completed` because the handled provider failure was returned as a user-facing assistant error; that is distinct from cancellation and consistent with current behavior. The personal Messenger on port 3103 was untouched. Isolated services were stopped after evidence capture.

### Remaining F13 limits

- The live browser request failed authorization too quickly to exercise the user's Stop action; browser-driven cancellation propagation through provider transport and client disconnect remains unverified. The regression proves the outer Orchestrator cancellation state transition, not a full network cancellation/recovery journey.
- A credentialed successful-provider browser run remains blocked by the configured provider's 401 response. This round does not qualify graceful cancellation during an in-flight provider request, cancellation settlement failure, or cancellation during egress.

## Outstanding acceptance work

1. Complete evidence qualification: fresh research output is now restricted to `ResearchBrief`, but quote provenance does not prove claim entailment or calibration. Run blinded human semantic review and successful live search/fetch/cited-answer browser flows.
2. Finish budget settlement for failed provider retries, especially unknown billed cost before transport retry; measure a defensible per-turn overshoot/reservation bound.
3. Complete request lifecycle behavior: cross-provider finish reasons/settings, malformed tool arguments, timeout/cancellation at the transport boundary, coherent tool-error events, and actual live streaming/progress.
4. Define graph-backed invocation checkpoints, replay, abandoned-run recovery, stable delivery IDs, egress acknowledgement, and response/task reconciliation without a second state database.
5. Bind signed host context to server-established caller/run identity, expiry, and replay controls; qualify multi-worker leases and cancellation under shared storage.
6. Publish a compatibility matrix for skills, Actions, providers, MCP, interviews, delegation, and writes; add adversarial security and deployment tests before admitting unsupported classes.
7. Measure repeated quality/cost/latency against the legacy driver on representative cases, then simplify or retire redundant legacy responsibilities based on evidence while preserving SKILL.md and Action interoperability.

## Phase 11 — Interrupted pilot recovery boundary (F11, partial)

### Changes completed

- Added `PilotTaskStore.active_run()` to locate a currently active run only when caller, skill digest and configuration digest all match. Its contract relies on being called after the new interaction has entered the existing conversation mutation lock; at that point a matching active task indicates an interrupted persisted run rather than a concurrent ordinary turn.
- Added `fail_interrupted()` to terminalize that run as failed while preserving its last typed snapshot and evidence. Before the harness starts another provider call, the Orchestrator now sends a visible recovery notice asking the user to repeat the request to start fresh. This prevents a stale active task from being silently ignored or overlapped by an implicit second run.
- Added TaskStore-backed tests for identity-scoped detection, checkpoint/evidence retention, persisted failure reason and removal from the active set.

### Verification

- Pilot and native harness recovery slice: **44 passed, 3 skipped** (the three skips require explicit live-provider/evaluation authorization). `pre-commit run --all-files` and an explicit changed-file run passed after formatting. `git diff --check` and `uv lock --check` passed.
- Full repository suite: **passed at 100% with 9 skips**. Skips were optional PDF/Deepgram dependencies, three gated live-provider/evaluation cases, unavailable checkout-local skills, and an empty parameter set. Existing Python 3.10 end-of-support, PyPDF2, pytest-mark and test JWT-key warnings remain.
- Real Chrome/JV Messenger smoke used isolated app `/private/tmp/jvagent-goal-smoke-interrupted-recovery-20261005/app`, backend port **8116**, and chat port **3116**, with a separate app graph and files root. The browser signed in as the disposable smoke user, submitted “Define a Python list in one sentence.” through Orchestrator and showed the bounded model-service error. The configured Ollama Cloud endpoint returned **401 Unauthorized**. The graph persisted failed task `pilot_6d97a0a23df94bd7b337603b4594d8df` (no output) and Interaction `n.Interaction.775217dc105a4d2dba23dfa3` (`closed=true`, `emitted=true`) with the visible fallback. Task duration was **0.282 s**; model attempt telemetry recorded **1 failed attempt / 0.252 s**, HTTP 401, provider usage unreported, **0 tokens** and **$0 reported/estimated cost**. This does not qualify successful generation, billing on rejected requests, or Serper/WebFetch calls. Both disposable services were stopped; the user's port 3103 Messenger was untouched.

### Remaining F11 limits

- This phase defines crash recovery as **fail and require explicit restart**, rather than unsafe implicit replay. It does not yet restore Pydantic AI message history or exact mid-run execution.
- Token and tool-call use are not accumulated across explicit restarts, and elapsed time across hard crashes is not yet settled. Dollar budget protection remains conversation/turn based but is not a substitute for lifetime token ceilings. Add durable request/tool invocation and usage accounting before treating this as full restart-budget control.
- Multi-worker lease expiry/fencing and cancellation recovery at provider transport boundaries remain unqualified.

## Phase 12 — Durable model/tool budget accounting across retries (F11/F03, partial)

### Changes completed

- Upgraded `PilotSnapshot` to schema v6 with durable model-request, unsettled-request, external Action invocation, provider-reported token, estimated-token, and usage-accounting-completeness fields.
- Before each Pydantic-level provider dispatch, the pilot now persists the incremented request count and an unsettled-request marker. A normalized response settles that marker and records token usage as provider-reported or estimated. A failure that yields no usage-bearing response keeps accounting explicitly incomplete instead of recording the request as zero-cost/zero-token work.
- External Action calls are checkpointed before invocation. An exact repeated failed question inherits the failed run's known lower-bound usage and is stopped before another model request when prior usage remains unknown; it cannot silently reset its request budget.
- Migrated schema v4 snapshots by initializing unknown counters conservatively and schema v5 snapshots by preserving their known counters while marking one unresolved request. Both migrate to v6 with accounting incomplete. Future schema versions and the unsupported v3 shape remain preserved and rejected for resume.
- Removed previous-task evidence carry-forward into a new objective. A completed earlier research run no longer authorizes sources for an unrelated follow-up.
- Added migration, failed-retry inheritance, response settlement, pre-dispatch persistence, and usage-observer regressions.

### Verification

- Focused contract, state, runtime and Orchestrator pilot tests: **47 passed**.
- Full repository suite: completed successfully after formatting, with **9 skips** and no failures. The initial full run overlapped Black's formatting and produced one source-inspection failure; after formatting completed, the focused test passed and the complete suite passed on the normalized tree.
- `pre-commit run --all-files` passed on the normalized tree (YAML/JSON, whitespace, Black, isort, Flake8, mypy and detect-secrets). `git diff --check` and `uv lock --check` passed.
- Real Chrome/JV Messenger smoke used a fresh disposable app at `/private/tmp/jvagent-goal-smoke-usage-accounting-20261005/app`, isolated JSON graph `jvdb/usage-accounting`, backend port **8118**, and Messenger port **3118**. The user-facing Orchestrator accepted “Define a Python list in one sentence.” and displayed: “I couldn't complete that request because the model service returned an error. Please try again later.” A real Ollama Cloud request to `https://ollama.com/api/chat` returned **401 Unauthorized** before generation.
- Persisted task `pilot_41a92d9f8600406da6c211af0522bd96` is terminal `failed`, snapshot schema **6**, with `model_requests_used=1`, `unsettled_model_requests=1`, `usage_accounting_complete=false`, `tool_calls_used=0`, no reported or estimated tokens, and no output. Interaction `n.Interaction.47d28d1e4d784f0d8e8b2e5f` is closed/emitted with the visible fallback and one failed provider attempt lasting **0.254 s**; the total task interval was about **0.282 s**. This is the intended conservative record for an attempted request whose provider returned no usage receipt. Serper was not called.
- Disposable backend and chat processes were stopped after verification. The user's browser app on port 3103 was untouched. No credentials were recorded in this report.

### Remaining F11/F03 limits

- Request/tool counters are enforced across an exact same-question failed retry, but the harness still does not replay Pydantic AI message history or resume an exact in-flight Action. It deliberately requires a fresh, explicit run.
- A request rejected before usage is returned remains financially unknown. The task is marked incomplete and the exact retry is blocked, but no conservative dollar reservation can be computed from the rejected response.
- Multi-worker leases/fencing, end-to-end cancellation against an in-flight provider request, and delivery acknowledgement/reconciliation remain open. The current smoke is a real negative provider path; successful model usage/cost, Serper execution, and final citation rendering remain blocked by provider authentication failures in this environment.

## Phase 13 — Provider-neutral model cost provenance (F17, partial)

### Changes completed

- Cost normalization now accepts finite, nonnegative provider-reported amounts, including **$0.00**, and rejects booleans, negatives, NaN, and Infinity. A legitimate free response is therefore no longer overwritten by an estimate.
- Model telemetry and normalized `Usage` now carry a provider-neutral cost record with amount, currency, source, estimated/reported flag, and pricing version. Existing `cost_usd`, `cost_source`, and `cost_estimated` fields remain for compatibility.
- LiteLLM and Ollama response handling now preserve valid zero cost. Aggregation separates provider-reported, estimated, and unknown cost; `litellm_cost_usd` remains as a compatibility bucket. When no known pricing exists, usage is represented as unknown rather than silently classified as free.
- Added regressions for zero-cost preservation, non-LiteLLM reported costs, non-finite values, estimates, and unknown pricing.

### Verification

- Focused model/usage regression slice: **62 passed**.
- Full repository suite: **passed with 9 skips** and no failures. Applicable pre-commit checks passed after Black/isort normalization: YAML/JSON, whitespace, Black, isort, Flake8, mypy, and detect-secrets. `git diff --check` and `uv lock --check` passed.
- The browser smoke used source from base revision **`82012aef` plus the current uncommitted working-tree changes**; these changes have not been committed.
- Real browser smoke used fresh disposable app `/private/tmp/jvagent-goal-smoke-cost-record-20261005/app`, its isolated JSON graph `jvdb/cost-record`, backend **8119**, and jvchat **3119**. Signed in and selected Orchestrator Agent, then submitted “Define a Python list in one sentence.” The actual configured Ollama Cloud request returned **401 Unauthorized**. Messenger visibly rendered “I couldn't complete that request because the model service returned an error. Please try again later.” The provider request took **0.267 s**; the persisted Interaction completed/emitted in about **1.20 s** and persisted the visible fallback.
- Graph evidence: Interaction `n.Interaction.f027cfc78ff441069d44ef12` persisted one failed model attempt, status 401, provider usage unreported, and no successful model call. The associated `CAPABILITY_PILOT` task is terminal `failed`, with one model request and one unsettled request; no tool invocation occurred. There is no provider usage or cost receipt from which to calculate a charge. The Interaction cost aggregate remains zero for this rejected request, so this smoke does **not** prove reported-cost mapping or estimated cost display. No Serper/WebFetch request occurred.
- Disposable backend and chat processes were stopped. The separate personal Messenger on port 3103 was untouched. No credentials were added to this record.

### Remaining F17 limits

- A successful response with actual provider usage/cost is still required to qualify provider field mapping and end-to-end attribution. Current Ollama Cloud authentication prevents that test.
- Provider cost on failed/unsettled requests remains financially unknown; the Interaction aggregate should not be interpreted as a statement that no charge occurred. Reconcile failed-attempt uncertainty with turn-dollar reservations and durable settlement before claiming complete cost control.
- Verify currency normalization and pricing-version provenance across the supported provider matrix, including LiteLLM's nested response shapes, and validate long-running cumulative user totals and exports.

## Phase 14 — Hardened tool binding and access-policy audit events (F01/F02, partial)

### Changes completed

- Added `_tool_name` and `_original` to the reserved model-argument set. They are rejected recursively even when an Action's JSON Schema permits additional properties, so these names cannot be reinterpreted downstream as Action extension arguments.
- Added a full Pydantic AI `Agent.run` adversarial test that supplies a forged `_tool_name` through a schema with `additionalProperties: true`; it verifies the Action and ACL callback are never reached. Existing schema validation continues to reject missing required values and incorrect types.
- Access-control denials now emit structured policy-denial records. Policy discovery or evaluation exceptions emit a structured error record with the ACL label, channel, stage, event, and exception class, while omitting the exception message to avoid leaking database/configuration secrets. Both paths deny dispatch; intentionally absent/non-applicable policy behavior remains explicitly open for compatibility.
- Added an assertion that a resolution failure is denied and logged as a structured, redacted event.

### Verification

- Focused access, tool-composition, and Pydantic AI run tests: **20 passed**.
- Full repository suite: **4,215 passed, 9 skipped, 31 warnings** in 148.69 seconds. `pre-commit run --all-files` passed after Black/isort formatting; `git diff --check` and `uv lock --check` passed.
- Real browser smoke used source based on **`82012aef` plus current uncommitted changes** in isolated app `/private/tmp/jvagent-goal-security-audit-20261005/app`, graph `jvdb/security-audit`, backend **8120**, jvchat **3120**. Messenger signed in, selected Orchestrator Agent, and submitted “Hi, please reply with one word.” A real request to Ollama Cloud (`kimi-k2.6:cloud`) returned **401 Unauthorized**. The UI showed the bounded model-service error. The provider attempt took **0.317 s**; the graph Interaction `n.Interaction.96d23812133f45d4a0480ca7` completed and emitted after about **1.264 s** with one failed attempt and one unsettled pilot request. No tool ran; provider tokens/cost remain unreported, not verified zero. The screenshot showed the persisted user prompt and bounded failure. The user's port 3103 Messenger was untouched.
- Both disposable services were stopped after the run. The isolated graph remains available for inspection.

### Remaining F01/F02 qualification

- The hostile tool call is now exercised through the actual Pydantic AI Agent runtime, but offline with a deterministic FunctionModel. A complete backend/browser run in which a configured live model requests a forged reserved field is not demonstrated; the configured endpoint rejects requests before tool selection.
- ACL policy lookup, policy applicability, and denial logging regressions use controlled test doubles. A persisted structured access-denial event from a graph-backed AccessControl policy failure still needs integration-level verification.
- Confirm structured event field retention and alert/report consumption in each production logging sink; the smoke backend did not invoke ACL failures and cannot qualify that operational path.

## Phase 15 — Preserve JV model request controls across the Pydantic adapter (F09, partial)

### Changes completed

- The configured-JV-Action bridge now maps Pydantic AI model settings for `tool_choice`, `parallel_tool_calls`, `response_format`, `reasoning_effort`, and `reasoning` into the corresponding typed `ModelRequest` fields.
- These controls are removed from generic provider extras after mapping; existing unknown/provider-specific settings continue through `ModelRequest.extra`, and explicit trusted host reasoning overrides retain precedence over model-level values.
- Extended the configured-Action adapter regression to assert all five typed controls and preserve existing passthrough assertions. This uses the single existing Action-backed adapter and adds no second model configuration registry.

### Verification

- Focused adapter/runtime tests: **16 passed**.
- Changed-file pre-commit hooks passed: trailing whitespace, Black, isort, Flake8, mypy, and detect-secrets.
- Full repository suite: **4,215 passed, 9 skipped, 31 warnings** in 139.22 seconds. Warnings include Python 3.10 support ending, PyPDF2 deprecation, existing pytest async-mark warnings, and short test JWT keys.
- `git diff --check` and `uv lock --check` passed. Tested source was base **`82012aef` plus current uncommitted working-tree changes**; no commit was created.
- Real Chrome/jvchat smoke used isolated app `/private/tmp/jvagent-goal-smoke-model-controls-20261005/app`, backend port **8121**, chat port **3120**, and a separate fresh JSON graph. Browser sign-in, Orchestrator selection, prompt submission, visible completion, and graph persistence all worked after correcting the disposable app's CORS origin and replacing a stale saved test account. Prompt: “Please say hello in one short sentence.”
- Messenger displayed: “I couldn't complete that request because the model service returned an error. Please try again later.” Interaction `n.Interaction.865c8df7258d493ba262132c` is closed and emitted. Task `pilot_fd7a4f9eeedb4e85be092048d6bb41ab` is terminal `failed`; schema 6 recorded one model request, one unsettled request, incomplete usage accounting, zero tool calls, no output, and no reported/estimated tokens. The model attempt was `ollama` / `kimi-k2.6:cloud`, HTTP **401**, duration **0.279 s**, `usage_status=provider_unreported`; the task interval was **0.306 s**. The response came from `https://ollama.com/api/chat`. No Serper/fetch Action ran. The returned HTTP error provides no evidence that the provider charged zero.
- The screenshot confirmed the prompt and bounded UI error. Both temporary services were stopped and the disposable app directory (including copied `.env`, graph, and logs) was removed after recording the redacted evidence summary. The user's port 3103 Messenger was untouched.

### Remaining F09 qualification

- The new typed setting mappings have deterministic Agent/runtime tests, but the live provider rejected authentication before request handling could exercise tool-choice, parallel-call, response-format, or reasoning behavior. Provider-specific compatibility must still be tested against working OpenAI and Ollama Cloud credentials, including unsupported setting failures and provider response finish reasons.
- Streaming remains event-compatible rather than live model-token streaming. Provider request IDs/reasoning replay metadata and full cancellation behavior remain open under F09/F13/F15.

## Phase 16 — Close pilot tool events when an Action is cancelled (F13/F14, partial)

### Changes completed

- `compose_skill_tools` now catches `asyncio.CancelledError` around Action invocation only long enough to emit a paired `tool_result` event with the explicit marker `(tool error: Action call cancelled)`, then re-raises cancellation unchanged.
- The event callback is shielded during this best-effort closure so the normal cancellation does not interrupt the terminal event write. Observer errors are logged with tool/run context; cancellation remains the caller-visible outcome.
- Expanded the Action failure/cancellation regression to assert both the initial `tool_call` and paired terminal `tool_result`, distinguish the cancellation marker from an ordinary error, and confirm no evidence is recorded.
- Existing outer Orchestrator cancellation coverage already verifies that the graph-backed TurnRun ends in `CANCELLED`; pilot-level cancellation coverage verifies the pilot TaskStore becomes terminal `cancelled` and propagates cancellation. This phase connects the missing tool-event edge in that lifecycle.

### Verification

- Focused tool and TurnRun recovery tests: **14 passed**.
- Changed-file pre-commit passed after Black reformatted `tools.py`: trailing whitespace, Black, isort, Flake8, mypy, and detect-secrets. `git diff --check` and `uv lock --check` passed.
- Full repository suite: **4,215 passed, 9 skipped, 31 warnings** in 148.08 seconds.
- Real Chrome/jvchat smoke used isolated app `/private/tmp/jvagent-goal-smoke-cancelled-tool-event-20261005/app`, backend **8122**, chat **3122**, and a fresh JSON graph. The browser authenticated, selected Orchestrator Agent, submitted “Please say hello in one short sentence.”, and displayed the bounded model-service error. Interaction `n.Interaction.ef88314e1d2c4dbca2b6182c` is closed/emitted. Task `pilot_092f3152bd2f4a3a819c5c5896fc7f20` is terminal `failed`, schema 6, with one model request, one unsettled request, incomplete usage accounting, zero tool calls, no output, and no reported or estimated tokens.
- The actual configured `ollama` model `kimi-k2.6:cloud` call to `https://ollama.com/api/chat` failed with **401 Unauthorized** after **0.255 s**; total task interval was **0.302 s**. Usage/cost is unreported, not confirmed zero. No Action/tool call occurred, so this browser smoke does not itself exercise cancellation; the cancellation evidence is the deterministic wrapper and TurnRun tests. The Messenger screenshot showed the submitted request and fallback.
- Backend and chat services were stopped, the temporary browser tab was closed, and the isolated app directory (including copied `.env`, graph, and logs) was removed after writing the redacted evidence summary. The user's port 3103 Messenger was untouched. Tested source was base **`82012aef` plus current uncommitted changes**; no commit was created.

### Remaining F13/F14 qualification

- Cancellation has deterministic coverage at a controlled Action and at outer TurnRun level, but model-wait cancellation, evidence-checkpoint cancellation, repeated cancellation during cleanup, and production TaskStore persistence failure still need failure-injection tests.
- The terminal event closure is best effort if its observer/storage callback fails. Current event payload uses the established error-string convention; F14 still needs explicit typed status independent of rendered text and consistent error/retry semantics for ordinary read failures.
- A browser-driven disconnect/cancel/reconnect/replay scenario and its retained event state remain unqualified.

## Phase 17 — Bind trusted host context to caller and single-use request (F16, partial)

### Changes completed

- Replaced v1 host context with a v2 signed envelope bound to issuer, audience, agent, resolved user, session, and host run ID. Envelopes have a dedicated `JVAGENT_HOST_CONTEXT_SECRET` (no JWT-key fallback), a short bounded expiry, nonce, 64 KiB size limit, and strict field validation.
- The request endpoint now performs the empty-utterance trust check only after identity resolution, using the effective authenticated user. `InteractWalker` consumes the signed envelope only after resolving the live conversation and records its nonce in that graph-backed Conversation before exposing the context to the Orchestrator. Save failure, malformed state, missing server correlation ID, expiry, identity mismatch, replay, or a full ledger fail closed.
- Both embed entry points use the same identity-bound verification helper. The Orchestrator now reads only the walker's verified private context; raw caller `data` is never promoted to system instructions.
- Documented the new secret and host signing contract, added the integration signer helper, and added adversarial tests for identity binding, expiry, tampering, dedicated-secret separation, replay, persistence failure, and promotion of unverified data.
- A targeted Flake8 pass found a stale empty-input branch referencing an undefined `empty_host_turn`; removed the duplicate branch so identity-bound validation is the sole check.

### Verification

- Focused host-context/endpoint/embed regressions: **47 passed**.
- Broader interaction, Orchestrator, and embed slice after the stale-branch removal: **931 passed, 4 skipped, 27 pre-existing async-mark warnings**.
- Changed-file pre-commit hooks passed after Black/isort normalization: whitespace, Black, isort, Flake8, mypy, and detect-secrets. No commit or staging was performed. `git diff --check` remains to be included in the next repository-wide gate.
- The browser smoke used source at base **`82012aef` plus the current uncommitted working-tree changes**. Chrome/jvchat used isolated app `/private/tmp/jvagent-pydantic-browser-smoke-20261005`, graph `graph2`, backend **8013**, and chat **3113**. The disposable app CORS allowlist was corrected for port 3113; the personal Messenger on port 3103 was not changed.
- The browser authenticated, selected Orchestrator Agent, submitted “Use your research skill to find a current official population estimate for Georgetown, Guyana. Give the estimate, year, and source URL.”, and visibly received the bounded provider-error response. Interaction **`n.Interaction.077abaaa49b6411ca96f445e`** was persisted and closed in **3.254 s** (16:47:19.245–16:47:22.499 -04:00). The actual Ollama Cloud `glm-5.3:cloud` endpoint returned **401 Unauthorized** on both attempts; model-call count is 0 successful calls, with 2 failed attempts and 0.522 s summed attempt duration. Token and cost fields are zero because the provider returned no usage receipt; they do not establish that the failed requests incurred no charge. No tool/fetch action ran and no pilot TaskStore result was present in the graph. The browser screenshot showed the submitted prompt and bounded error.

### Remaining F16 qualification

- Nonce persistence is graph-backed and uses the current conversation mutation lock. Multi-process race/replay tests with a configured shared lock, store-restart tests, and deployment-level guarantees for every supported jvspatial backend remain necessary before claiming cross-worker single-use enforcement.
- The host envelope intentionally requires callers to know/resolve the same effective identity fields as the server; host SDK compatibility and key rotation/rollover need an integration contract and tests. Existing v1 envelopes are rejected and require host migration.
- A successful authenticated model interaction with an actual verified host envelope is not demonstrated. Current provider authentication failure prevents observing system-context consumption through a successful model response. Provider credentials also block live research/fetch qualification in this browser round.

## Phase 18 — Serialize host nonce admission against fresh graph state (F16, partial)

### Changes completed

- Closed a TOCTOU gap in Phase 17: host-context consumption previously relied on the later walker lock, but bootstrap can occur before that lock. Consumption now acquires the conversation mutation lock itself, reloads the Conversation from the graph while holding it, checks the nonce in that fresh context, persists the bounded ledger, and replaces the walker's stale Conversation reference with the fresh node.
- Added a concurrent regression in which two requests begin with separate stale snapshots; only one can consume the same signed nonce and only one graph save occurs.
- Closed a distributed-lock downgrade gap: when Redis or DynamoDB lock configuration is explicitly present but the corresponding Python client is missing, lock acquisition now raises instead of silently using a process-local lock. The host-context consumer consequently fails closed. Added regressions for both configured backends and documented the behavior.

### Verification

- Changed-file pre-commit hooks passed after formatting: whitespace, Black, isort, Flake8, mypy, and detect-secrets.
- Interaction, Orchestrator, memory-lock, and embed suites: **939 passed, 4 skipped, 27 existing async-mark warnings**. Focused host-context/endpoint/embed slice before adding the lock-client regressions was 48 passed; the final combined targeted run including them was 55 passed.
- The browser smoke used source at base **`82012aef` plus the current uncommitted working-tree changes**, after restarting the isolated backend on the latest code. jvchat used isolated app `/private/tmp/jvagent-pydantic-browser-smoke-20261005`, graph `graph2`, backend **8013**, and chat **3113**. Browser login and prompt submission succeeded.
- Prompt: “Use your research skill to find a current official population estimate for Georgetown, Guyana. Give the estimate, year, and source URL.” The configured Ollama Cloud `glm-5.3:cloud` endpoint returned **401 Unauthorized** on both attempts. The UI displayed the bounded provider error. Interaction **`n.Interaction.786fed446c4a40c6934e6294`** closed and emitted in **3.465 s** (16:57:11.650–16:57:15.115 -04:00); it records 2 failed attempts, 0 successful model calls, and 0.505 s summed attempt time. Provider tokens and cost were not returned; persisted zero counters do not establish zero charge. No search/fetch tool ran and no pilot TaskStore result was present. A screenshot captured the visible prompt and error.
- Local Redis and its Python client were unavailable, so this round did **not** qualify a real cross-process Redis lock. DynamoDB locking was covered only by the existing controlled lock tests and missing-client failure regression, not by a live DynamoDB table. The browser smoke did not include a signed host-context envelope, so it does not prove successful context consumption through the browser.
- `git diff --check` passed after the latest code and documentation changes. No staging or commit was performed. Both disposable services were stopped; the app/graph directory remains on disk for inspection. The user's port 3103 Messenger was untouched.

### Remaining F16 qualification

- Exercise same-nonce requests across separate processes against a real shared Redis or DynamoDB lock and the configured graph backend, including a lock lease expiry/failure case. Confirm stale reads cannot cross the lock boundary for each supported storage engine.
- Exercise a signed host envelope through HTTP/embed to verified system-prompt construction and prove expiry, identity mismatch, save failure, replay, and key rollover behavior at integration level. Version 1 envelopes are rejected; host clients need an explicit v2 migration path.

## Phase 19 — Verify nonce replay against the real JSON graph (F16, partial)

### Changes completed

- Replaced the fake Conversation-store concurrency regression with an integration test against the autouse jvspatial JSON graph. Two requests resolve independent stale Conversation nodes before contending; exactly one consumes the nonce, and a new `Conversation.get()` confirms the winning correlation ID and nonce are persisted in `Conversation.context`.
- This verifies graph read/write behavior and stale-snapshot serialization within one process. It does not stand in for a multi-process Redis/DynamoDB run.

### Verification

- `tests/action/interact/`, `tests/action/orchestrator/`, `tests/memory/test_lease_renewal.py`, and embed suites: **939 passed, 4 skipped, 27 existing async-mark warnings**.
- Changed-file pre-commit hooks passed: whitespace, Black, isort, Flake8, mypy, detect-secrets; `git diff --check` passed.
- Real jvchat smoke on updated source used isolated app `/private/tmp/jvagent-pydantic-browser-smoke-20261005`, graph `graph2`, backend **8013**, chat **3113**. Browser authenticated and submitted the Georgetown research prompt to the configured Ollama Cloud `glm-5.3:cloud` model. The endpoint returned **401 Unauthorized** twice; the UI displayed the bounded provider error.
- Interaction **`n.Interaction.a958104522294c9a84444299`** closed/emitted in **3.239 s** (17:01:16.744–17:01:19.983 -04:00), with 2 failed attempts, 0 successful model calls, and 0.529 s summed attempt time. No provider token/cost receipt was returned, so zero-valued usage fields are not evidence of zero cost. No search/fetch tool or pilot TaskStore task ran. The browser screenshot showed the submitted request and response.
- No staging or commit. The isolated backend/chat processes and test tab were closed; the disposable app/graph remains on disk. The personal port 3103 service was untouched.

### Remaining F16 qualification

- Run two independent processes against a real Redis or DynamoDB lock and verify one-time admission through the actual graph store; inject lease loss and backend restart while consuming a nonce.
- Run a signed v2 envelope through an authenticated embed or HTTP interaction and inspect the resulting system prompt/run events, including expiry, identity mismatch, duplicate nonce and key rollover. A successful model call remains blocked by provider authentication in this environment.

## Phase 20 — Harden signed host-context input validation (F16, partial)

### Changes completed

- Require a dedicated host-context signing key of at least 32 UTF-8 bytes; weak or absent keys fail closed.
- Reject non-integer TTLs/timestamps (including booleans), malformed explicit run IDs/nonces, and oversized run IDs/nonces before accepting signed context.
- Added focused signer/verifier regressions and documented the signing-key minimum in the operator references.

### Verification and browser qualification

- Host-context focused tests: **8 passed**.
- Full repository pre-commit suite: **passed** (YAML/JSON, whitespace, Black, isort, Flake8, mypy, detect-secrets).
- Relevant regression suites, run using the project `.venv`: **passed**; 4 existing environment-gated/unavailable cases skipped. (An initial invocation with the system Python could not import optional `pydantic_ai`; rerunning with `.venv` resolved collection.) `git diff --check` passed.
- Real browser smoke used the isolated app `/private/tmp/jvagent-pydantic-browser-smoke-20261005`, graph `graph2`, API **8013**, jvchat **3113**, and the configured `glm-5.3:cloud` Ollama provider. The browser authenticated, started a fresh conversation, sent the Georgetown research prompt, and displayed the bounded “trouble reaching my language model” response; it did not hang.
- Persisted Interaction **`n.Interaction.493ee6b290d34efcb3bb9221`** includes the submitted prompt, the same user-facing response, the Orchestrator action and correlation ID. Provider logs show **401 Unauthorized** on `https://ollama.com/api/chat`; there was no successful model call, research-tool execution, provider token receipt, or cost receipt. The smoke verifies request completion and error presentation, not a successful model response; zero-valued usage must not be interpreted as zero cost.
- Both isolated services were stopped. User port **3103** was left untouched. No staging, commit, or push was performed.

### Remaining F16 qualification

- Multi-process nonce admission under a real shared Redis/DynamoDB lock, including lease loss and backend restart, remains untested.
- Signed envelope traversal through authenticated HTTP/embed into prompt/run events, plus expiry, identity mismatch, replay and key-rotation integration coverage, remains incomplete.
- The live-provider success gate remains blocked by the provider’s 401 response. Resolve Ollama Cloud credentials/configuration before claiming model-backed browser qualification.

## Phase 21 — Close stale ACL resolution and prevent compose-prompt leakage (F02/F14)

### Changes completed

- `Agent.get_access_control_action()` now raises a resolution error when persisted AccessControl records exist but the selected record cannot be loaded. Only a genuinely absent record retains the explicitly open compatibility path. This prevents a stale/deleted graph object from being misclassified as “no policy.”
- Added a regression proving a found-but-unloadable AccessControl record cannot resolve as absent.
- The real browser smoke uncovered a second failure-path issue: if ReplyAction's composing model call failed, its fallback could publish the assembled compose input, including user text and the internal directive reminder. ReplyAction now emits a fixed neutral error string on compose failure; a regression asserts user text, workflow directives and `MANDATORY` scaffolding are not returned.

### Verification and browser qualification

- Focused AccessControl, Orchestrator access and ReplyAction tests passed; the broader `tests/action/access_control/`, `tests/action/orchestrator/` and `tests/action/reply/` slices completed successfully. Four environment-gated/unavailable tests were skipped.
- `pre-commit run --all-files` passed all configured hooks; `git diff --check` passed.
- Real jvchat smoke ran from updated source **HEAD `82012aef` plus uncommitted changes**, with disposable app `/private/tmp/jvagent-enterprise-smoke-20261005-1708/app`, fresh graph `graph_final`, API **8014**, UI **3114**. The browser authenticated, submitted “Please say hello in one short sentence.”, and received a bounded error without exposing prompt content. Interaction **`n.Interaction.f80894ce4a2741d191c27868`** persisted `closed=true`, `emitted=true`, with response “I'm having trouble reaching my language model right now. Please try again in a moment.”; its interval was **3.209 s** and it recorded 2 failed attempts totaling **0.652 s**. The earlier pre-fix Interaction **`n.Interaction.943aedffa0bb400f8f7b753c`** records the reproduced prompt leak, which the new regression and post-fix browser result address.
- The configured OpenAI provider returned **401 Unauthorized** (invalid API key). No successful model response or provider usage/cost receipt was returned. Persisted zero usage counters do not prove zero provider charge; successful model-backed browser qualification remains unavailable until credentials are corrected.
- The temporary services and browser tab were closed; the disposable app/graph, including copied environment material, was removed. The browser's saved smoke-account URL was restored and the temporary test login removed. User port **3103** was not touched. No files were staged or committed.

### Remaining F02/F14 qualification

- Verify ACL-resolution failure records survive the graph-backed production log sink and are visible to an operator; current regressions assert denial and redacted structured logging, not sink ingestion/alerting.
- Exercise the ReplyAction failure fallback through streaming and delivery-acknowledgement failure injection, and make error/retry status explicit in the durable event protocol.
- Revisit F01 end-to-end provider tool-schema enforcement and F02 compatibility across all non-Orchestrator access-control consumers; this round closes only the identified ACL-resolution ambiguity in the Orchestrator boundary.

## Phase 22 — Canonical evidence URLs and collision-resistant citations (F08, partial)

### Changes completed

- Added one HTTP(S) evidence URL canonicalizer. It rejects credentials, invalid ports/hosts, whitespace/control characters, backslashes and non-HTTP schemes; it normalizes scheme/host case, IDNs, IP literals, default ports, empty paths and fragments while preserving query strings.
- `EvidenceReference` now validates and stores the canonical URL, so collector deduplication and rendered citations use the same representation.
- Evidence source IDs now bind to the canonical URL digest. Short upstream Action IDs remain visible as namespaced prefixes but also include a URL digest, preventing two distinct URLs with the same provider ID from aliasing. URL-only sources use a full SHA-256 identifier.
- Updated integration fixtures to produce the collector's source ID rather than assuming the raw URL is the ID. Added regressions for equivalent URL deduplication, credentials/invalid syntax, IDN and IPv6 normalization, non-default ports, long URLs, and same-ID/different-URL collisions.

### Verification and browser qualification

- Focused pilot, WebFetch and tool-wrapper tests: **140 passed, 3 skipped**. The skips are opt-in live evaluation/model API tests.
- Changed-file pre-commit hooks passed after rerunning Black/isort changes: trailing whitespace, Black, isort, Flake8, mypy and detect-secrets. `git diff --check` and `uv lock --check` passed.
- Real Messenger smoke used source base **`82012aef` plus the current uncommitted working tree**, disposable app `/private/tmp/jvagent-goal-smoke-f08-20261005/app`, isolated JSON graph `/private/tmp/jvagent-goal-smoke-f08-20261005/graph`, API **8107**, and jvchat **3108**. Browser login and research-prompt submission succeeded. The UI completed with: “I couldn't complete that request because the model service returned an error. Please try again later.” The interaction did not hang.
- Interaction **`n.Interaction.94e367d1f4c146dd8e2460c2`** persisted closed/emitted in **1.079 s** (17:29:42.339–17:29:43.418 -04:00). The Conversation contains a failed `CAPABILITY_PILOT` task with schema version 6, one model request, zero tool calls, no evidence/output, and `usage_accounting_complete=false` with one unsettled request. The sole Ollama Cloud `kimi-k2.6:cloud` request to `https://ollama.com/api/chat` returned **401 Unauthorized** in **0.254 s**. No provider token or cost receipt was returned; persisted zero usage/cost fields do not prove zero charge. Serper was not called.
- The isolated services were stopped, the temporary browser tab was closed, and the copied app/graph (including its copied environment file) was removed. User service port **3103** and other worktree edits were not changed. No files were staged or committed.

### Remaining F08 qualification

- Canonicalization currently normalizes host/scheme/default ports/path/fragment and validates authority syntax, but must still be reviewed against percent-encoded paths, Unicode normalization, query-order semantics, and provider-specific tracking parameters before declaring a universal equivalence policy.
- Source URL membership still does not verify semantic claim support, redirect trust, or evidence freshness. Continue F04/F06/F07 evaluation and fetch-receipt work before widening citation use.
- Provider authentication remains broken for the copied Ollama Cloud credentials, so this round validates the negative-path UI/persistence and not successful provider execution, Serper usage, source rendering, or citation behavior in the browser.

## Phase 23 — Operational driver-blind semantic review packets (F04 evaluation, partial)

### Changes completed

- Added `tests/action/orchestrator/pilot/eval/blind_review.py` to convert matched legacy/pilot evaluation records into randomized paired response packets for the fixed-source rubric. The packet includes the task, source text, and two arbitrarily ordered candidate responses, but excludes driver, provider, model, and run telemetry.
- Pilot source IDs and source URLs are translated into source labels shared with the paired legacy answer. Unmatched source IDs/URLs remain visibly marked as unmatched rather than being silently discarded. No-response cases use the same neutral placeholder, while provider error details stay out of the blind packet.
- The builder returns the driver mapping separately as an answer key; its contract warns the evaluation custodian not to distribute that key with reviewer materials. Packet generation rejects missing/duplicate pair keys, mismatched pilot/legacy run coverage, unknown cases, and unsafe manifest source URLs.
- Added annotation validation for exact pair/candidate coverage, every rubric dimension, integer scores from 0–2, explicit critical-failure status, and a written rationale. Automated phrase/source scoring remains an independent regression signal; human reviewers still judge entailment and calibration.
- Added tests for metadata exclusion, citation/source-label mapping, unpaired and duplicate-run rejection, valid annotations, malformed ratings, and schema versioning.

### Verification and browser qualification

- Pilot slice: **114 passed, 3 skipped** (opt-in live evaluation and model tests). Blind packet tests plus manifest scorer tests: **10 passed**.
- `pre-commit run --all-files` passed. Direct mypy on both new files passed; `uv lock --check` and `git diff --check` passed. The repository-wide pytest suite was not rerun this round.
- Real Messenger smoke used source base **`82012aef` plus current uncommitted changes**, an isolated app at `/private/tmp/jvagent-goal-smoke-evalblind-20261005/app`, a fresh graph at `/private/tmp/jvagent-goal-smoke-evalblind-20261005/graph`, API **8108**, and jvchat **3109**. The browser authenticated and submitted the Georgetown population research request. Messenger displayed the bounded model-service error; it did not hang.
- Interaction **`n.Interaction.fb66c9ca7daa4801a61fb6f9`** was closed/emitted in **1.073 s** (17:39:19.122–17:39:20.195 -04:00). Its pilot task **`pilot_dae802bd45b34c3aa95a9721a3299abd`** is failed with no result or evidence, one model request, zero tool calls, one unsettled request, and `usage_accounting_complete=false`. Ollama Cloud model **`kimi-k2.6:cloud`** returned HTTP **401** from `https://ollama.com/api/chat` after **0.246 s**; no token or provider cost receipt was returned. Zero-valued usage/cost fields do not establish zero billing. Serper did not run. The current saved Action selected Kimi despite the temporary YAML replacement intended to select GLM-5.3; model/config resolution must be inspected separately before the next provider comparison.
- Smoke startup reported the checkout's missing `pip` module while attempting optional Action dependency installation; the browser request still reached the installed Ollama Action. Port **8108** was also mapped by the pre-existing `typesense` Docker container. The temporary jvagent process and jvchat process were stopped, and the disposable app/graph (including copied `.env`) were removed. The Typesense container was left running. The test login was logged out and its newly saved browser credential was removed. No user-port-3103 service was touched; no files were staged or committed.

### Remaining F04 evaluation limits

- The packet builder and annotation validator are tested as utilities; no two-reviewer semantic ratings or inter-rater agreement have yet been collected. Add a documented adjudication/agreement method before treating scores as qualification evidence.
- The output-mode escape remains a runtime concern: factual model claims can still select `ConversationalReply` outside the fixed evaluation. Avoid a brittle core intent classifier; establish an authored skill/output-contract policy or separately bounded verifier and qualify it.
- Provider authentication still prevents successful pilot-vs-legacy repeated evaluation, semantic packet population from actual successful runs, and successful browser/Serper evidence. The unplanned Kimi selection means the disposable YAML-to-model resolution path also needs a direct graph readback regression.

## Phase 24 — Requalify the F01/F02 trust boundary (verification checkpoint)

- Rechecked the existing Agent-run spoofing regression in `tests/action/orchestrator/pilot/test_pydantic_ai_smoke.py`; it invokes the adapted tool through Pydantic AI's `Agent` and proves injected `_tool_name` data cannot change the authorization label or call the Action. Schema-type and required-field failures are covered before ACL evaluation or dispatch. No duplicate test was added.
- Re-ran the trust-boundary slice: **47 passed** across the Pydantic Agent runner, Action wrapper, AccessControl resolution, and AccessControl Action suites.
- `pre-commit run --all-files` passed; `git diff --check` and `uv lock --check` passed. Full repository suite: **4,242 passed, 9 skipped, 31 warnings** in 145.55 seconds.
- Reproduced a fresh disposable graph at `/private/tmp/jvagent-goal-f01-repeat-20261005/app/jvdb` and read the persisted Orchestrator node: heavy/light model are both `glm-5.3:cloud`, both Action classes are `OllamaLanguageModelAction`, and `skill_runtime` is `capability_pilot`. The isolated API health endpoint returned HTTP 200 with `database=connected`.
- Messenger is served at `http://127.0.0.1:3122/login`, targeting the isolated API on port 8122. The browser is waiting at login; the new disposable test password has not been entered or submitted by the agent. A fresh browser model call and graph result therefore remain pending user handoff. No user service on port 3103 was touched, and nothing was staged or committed.

## Phase 25 — Enforce authored evidence output for the research pilot (F04, partial)

- Added the `output-contract` frontmatter key to the recognized skill metadata and verified it survives resolution as `evidence_required` without being marked unsupported. The research SKILL.md declares that contract; app skills without it fail closed in the pilot compiler.
- Restricted newly built Pydantic AI research agents to `ResearchBrief`, and made `PilotEvidenceCollector` reject source-free conversational output. The old `ConversationalReply` type remains in the snapshot union to deserialize prior pilot state, but is no longer a valid output for a fresh research run.
- Updated run and live-evaluation instructions to describe the pilot's research-only scope and tested the updated behavior. Focused contract/runtime/context/scaffold/evaluation checks: **78 passed, 1 skipped**.
- `uv lock --check`, `git diff --check`, pre-commit (second clean pass) passed. First pre-commit pass reformatted a tracked pilot test and flagged an unused import; those were corrected before the clean pass. The untracked `tests/action/orchestrator/test_access.py` was formatted separately because the repo hook scans only tracked files. Full repository suite: **4,243 passed, 9 skipped, 31 warnings** in 140.40 seconds.
- Restarted the isolated API from the updated source: `/health` returned **200** with the disposable graph connected. Messenger at `http://127.0.0.1:3122/login` remains at sign-in. A fresh real-provider browser interaction and persisted result cannot yet be recorded because sign-in handoff is still pending; no credentials have been entered by the agent.
- Current assessment: this closes the runtime/schema route for unsupported source-free output in the research pilot, but it does not demonstrate claim entailment. Semantic support/calibration review, successful provider and search/fetch runs, and the remaining enterprise findings stay open.

## Phase 26 — Bound evidence receipts and validate fetch provenance (F05/F06/F08, partial)

- Fetch receipts now require the typed success outcome and HTTP 200, a credential-free normalized requested and final URL, a returned `# Source:` line matching the final URL, and either a supported or absent content type. Mismatched content/receipt URLs and unsupported media do not become evidence.
- Capped structured Action-result inspection and citation annotation at 256,000 characters. Oversized search output fails closed for evidence parsing, and the tool wrapper returns a small valid JSON truncation envelope without first parsing the oversized payload. URL normalization now rejects inputs longer than the persisted EvidenceReference URL limit instead of allowing model-turn validation to crash.
- Added adversarial regressions for fetch receipt/source mismatch, unsupported media, oversized structured results, and overlong source URLs. Focused pilot evidence/tool plus WebFetch Action suites: **55 passed**.
- Full repository suite: **4,248 passed, 9 skipped, 31 warnings** in 147.89 seconds. Pre-commit passed; Black, Flake8, and mypy passed on untracked additions; `uv lock --check` and `git diff --check` passed.
- Messenger tab `http://127.0.0.1:3122/login` was restored and checked; it remains at sign-in. The current round's required real configured-provider browser call and graph readback cannot be completed until the user signs in. No password was entered or submitted by the agent.
- Remaining provenance qualification: the fixed 256 KB ceiling intentionally rejects larger results rather than partially trusting a partial parse; no successful live-provider search/fetch/citation flow has been recorded in this round. Semantic entailment, freshness timestamps, redirect policy evaluation, and remaining review findings are still open.
- Tested source identity: branch `codex/pydantic-inspired-skill-pilot`, base commit `82012aef`, plus dirty implementation snapshot SHA-256 `fcb339f8510da5e2cd808839fc6aaf2100e0f3ee35722180bff97802fe720bc8` (progress/review reports excluded). The isolated API was restarted from this source state and `/health` returned 200 with its graph connected. Startup still reports missing `pip` during optional Action dependency installation; qualification of that deployment path remains open.

## Phase 27 — Stop internal provider retries after unpriced failures (F03, partial)

- Added an optional task-local model-attempt guard at the shared `BaseModelAction` retry seam. The Pydantic skill pilot binds it only around its model run; non-pilot and uncapped calls remain unbound. Each failed attempt is persisted without invented usage/cost, and a configured turn or conversation dollar ceiling now blocks the next hidden transport retry when that attempt has no price receipt.
- Added regression coverage that a retryable first transport failure is recorded once and a task-local budget rejection prevents the second provider operation. Failed/cancelled attempt telemetry also marks dollar accounting incomplete. Focused model retry and Orchestrator resilience tests passed; `pre-commit run --all-files` passed. Full repository pytest completed through 100% with no reported failure; 9 environment/unavailable tests were skipped and existing warnings remain. `uv lock --check` and `git diff --check` passed.
- Restarted the disposable app with `--purge --yes` after setting a fresh local test admin password. Bootstrap created `admin@jvagent.example`; API health on **8122** returned HTTP 200 with the database connected. Messenger remains on **3122**. Its login form is pointed at the API on 8122 and remains at the manual sign-in handoff; no credential was entered into the browser and no model request was made.
- Corrected the disposable app's file-storage root to `/private/tmp/jvagent-goal-f01-repeat-20261005/app/.files` before the final restart. The purge targeted only that disposable app's `jvdb` and `jvagent_logs`; it did not purge the checkout's `.files` directory. Optional Action dependency setup still reports that the checkout Python has no `pip`, although API startup and health succeed.
- This round does not yet qualify password acceptance or a real model-backed browser interaction. The saved browser account remains separate from the freshly purged graph, so the user must sign in manually with the bootstrap email and current temporary-app `.env` password. No user service on port **3103** was stopped or purged; nothing was staged or committed.

## Phase 28 — Typed Action failure outcomes and controlled read recovery (F14, partial)

- Action tool outcomes now carry an explicit status (`failed`, `timed_out`, or `cancelled`) separate from rendered text. Pydantic tool events preserve that status, so the UI/error lifecycle no longer depends solely on matching English prefixes; legacy event parsing remains for the existing driver.
- The read-only research pilot explicitly allows recoverable failures only for its declared search/fetch tools. Those failures return a bounded generic limitation to the model for recovery, do not pass provider error text to the model or user, and do not enter evidence collection. Effectful tools cannot opt into this behavior. Cancellation closes the tool event on a best-effort basis while preserving task cancellation.
- Added tests for typed failed status, timeout and `ToolResult.is_error` recovery, redaction of exception details, exclusion from evidence, cancellation status, and event serialization. Focused pilot tool/runtime/stream suites: **41 passed**. Full repository suite: **4,256 passed, 9 skipped, 31 warnings** in 136.46 seconds, exit 0. `pre-commit run --all-files` passed after formatting changes were applied and rerun clean; `git diff --check` passed in the checked working tree.
- Real browser smoke ran in `http://127.0.0.1:3122` against isolated API **8122** and graph `/private/tmp/jvagent-goal-f01-repeat-20261005/app/jvdb`. The login form was incorrectly pointing to Messenger itself on port **3122**, producing HTTP 501. Correcting it to `http://127.0.0.1:8122` and signing in with the disposable admin account resolved authentication; login and subsequent agent reads returned HTTP 200. On the configured `glm-5.3:cloud` route, the UI rendered the safe error “I couldn't complete that request because the model service returned an error. Please try again later.” The graph/UI debug readback showed Interaction `n.Interaction.8c389f1379a847d4b8e7479b`, failed TaskStore task `pilot_fe08ad685407434a950d039aaa7cb7ca`, one model attempt, zero tool calls, no output/evidence, and usage accounting incomplete. Ollama returned **401** in **0.286 s**; token usage and cost were unreported, so persisted zero counters do not prove zero charge.
- As a provider diagnostic, the disposable app's Orchestrator model settings were temporarily switched through its admin Action endpoint to configured OpenAI (`gpt-4.1-nano` light, `gpt-4.1` heavy), then restored to the requested `glm-5.3:cloud` Ollama settings in both graph config and temporary `agent.yaml`. A second browser turn rendered the same safe error and persisted Interaction `n.Interaction.b650ac62bf304366a4631d98`, failed task `pilot_ef1e10d483fd42719fd8a171aef26618`, one model attempt, zero tool calls, no output/evidence, and incomplete usage. OpenAI returned **401 invalid_api_key** in **0.624 s**; no token/cost receipt was returned. The copied isolated `.env` contains provider keys, but both configured provider credentials tested are invalid. No search/fetch or successful provider response was exercised. The app is now configured back to GLM; browser remains authenticated and its diagnostic view shows the persisted failures.
- Both end-user requests reached their terminal UI error within the 8–10 second browser observation window; provider attempt durations were 0.286 s and 0.624 s. No successful usage should be inferred from zero values. The login fix, readable failure response, TaskStore terminal state, and attempt telemetry are evidenced; successful model/tool/event streaming and actual usage remain unqualified. The attempt reached the Pydantic pilot, but failed before any tool event, so F14's successful and read-tool recovery path was not exercised in browser.
- The latest backend was restarted from the current source without another purge; health returned **200**, graph connected. Optional Action dependency installation still logs failure because the checkout virtualenv lacks `pip`. This round did exercise the browser against the selected configured providers but did not achieve a successful model call; the user needs to update the Ollama Cloud and/or OpenAI key in the isolated app `.env` before positive provider qualification. No unrelated service or workspace `.files` data was touched, and nothing was staged or committed.
- Tested source identity: branch `codex/pydantic-inspired-skill-pilot`, base commit `82012aef`, plus implementation snapshot SHA-256 `ec76f6e7b362808c2ff0c4141f51980baf9c38a035e48be18554055b5b7c5183` (planning review/progress documents excluded; includes 64 changed or untracked source/test files).
- Startup still logs optional Action dependency installation failures because this checkout virtualenv has no `pip`; API startup and health succeed, but those optional Actions are not deployment-qualified. No unrelated services were stopped, no workspace `.files` data was purged, and nothing was staged or committed.

### Remaining F14 qualification

- Define durable, typed failure/retry semantics for provider calls and persisted event history, including observer/storage failures, stream disconnects, cancellation after an irreversible effect, and ReplyAction delivery acknowledgement. Current tool-result event typing does not close the full lifecycle contract.
- Repeat the isolated browser smoke after provider credentials are corrected, capture one successful configured-provider reply and tool/event sequence, and record exact source digest, persisted completed task/result, duration and provider-reported or explicitly estimated usage. Current browser evidence is a pair of persisted, bounded 401 failures only.

## Phase 29 — Reset isolated test admin and verify login

- The user reported that the disposable Messenger admin password was unknown. Inspected only `/private/tmp/jvagent-goal-f01-repeat-20261005/app/.env`: `JVSPATIAL_ENVIRONMENT=development`, username `admin`, email `admin@jvagent.example`, and a configured password. The password value is intentionally not copied into this progress record.
- Confirmed the CLI purge guard applies in explicit development mode and that configured local targets resolve within the disposable app. Stopped only its API process, then ran `jvagent --purge` with `JVAGENT_ASSUME_YES=true`; it removed that app's `jvagent_logs` and `jvdb`, rebuilt the graph, and bootstrapped the admin from `.env`. The user browser's existing saved admin account then signed in successfully, landing on the freshly bootstrapped Orchestrator Agent with no prior conversations. The API health endpoint returned HTTP 200 with database connected.
- Messenger remains at `http://127.0.0.1:3122` and API at `http://127.0.0.1:8122`; the signed-in browser tab was retained for the user. This auth/reset smoke does not change the previous finding that configured model provider keys returned HTTP 401. No source files were changed or committed in this phase.

## Phase 30 — Serialize concurrent pilot checkpoints (F15, partial)

- The new Pydantic `max_concurrency` mapping exposed a shared-state hazard: concurrent tool-result, model-usage, request-budget, and tool-count callbacks all update the same TaskStore snapshot. Added a per-run `asyncio.Lock` around these snapshot writes, including evidence collection and persistence, so one checkpoint cannot overwrite another's newer data.
- Extended the Orchestrator integration regression to run the real Pydantic AI dispatch with two concurrent search calls and to deliberately delay the one-source checkpoint. The last running checkpoint retained both source receipts; the completed graph-backed task retained both references and all three Action calls. Focused pilot integration/runtime tests passed. Full repository suite: **4,267 passed, 9 skipped, 31 warnings** in 134.75 seconds. `pre-commit run --all-files` passed cleanly after Black's first pass reformatted two touched files; `uv lock --check` and `git diff --check` passed.
- Restarted the isolated app API from this source state without purge. Health returned HTTP 200 with the database connected. Messenger's actual browser call to configured Ollama Cloud `glm-5.3:cloud` rendered the bounded service-error response; persisted task `pilot_27a42eede8c94d16bd7705771231df25` ended `failed`, with one model attempt, zero tool calls, no output/evidence, and incomplete usage accounting. Ollama returned HTTP **401** in **0.253 s**; no token or price receipt was reported. Thus the in-browser provider path is exercised, but successful generation and billable-usage reconciliation remain unverified until the key is corrected.
- Browser tab `http://127.0.0.1:3122/chat/n.Agent.891ce63713b544daafbba61f` remains signed in, with the failed turn's Debug view open. Isolated API remains on **8122**. Startup still logs optional Action dependency-install errors because this checkout virtualenv has no `pip`; startup and health succeed, but those optional integrations are not qualified.
- Tested source identity: branch `codex/pydantic-inspired-skill-pilot`, base commit `82012aef`, plus implementation snapshot SHA-256 `a5ae2a8de0a7a184961f6a17a8344aeddfea85648ece3d9dfcc53e3573e94212` (64 changed/untracked implementation and test files; progress/review reports excluded). Nothing was staged or committed.

## Phase 31 — Require fresh fetched-source quote anchors (F04/F07, partial)

- Added host-observed UTC provenance and a 24-hour freshness window to evidence references. Fresh research outputs now require each cited reference to be a successful fetched-page receipt; every finding must name one of its citations as its supporting source and provide a quote found in that bounded fetched excerpt. Rendered citations include the host observation time. A quote match proves excerpt provenance only; it does not establish semantic entailment.
- Updated the authored research skill, Orchestrator instructions, live evaluation prompt, and persisted-task fixtures to require successful fetches and exact quote anchors. Search snippets remain useful for discovery but cannot alone support a factual claim. Added/adapted tests for stale evidence, quote mismatch, missing fetch provenance, successful rendering, and task completion.
- Focused pilot evidence, contract, runtime, integration, context and live-evaluation tests passed; the opt-in live evaluation remains skipped. `pre-commit run --all-files`, `uv lock --check`, and `git diff --check` passed. Full repository run exited 0: **4,266 passed, 9 skipped** (4,275 tests collected); warnings remain in the suite.
- Provider-backed in-browser qualification is **pending**. During the requested admin-env inspection, an incorrectly scoped shell filter printed the isolated app's full `.env` into tool output. This exposed its configured API credentials, JWT signing secret and temporary admin password. Values were not copied into this record or repeated. The existing isolated app at API **8122**, Messenger **3122**, graph `/private/tmp/jvagent-goal-f01-repeat-20261005/app/jvdb` remains live; `/health` and Messenger root return HTTP 200. The app's `.env` credentials authenticated successfully at `/api/auth/login` (HTTP 200), but that verifies only this disposable login path, not this source round's model behavior. Do not make another provider request until the exposed credentials and signing secret are rotated in the isolated `.env`.
- No current-revision browser model call, successful result/task readback, source/tool event sequence, or fresh usage receipt was recorded this round. The previous browser smoke is historical and used the prior source state. This is an explicit qualification blocker pending credential rotation; no app purge, source commit, or staging occurred in this phase.
- Rechecked the isolated `.env` on the following continuation; its modification timestamp is unchanged, so rotation is not yet evidenced. F07's original cross-objective evidence carry-forward is removed and fresh tasks require fresh evidence; explicit reuse of prior-run evidence in genuine follow-up research remains intentionally unsupported. The existing regression proves the collector starts empty when handed old references.

## Phase 32 — Render bounded research limitations (F04/F14, partial)

- The research skill instructed the model to disclose failed fetches and conflicting/incomplete evidence, but final rendering silently omitted `ResearchBrief.limitations`. User-visible output now includes model-reported limitations under an explicit “not independently verified” label. Each item is non-empty and capped at 1,000 characters; URLs and inline citation markup are rejected so the field cannot become an alternate citation channel.
- Updated the research skill guidance and added renderer/schema regressions for limitation visibility, the unverified label, text-length bounds, and link rejection. Focused contract, evidence, state, and runtime suites passed. Full repository pytest passed: **4,268 passed, 9 skipped** (4,277 collected). `pre-commit run --all-files`, `uv lock --check`, and `git diff --check` passed; pre-commit's first pass applied Black formatting and the subsequent full gate was clean.
- This renderer behavior change has not yet been qualified in the browser or against an actual provider result. The isolated app `.env` still has the same modification timestamp (**2026-10-05 19:47:25 -04**); no credential rotation is evidenced, so the exposed key set was not used. API 8122 and Messenger 3122 remain available from the previous source state and are not evidence for this renderer change. No staging or commit occurred.

## Phase 33 — Require supporting-source quotes in fixed-case scoring (F04/F09, partial)

- Tightened the fixed-source evaluator so each factual finding must declare a supporting source that is also cited, and a non-empty supporting quote that occurs in that fixture's source text. The scorer maps both fixture IDs and the runtime's namespaced IDs back to fixture sources, then reports missing/uncited support and unobserved quotes as separate failures. It continues to state `semantic_entailment_assessed=False`; exact quote occurrence does not prove entailment.
- Added adversarial cases for runtime-namespaced source IDs, fabricated/mismatched quotes, missing supporting sources, and uncited supporting sources. Focused evaluation and blind-review tests: **12 passed**. `pre-commit run --all-files`, `uv lock --check`, and `git diff --check` passed. The prior full repository run for this working tree exited 0 with **4,270 passed and 9 skipped**; no runtime code changed in this evaluator-only round.
- Current branch `codex/pydantic-inspired-skill-pilot`, base HEAD `82012aef` plus the existing uncommitted worktree. No files were staged or committed.
- Fresh browser-visible Messenger readback at `http://127.0.0.1:3122/chat/n.Agent.891ce63713b544daafbba61f` confirmed the chat shell, Orchestrator Agent, debug view, and persisted capability-pilot task. The displayed turn is a historical failed run, not a current-source qualification: task `pilot_27a42eede8c94d16bd7705771231df25` is `failed`; its saved Ollama `glm-5.3:cloud` attempt returned HTTP **401** in **0.253 s**, with no output, tools, evidence, or token/price receipt. `/health` on API 8122 and Messenger root on 3122 both returned HTTP 200. The live process was not restarted for this evaluator-only change, so the UI readback proves service/display/persisted-state availability only, not current-source model behavior.
- The admin login pair configured for the isolated app authenticated at `/api/auth/login` on 8122 with HTTP **200**. This unblocks access to the disposable app but does not validate provider behavior. No model call was made because the app's exposed provider keys and JWT secret have not been rotated; actual-provider browser qualification, successful result persistence, usage reconciliation, and source/tool event readback remain unverified.
- Direct Black, isort, Flake8, and mypy checks passed on the three new untracked evaluator files after the isort check caught and corrected one import-order issue that tracked-file-only pre-commit did not see. The 12 focused evaluation/blind-review tests passed again after that correction; `uv lock --check` and `git diff --check` passed.
- Browser interaction caveat: selecting **New Conversation** cleared the active session indicator but left the previous failed transcript visible. The empty composer kept Send disabled; a temporary unsent draft enabled Send and was then cleared. No prompt was submitted, no new model request occurred, and no graph write was confirmed from this click. Because the transcript/session boundary was not visibly clear, this remains a UI-state/recovery observation, not a successful fresh-conversation or model smoke.

## Phase 34 — Exercise authorization at the Agent boundary (F01/F02, partial)

- Extended the deterministic Pydantic AI `Agent` regression to reject both a model-supplied `_tool_name` and a type-invalid query. Both failures happen before the authorization callback and before the underlying Action, closing the previously reproduced identity-substitution/schema-validation seam at the actual Agent call path.
- Added a dispatch regression proving an authorization callback exception propagates before Action execution. Existing AccessControl regressions continue to prove graph-resolution/evaluation failures deny with redacted structured logging, while a genuinely absent AccessControlAction retains the current compatibility behavior of an open deployment.
- The five focused suites covering Agent-run adapter attacks, Action dispatch, AccessControl, fixed-case scoring and blind review: **37 passed**. Full `pytest -q --disable-warnings --tb=no tests/` completed at 100% with exit **0**; **9 tests were skipped** for missing optional dependencies, absent local Zoon skills, or explicitly opt-in live-provider tests. `pre-commit run --all-files`, direct Black/isort/Flake8/mypy checks for new untracked evaluator files, `uv lock --check`, and `git diff --check` passed. No runtime source behavior changed in this phase.
- The Messenger UI was exercised again on the isolated app. A draft toggled Send enabled and was cleared without submission. The previous failed transcript remained visible after selecting New Conversation, so a clean chat boundary was not proven. API health and Messenger root remained HTTP 200. The only saved provider result remains the historical Ollama 401 recorded in Phase 33; no fresh actual-provider request, successful response, task completion, or usage receipt was produced. Key rotation is still required before provider-backed qualification.
- Branch remains `codex/pydantic-inspired-skill-pilot` at base HEAD `82012aef` plus the pre-existing dirty worktree. No staging or commit occurred.

## Phase 35 — Prove pilot dollar-budget admission and settlement (F03, partial)

- Source inspection found the pilot already checks per-turn and conversation ceilings before each Pydantic model request, rechecks the host guard before internal provider retries, and settles known cost in `_execute_turn`'s `finally` path. The missing evidence was pilot-path integration coverage, not another budget implementation.
- Added regressions proving (a) an already-exhausted turn ceiling, exhausted conversation total, and incomplete conversation accounting each stop before model selection and create no pilot task; (b) a deterministic first model response that crosses the configured turn ceiling is recorded with its reported token usage and the second Pydantic model request is blocked; and (c) a pilot exception after a priced model response still settles that amount into the conversation cost total.
- The targeted pilot, Orchestrator resilience, Pydantic runtime, and transport-retry suites passed (**59 tests**). The full repository pytest run completed at 100% with exit **0** and **9 skips** for optional dependencies, unavailable Zoon skills, or explicitly opt-in live-provider tests. `pre-commit run --all-files` passed on the clean rerun after Black reformatted the new test; `git diff --check` and `uv lock --check` passed. No runtime behavior changed in this test-coverage round.
- Read-only browser smoke on the isolated Messenger at `http://127.0.0.1:3122/chat/n.Agent.891ce63713b544daafbba61f` confirmed the Orchestrator chat shell, the saved research-pilot interaction, and its historical user-visible provider error. The persisted task remains failed from the prior Ollama HTTP **401** in **0.253 s**; this round sent no request, added no task/result, and produced no usage receipt. The current `.env` timestamp remains **2026-10-05 19:47:25 -04**; no rotation is evidenced, so no provider call was made. The in-browser state is operational display/readback evidence only, not current-source model qualification.
- Working tree remains on `codex/pydantic-inspired-skill-pilot` at base HEAD `82012aef`, with all accumulated edits preserved and unstaged. No commit occurred.

## Phase 36 — Fail closed on ambiguous active pilot recovery (F11/F15, partial)

- `PilotTaskStore.active_run()` previously returned `None` when more than one matching active run existed. The orchestrator interpreted that as no interrupted run and could admit another pilot task. It now raises `PilotStateError` on that ambiguous state, preserving all tasks and preventing a silent fork; no recovery or replay is attempted without a unique checkpoint.
- Added a regression that creates two matching active pilot tasks and verifies lookup fails closed while both tasks remain active and no third task is created.
- Focused pilot-state, orchestrator-pilot and TurnRun recovery tests: **30 passed**. Full repository pytest completed at **100%** with no failures and **9 skipped** (optional `pypdf`/`deepgram`/PDF engine, gated live-provider cases, unavailable Zoon skills, and an empty requirements-sync parameter set). `pre-commit run --all-files`, `git diff --check`, and `uv lock --check` passed.
- Restarted only the isolated disposable backend (port **8122**) so it imported the current source; the JSON graph was preserved. jvchat at **3122** was refreshed in the browser and visibly rendered the sign-in form with the expected backend URL. The local admin login endpoint returned HTTP **200** for the isolated test credentials. No interaction was submitted, so this round has no new task/result, model request, duration or token/price receipt. The prior graph state is unchanged.
- Startup reached `Application startup complete` and the API responds, but optional action dependency installation logged errors because this checkout's virtualenv has no `pip` module. That deployment/bootstrap limitation remains open; the Messenger UI check is not proof of a successful provider turn.
- Tested source is base commit `82012aef` plus the working tree. No staging or commit occurred. Browser evidence for a clean conversation/task after this code change remains pending; this round only refreshed the login screen and verified auth/health endpoints.

## Phase 37 — Surface ambiguous recovery as a controlled reply (F11/F15, partial)

- Added `_resolve_pilot_active_run()` to convert ambiguous recovery state into a bounded user-facing notice and stop before model selection or task creation. The exception is logged for operator diagnostics; internal exception details are not sent to the caller. Extracted the existing unique-interrupted-run settlement path into `_settle_interrupted_pilot_run()` to keep the main pilot method below the configured complexity ceiling.
- Added a regression for the caller-visible ambiguity notice and retained Phase 36's state-level regression proving multiple matching tasks are not mutated and no new task is created.
- Focused pilot-state, orchestrator-pilot and TurnRun recovery tests: **31 passed**. The full suite passed immediately before this small follow-up helper extraction; after the extraction the focused suite, clean `pre-commit run --all-files`, `git diff --check`, and `uv lock --check` passed.
- Restarted the isolated backend again from current source on **8122** with its JSON graph preserved; refreshed jvchat on **3122**. Browser AX state showed an authenticated, clean chat composer and the expected Orchestrator agent. Backend health, Messenger root, and admin login each returned HTTP **200**. No chat was submitted. This smoke therefore produced no new Interaction/task/result and has no model request, duration, tool/event sequence, or token/cost receipt; graph task details were not read back in this check.
- The current startup still reports optional Action dependency-install errors because the project virtualenv has no `pip`. The isolated app remains available, but optional integrations are not qualified by this UI smoke.
- Tested source is base commit `82012aef` plus the current working tree. No staging or commit occurred. Successful provider response, evidence/tool lifecycle, graph-task readback, delivery acknowledgement/replay, and ambiguity-path browser interaction remain unverified.

## Phase 38 — Enforce fail-fast response filters before egress (F11/F14, partial)

- A fail-fast `ChannelFilter` exception previously returned early only from `_deliver_flush()`. `ResponseBus.publish()` then continued to append and fan out the unfiltered `ResponseMessage`, while its egress claim could make downstream code treat the response as delivered. Non-streaming messages now run filters before claiming egress; rejection returns an empty suppression envelope without adapter call, interaction persistence/emitted latch, queue append, or subscriber notification.
- `ReplyAction._pipe_response()` now returns false for empty suppression envelopes. Pilot task completion therefore cannot count this filtered/replayed publish as a successful response.
- Added adversarial regressions proving fail-fast filter errors do not reach queue/subscribers or mutate `Interaction`, and that ReplyAction reports a suppressed bus response as not delivered.
- Response, reply, channel-filter and emitted-latch suites passed. Clean `pre-commit run --all-files`, `git diff --check`, and `uv lock --check` passed. No model prompt/provider behavior changed; no provider call was made.
- Restarted the isolated backend at **8122** from the changed source with its graph preserved; reloaded Messenger at **3122**. The actual browser showed an authenticated clean chat composer and Orchestrator identity; health and Messenger root returned HTTP **200**. No chat request was submitted, so this browser smoke created no Interaction/task/result and has no model duration or usage. The filter-rejection outcome is proved by the adversarial ResponseBus tests, not by the browser configuration.
- Tested source is branch `codex/pydantic-inspired-skill-pilot`, base commit `82012aef`, plus working-tree changes. Key source SHA-256: ResponseBus `af28d4314bf9028c5fc3dc0b54d4a42632f683b64e2d3c697de504b675cd16c4`; ReplyAction `c648528980737537d3ea3a44d2ee80bd3fa469a603d13165ce00ae6ca830280e`. No staging or commit occurred.
- Remaining delivery qualification: channel adapter `send()` failures/ambiguous external acceptance do not yet produce a durable acknowledgement or replay-safe receipt. Streamed outputs also need explicit final-delivery semantics. The app startup still reports optional Action dependency installation errors because its virtualenv lacks `pip`.

## Phase 39 — Merge checkpoint outbox events without stale rollback (F11, partial)

- `HarnessRuntime.import_checkpoint()` now validates imported envelopes and session ownership before changing runtime state, rejects conflicting duplicate sequence numbers, and merges checkpoint events with the live outbox rather than replacing newer live events. A stale checkpoint therefore cannot roll back the event cursor or reuse an already-issued sequence. `EventEnvelope` now rejects invalid sequence/session/cursor combinations.
- Added regressions for stale checkpoint replay, conflicting same-sequence events, and a cursor naming the wrong session. Focused recovery/replay suites: **47 passed**. Full `pytest -q --disable-warnings --tb=no tests/` exited **0**; optional dependency, PDF engine, Zoon skill, and opt-in live-provider cases were skipped. `pre-commit run --all-files`, `git diff --check`, and `uv lock --check` passed before this full run.
- Restarted the isolated app from the current source on API **8122** with its graph preserved, then reloaded the actual jvchat browser at **3122**. Browser AX readback confirmed the authenticated Messenger shell, Orchestrator identity, and enabled clean composer state (no session active). API `/health` returned **200** with database connected; Messenger root returned **200**. No chat prompt was submitted; this smoke produced no model call, new task, result, tool event, or usage receipt. The visible sidebar retains a historical test prompt, so this is service/UI smoke, not a clean-session interaction test.
- Startup still logs optional dependency installation failures because the isolated checkout virtualenv lacks `pip`. Provider calls remain unqualified pending rotation of previously exposed keys. This patch does not provide a durable graph-backed outbox or external delivery acknowledgement; it only prevents stale/conflicting checkpoint imports from corrupting the process-local event sequence.
- Source is branch `codex/pydantic-inspired-skill-pilot`, base `82012aef`, plus the accumulated goal worktree. The worktree contains 72 changed/untracked paths, including the current integration review and progress records; files will be staged and checked as one milestone before commit. The goal remains active.

## Phase 40 — Revalidate grounding at the TaskStore completion boundary (F04, partial)

- The running Agent path already requests `ResearchBrief` and validates fresh fetched-source citations, but the TaskStore's independent `complete()` boundary still accepted the legacy `ConversationalReply` union member and trusted that the caller had validated it. It now rejects source-free output and re-renders the typed brief against persisted evidence before transitioning the task to completed. An invalid source, stale quote, or unmatched quote raises `PilotStateError` while the task remains active.
- Added adversarial persistence tests for a source-free conversational answer and a fabricated supporting quote. Focused state/runtime/evidence tests passed; full `pytest -q --disable-warnings --tb=no tests/` exited **0**, with the known optional-dependency, PDF-engine, local-skill and opt-in-provider cases skipped.
- Restarted the disposable API from this source against the preserved graph and reloaded the actual Messenger browser. AX readback showed the authenticated UI, Orchestrator identity and composer; API health reported connected database with HTTP **200**, Messenger root HTTP **200**. No prompt was submitted, so there is no task/result or request duration/usage for this browser check. The historical sidebar prompt remains visible after refresh.
- The isolated `.env` modification time is still **2026-10-05 19:47:25 -0400**, matching the previously recorded unrotated credentials. No actual provider call was made; provider-backed result and usage qualification remains outstanding until credentials are rotated. Startup continues to report missing `pip` for optional Action dependency installs.
- Current source is commit `e9f85d13` plus this focused change. This improves persistence-layer grounding enforcement but does not establish semantic entailment; the quote check proves excerpt occurrence only. Goal remains active.

## Phase 41 — Persist final output before egress and recover interrupted delivery (F11/F12, partial)

- Added `delivery_pending` to the graph-backed pilot task snapshot lifecycle. A validated `ResearchBrief` and its evidence are now persisted on the existing active TaskStore task before ReplyAction egress. If delivery or terminal persistence raises or is cancelled, the pilot leaves that checkpoint recoverable instead of overwriting it as failed/cancelled. On a subsequent matching request, recovery republishes the saved validated answer and completes the task without another model call. A different request still settles the interrupted task and asks for an explicit restart.
- Added tests proving the pending result survives TaskStore recreation and is discoverable as an active run, and that matching recovery republishes it and marks the task complete. Focused pilot state/orchestrator, harness recovery, ReplyAction and ResponseBus suites passed. Full repository pytest exited **0** with the established nine environment/opt-in skips; `git diff --check` passed.
- Restarted the isolated API from current source against the preserved disposable graph and reloaded Messenger in the browser. The UI showed the authenticated Orchestrator agent, composer, and two saved conversation entries including a historical “Hello there”; I submitted no prompt. API `/health` returned **200** with the graph connected and Messenger root returned **200**. No task/result or provider usage was created by this browser readback. A user-originated request appeared in the server log before the final restart and failed at the configured provider; it was not sent by this test and is not attributed to the final source revision.
- Provider credentials in the isolated `.env` still have the previously recorded modification time (**2026-10-05 19:47:25 -0400**), so no new model call was made. The implementation changes delivery/recovery behavior, but current-revision actual-provider/browser recovery is unqualified. Startup continues to report missing `pip` for optional Action dependency installs.
- Recovery is **at-least-once**, not exactly-once: a process failure after channel acceptance but before TaskStore completion can resend the saved final output on a later matching request. Channel-level idempotency keys and externally acknowledged delivery receipts remain open. The goal remains active.
- Tested source identity is branch `codex/pydantic-inspired-skill-pilot`, base commit `2b665e1a`, plus the staged Phase 41 source/tests. The full repository pytest run completed with exit **0**; focused pilot recovery tests, `pre-commit run --all-files`, `git diff --cached --check`, and `uv lock --check` also passed on the staged snapshot. The checked-out goal branch is the isolated disposable app's source.

## Phase 42 — Preserve a stable ResponseMessage ID across final-output replay (F12, partial)

- Pilot final/recovered output now derives a bounded `o.ResponseMessage.*` identifier from its TaskStore task ID and passes it through `ReplyAction.publish()` into `ResponseBus`. The bus validates supplied IDs and uses the stable value as the returned message ID across separate Interaction IDs. Messenger's existing `useStreaming` adhoc upsert updates a message with the same ID instead of adding a duplicate bubble; channel adapters receive the same ID on their ResponseMessage and can use it as their idempotency key. Calls without a supplied ID preserve the prior random-ID behavior and legacy ReplyAction call shape.
- Added regressions for same-ID replay across distinct interactions, invalid ID rejection, ReplyAction forwarding, and pilot recovery deriving an ID from the task. Focused Python response/reply/pilot tests passed. The `jvchat` `useStreaming.test.tsx` suite passed **7 tests**, including its existing user-adhoc upsert case. Full repository pytest exited **0** with the established nine optional/environment/opt-in skips.
- Restarted the isolated backend against its preserved disposable graph and reloaded the browser. Messenger rendered the authenticated Orchestrator shell, historical conversations and clean composer; API health reported a connected graph with HTTP **200**, Messenger root returned HTTP **200**. No prompt was submitted, so this check created no task/result or provider usage. The `.env` timestamp remains **2026-10-05 19:47:25 -0400**; no live model call was made because the previously exposed provider credentials have not been rotated.
- Stable UI message identity makes Messenger replay upsert-safe, but it does not itself make a third-party adapter idempotent or supply a durable remote-delivery acknowledgement. A crash after channel acceptance can still cause resend; adapter-specific acknowledgement/reconciliation remains open. Startup continues to log missing `pip` for optional Action dependency installation.
- Tested source is branch `codex/pydantic-inspired-skill-pilot`, commit `296ae057` (`Stabilize pilot response replay identity`). The full Python suite, staged pre-commit checks, lock validation, browser reload, and push hooks passed; the worktree was clean after push.

## Phase 43 — Require adapter confirmation before pilot task completion (F12, partial)

- `ResponseBus.publish()` now accepts an opt-in `require_adapter_ack` flag. When enabled and the selected channel adapter returns `False` after its existing retry policy, the bus raises a redacted `ChannelDeliveryError` before recording the message in the session queue or marking the Interaction emitted. `ReplyAction` enables this only when the caller supplies a stable message ID, currently the pilot final-output path, preserving legacy adapter semantics for other callers and queue-based Messenger egress without an adapter.
- The pilot's existing delivery wrapper converts this error into `PilotDeliveryPendingError`; its graph-backed TaskStore snapshot remains `delivery_pending` and can be retried by a later matching interaction. Tests cover adapter rejection/no queue or emitted latch, forwarding the ack requirement for stable-ID replies, and existing pending-delivery recovery. Focused response/reply/pilot suites passed (all collected tests, with three opt-in live-provider skips).
- In-browser smoke: restarted the isolated disposable app at `/private/tmp/jvagent-goal-f01-repeat-20261005/app` against its preserved graph and reloaded Messenger at `http://127.0.0.1:3122/chat/n.Agent.891ce63713b544daafbba61f`. Tested source was commit `296ae057` plus the current Phase 43 changes. Browser displayed the authenticated Orchestrator chat and two saved conversations; API health and Messenger root returned HTTP **200**. No prompt was submitted, so no new Interaction, pilot task/result, or model request was created; persisted task state remains unchanged, duration for the browser reload was about **1.6 s**, and usage was **0 tokens / $0** (no provider request). Since this changes egress acknowledgement, not model behavior, a provider call was not needed. Previously exposed isolated credentials remain unrotated; successful provider-backed qualification is still open.
- Startup still logs optional Action dependency installation failures because the checkout virtualenv has no `pip`. The adapter `False` path is now distinguishable from queue acceptance, but adapter-specific idempotency, ambiguous remote acceptance, durable delivery receipts, and external acknowledgement/reconciliation remain open. This in-memory bus result is not an exactly-once delivery guarantee.
- Full repository pytest exited **0** with nine environment/optional/opt-in skips. `pre-commit run --all-files`, `git diff --cached --check`, and `uv lock --check` passed on the staged tree after isort's first pass corrected the test import order. The goal remains active; the milestone is ready for commit/push.

## Phase 44 — Persist replayable delivery attempts and acknowledgments in TaskStore (F12, partial)

- Bumped `PilotSnapshot` to schema 7 with graph-persisted delivery attempt count, stable message ID, last attempt time, acknowledgment flag, and acknowledgment time. `PilotTaskStore.prepare_delivery()` resets receipt state for a newly generated result; each send attempt persists the stable ID before crossing egress; a positive egress/adapter response persists its acknowledgment before the task transitions to complete. Completion now requires that persisted acknowledgment. v4/v5 usage migration remains intact, v6 migrates to schema 7 with an unacknowledged receipt, and rollback parking recognizes schema 7.
- Recovery now has two explicit cases: an unacknowledged pending task replays the same task-derived ResponseMessage ID; a task whose delivery acknowledgment was saved but whose terminal TaskStore transition was interrupted completes from its graph snapshot without re-sending. Added restart simulations for both cases, a durable failed-send attempt regression, a schema-6 migration check, and reset-on-new-output coverage through the multi-turn integration fixture.
- Focused pilot contract/state/orchestrator/process-restart suites passed: **49 passed**. Full repository pytest exited **0** with nine optional/environment/opt-in skips. `pre-commit run --all-files`, `git diff --cached --check`, and `uv lock --check` passed after the formatter's initial rewrite and clean rerun.
- In-browser smoke after restarting the isolated app at `/private/tmp/jvagent-goal-f01-repeat-20261005/app` against its preserved graph: Messenger at `http://127.0.0.1:3122/chat/n.Agent.891ce63713b544daafbba61f` reloaded in about **1.7 s**, showing the authenticated Orchestrator chat and two saved conversations. API health and Messenger root returned HTTP **200**. No prompt was submitted; visible conversation/task state remained unchanged and no graph write was initiated. Request outcome: none. Provider/estimated usage: **0 tokens / $0**. This is a state/recovery change, not a model behavior change, so no provider request was made. The unrotated exposed provider credentials remain unused.
- The acknowledgment is a graph-persisted record of the adapter/bus confirmation; it is not recipient read-receipt evidence. A crash after remote acceptance but before persisting this receipt is still at-least-once and needs adapter-specific deduplication using the stable ID. Multi-worker races/fencing, in-flight cancellation, and adapter qualification remain open. Optional Action dependency installation still logs missing `pip` in this isolated environment.
- Tested source is branch `codex/pydantic-inspired-skill-pilot`, base commit `6174ebe5`, plus the current Phase 44 implementation/test paths. Their binary diff SHA-256 (excluding this progress record) is `e23e909c99b251c08d93e04267dc607406ff653ae72d1985686056fb421c4089`. The goal remains active.

## Phase 45 — Fail closed when a distributed lease becomes uncertain (F12/F19, partial)

- The shared Redis/DynamoDB lease heartbeat now cancels the owning turn/critical section when Redis compare-and-expire or DynamoDB holder-conditional renewal proves the token was lost. Transient renewal failures retry, but a stalled renewal is bounded by the remaining lease safety window and the owner is cancelled before the lease could expire. Redis renewal results and DynamoDB conditional-check failures now preserve definitive ownership-loss signals. The shared path covers both per-conversation mutation locks and the bootstrap distributed lease.
- Added adversarial heartbeat tests for explicit token loss, repeated renewal outages, and a hung renewal call. Added integration regressions proving both conversation turns and the shared bootstrap lease owner are cancelled when fake Redis returns the lost-token result. Focused lock tests: **20 passed**. Full repository pytest passed with **9 skips** (optional dependencies, missing PDF engine, opt-in live model tests, and unavailable local skill); existing Python 3.10/google-api-core, PyPDF2, and pytest-mark warnings remain. `pre-commit run --all-files` passed on the staged tree; `git diff --cached --check` passed.
- Restarted API process **84507** (old source) and launched the isolated app from the current checkout with the preserved disposable graph at `/private/tmp/jvagent-goal-f01-repeat-20261005/app/jvdb`. Startup loaded that app's `.env`, initialized the connected JSON graph, and completed despite optional Action dependency install messages caused by the checkout Python lacking `pip`. API `/health` returned **200** and Messenger root returned **200**.
- Real browser smoke: reloaded authenticated Messenger tab `http://127.0.0.1:3122/chat/n.Agent.891ce63713b544daafbba61f` after the API restart. The UI settled to the Orchestrator chat in under one second and visibly showed two saved conversations, the existing “Reply with exactly: concurrency checkpoint smoke complete.” conversation, and an enabled composer. No message was sent. No new Interaction or pilot TaskStore result was created; graph state remained unchanged by chat submission. Request outcome: none. Model/provider usage and cost: **0 tokens / $0** because this lease-infrastructure change does not alter model behavior and no model request was made.
- This does not establish multi-process Redis/DynamoDB integration against live services, fencing against a paused process that resumes after cancellation, shutdown behavior under cancellation suppression, provider delivery recovery, or deployment readiness. Startup still reports optional Action dependency installation failures due to missing `pip`. The broader goal and enterprise qualification remain active.
- Tested source identity: branch `codex/pydantic-inspired-skill-pilot`, base commit `bf4c2aa6`, plus the staged implementation/test diff with SHA-256 `d63d76c9cde58bb572b871520b6db25d8b407a1842f0630c9c31218b93bc1c02` (acceptance record excluded). The full repository suite passed before the final two lock-owner integration tests were added; those final changes were independently covered by the 20-test focused slice and a clean pre-commit run.

## Phase 46 — Separate mechanical evidence checks from factual qualification (F07, partial)

- The fixed-evidence scorer retains `passed` for compatibility but now labels it explicitly as `mechanical_checks_passed`; every deterministic pass remains `fully_qualified: false` and `pending_blind_review`. Deterministic failures report `mechanical_failure`. The manifest links the required review protocol.
- Added an adversarial exact-quote/invalid-causality case: the quoted source says revenue rose after launch but does not establish cause; the scorer can mechanically pass the excerpt while correctly leaving semantic entailment unassessed and qualification pending. Added a blind review and adjudication protocol requiring two independent raters, source-entailment review at full claim scope, preserved annotations, third-party adjudication of disagreements, and explicit reporting of agreement and qualification limits.
- Focused eval tests: **13 passed**. Full `./.venv/bin/python -m pytest tests/ -q` exited **0** with **9 skips** for absent optional dependencies, PDF engine, unavailable local skill, and opt-in live-provider tests. `pre-commit run --all-files` passed cleanly after Black's first run reformatted the new regression test and the required clean rerun. `git diff --check` passed.
- In-browser smoke: refreshed the existing authenticated Messenger at `http://127.0.0.1:3122/chat/n.Agent.891ce63713b544daafbba61f`; AX readback showed the authenticated Orchestrator, two saved conversations, and enabled composer. API health and Messenger root returned HTTP **200**. No message was submitted and no graph interaction/task/result was created. This evaluation-only change made no model request; usage was **0 tokens / $0**. The existing sidebar contains a historical prompt, so this is service/UI smoke rather than a fresh conversation interaction test.
- Provider calls remain intentionally unrun pending rotation of previously exposed provider credentials. This protocol establishes a review method, not completed semantic human ratings or a model/provider qualification. Optional Action installation still reports missing `pip` in the isolated app environment.
- Tested source is branch `codex/pydantic-inspired-skill-pilot`, base commit `2737819f`, plus staged changes with binary diff SHA-256 `e3f98dc328e56f6c771f9cf274a6f52a132a5ee10941dcd00e0bc0b329a33167` (this acceptance record excluded). The goal remains active.

## Phase 47 — Remove query credentials from evidence citations (F07, partial)

- `normalize_evidence_url()` now strips common credential-bearing query keys before evidence URLs are persisted, hashed into source IDs, or rendered as citations. Covered generic tokens/API keys/passwords/signatures, AWS signed-query fields, Google signed-query fields, and Google access IDs; ordinary query parameters remain. Existing URL canonicalization still rejects user-info, removes fragments, normalizes host/port, and applies the same safe normalization to the requested URL, typed fetch receipt, and rendered `# Source` marker.
- Added adversarial checks for generic token/API-key/client-secret parameters, AWS signed URLs, preserved non-secret parameters, and an end-to-end typed fetch receipt whose resulting citation must not expose its query token. Updated the long-URL regression to assert the secret query is absent.
- Focused evidence/contract tests: **42 passed**. Full repository pytest exited **0** with **9 skips** (optional dependencies, no PDF engine, live provider tests opt-in, unavailable local skill, and empty environment-dependent parameter set). `pre-commit run --all-files` passed after its first run reformatted and sorted imports in the new tests, followed by a clean rerun. No unrelated paths were staged.
- Restarted the isolated API from this checkout with the existing disposable graph at `/private/tmp/jvagent-goal-f01-repeat-20261005/app/jvdb`; startup loaded the app `.env`. API health and Messenger root returned **200**. Refreshed authenticated Messenger tab `http://127.0.0.1:3122/chat/n.Agent.891ce63713b544daafbba61f`; AX readback showed Orchestrator identity, two saved conversations, and enabled composer. Persisted conversation/task/result state did not change. No request was submitted: outcome **none**, duration **N/A**, usage **0 tokens / $0**. The `.env` modification timestamp remains 2026-10-05 19:47:25 -0400, so the provider credential rotation requested earlier is not evidenced; no provider request was made.
- Source-level checks establish query sanitization, but actual browser-to-provider citations remain unqualified until the credentials are rotated and a model-backed research call is run. Query-key sanitization cannot detect every provider-specific secret parameter name; extend the denylist when integrations reveal additional schemes. The broader goal remains active.
- Tested source is branch `codex/pydantic-inspired-skill-pilot`, base commit `281a3ff7`, plus staged code/test diff SHA-256 `9cad44475ec7ff2ebf561b440e0dfa8e34992a3c86fb5aaab7174a9d4b4fa11b` (this acceptance record excluded).

## Phase 48 — Make per-run evidence saturation explicit and credential-safe (F07, partial)

- The 30-reference per-run cap previously dropped excess valid search/fetch receipts without surfacing the omission. The collector now counts distinct withheld source URLs; search output tags unavailable rows and reports the cumulative omitted count, while a fetched page that cannot be retained receives a clear non-citable notice. `PilotSnapshot.evidence_overflow_count` is saved through the existing graph-backed TaskStore snapshot; no second state store was introduced. Search links and the host-generated fetch `# Source:` header are normalized before tool-result text is returned to the model, closing the remaining query-credential leak in the model-visible path as well as persisted citations.
- Added tests for 31 search results against the 30-source bound, explicit unavailable-source annotations, disclosed fetch overflow, TaskStore round-trip of overflow count, and removal of query credentials from search/fetch tool results and final citations. The real Orchestrator integration regression performs two concurrent 31-source searches, persists exactly 30 references and one omitted source, and still completes from the fetched retained source.
- Focused evidence/state/Orchestrator tests: **62 passed**. Full `./.venv/bin/python -m pytest tests/ -q` exited **0** with **9 skips** (optional dependencies, unavailable PDF engine/local skill, and opt-in live-provider tests). `pre-commit run --all-files` and `git diff --cached --check` passed; initial formatting changes were staged and the clean pre-commit rerun passed. No unrelated work was staged.
- Restarted the isolated API from this checkout against `/private/tmp/jvagent-goal-f01-repeat-20261005/app/jvdb` and refreshed Messenger at `http://127.0.0.1:3122/chat/n.Agent.891ce63713b544daafbba61f`. API health and Messenger root returned **200**. AX readback showed the authenticated Orchestrator, two saved conversations, and enabled composer. Existing graph conversation/task/result state was unchanged. No prompt was submitted: request outcome **none**, request duration **N/A**, token/cost usage **0 / $0**. The isolated `.env` modification time is unchanged at **2026-10-05 19:47:25 -0400**; no actual-provider call was made, so model-consumption of the new omission notice is not qualified in-browser.
- The cap is now auditable and non-silent, but this does not establish semantic factual entailment or actual-provider behavior. Source credential redaction uses a bounded known-key list; provider-specific credential names may need additions. App startup still reports missing `pip` for optional Action dependency installation. Goal remains active.
- Tested source is branch `codex/pydantic-inspired-skill-pilot`, base commit `ad239a87`, plus staged implementation/test diff SHA-256 `d20eb7ab329f7702555443fe7ef0aa2a6dc696b4b530f2907e1a37526344aa17` (this acceptance record excluded).

## Phase 49 — Verify persisted AccessControl security events (F01/F02, partial)

- The tool binding, schema validation, reserved wrapper-key rejection, and fail-closed configured AccessControl resolution described in Phases 1, 14, 21, and 34 were already present on the current branch. This round closed the remaining persistence/discoverability gap: Orchestrator access denial and policy-failure records now set both the existing structured `event` field and the indexed `event_code` field, preserving compatibility while allowing the `logs` service to locate them by code.
- Added an integration regression that invokes the real access-policy failure path, persists through jvspatial's actual `DBLogHandler` into a separate disposable JSON logs database, reads the saved `DBLog` object back, verifies the indexed event code and structured fields, and confirms exception details remain redacted.
- Focused AccessControl, Orchestrator tool-composition, full Pydantic AI Agent-run adversarial, and DBLog persistence tests passed. Full suite: **4,315 passed, 9 skipped, 31 warnings** in **184.97 seconds**. `pre-commit run --all-files` passed all configured hooks before this acceptance-record append; rerun it after staging the complete milestone. The earlier hook run also passed Black, isort, Flake8, mypy, and detect-secrets.
- Browser smoke used isolated app `/private/tmp/jvagent-goal-f01-repeat-20261005/app` and its JSON graph, API **8122**, Messenger **3122**. Restarted the API from the updated source and reloaded the authenticated Messenger. The UI showed the existing two conversations, Orchestrator identity, and ready composer; both API health and Messenger root returned HTTP **200**. No prompt was submitted, so there were no new reasoning/tool events, model request, task, or result; the graph retained its two existing closed/emitted interactions. Reload took under one second. Model usage was **0 tokens / $0**; an actual provider call was not needed because this round changed security-event persistence metadata, not model behavior.
- This proves the local graph-backed logging path and stored event fields only. It does not qualify the production logging backend, retention, operator alerts/report UI, live-provider tool-selection attack behavior, or all non-Orchestrator AccessControl consumers. The configured provider credential remains unconfirmed, so no live model call was made.
- Tested source is branch `codex/pydantic-inspired-skill-pilot`, commit **`e45e66ae` plus the Phase 49 source/test diff**. The broader enterprise qualification goal remains active.

## Phase 50 — Close artifact-vault AccessControl fallback and account for failed provider spend (F01/F02/F10, partial)

- Closed a fail-open seam outside the Orchestrator: required AccessControlAction resolution in the artifact-vault skill and Action now raises a sanitized failure when policy lookup is missing or fails, and group-membership failures propagate. Declared the artifact Action's AccessControl dependency so bootstrap ordering matches the Skill contract. Added adversarial tests for missing policy, sanitized lookup failure, membership failure, and the direct Action path.
- Failed or cancelled provider attempts marked `provider_unreported` now increment `unknown_cost_call_count`. They do not fabricate tokens or cost, and remain uncertain even if a later retry has its own reported usage. Added regressions for standalone failed attempts and failed-then-reported retry accounting.
- Focused artifact-vault and usage tests passed. Full repository pytest exited **0** with **9 skips** (optional PDF/STT dependencies, PDF engine, opt-in live-provider tests, unavailable local skill, and an empty environment-dependent parameter set). `pre-commit run --all-files` passed all configured checks.
- Restarted the isolated backend from the updated checkout against `/private/tmp/jvagent-goal-f01-repeat-20261005/app/jvdb`; API health and Messenger root each returned HTTP **200**. In Messenger at `http://127.0.0.1:3122/chat/n.Agent.891ce63713b544daafbba61f`, submitted “Please say hello in one short sentence.” The configured Ollama Cloud request failed with HTTP **401** in about **0.25 s**; the closed/emitted Interaction returned the service-error fallback in about **1.1 s**. Its persisted usage shows `model_attempt_count=1`, `failed_model_attempt_count=1`, `unknown_cost_call_count=1`, `model_call_count=0`, zero tokens and `$0.00` reported/estimated cost. The UI presented the failure instead of hanging. This does not qualify successful provider behavior; the isolated key remains unauthorized and was not read or exposed.
- Startup still reports optional Action dependency installation errors because the checkout virtualenv lacks `pip`. The user-facing provider error is bounded, and failed-attempt charge remains explicitly unknown; a zero dollar total must not be interpreted as proof that the provider did not bill.
- Tested source is branch `codex/pydantic-inspired-skill-pilot`, commit `752d33e9` plus the Phase 50 implementation/test diff. The staged snapshot passed the repository commit gates; this milestone is ready for commit and push. Goal remains active.
