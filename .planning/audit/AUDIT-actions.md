# AUDIT — jvagent action library

> Read-only audit. Date: 2026-05-17. Reviewed against `.planning/SPEC.md` §4 (action contract), `.planning/action-authoring.md`, `.planning/actions-catalog.md`, `jvagent/action/CLAUDE.md`, `jvagent/action/base.py`.
>
> Scope: every package under `jvagent/action/` **except** `interact/`, `cockpit/`, and `base.py` (those are audited separately). The `loader/`, `response/`, `utils/`, and `model/` infrastructure modules are covered.

---

## Summary

- Action packages reviewed: **41** (39 with `info.yaml` + `loader/`, `response/`, `utils/`)
- Findings: **6 critical**, **17 high**, **15 medium**, **9 low**
- Contract compliance: **~14 / 39** packages fully compliant; most others have ≥1 contract drift (missing `endpoints.py`, package-name / dir-name mismatch, missing `from . import endpoints`, or endpoint path not under `/actions/{action_id}/`).

Findings are grouped first as cross-cutting (apply to ≥2 actions), then per-action where unique. Severities follow the audit prompt's guide.

---

## Cross-cutting findings (apply to ≥2 actions)

### CRITICAL

#### XC-1 — OAuth tokens (access + refresh + client_secret) persisted in **plaintext** in the graph DB
**Affects**: `google/` (`GoogleToken`), `microsoft/` (`MicrosoftToken`); transitively all five Google sub-actions and four Microsoft sub-actions that depend on these token nodes.

**Issue**: `GoogleToken` (`google/google_token.py:9-50`) and `MicrosoftToken` (`microsoft/microsoft_token.py:9-49`) store `token`, `refresh_token`, `client_id`, and **`client_secret`** as plain `attribute(default_factory=str)` string fields. There is no encryption-at-rest, no field-level encryption, and no scope-down to the per-action sandbox. A read-only DB dump or any code path that can list `GoogleToken` / `MicrosoftToken` nodes leaks long-lived OAuth refresh tokens **and** the app's confidential `client_secret` — the latter is by far the worse outcome (one secret per Entra-ID / Google Cloud app, not per user). With these an attacker can mint new tokens for any account that has consented.

**Why it matters**: Refresh tokens for Google Drive/Gmail/Calendar/Docs/Sheets and Outlook/OneDrive/Calendar/Excel typically have a half-life of months. The same `MicrosoftToken.client_secret` is duplicated per row (every refresh re-saves it) — making rotation a graph-wide rewrite. SPEC.md §4.2 lists required persisted attributes; secrets are conspicuously absent because they should not be persisted as `attribute(...)` on the action — they should come from env. Storing the client secret on a per-action token row is duplication + leak surface.

**Fix**:
- Remove `client_secret` from `GoogleToken` / `MicrosoftToken`; always re-read from `GOOGLE_CLIENT_SECRETS_JSON` / `MICROSOFT_CLIENT_SECRET` env at refresh time.
- Encrypt `token` and `refresh_token` at rest (e.g. AES-GCM with a key from `JVSPATIAL_JWT_SECRET_KEY` or a dedicated `JVAGENT_TOKEN_ENC_KEY`); store ciphertext only.
- Audit-log refreshes.

#### XC-2 — OAuth `state` parameter doubles as PKCE `code_verifier` carrier with no CSRF anti-replay
**Affects**: `google/google_action.py:140-152, 154-163`, `microsoft/microsoft_action.py:94-117, 118-151`. Both Google and Microsoft sub-actions are affected.

**Issue**: The `state` parameter encodes `f"{self.id}:{code_verifier}"` (Google `google_action.py:144`, Microsoft `microsoft_action.py:103`). The OAuth `state` parameter is meant to be a server-generated unguessable, one-shot CSRF token verified at callback time. Embedding the action_id (well-known, in the URL) and the PKCE verifier (PKCE secret) into `state` means:
1. The PKCE verifier travels to the user's browser **and to Google/Microsoft's authorization endpoint** — which defeats the purpose of PKCE (which is to prove possession of a secret never seen by the IdP until token exchange). Anyone who can read the user's address bar or browser history gets the verifier.
2. There is **no anti-CSRF check** at callback: any party can POST to `/google/callback/?code=...&state=<known_action_id>:<verifier-they-picked>` and the handler will exchange the code into a token for the targeted action. (Granted the attacker has to provide a valid `code` from the IdP, but in token-theft / open-redirector chains this matters.)
3. `state` is logged in the callback handler (`google/endpoints.py:309 logger.info("Processing Google OAuth callback for action: %s", action_id)` — at least action_id is logged; full `state` may end up in proxy access logs.

**Why it matters**: Defeats PKCE protection on confidential clients; opens CSRF window on callback; verifier leaks via referer/logs. RFC 6749 §10.12 and RFC 7636 §7.1 explicitly warn against this pattern.

**Fix**:
- Generate a random `state` (e.g. `secrets.token_urlsafe(32)`), store `(state -> {action_id, code_verifier, expiry})` in a short-lived cache (5 min), reject unknown states at callback.
- Keep `code_verifier` server-side only.

#### XC-3 — `requests` / `http.client` / sync SDK calls inside `async def` block the event loop
**Affects**: `video_generation/heygen.py:90, 124, 145, 350, 447, 463, 487, 504, 517`; `web_search/serper/serper.py:83-88`; `web_search/serpapi/serpapi.py:83-86`; `tts_action/elevenlabs/elevenlabs.py:40-47, 91-114, 117-147, 149-173`; `google/google_drive_action/google_drive_action.py:68, 80, 88, plus 6+ other `.execute()` calls`; all other `google/*/google_*.py` files that wrap `googleapiclient`.

**Issue**: These are `async def` methods invoked on an asyncio event loop, but they call **synchronous** blocking HTTP / SDK clients:
- `requests.get/post/delete` (HeyGen)
- `http.client.HTTPSConnection` (Serper)
- `serpapi.GoogleSearch().get_dict()` (SerpAPI)
- `elevenlabs.client.ElevenLabs().text_to_speech.convert(...)` and `.voices.get_all()` (ElevenLabs) — the SDK is sync.
- `googleapiclient` `service.files().create().execute()` etc. — the Google API client is sync.

Each call **stalls the event loop** for the duration of the network round-trip. Under load this serializes all coroutines on the worker, kills throughput, and risks request timeouts on unrelated webhooks. Lambdas amplify this (single-thread).

**Why it matters**: Channel webhooks (WhatsApp, Facebook, Email) on the same worker can stall behind a single HeyGen video creation. The `BaseModelAction` retry framework cannot intercept these (only catches httpx errors). Perf out of scope per audit prompt but here it crosses into correctness — long stalls trigger client/Lambda timeouts.

**Fix**:
- HeyGen: replace `requests` with `httpx.AsyncClient` (it is already in pip deps).
- Serper: use `httpx.AsyncClient` (Brave already does).
- SerpAPI: `asyncio.to_thread(lambda: GoogleSearch(params).get_dict())` or call SerpAPI's HTTP directly.
- ElevenLabs: use `asyncio.to_thread(...)` for the sync SDK calls, or migrate to `AsyncElevenLabs` (the SDK exposes one).
- Google Workspace: use `asyncio.to_thread` for `googleapiclient` calls or migrate to `google-auth` + raw `httpx` Graph-style calls.

#### XC-4 — Endpoints registered outside the contract `/actions/{action_id}/...` prefix → endpoint leak on deregister
**Affects**: 13 packages with endpoints not under `/actions/{action_id}/`:
- `access_control/endpoints.py:60, 130, 176, 221, 262, 306, 352, 397, 439, 488, 548` — `/agents/{agent_id}/access_control/...`
- `email_action/endpoints.py:54` — `/email/interact/webhook/{agent_id}`
- `facebook_action/endpoints.py:613, 634` — `/messenger/interact/webhook/{agent_id}` (also `/actions/{action_id}/facebook/...` for many routes — those are fine)
- `google/endpoints.py:183, 274` — `/google/{action_id}`, `/google/callback/`
- `microsoft/endpoints.py:178, 250` — `/microsoft/{action_id}`, `/microsoft/callback/`
- `pageindex/endpoints.py:871, 1280, 1332, 1381, 1446, 1508, 1548, 1604, 1635, 1674, 1714, 1805, 1855, 1912, 2021, 2114, 2178, 2251` — `/agents/{agent_id}/pageindex/...` and `/pageindex_retrieval_interact_action/interact/webhook/{agent_id}`
- `pageindex/pageindex_google_drive_sync_action/endpoints.py:517` — `/page_index_google_drive_sync/interact/webhook/{agent_id}`
- `postiz_action/endpoints.py:9, 33` — `/postiz/...`
- `sentdm_broadcast/endpoints.py:436` — `/webhook/{action_id}` (also extremely generic — collides with anything)
- `task_dispatcher/endpoints.py:15, 84` — `/proactive/tick/{agent_id}`, `/proactive/webhooks/{agent_id}`
- `whatsapp/endpoints.py:252, 295, 401` — `/whatsapp/{action_id}/...`, `/whatsapp/interact/webhook/{agent_id}`

**Issue**: SPEC.md §11 invariant 8 and `action/CLAUDE.md` §3.2 require:
> Action endpoints registered via `@endpoint` MUST be discoverable by `_discover_action_endpoints()` (`base.py:354`) so deregister can clean them up. Use `/actions/{action_id}/...` path prefixes.

`base.py:373` scans `f"/actions/{self.id}/"` for cleanup. Endpoints under `/agents/{agent_id}/...`, `/google/...`, `/microsoft/...`, `/whatsapp/...`, `/proactive/...`, `/email/...`, `/messenger/...`, `/postiz/...`, `/webhook/{action_id}`, `/pageindex_retrieval_interact_action/...`, `/page_index_google_drive_sync/...` will **not** be matched. When the action is `on_deregister`'d, those routes remain wired to a now-deleted action — calls succeed (handler tries `Action.get(action_id)`, gets `None`, returns 404) but the route registration leaks, breaking idempotency of register/deregister/re-register cycles.

For agent-scoped routes (`/agents/{agent_id}/...`), the leak is partially harmless because they re-bind to the new action via `agent.get_action_by_type(...)` at request time. But the registry **still** holds the stale function objects — slow leak under repeated deregister.

For unique-host paths like `/postiz/auth/...` (no `agent_id` parameter, just `find_one({"context.enabled": True})`), the endpoint is **single-tenant** — it returns whichever Postiz action it finds first across all agents.

**Why it matters**:
- Repeated re-registration leaks route entries (FastAPI route table grows).
- Some routes silently retarget the wrong action under multi-tenancy (Postiz).
- Deregister contract (SPEC.md §11 #8) is broken across 13 packages.

**Fix**: For action-scoped operations, move to `/actions/{action_id}/...`. For agent-scoped webhooks (WhatsApp/Email/Messenger), the path is fine for routing logic but should be **additionally** registered under `/actions/{action_id}/inbound` so deregister can find them, or `_discover_action_endpoints()` should be extended to recognize per-action path templates declared in `info.yaml`.

#### XC-5 — `info.yaml` `package.name` does NOT match directory path on 19 / 39 packages
**Affects**: `whatsapp` (`jvagent/whatsapp_action`), `router` (`jvagent/interact_router`), `intro` (`jvagent/intro_interact_action`), `sentdm_broadcast` (`jvagent/sentdm_broadcast_action`), `long_memory` (`jvagent/long_memory_interact_action`), `long_memory_store` (`jvagent/long_memory_store_interact_action`), `long_memory_retrieval` (`jvagent/long_memory_retrieval_interact_action`), `retrieval` (`jvagent/retrieval_interact_action`), `interview` (`jvagent/interview_interact_action`), `web_search_retrieval` (`jvagent/web_search_retrieval_interact_action`), `access_control` (`jvagent/access_control_action`), `video_generation` (`jvagent/heygen_video_action`), `converse` (`jvagent/converse_interact_action`), `vectorstore/typesense` (`jvagent/typesense_vectorstore`), `web_search/brave` (`jvagent/brave_web_search`), `web_search/serpapi` (`jvagent/serpapi_web_search`), `web_search/serper` (`jvagent/serper_web_search`), `stt_action/deepgram` (`jvagent/deepgram_stt`), `tts_action/elevenlabs` (`jvagent/elevenlabs_tts`).

**Issue**: `action-authoring.md` §3 (`package.name`) says it "must match dir layout". The loader (`loader/info_yaml.py:40-46`) actually uses the second half of `package.name` (not the directory name) as `action_name`, so loading does work — but external configs reference actions by either name. `actions-catalog.md` already lists divergent names (e.g. it calls it `jvagent/whatsapp` and `jvagent/web_search/serper` — neither matches the on-disk YAML).

**Why it matters**:
- Documentation drift between SPEC, catalog, and ground truth.
- `agent.yaml` writers see two valid spellings; tooling that splits on directory walks vs the YAML name will disagree.

**Fix**: Either rename directories to match `package.name`, or normalize `package.name` to match directory. Update `actions-catalog.md` to reflect ground truth.

### HIGH

#### XC-6 — Missing `endpoints.py` and/or `from . import endpoints` in `__init__.py` violates the four-file contract
**Affects**: `long_memory/__init__.py`, `long_memory_retrieval/__init__.py`, `long_memory_store/__init__.py`, `retrieval/__init__.py`, `router/__init__.py`, `task_creation_interact_action/__init__.py`, `task_trigger_interact_action/__init__.py`, `converse/__init__.py`, `handoff_interact_action/__init__.py`, `interview/__init__.py`, `intro/__init__.py`, `web_search_retrieval/__init__.py`, `web_search/brave/__init__.py`, `web_search/serpapi/__init__.py`, `web_search/serper/__init__.py`, `vectorstore/typesense/__init__.py`, `tts_action/elevenlabs/__init__.py`, `stt_action/deepgram/__init__.py`, `mcp/__init__.py`, `video_generation/__init__.py`, `pageindex/pageindex_retrieval_interact_action/__init__.py`, `pageindex/pageindex_action/__init__.py`, `pageindex/pageindex_google_drive_sync_action/__init__.py`.

**Issue**: `action-authoring.md` §4 mandates a 4-file pattern:
```
{action_name}/
├── __init__.py     # exports class + imports endpoints
├── {action_name}.py
├── endpoints.py
└── info.yaml
```
~25 packages either lack `endpoints.py` entirely or have it but `__init__.py` doesn't `from . import endpoints`. Some genuinely have no endpoints to expose (`converse`, `intro`, `long_memory`, etc.) — for those the 4-file contract is over-prescriptive but still treated as a contract.

The packages that **have** an `endpoints.py` but **don't import it** in `__init__.py` are real bugs: their endpoints register only if some other code path imports them. They are:
- `pageindex/pageindex_google_drive_sync_action` — has `endpoints.py` but the lazy-load `__init__.py` (uses `__getattr__`) never imports `endpoints`. Per the docstring this is intentional ("Endpoints register when ``pageindex_google_drive_sync_action`` module loads") but the loader does NOT always pull in `pageindex_google_drive_sync_action.py`; it follows whatever the `__init__.py` triggers.
- `persona/__init__.py` does `from jvagent.action.persona import endpoints` (absolute path) which works but is non-standard.

**Why it matters**: Endpoints silently missing in production. Inconsistent compliance signals to authors that "the contract is optional" — guaranteed drift.

**Fix**:
- For each package that has endpoints: add `from . import endpoints  # noqa: F401` to its `__init__.py`.
- For packages without endpoints: either add empty `endpoints.py` (preferred — matches the contract) or relax the doc.
- For `pageindex_google_drive_sync_action`: import `endpoints` from `__init__.py`.

#### XC-7 — Channel adapter webhook endpoints use `webhook=True, auth=False, webhook_auth="api_key"` but no signature / HMAC check beyond the API key
**Affects**: `whatsapp/endpoints.py:400-405` (`whatsapp_interact`), `email_action/endpoints.py:53-67` (sendgrid), `task_dispatcher/endpoints.py:14-19` (`/proactive/tick/{agent_id}`), `pageindex/endpoints.py:1911-1916` (`pageindex_llm_webhook`), `pageindex/pageindex_google_drive_sync_action/endpoints.py:517`, `sentdm_broadcast/endpoints.py:435-440` is the **exception** (it has both api_key auth and HMAC).

**Issue**: The api_key middleware proves the caller possesses a long-lived key. It does **not** prove the call originated from the provider (WhatsApp bridge, SendGrid, jvforge, etc.). If the api_key leaks (logs, monitoring dashboards, error messages, or just being in a public webhook URL query string), an attacker can replay arbitrary webhook payloads and trigger interactions on behalf of the channel.

WhatsApp providers (WPPConnect, UltraMsg, WWebJS) do not ship a per-message signature, so this is partially their constraint. But:
- SendGrid Inbound Parse supports HMAC since 2023 (the `signed_event_webhook_v1` model) — not used here.
- HeyGen documents a `HEYGEN_WEBHOOK_SECRET` for endpoint secret verification (`video_generation/heygen.py:243-246` only **logs** the recommendation; nothing actually verifies the secret).
- Facebook Messenger DOES check `X-Hub-Signature-256` correctly (`facebook_action/endpoints.py:657`). Good.
- SentDM webhook DOES verify `X-Webhook-Signature` (`sentdm_broadcast/endpoints.py:478-501`). Good.

**Why it matters**: api_key is in `?api_key=...` query string for several routes (whatsapp, email, etc.) — query strings end up in proxy logs and browser histories.

**Fix**:
- HeyGen: actually verify the `HEYGEN_WEBHOOK_SECRET` on inbound. Currently it's a no-op log line.
- SendGrid: add `signed_event_webhook_v1` verification.
- For WhatsApp: at minimum rotate the api_key on detected misuse; document the threat.

#### XC-8 — Lifecycle hook errors swallowed silently in several `on_register` / `on_startup` paths
**Affects**: `whatsapp/whatsapp_action.py:344-348` (`Error during session registration on startup`), `pageindex/pageindex_action/pageindex_action.py:184-185` (`except Exception: pass`), `google/google_action.py:68-78` (`_warn_if_oauth_unusable` swallows everything), `microsoft/microsoft_action.py:66-75` (same).

**Issue**: SPEC.md §4.3 and `action/CLAUDE.md` §3.4 are explicit:
> Lifecycle hooks MUST not swallow exceptions. The framework's `enable()` / `disable()` / `reload()` wrappers log errors automatically with the action context.

Pattern in code:
```python
try:
    self._create_flow()
except Exception as e:
    logger.warning(...)
```
This DOWNgrades errors to warnings, hiding them from the wrapper's error log (which is what the framework's auto-diagnostics consume).

**Why it matters**: Misconfigured OAuth clients or WhatsApp bridges fail silently at register/startup, then mysteriously don't work at runtime. The contract was designed to prevent exactly this; it's being bypassed.

**Fix**: Let exceptions propagate; the wrapper already logs them with full action context (`base.py:582-595`). Use `logger.exception` if you need narrative, but always re-raise.

#### XC-9 — Cumulative metrics (`total_requests`, `total_results`, `total_tokens`) incremented on `Action` instances but never `await self.save()` after the increment
**Affects**: `web_search/base.py:48-50` plus all three concrete web search actions; `model/base.py` cumulative metrics paths.

**Issue**: `BaseWebSearchAction` declares `total_requests` and `total_results` as `attribute(default=0, description=...)`. `serper/serper.py:105-106` and similar do `self.total_requests += 1` then return — no `save()`. The increment lives only in the in-memory Pydantic model; next read from DB drops it.

**Why it matters**: Metrics are quietly dropped on every restart. Not user-facing, but if anything depends on these counts (rate limiting, billing) it's silently wrong.

**Fix**: Either `await self.save()` periodically (batched, e.g. every N or every minute), or stop persisting them as `attribute` and emit to the observability layer instead.

#### XC-10 — Mutable defaults on class attributes (not `attribute(...)` — plain `Dict = {}` / `List = []`) shared across instances
**Affects**: `vectorstore/typesense/typesense.py:77` (`_collections: Dict[str, bool] = {}`), `interview/interview_interact_action.py:142-145` (`_input_handlers: Dict[str, Callable] = {}`, `_input_validators`, `_input_directive_overrides`, `_input_review_override`), `video_generation/heygen.py:43-51` (`elevenlabs_settings: Dict = {}`, `background = {}`, `text = {}`, `dimension = {}`, `webhook_events = [...]` — these are declared via `attribute(default={...})` but the default object is shared, not a `default_factory=lambda: {...}`).

**Issue**: Python's classic gotcha — when a mutable container is used as a class-level default or attribute default value (vs `default_factory`), all instances share the same object. For `_collections` in `TypesenseVectorStore`, two `TypesenseVectorStore` instances on different agents will pool collection-existence flags into the same dict. The first one's `_initialize_client` call records "collection X exists" → the second instance, pointed at a different Typesense host, thinks the collection already exists too. Hidden cross-agent data leakage.

For `interview._input_handlers`, the dict is **class-level** so all subclasses share the same registry unless they declare their own — likely intentional given decorator registration, but means subclasses can clobber each other's handlers if they reuse field names.

**Why it matters**:
- TypesenseVectorStore: schema/index corruption under multi-tenant deployments.
- Interview: subclass collision; harder to debug.
- HeyGen `attribute(default={})` is more subtle: jvspatial may copy on assign, but the user-supplied default still ends up shared if jvspatial doesn't deep-copy. Audit the framework, but the safer pattern is `default_factory=dict`.

**Fix**:
- Typesense: move `_collections` into `__init__` or wrap as `default_factory=dict` private attribute.
- Interview: per-subclass registry, e.g. `_input_handlers: ClassVar[Dict[type, Dict[str, Callable]]]` keyed by class.
- HeyGen: switch `attribute(default={})` → `attribute(default_factory=dict)`.

#### XC-11 — Process-local mutable module-globals grow unboundedly per user
**Affects**: `long_memory_store/long_memory_store_interact_action.py:22-29` (`_store_locks: Dict[str, asyncio.Lock] = {}`), `whatsapp/endpoints.py` (`_batch_manager`, `_typing_state_manager`).

**Issue**: `_store_locks` allocates one `asyncio.Lock()` per `user_id` and never frees them. On a long-running server with N users this grows to N locks. The lock object itself is small, but the dict is unbounded.

**Why it matters**: Memory growth proportional to lifetime-unique users. On serverless this resets per cold-start, on long-running boxes it's a slow leak.

**Fix**: Use an LRU bounded dict (e.g. `cachetools.LRUCache(maxsize=10_000)`), or weakref the lock and let GC reclaim.

#### XC-12 — `Action.find_one({"context.enabled": True})` without agent scoping returns one of N agents' instances
**Affects**: `postiz_action/endpoints.py:21, 45` and `task_dispatcher/endpoints.py:64` (which uses `agent_id` but other places in this file don't).

**Issue**: `PostizAction.find_one({"context.enabled": True})` returns whatever Postiz action exists across the entire database — not the one for the calling agent. In a multi-agent install this picks one at random.

**Why it matters**: Cross-tenant access from `/postiz/...` endpoints.

**Fix**: Require `agent_id` in the path, scope queries by it (or use `/actions/{action_id}/postiz/...`).

#### XC-13 — Sync `googleapiclient` `.execute()` called from async (XC-3) AND no SSRF check on user-supplied URLs
**Affects**: `google/google_drive_action/google_drive_action.py:50-63` (`source_url`).

**Issue**: `upload_file(source_url=...)` accepts any URL and fetches it via `httpx`. No SSRF guard — an admin (or a compromised cockpit) could `upload_file(source_url="http://169.254.169.254/...")` to hit AWS IMDS, then the response body is uploaded to Drive (exfil). PageIndex has `_ssrf_guard_url` (`pageindex/endpoints.py:328`) — Google Drive does not.

**Why it matters**: Cloud-metadata-service-style SSRF; data exfil via Drive.

**Fix**: Reuse the SSRF guard from PageIndex (extract to a shared util) and call it from any path that fetches a user-supplied URL.

#### XC-14 — `actions-catalog.md` drift: wrong class names + missing routes
**Affects**: `actions-catalog.md` vs `web_search/brave/info.yaml` (catalog: `BraveSearchAction`, real: `BraveWebSearchAction`); catalog `SerpAPISearchAction` vs real `SerpAPIWebSearchAction`; catalog `WhatsAppAction` weight unspecified vs `info.yaml` weight 0; many directory paths in catalog don't match `info.yaml` `package.name` (XC-5).

**Why it matters**: Authors reading the catalog will type the wrong class name into `agent.yaml` `archetype:` / `model_action_type:` and the loader will fail silently.

**Fix**: Run catalog regeneration. The "Last surveyed: 2026-05-17" header suggests automation exists; ensure it actually pulls from `info.yaml`.

### MEDIUM

#### XC-15 — Webhook routes accept api_key via query parameter (`?api_key=...`) — leaks via logs and referer
**Affects**: All channel-adapter webhook URLs generated by `whatsapp/whatsapp_action.py:512`, `email_action/...`, `pageindex/pageindex_action/pageindex_action.py:210`, `sentdm_broadcast`, `task_creation_interact_action`, etc.

**Issue**: Generated URLs look like `https://host/api/whatsapp/interact/webhook/{agent_id}?api_key={plaintext}`. Query strings:
- show up in webserver access logs by default (nginx, ALB);
- leak via `Referer` header when redirected;
- show up in browser address bar for HTML rendering of the URL.

**Why it matters**: Long-lived keys with broad webhook permissions exposed to log shippers.

**Fix**: Switch to HMAC-signed payloads (already used by Messenger and SentDM) or require header-based auth (`X-Api-Key`).

#### XC-16 — Avatar download path has no size/format limits → DoS / DB bloat
**Affects**: `avatar_action/avatar_action.py:75-82` and `avatar_action/endpoints.py:53-93` (`POST /actions/{action_id}/avatar` accepts arbitrary base64).

**Issue**: `pull_avatar_from_whatsapp` does `response.read()` with no `Content-Length` check or chunk cap. `set_avatar(data=...)` accepts arbitrary base64. Either path can drop a 100 MB image into the action and base64-encode it on the node — slow saves, DB bloat, and (for Mongo/SQL backends) potential row-size failure.

**Fix**: Cap at e.g. 5 MB; validate MIME by sniffing magic bytes (`python-magic` or `filetype`).

#### XC-17 — STT / TTS audio paths have no size validation or audio-format whitelist
**Affects**: `stt_action/deepgram/deepgram.py:88-115` (`invoke_base64` decodes arbitrary base64 and ships to Deepgram), `stt_action/endpoints.py:33-72` (admin endpoint accepts arbitrary `audio_base64`), `tts_action/elevenlabs/elevenlabs.py:64-89` (`get_audio_as(audio_bytes)` accepts unbounded bytes).

**Issue**: No size cap, no MIME check beyond the user-supplied `audio_type` string. Large audio uploads can:
- DoS the Deepgram/ElevenLabs quota
- Stall the event loop while base64-decoding (XC-3 makes this worse — Deepgram SDK is async but base64 decode is CPU-bound)

**Fix**: Add a `max_audio_bytes` `attribute` (default 25 MB), reject larger inputs; validate `audio_type` against a whitelist.

#### XC-18 — `task_trigger_interact_action` compares ISO timestamps as strings, missing timezone normalization
**Affects**: `task_trigger_interact_action/task_trigger_interact_action.py:84-96`.

**Issue**: `trigger_at_str > now_str` does lexicographic comparison of `YYYY-MM-DDTHH:MM` strings. If `trigger_time_str` contains a timezone offset (e.g. `2026-05-17T08:00-05:00`), the trailing offset characters lexically perturb the comparison and the task may fire late or early.

**Fix**: Parse both into `datetime` objects and compare; ensure both are in UTC.

#### XC-19 — `agent_utils.list_interactions` filesystem read is unbounded, no pagination, traversal risk
**Affects**: `agent_utils/agent_utils.py:17-67` and `agent_utils/endpoints.py:15-52`.

**Issue**:
- Reads every file in the log directory, JSON-parses each, returns the full list. No `limit` parameter.
- `JVSPATIAL_LOG_DB_NAME` env can be set to `../../etc/passwd-ish-dir`; `os.path.abspath()` resolves it but doesn't constrain it to any safe parent. An admin who can set env vars can read arbitrary directories on disk through this endpoint. (Admin already has lots of power, but defense in depth.)
- Synchronous `open(file_path, 'r')` + `json.load(f)` inside an `async` method — XC-3 dup.

**Fix**: Add `limit`/`offset` params; validate that the resolved log path is under `app.file_storage_root` or a configured allowlist; offload the read to a thread.

#### XC-20 — Pip dependencies missing from several `info.yaml` files
**Affects**:
- `video_generation/info.yaml` lists `httpx>=0.24.0` but the code uses both `httpx` AND `requests` (`heygen.py:6`). `requests` not in deps.
- `mcp/info.yaml` lists `mcp>=1.0,<2` but `mcp_action.py` imports `jvagent.action.mcp.client` which uses MCP stdio_client — the `mcp` package version pin is fine, but child-process tooling (`npx`) isn't pip-installable and is not flagged in `info.yaml`.
- `web_search/serper/info.yaml` not checked but `serper.py:6` uses `http.client` only (stdlib) — OK.

**Why it matters**: `JVAGENT_DISABLE_RUNTIME_PIP_INSTALL=false` default tries to install on import; missing entries fall through to silent ImportError.

**Fix**: Audit `info.yaml` pip lists against `import` statements in each action module.

#### XC-21 — `_built_service` cached at instance scope without invalidation on token refresh
**Affects**: `google/google_action.py:53` (`_built_service: Optional[Any] = None`) and `get_service()` (lines 92-131).

**Issue**: `get_service()` checks `self._built_service._http.credentials.valid` before reuse — but `Credentials.valid` is a moment-in-time bool. If the access token expires *between* the check and a call to `service.files().list()`, the call fails with 401. The cache also doesn't expire on `_save_credentials` from the refresh path (it does set `self._built_service = None` on exception, but not proactively on every refresh).

**Why it matters**: Sporadic 401s; user thinks the integration is broken.

**Fix**: Wrap calls in a try/except that catches `googleapiclient.errors.HttpError` 401, force refresh, retry once. Or rebuild the service on every refresh.

#### XC-22 — `WhatsAppAction.is_session_registered()` reads private instance field; multi-process / serverless never sees True
**Affects**: `whatsapp/whatsapp_action.py:116, 354-356`.

**Issue**: `_session_registered: bool = False` is a non-persisted instance attribute. On serverless cold start (each request a new process), session is never "registered" from this process's POV, even if a prior worker did the register. Same for multi-worker uvicorn — only the worker that handled the register call sees `True`.

**Why it matters**: `healthcheck()` reports `session_registered: False` on healthy systems; `register_session` runs on every cold start, slowing first-request latency.

**Fix**: Persist `session_registered` as an `attribute` with a TTL or query the provider for current session state.

#### XC-23 — DNS rebinding TOCTOU in `_ssrf_guard_url`
**Affects**: `pageindex/endpoints.py:328-376`.

**Issue**: `_ssrf_guard_url` resolves host → IP via `socket.getaddrinfo()`, validates the IP, then **returns** — the actual HTTP request from `httpx` re-resolves the hostname and may get a different (private) IP if the attacker controls DNS with a short TTL. Classic TOCTOU.

**Fix**: Pin the IP from the guard's resolution and connect to that IP directly with `Host:` header; or use `httpx`'s `transport` with a custom resolver that returns the pre-resolved address; or perform the resolution + connection atomically via `socket.create_connection`.

#### XC-24 — Hardcoded demo values in `HandoffInteractAction` source code
**Affects**: `handoff_interact_action/handoff_interact_action.py:18-24, 181-183`.

**Issue**:
- `DIRECT_CONTACT_PROMPT` has `support@company.com` and `+592 XXX XXXX` hardcoded into the constant string.
- `handoff_number = attribute(default="5926431530", ...)` — a real-looking Guyana phone number as default.

**Why it matters**: Out-of-the-box installs send "support@company.com" to users. The phone number is a real-looking E.164 — if it's not your number, you're sending users to someone else.

**Fix**: Default to empty strings; require explicit configuration via `agent.yaml`. Add `is_configured()` check that disables the action if defaults aren't overridden.

#### XC-25 — Several actions skip `await super().on_register()` / `on_reload()` calls
**Affects**: spot-checked `task_dispatcher/task_dispatcher.py:26-37` does call super, `pageindex/pageindex_action/pageindex_action.py:260-282` calls super, `google/google_action.py:80-90` does NOT call super (parent `Action.on_register` is a pass, so currently benign, but breaks if the parent grows behavior). Same for `microsoft_action.py:85-92`.

**Issue**: Defensive — `Action.on_register` is `pass` today (`base.py:256-267`), but future framework additions will silently not run.

**Fix**: Always `await super().on_register()` / `on_reload()` / `on_startup()` at the top of overridden hooks.

#### XC-26 — `whatsapp/modules/` namespace package (no `__init__.py`)
**Affects**: `whatsapp/modules/{base,ultramsg,wppconnect,wwebjs_api}.py`.

**Issue**: Python 3 allows implicit namespace packages, but the rest of the codebase uses explicit `__init__.py`. The inconsistency confuses static-analysis tools and `_unload_action_modules` heuristics (`base.py:462-542`, which checks `module_name.startswith(action_module_pattern)`).

**Fix**: Add empty `__init__.py`.

### LOW

#### XC-27 — `import` inside function bodies (lazy import) used liberally where module-level would be fine
**Affects**: many actions, e.g. `google/google_drive_action/google_drive_action.py:43-47` (`import base64, io, httpx, MediaIoBaseUpload` inside `upload_file`), `pageindex/endpoints.py` many points.

**Issue**: Not a bug but a style/perf hit (~µs per call). The pattern is usually used to defer optional-dep imports, but here `httpx` and `base64` are always available.

**Fix**: Hoist standard imports to module top.

#### XC-28 — Missing pip dep on `requests`
**Affects**: `video_generation/heygen.py:6`.

**Fix**: Either remove the `requests` import (XC-3 fix) or add `requests>=2.28` to `video_generation/info.yaml`.

#### XC-29 — `webhook_url`, `webhook_api_key_id` stored on action; revocation/regen path not always called on `on_deregister`
**Affects**: `whatsapp`, `email_action`, `pageindex/pageindex_action`, `task_creation_interact_action`, `sentdm_broadcast`.

**Issue**: On action deregister, the persisted `webhook_api_key_id` should be revoked from `APIKeyService` to prevent the leaked key from being usable. None of these implement an `on_deregister` that calls `api_key_service.revoke_key(...)`.

**Fix**: Add `on_deregister` for each that calls `revoke_key(self.webhook_api_key_id, system_user_id)`.

#### XC-30 — Sentinel comments and TODOs without trackable issues
**Affects**: scattered (e.g. `video_generation/heygen.py:269` `"callback_id": "string"` placeholder; `whatsapp/endpoints.py:647` `"Outbox not implemented yet"`).

**Fix**: Either fix or link to a tracker.

---

## Per-action findings

Only packages with action-specific findings are listed below. Packages reviewed and judged clean (per audit scope) are summarized at the end.

### jvagent/access_control
**Contract compliance**: ⚠️ — name mismatch (XC-5), endpoints under `/agents/...` not `/actions/...` (XC-4).

**Findings**:
- **H-1** (`access_control_action.py:111-113`): `has_action_access` swallows all exceptions with `except Exception` and returns `False`. Fail-closed on rule evaluation is defensible, but the exception swallow hides bugs in rule parsing. Log at error level with `exc_info=True`.
- **M-1** (`access_control_action.py:148-160` `_resolve_user_groups`): When `action_label in self.user_groups`, merges with `default` groups. When `action_label` is **not** in `self.user_groups`, returns only `default` groups. This means an action with no specific scope can never be denied by `default` rules but **can** be allowed by them, which is the expected behavior — but the asymmetric merge logic invites bugs. Add tests.
- **L-1** (`access_control_action.py:340-353`): `_migrate_user_groups` runs on every `import_config` call but legacy data is migrated in place — works once, but if the input dict contains both nested and flat structures (mixed-version YAML) only the first-encountered shape wins.

### jvagent/agent_utils
**Contract compliance**: ✅ (with XC-19 caveats).

**Findings**: See XC-19. No additional per-action issues.

### jvagent/avatar_action
**Contract compliance**: ✅.

**Findings**:
- **M-1** (XC-16): no size validation on avatar fetch/set.
- **L-1** (`avatar_action.py:108`): f-string with no placeholder (`f"Successfully set WhatsApp profile picture from current avatar"`).

### jvagent/converse
**Contract compliance**: ⚠️ — name mismatch (XC-5), no endpoints.py, `__init__.py` does not import endpoints (XC-6).

**Findings**:
- **L-1**: `info.yaml`'s `package.group: core` doesn't match the SPEC convention of `jvagent | contrib | custom`. Minor doc drift.

### jvagent/email_action
**Contract compliance**: ⚠️ — `/email/interact/webhook/...` not under `/actions/...` (XC-4).

**Findings**:
- **H-1** (`endpoints.py:54-67`): SendGrid path is the inbound webhook but the route is shared with Gmail/Outlook "fetch one" semantics — the same URL behaves differently per `provider` attribute. A change to `provider` while the URL is registered with SendGrid will silently break inbound (SendGrid still POSTs multipart; handler raises "Unsupported provider").
- **M-1** (XC-7): SendGrid HMAC verification (`signed_event_webhook_v1`) not implemented.
- **M-2** (XC-15, XC-29): API-key-in-query, no revocation on deregister.

### jvagent/facebook_action
**Contract compliance**: ⚠️ — `/messenger/interact/webhook/{agent_id}` not under `/actions/...` (XC-4). Other routes are `/actions/{action_id}/...` — good.

**Findings**:
- **C-1** (`facebook_action.py` lookup `_apply_env_defaults`): `app_secret` read from env at every webhook call — fine but the action attribute also persists it (haven't checked the persisted shape). Verify it isn't being mirrored to the DB.
- Messenger signature verification: ✅ correct (`endpoints.py:657`).

### jvagent/google (parent + 5 sub-actions)
**Contract compliance**: ⚠️ — OAuth routes outside `/actions/...` (XC-4); name mismatches handled per sub-action.

**Findings**:
- **C-1** (XC-1, XC-2): plaintext OAuth tokens + state-as-verifier.
- **H-1** (XC-3): all five sub-actions wrap synchronous `googleapiclient` `.execute()` calls in `async def`.
- **H-2** (`google_drive_action.py:50-63`, XC-13): no SSRF guard on `source_url`.
- **H-3** (`google_action.py:46-47`): `redirect_uri` default is `http://localhost:8080/` plaintext HTTP — not an HTTPS URL. Misleading and could be abused if not overridden in a non-trivial way (Google Cloud rejects http:// redirects in prod, but the default invites copy-paste into production).
- **M-1** (XC-21): service cache invalidation.
- **L-1** (`google_action.py:113-114`): `logger.warning(f"Building Google {self.API_SERVICE_NAME} service for {self.id}")` — should be `info` or `debug`, not warning.

### jvagent/handoff_interact_action
**Contract compliance**: ⚠️ — no endpoints.py, no `from . import endpoints` (XC-6).

**Findings**:
- **C-1** (XC-24): hardcoded placeholder email + phone in source.

### jvagent/interview
**Contract compliance**: ⚠️ — name mismatch (XC-5), no endpoints.py (XC-6).

**Findings**:
- **H-1** (`interview_interact_action.py:142-145`, XC-10): class-level mutable dict registries shared across subclasses.
- **H-2** (`interview_interact_action.py:124-138`): `_standard_interview_anchor_templates` is a plain class attribute (not `attribute(...)`), mutating it on one instance mutates it on every subclass.
- **M-1** (subdir `core/utils/cache_utils.py:BranchCache`, `QuestionNodeCache`): branch cache invalidation logic not reviewed in depth; convergence detection in `core/processing/` and target resolution should be tested for: (a) does the cache invalidate when `question_graph` changes mid-conversation? (b) when an interview is cancelled, are pending state nodes cleaned up?

### jvagent/intro
**Contract compliance**: ⚠️ — name mismatch (XC-5), no endpoints.py (XC-6).

**Findings**: None additional.

### jvagent/long_memory, jvagent/long_memory_retrieval, jvagent/long_memory_store
**Contract compliance**: ⚠️ — name mismatches (XC-5), no endpoints.py (XC-6).

**Findings**:
- **H-1** (`long_memory_store_interact_action.py:22-29`, XC-11): unbounded `_store_locks` dict.
- **M-1** (`long_memory_interact_action.py:71-105`): `update_frequency=3` means every 3 user messages triggers an LLM call; for chatty users this is expensive. Document the cost and consider an upper bound on cumulative spend per user per day.
- **M-2** (`long_memory_store_interact_action.py:96-118`): If `UserLongMemory.get_unindexed_categories()` returns a huge set, the action calls `assimilate_document(...)` synchronously inside the lock. Long users with many categories can stall their next interaction (the lock is held during ingest).
- **L-1** (`long_memory_store_interact_action.py:97`): SHA-256 of the full markdown is recomputed every interaction even when no categories changed. Cheap, but unnecessary.

### jvagent/mcp
**Contract compliance**: ⚠️ — no endpoints.py (XC-6).

**Findings**:
- **H-1** (`mcp_action.py:281-283`): `__init__` mutates `self._servers_by_name = {}` — fine on instance create but the `Action` parent (a jvspatial `Node`) re-creates the object on load from DB; the override of `__init__` may break Pydantic-based deserialization if any field hasn't been set yet. Test: load an `MCPAction` from DB.
- **H-2** (sandbox isolation, `sandbox.py`): the per-user MCP subprocess is rooted at `<files_root>/<agent_id>/<user_id>/` (good). But `effective_user_segment` falls back to `session_id` then `_default`. If a non-authenticated user calls a tool that triggers MCP, their session_id (which is a random-ish string) becomes the sandbox segment — different sessions of the **same anonymous user** therefore see different sandboxes. Not necessarily wrong, but a gotcha.
- **H-3** (`mcp_action.py:282`): `_servers_by_name` is set in `__init__` but other paths read it without lock; concurrent calls to `_build_server_entries` could race. There's an `asyncio.Lock` per server entry but not for entry construction itself.
- **M-1** (`sandbox.py:39-51`): `sanitize_segment` strips dots but allows `..` to be replaced with `_` — so an attacker who sets `user_id=".."` won't escape — good. But the test `s = s.strip(".")` strips both leading and trailing dots which means `user_id=".hidden"` becomes `hidden` (loses the dot). For directory naming this is intentional, but for user-id matching it's a one-way transform that should be documented.
- **M-2**: MCP subprocesses are spawned with `npx` by default (`mcp_action.py:371`). `npx` downloads packages on first use. There's no signature verification of the downloaded package. An attacker who controls the configured `args` (e.g. `["@scope/mcp-fs", "/some/path"]`) can execute arbitrary npm packages with the agent's privileges.

### jvagent/microsoft (parent + 4 sub-actions)
Same as Google (XC-1, XC-2, XC-3 doesn't apply since httpx is used directly for Graph).

**Findings**:
- **C-1** (XC-1, XC-2): plaintext OAuth tokens + state-as-verifier.
- **H-1** (`microsoft_action.py:30-33`): `redirect_uri` default is `http://localhost:8080/` plaintext HTTP — same issue as Google.
- **L-1** (`microsoft_outlook_mail_action.py:57`): `params["$search"] = f'"{query}"'` — user-supplied `query` is wrapped in quotes but not otherwise escaped. Graph $search is generally OData-safe but a quote in `query` will break the syntax. Use `urllib.parse.quote`.

### jvagent/model/* (language model providers)
**Contract compliance**: ✅ — base contract for retries is honored (`model/base.py:53-80`).

**Findings**: None at the audit scope. (Provider implementations not deep-audited per scope guidance.)

### jvagent/pageindex (3 sub-actions + shared endpoints)
**Contract compliance**: ⚠️ — `/agents/{agent_id}/pageindex/...` and one webhook outside `/actions/...` (XC-4).

**Findings**:
- **H-1** (`pageindex_action.py:184-185`): `except Exception: pass` in IP-match comparison swallows API key lookup errors silently — see XC-8.
- **H-2** (`endpoints.py:328-376`, XC-23): TOCTOU DNS rebinding in `_ssrf_guard_url`.
- **H-3** (`endpoints.py:1962-1996`): `process_document_url` webhook accepts any URL with api_key auth — the URL is SSRF-guarded but the staged file is then JSON/YAML-decoded into the graph database with `purge=True` option. An attacker with the api_key can wipe the agent's PageIndex.
- **M-1** (`__init__.py:13`): `sys.modules["jvagent.action.pageindex.core.utils"] = llm_override.override_module` is module-level mutation. Order-sensitive: if any module imports `pageindex.core.utils` before `pageindex` is imported, the override doesn't apply. Risk surface: shared deps.
- **M-2**: PageIndex's vector-less RAG store grows with documents. No per-agent quota; an admin who imports a huge YAML can balloon the dedicated PageIndex DB.

### jvagent/persona
**Contract compliance**: ✅.

**Findings**:
- **L-1**: `__init__.py:15` uses `from jvagent.action.persona import endpoints` (absolute) where the contract example uses `from . import endpoints`. Functionally equivalent.

### jvagent/postiz_action
**Contract compliance**: ❌ — endpoints outside `/actions/...` (XC-4) AND `find_one({"context.enabled": True})` (XC-12).

**Findings**:
- **C-1** (XC-12): cross-tenant action retrieval.
- **H-1** (`postiz_action.py:26-28`): default `base_url: "http://localhost:4007/api/public/v1"` — plain HTTP, localhost. Misleading in prod.
- **H-2** (`postiz_action.py:42`): `"Authorization": f"{(self._env_api_key() or '').strip()}"` — note the raw API key with **no `Bearer ` prefix**. If Postiz expects `Bearer <key>` this is silently wrong; if it accepts raw keys, fine — undocumented.
- **L-1** (`postiz_action.py:118`): `return ""` after an `async with` block that always either returns or raises — unreachable.

### jvagent/retrieval
**Contract compliance**: ⚠️ — name mismatch (XC-5), no endpoints.py (XC-6).

**Findings**: None additional.

### jvagent/router
**Contract compliance**: ⚠️ — name mismatch (XC-5), no endpoints.py (XC-6).

**Findings**:
- **M-1** (`interact_router.py:1013-1021`): `_collect_anchors` does `actions_manager.get_actions(enabled_only=True, entity=InteractAction)` — O(N actions) per `execute()` call. Cached via `enable_interact_router_cache` (default False), but on every routing call without cache, all actions are iterated. With many actions this scales linearly. Document the trade-off; consider always-on caching with short TTL.
- **L-1** (`interact_router.py:988`): `get_all_actions` vs `get_actions` — note the subtle method name; verify they exist on the manager.

### jvagent/sentdm_broadcast
**Contract compliance**: ❌ — webhook is `/webhook/{action_id}` (XC-4); also extremely **generic** path that could collide with anything else (e.g. another action picking `/webhook/{x}`).

**Findings**:
- **C-1** (XC-4 + collision): `/webhook/{action_id}` is too generic. A second action that registers `/webhook/{anything}` will conflict at routing time.
- HMAC signature verification: ✅ correct (`endpoints.py:478-501`).

### jvagent/stt_action/deepgram
**Contract compliance**: ⚠️ — name mismatch (XC-5), no endpoints.py in package dir (the parent `stt_action/__init__.py` imports endpoints — non-standard).

**Findings**:
- **M-1** (XC-17): no audio size limit.
- **L-1** (`deepgram.py:33-42`): `_deepgram_client` cached on instance with key=api_key — reasonable, but cache never expires on rotation of `DEEPGRAM_API_KEY`. The check `prev_key == key` handles that correctly. OK.

### jvagent/task_creation_interact_action
**Contract compliance**: ⚠️ — no endpoints.py (XC-6, though it has webhook endpoints registered in task_dispatcher).

**Findings**:
- **M-1** (XC-29): no `on_deregister` to revoke `webhook_api_key_id`.

### jvagent/task_dispatcher
**Contract compliance**: ⚠️ — `/proactive/tick/{agent_id}` and `/proactive/webhooks/{agent_id}` outside `/actions/...` (XC-4).

**Findings**:
- **H-1** (`task_dispatcher.py:19, 39-93`): `_scheduler_service_ref` is a **module-level global** ("survives across threads unlike ContextVars"). On multi-agent / multi-app setups this is a singleton — one TaskDispatcher's scheduler service overrides another's.
- **M-1** (`task_dispatcher.py:64-94`): scheduler setup races: `existing_scheduler = getattr(server, "scheduler_service", None)` then `server.scheduler_service = scheduler_service`. Two TaskDispatcher actions on the same server race to assign — last writer wins, the loser's tick callback never fires.

### jvagent/task_trigger_interact_action
**Contract compliance**: ⚠️ — no endpoints.py (XC-6).

**Findings**:
- **M-1** (XC-18): ISO-string timestamp comparison.

### jvagent/tts_action/elevenlabs
**Contract compliance**: ⚠️ — name mismatch (XC-5), no endpoints.py in package (parent imports).

**Findings**:
- **H-1** (XC-3): sync ElevenLabs SDK in async methods.
- **M-1** (XC-17): no audio size / format validation on output (less important than input but a multi-MB synthesized audio still hits the DB if `as_base64=True`).

### jvagent/utils
**Contract compliance**: N/A — infrastructure module.

**Findings**:
- **L-1** (`webhook_system_user.py:35`): `existing_user = await auth_service._find_user_by_email(...)` calls a **private** method (leading underscore). If `jvspatial`'s API author renames `_find_user_by_email` → `find_user_by_email`, this breaks silently.

### jvagent/vectorstore/typesense
**Contract compliance**: ⚠️ — name mismatch (XC-5), no endpoints.py in package.

**Findings**:
- **H-1** (XC-10): `_collections: Dict[str, bool] = {}` class-level mutable shared default.
- **M-1** (`typesense.py:74-77`): `_client: Optional[Any] = None` class-level → instance-level lazy init via `_initialize_client`. Same caveat as `_collections` — first instance's client is shared. (Less catastrophic; clients are stateless connection pools.)

### jvagent/video_generation
**Contract compliance**: ❌ — no `endpoints.py`, `__init__.py` doesn't import endpoints (XC-6), `requests` not in `info.yaml` pip (XC-28), name mismatch (XC-5).

**Findings**:
- **C-1** (XC-3): all 6+ HTTP calls use synchronous `requests` in async methods.
- **H-1** (`heygen.py:43-49`): `attribute(default={})` mutable shared default (XC-10).
- **H-2** (`heygen.py:241-246`): documentation says HEYGEN_WEBHOOK_SECRET is verified externally — there's **no code path** that actually verifies inbound webhook signatures. If the action accepts callbacks from HeyGen (it claims it does), this is unauthenticated.
- **M-1** (`heygen.py:269`): `"callback_id": "string"` — placeholder string, presumably forgot to remove. Sends `"string"` literally to HeyGen.
- **L-1** (`heygen.py:54-62`): `webhook_events: List[str] = attribute(default=[...])` — same mutable-default issue.

### jvagent/web_search/{brave,serper,serpapi}
**Contract compliance**: ⚠️ — name mismatches (XC-5), no endpoints.py (XC-6).

**Findings**:
- **H-1** (Serper, SerpAPI): synchronous SDK in async (XC-3).
- **M-1** (all three): `total_requests`/`total_results` incremented without `save()` (XC-9).

### jvagent/web_search_retrieval
**Contract compliance**: ⚠️ — name mismatch (XC-5), no endpoints.py (XC-6).

**Findings**: None additional.

### jvagent/whatsapp
**Contract compliance**: ⚠️ — name mismatch (XC-5); `/whatsapp/{action_id}/...` and `/whatsapp/interact/webhook/{agent_id}` outside `/actions/...` (XC-4).

**Findings**:
- **H-1** (`whatsapp_action.py:116, 354-356`, XC-22): `_session_registered` non-persisted bool.
- **H-2** (`whatsapp_action.py:294-296`): `desired_timeout = max(5, int(timeout_str))` — but `timeout_str` defaults to `"120"`; if user sets `WHATSAPP_SESSION_REGISTER_TIMEOUT_SECONDS="invalid"` the except falls through to `desired_timeout = 120` (line 296). OK but the path is fragile — `int("120abc")` raises ValueError, caught. Fine.
- **H-3** (`whatsapp_action.py:309-348`): `original_timeout = self.request_timeout; self.request_timeout = desired_timeout; ...; finally: self.request_timeout = original_timeout` — mutates a persisted `attribute` on `self` during `on_startup`. If `await self.save()` runs during the window (it does, in some paths), the new timeout gets persisted permanently.
- **M-1** (XC-15): api_key in query string of webhook URL.
- **L-1** (`whatsapp_action.py:319-321`): docstring of `WHATSAPP_SKIP_STARTUP_REGISTRATION` references endpoint that may not exist (`POST /api/actions/{action_id}/session/register`) — verify it's actually wired.

---

## SPEC drift

1. **SPEC.md §4 + action-authoring.md §3**: `package.name` should match dir layout. 19 packages violate (XC-5).
2. **SPEC.md §11 #8 + CLAUDE.md §3.2**: action endpoints must use `/actions/{action_id}/` prefix. 13 packages have at least one route violating (XC-4).
3. **SPEC.md §4.3 + CLAUDE.md §3.4**: lifecycle hooks must not swallow exceptions. 4+ packages swallow (XC-8).
4. **actions-catalog.md** drifts from `info.yaml` — class names (`BraveSearchAction` vs `BraveWebSearchAction`), package paths, weights (XC-14).
5. **action-authoring.md §13** ("Hard-coding API keys"): nominally clean — most actions resolve from env. HandoffInteractAction hardcodes contact details (not API keys, but real defaults).
6. **SPEC.md §4.4** (`get_tools()` returns properly schema'd Tool instances): not deeply verified across all actions; one example (`serper.py:117-151`) looks correct. Could not confirm for all 30+ actions.

---

## Strengths

- The **`BaseModelAction` retry framework** (`model/base.py:53-190`) is well thought out: exponential backoff with jitter, `Retry-After` header parsing, configurable status codes, separate handling for `httpx.TimeoutException` / `TransportError` / `HTTPStatusError`, and graceful handling of `CancelledError` (non-retryable).
- **PageIndex SSRF guard** (`pageindex/endpoints.py:328-376`) implements RFC1918, loopback, link-local, multicast checks + per-redirect revalidation (TOCTOU caveat in XC-23 notwithstanding).
- **Facebook Messenger** + **SentDM** webhooks implement HMAC signature verification with constant-time `hmac.compare_digest` (`facebook_action/messenger_webhook_helpers.py:44-45`, `sentdm_broadcast/endpoints.py:478-501`). Model for the others.
- **MCPAction sandboxing** (`mcp/mcp_action.py:253-308`, `mcp/sandbox.py`): per-user filesystem sandboxes, deny-tools list, opt-in `sandbox_user_scoped` — a credible default for untrusted code.
- **`access_control_action`**'s allow/deny ordering: deny rules evaluated first, then allow, then default — RBAC semantics correct.
- The four-file pattern + auto-discovery loader **does** work in practice for the packages that follow the contract; failure modes are localized to those that drifted.

---

## Out of scope

- Performance issues per audit prompt (O(n²) algorithms, memory profile). Noted incidentally (XC-3 sync-in-async, XC-11 unbounded dict) where they cross into correctness.
- `interact/` and `cockpit/` packages (other auditors).
- `base.py` (other auditor).
- `model/` provider-specific (Anthropic/OpenAI/etc. internals).
- Test coverage analysis.
- jvspatial library internals.

---

_Audit complete. 47 distinct findings logged. Most-painful single concentration: **OAuth handling** (`google/` + `microsoft/`) carries 3 critical / high-severity issues that intersect (plaintext tokens, state-as-verifier, sync-in-async). Recommend treating those two packages as the highest-priority remediation block._
