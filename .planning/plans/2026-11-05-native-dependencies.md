# One Pydantic core version and installable native test profile

Parent revision: `4a0e6e6cdcdaba74c9bdcc156aa9112a4d97ad11`.
All nine parent CI checks passed. This increment addresses dependency/profile
qualification; native cutover and legacy deletion remain incomplete.

## Changes and boundaries

The retained `pydantic-pilot` extra now selects the same core 2.66.0 as
`native-harness`/`native-mcp`; official Harness remains 0.66.0. See
[`pyproject.toml:46`](../../pyproject.toml#L46). This does not promote the pilot
to the final execution path. Its alias, implementation and tests remain subject
to explicit migration/removal.

The general test extra no longer installs LiteLLM. That package remains in its
existing explicit `litellm` extra, selected in the transitional full CI job and
other legacy lanes. Its OpenAI <3 constraint is incompatible with the native
OpenAI >=3.31 requirement. No package metadata override or LiteLLM upgrade was
used. Native and general test extras can now install together; a universal claim
that all existing Action dependencies install with native execution would still
be false. The full PageIndex/LiteLLM manifests and requirements-all remain
transitional migration work.

The refreshed uv.lock contains one Pydantic core version and declares the
`litellm`/`native-harness` extra conflict explicitly at
[`pyproject.toml:152`](../../pyproject.toml#L152), using
[uv's documented conflict mechanism](https://docs.astral.sh/uv/concepts/resolution/#conflicting-dependencies).
Remove this boundary when the obsolete transport adapters/dependencies are
retired. Native CI now installs the combined test/native/MCP/pilot-compatibility
profile and runs the full applicable suite on both Python versions
([workflow](../../.github/workflows/test-jvagent.yaml)).

## Test qualification

The first full native-profile probe exposed 66 LiteLLM conformance failures:
that shared fixture assumed an optional package was installed. The fixture now
uses the same explicit optional-dependency skip convention as the existing
LiteLLM unit module, before constructing a LiteLLM adapter
([fixture](../../tests/action/model/conformance/conftest.py#L171)).
Those tests are not silently counted as native passes: the explicit transitional
profile still runs and passes them. [Initial failure summary](2026-11-05-native-dependencies-initial.txt) and final
results are retained below. The new requirement regressions prevent multiple
core SDK pins or LiteLLM returning to the general test extra
([tests](../../tests/test_requirements_sync.py#L156)).

- Python 3.11 combined native profile, with disposable PostgreSQL: 4,942 passed,
  87 skipped. [Full result](2026-11-05-native-dependencies-full311.txt).
- Python 3.12 locked combined profile, without DSN: 4,906 passed, 123 skipped.
  [Full result](2026-11-05-native-dependencies-full312.txt). Separately, all 358
  native/host/read-tool/PostgreSQL cases pass on this same environment.
- Native/host/read-tool/real PostgreSQL suites: 358 pass per
  [Python 3.11](2026-11-05-native-dependencies-pg311.txt) /
  [Python 3.12](2026-11-05-native-dependencies-pg312.txt).
- [Transitional Pydantic 2.66 pilot compatibility/requirements](2026-11-05-native-dependencies-compat311.txt):
  195 pass, six environment/live skips.
  [Explicit LiteLLM conformance/requirements](2026-11-05-native-dependencies-legacy-profiles311.txt):
  139 pass, one recording-mode skip.
  [Native equivalent](2026-11-05-native-dependencies-native-profiles311.txt):
  67 pass, 73 explicit skips. The 72 optional LiteLLM transport cases are
  exercised by the transitional profile, not counted as native passes.
- `uv lock --check` and dependency checks pass in all recorded profiles.
  [Environment inventories](2026-11-05-native-dependencies-environments.json)
  distinguish fresh pip-interface resolution from locked resolution and retain
  exact package versions. They do not assert identical complete package sets.

Native profiles deliberately do not install LiteLLM. Remaining skips include
opt-in paid/live cases and environment-gated tests; separate browser and real-PG
proofs do not turn every skipped scenario into a qualified case. Final all-files,
normal full pre-push and exact-head CI are separate publication gates.

## Browser and artifact qualification

Disposable app/graph and authenticated synthetic principal retained from the
previous local qualification, backend 8136/reference UI 3136. Fresh Python 3.11
combined-profile backend, actual `openai:gpt-4.1`, isolated browser context
`native-unified-dependencies`:

- Foreground `NATIVE-DEPENDENCY-CHECK: Reply exactly READY.` returned READY.
  Browser HTTP resource duration 9.0341s; persisted Interaction 8.870914s.
- A due read task queued at 23:43:23.832 UTC was automatically claimed at
  23:44:00.744059 and completed at 23:44:05.708663. No manual tick was called.
  Actual MCP lookup returned room Cedar/start 14:30; browser displayed reply,
  tool activity and model/token/timing statistics. Interaction 4.931143s.
- Three provider requests total: 4,102 reported tokens, USD 0.008492 estimated
  cost. Graph task/result, SDK outcomes and conversation spending reconcile;
  no remaining usage reserves. [Browser](2026-11-05-native-dependencies-browser.json),
  [SQL/source proof](2026-11-05-native-dependencies-final-smoke.json).
- A wheel was built and installed with native/MCP/PostgreSQL runtime extras in
  a fresh environment. Import outside the checkout resolves to site-packages;
  LiteLLM is absent and native Action/interview skill assets are present.
  [Artifact hash, package inventory and checks](2026-11-05-native-dependencies-wheel.json).
- The disposable backend then ran that wheel from site-packages. An actual
  browser follow-up returned WHEEL-READY with its usage/timing visible; HTTP
  resource 5.2628s, persisted Interaction 5.13344s. Fresh SQL proves the third
  delivered Interaction and original task/result retained. The runtime observer
  identifies the wheel interpreter/module origin and zero legacy execution
  modules. [Wheel browser](2026-11-05-native-dependencies-wheel-browser.json),
  [fresh SQL/runtime receipt](2026-11-05-native-dependencies-wheel-smoke.json).
- Entire browser round: four provider requests, 6,830 reported tokens and
  USD 0.011872 estimated cost. The wheel follow-up adds 2,728 tokens and
  USD 0.003380 estimated, with no unsettled reserves. This qualifies the backend
  wheel only; the UI still comes from the separately source-built reference client.

This is not a performance comparison: providers and concurrent qualification
work affect timings. Python >=3.11 was already declared before this increment.
No claim of reduced runtime footprint follows from the shorter lockfile.
Broader provider/extension qualification, packaged frontend delivery, security
remediation, historical migration, default native cutover and legacy/pilot
deletion remain open.
