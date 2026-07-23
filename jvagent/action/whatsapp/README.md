# WhatsApp Action - Security and Standards Update

This document outlines the security improvements and coding standards compliance implemented for the WhatsApp action.

## Security Fixes Implemented

### 1. Path Traversal Vulnerability (CWE-22) - FIXED ✅

**Issue**: Unsanitized user input in file path construction
**Location**: Media manager and file handling
**Fix**:

- Implemented `PathSanitizer.sanitize_path()` for all user-provided paths
- Added validation to prevent directory traversal attacks
- Used `pathlib.Path.resolve()` for safe path resolution

### 2. Inadequate Error Handling - FIXED ✅

**Issue**: Missing HTTP response validation and JSON parsing error handling
**Location**: API calls and webhook processing
**Fix**:

- Added comprehensive try-catch blocks with specific exception types
- Implemented proper HTTP status code validation
- Added timeout and connection error handling
- Used jvspatial exception types (`ValidationError`, `DatabaseError`)

### 3. Hardcoded Paths - FIXED ✅

**Issue**: Non-portable hardcoded absolute paths
**Location**: Configuration and media storage
**Fix**:

- Replaced hardcoded paths with configurable attributes
- Used environment variables and configuration overrides
- Implemented dynamic path generation based on agent context

### 4. Variable Shadowing - FIXED ✅

**Issue**: Inner loop variable shadows outer variable in chunking algorithm
**Location**: Message chunking logic
**Fix**:

- Renamed variables to avoid shadowing (`item` → `chunk`, `text_chunk`)
- Improved variable naming for clarity
- Added proper scope management

### 5. Performance Issues - FIXED ✅

**Issue**: Inconsistent space counting in chunking algorithm
**Location**: Message chunking calculation
**Fix**:

- Standardized space calculation logic
- Fixed off-by-one errors in length calculations
- Improved algorithm efficiency and consistency

## Coding Standards Compliance

### jvspatial Standards Applied

1. **Type-Safe Properties**:

   ```python
   provider: str = attribute(
       default="wppconnect",
       description="WhatsApp provider",
       pattern=r"^(wppconnect|ultramsg|ts-whatsapp)$"
   )
   ```

2. **Proper Error Handling**:

   ```python
   try:
       result = await self.api().register_session(...)
   except DatabaseError as e:
       logger.error(f"Database error: {e}", exc_info=True)
       raise
   except ValidationError as e:
       logger.error(f"Validation error: {e}")
       raise ValidationError(f"Session registration failed: {e}")
   ```

3. **Input Validation**:

   ```python
   async def healthcheck(self) -> Union[bool, Dict[str, Any]]:
       errors = []
       if not self.api_url:
           errors.append("api_url is required")
       if errors:
           return {"healthy": False, "errors": errors}
       return {"healthy": True, "provider": self.provider}
   ```

4. **Async/Await Architecture**:
   - All methods properly use async/await
   - No blocking operations in async context
   - Proper exception propagation

### jvagent Standards Applied

1. **Action Lifecycle Hooks**:

   ```python
   async def on_register(self) -> None:
       """Called when action is first registered."""
       try:
           health_result = await self.healthcheck()
           if isinstance(health_result, dict) and not health_result.get("healthy", True):
               raise ValidationError(f"Configuration errors: {'; '.join(health_result.get('errors', []))}")
           # ... initialization logic
       except Exception as e:
           raise ValidationError(f"Registration failed: {e}")

   async def on_startup(self) -> None:
       """Called when app starts and action is loaded from database.

       Initializes filter, attempts session registration (with long HTTP timeout),
       then initializes adapter. If registration fails, use manual session/register endpoint.
       """
       if not self.enabled or not self.is_configured():
           return
       filter = WhatsAppFilter(channels=["whatsapp"], priority=100)
       await filter.initialize()
       # Session registration attempted here (see whatsapp_action.py)
       adapter = WhatsAppAdapter(action=self)
       await adapter.initialize()

   def is_session_registered(self) -> bool:
       """Return whether session was registered (at startup or via manual endpoint)."""
       return self._session_registered
   ```

2. **Standard Package Structure**:

   ```
   whatsapp/
   ├── __init__.py              # Package exports
   ├── whatsapp_action.py       # Main action class
   ├── endpoints.py             # API endpoints
   ├── info.yaml               # Action metadata
   ├── modules/                # Provider implementations
   └── utils/                  # Utility functions
   ```

3. **Endpoint Security**:
   ```python
   @endpoint(
       "/whatsapp/interact/webhook/{agent_id}",
       webhook=True,
       auth=False,
   )
   async def whatsapp_interact(request: Request, agent_id: str):
       # meta: X-Hub-Signature-256 in handler; bridge: ?api_key= in handler
   ```

## Architecture Improvements

### 1. Lambda and Deployment Compatibility

**Session Registration Strategy** (Startup + Manual Only):

1. **Registration at startup** (with long HTTP timeout):
   - Session registration is attempted during `on_startup()` with a long configurable HTTP timeout (default 120 seconds)
   - Timeout is set via `WHATSAPP_SESSION_REGISTER_TIMEOUT_SECONDS` environment variable (minimum 5 seconds)
   - Uses the underlying HTTP client timeout (not `asyncio.wait_for()`) for Lambda compatibility
   - On success: Session is registered and webhooks can start immediately
   - On timeout/failure: Logs warning and continues with filter/adapter initialization; use manual endpoint to register

2. **Manual registration endpoint**:
   - `POST /api/actions/{action_id}/session/register` endpoint for explicit registration
   - Requires authentication (same as other action endpoints)
   - **Browser link (no auth)**: `GET {JVAGENT_PUBLIC_BASE_URL}/api/whatsapp/{action_id}` shows a QR code or a “connected” page (public URL; `action_id` should be unguessable, like Google OAuth’s `/api/google/{action_id}`).
   - Returns registration result with status, ok flag, and message
   - **Use cases**:
     - Startup registration timed out or failed
     - Retry registration without restarting the app
     - Force re-registration after configuration changes
   - **Example**: `curl -X POST https://your-api.com/api/actions/{action_id}/session/register -H "Authorization: Bearer YOUR_TOKEN"`

**Benefits**:
- **Long timeout**: 120s default allows for slow WhatsApp API responses
- **Lambda compatible**: Uses HTTP client timeout for proper Lambda support
- **Simple model**: No lazy registration; session is registered at startup or via manual endpoint
- **Visibility**: Health check exposes `session_registered` status

**Deployment Flow**:

For **long-running servers**:
1. Deploy and start app → `on_startup` runs → session registers (if API is up)
2. Webhooks start flowing automatically
3. If registration fails: Call `POST /api/actions/{action_id}/session/register` to register manually

For **Lambda**:
1. Deploy → First invocation runs bootstrap → `on_startup` attempts registration with 120s HTTP timeout
2. If registration succeeds: Done, webhooks will flow
3. If registration times out or fails: Call `POST /api/actions/{action_id}/session/register` after deploy to register

**Note**: The timeout is implemented at the HTTP client level (not using `asyncio.wait_for()`) for Lambda compatibility.

### 2. Enhanced Error Handling

- Specific exception types for different error conditions
- Proper logging with structured error information
- Graceful degradation for non-critical failures

### 2. Security Enhancements

- Path sanitization for all file operations
- Input validation with type checking
- Secure webhook URL generation with API keys
- XSS prevention in message sanitization

### 3. Performance Optimizations

- Fixed chunking algorithm efficiency
- Reduced redundant calculations
- Improved async operation handling

### 4. Code Quality

- Eliminated variable shadowing
- Improved naming conventions
- Added comprehensive type hints
- Enhanced documentation

## Testing

### Security Tests

- Path traversal prevention tests
- Input validation tests
- Error handling verification
- Message sanitization tests

### Performance Tests

- Chunking algorithm correctness
- Memory usage optimization
- Async operation efficiency

## Configuration

### Required Environment Variables (bridge providers: wwebjs, wppconnect, ultramsg)

```env
# WhatsApp API Configuration
WHATSAPP_API_URL=https://api.whatsapp.provider.com
WHATSAPP_API_KEY=your_api_key
WHATSAPP_SESSION=your_session_name
WHATSAPP_TOKEN=your_token
```

### Meta Cloud API (`provider: meta`)

Use the official WhatsApp Business Cloud API instead of a self-hosted bridge. Supports 1:1 text, images, documents, video, voice notes (inbound STT / outbound TTS), location, typing indicators (within the 24-hour customer service window), and **approved message templates (HSMs)** via Orchestrator tools `whatsapp__list_templates` / `whatsapp__send_template`.

Template tools are hard-gated: they only run on inbound `channel=whatsapp` **or** `channel=whatsapp_call` (jvvoice) turns and always send to the inbound sender/caller (`user_id`). Optional `template_allowlist` / `default_template_language` on the action bound which Meta templates may be sent. Listing goes through jvconnect `GET /api/v1/meta/whatsapp/templates`; send uses `POST /api/v1/meta/whatsapp/messages` with `type: template`.

**Flows:** `whatsapp__list_flows` / `whatsapp__send_flow` send interactive Flow messages (`type: interactive`, `interactive.type: flow`) with the same WhatsApp text/call gate. Optional `flow_allowlist` (ids or names). Listing via jvconnect `GET /api/v1/meta/whatsapp/flows`. In the jvconnect **Flows** UI, the Send dialog can **Copy JSON** / **Copy jvconnect curl** for the Cloud API payload.

**Flow prefill (navigate):** pass `screen` plus `screen_data` (object of field keys → values) on `whatsapp__send_flow`. That maps to Meta `flow_action_payload.data`. Keys must match bindings in the published Flow JSON; not valid with `flow_action=data_exchange` (INIT path).

**Flow inbound paths (distinct from chat webhooks):**

| Path | Transport | Agent handling |
|------|-----------|----------------|
| User completed Flow | Meta `messages` webhook → jvconnect forward → agent POST | `interactive` / `nfm_reply` becomes a chat utterance (`response_json` as body) |
| Request-data / INIT screens | Meta Flow runtime → jvconnect `/api/flows/data/{phoneId}` → agent POST with `X-Jvconnect-Flow-Exchange: 1` | Slim handler returns `{screen,data}` (or `endpoint_not_configured` for INIT). Prefer navigate Flows (“No data”) unless you implement INIT screens |
| Agent GET hub.challenge | Unused when `provider=meta` via jvconnect | Meta verifies jvconnect only (`FB_VERIFY_TOKEN`) |

Configure `stt_action` and `tts_action` on the WhatsApp action (same as bridge providers) for voice note transcription and voice replies.

#### jvconnect credential proxy (required for `provider: meta`)

WhatsApp Cloud API traffic goes through **jvconnect** so Meta access tokens and the app secret never land in jvagent `.env`. Each jvconnect API key is bound to one WhatsApp phone; jvagent does not need `WHATSAPP_PHONE_NUMBER_ID`.

```yaml
- action: jvagent/whatsapp_action
  context:
    provider: meta
```

```env
JVCONNECT_URL=https://your-jvconnect.example.com
JVCONNECT_API_KEY=jvk_...
JVAGENT_PUBLIC_BASE_URL=https://your-app.com
```

Create a **phone-bound** API key on jvconnect → **API Credentials** (pick the connected number). On startup, jvagent calls `GET /api/v1/meta/whatsapp/account` to resolve the phone, then `POST /api/v1/meta/whatsapp/webhook/register` (Meta → jvconnect → agent).

Bridge providers (`wwebjs`, `wppconnect`, `ultramsg`) are unchanged and do not use jvconnect.

#### Optional overrides

`phone_number_id` / `waba_id` on the action (or `WHATSAPP_PHONE_NUMBER_ID` / `WHATSAPP_WABA_ID`) are optional caches for inbound filtering; if unset, they are loaded from jvconnect `/account`. `access_token` / `WHATSAPP_ACCESS_TOKEN` and `WHATSAPP_APP_SECRET` are unused for `provider: meta`.

**Verify token:** Meta verifies against jvconnect (`FB_VERIFY_TOKEN`). The agent webhook is signed with the jvconnect-issued `JVCONNECT_WEBHOOK_SECRET`. The agent’s own GET hub.challenge endpoint is vestigial for `provider=meta` (Meta never challenges the agent URL).

**Meta webhook callback** (automatic override on startup):

On startup (meta provider), jvagent registers via jvconnect (`POST /api/v1/meta/whatsapp/webhook/register`) in a background task **after** uvicorn reports `Application startup complete` (optional `WHATSAPP_WEBHOOK_REGISTER_DELAY_SECONDS`, default **0**). Meta points at jvconnect; jvconnect forwards to this agent.

**The Meta App Dashboard callback URL will not change automatically.** Dashboard shows the app default (`application` layer). Verify with `GET /api/actions/{action_id}/meta/webhook-status`.

#### Purge and callback URLs

`jvagent --purge` creates a **new agent node id** (`n.Agent.*`). Every Meta callback URL embeds that id, so after a purge you must realign **all three Meta routing layers**:

| Layer | Where it lives | Updated by jvagent? |
|-------|----------------|---------------------|
| `application` | Meta App Dashboard → WhatsApp → Configuration | **No** — manual update required |
| `whatsapp_business_account` | WABA `subscribed_apps` override | Yes (when `waba_id` set) |
| Phone override | Phone `webhook_configuration` | Yes (when `phone_number_id` set) |

When both `waba_id` and `phone_number_id` are set, startup registers **both** overrides. If inbound POSTs still hit an old agent id (404 Agent not found), check `GET .../meta/webhook-status` → `stale_callbacks` and follow `dashboard_action` (usually: update App Dashboard callback + verify token, then restart or `POST .../meta/webhook-register`).

Avoid `--purge` on production unless you intentionally reset the database and can update Meta callbacks immediately.

1. **Callback URL**: `{JVAGENT_PUBLIC_BASE_URL}/api/whatsapp/interact/webhook/{agent_id}` — `{agent_id}` is the agent **node id** (e.g. `n.Agent.xxxx`), not the YAML path; no `api_key` query param.
2. **Verify token**: derived automatically; `GET .../meta/webhook-url` (admin) shows the active token for debugging.
3. Subscribe to the **messages** field in the dashboard (one-time app setup).

**Webhook field subscriptions (Meta App Dashboard):**

- Subscribe to **`messages`** only (one-time app setup). That covers chat and Flow completion (`nfm_reply`).
- Add **`calls`** only if `WhatsAppVoiceAction` is enabled.
- Do **not** subscribe `smb_message_echoes`, `message_template_status_update`, or account/quality fields for agent traffic — jvconnect filters agent forwards to `messages` + `calls`; other fields stay on the Inbox/Ably path only.

**Retry idempotency (wamid dedup):**

Meta delivers webhooks **at-least-once** and may retry for up to 7 days. jvagent keeps an in-process cache of seen inbound **`messages[].id`** (wamid) for the meta provider and returns `duplicate webhook` (HTTP 200) on replay so the agent does not reply twice.

Env tuning (optional):

- `WHATSAPP_META_WAMID_DEDUP_TTL_SECONDS` — default **86400** (24h).
- `WHATSAPP_META_WAMID_DEDUP_MAX` — default **10000** entries.

For multi-worker deployments, consider a shared dedup store (not included in the default in-process cache).

Env toggles:

- `WHATSAPP_SKIP_STARTUP_WEBHOOK_REGISTRATION=true` — skip override on startup; call `POST /api/actions/{action_id}/meta/webhook-register` when ready.
- `WHATSAPP_WEBHOOK_REGISTER_DELAY_SECONDS` — optional delay before override (default **0**).
- `WHATSAPP_RELOAD_WEBHOOK_SUBSCRIBE=false` — skip override on action reload.

Admin helpers:

- `GET /api/actions/{action_id}/meta/webhook-url` — returns `meta_callback_url` (stripped of `api_key`).
- `POST /api/actions/{action_id}/meta/webhook-register` — register override immediately.

**Media and voice (meta provider):**

- Inbound media: Meta sends a media id in the webhook; jvagent downloads via Graph and feeds the same pipeline as bridge providers (vision, STT, etc.).
- Outbound media: agent replies upload files from jvagent public URLs (`JVAGENT_PUBLIC_BASE_URL` + `/api/files/...`) to Meta before send.
- Typing: uses inbound `wamid` (`message_id`) with Meta’s read + typing_indicator API.
- Outbound voice notes (native PTT bubble): requires OGG/OPUS; when TTS returns MP3, jvagent transcodes via **ffmpeg** on PATH (`libopus` → OGG) before upload when available; otherwise sends as a basic audio file.

**Production smoke (Meta 1:1):**

1. Send a user message → confirm **one** agent reply.
2. Replay the same webhook POST body (same wamid) → response `duplicate webhook`, no second reply.
3. Automated regression: `pytest tests/action/whatsapp/test_meta_webhook_interact_smoke.py`.

**Meta Groups:** deferred — see [ADR-0028](../../../.planning/adr/0028-defer-meta-whatsapp-groups.md). Not wwebjs parity; requires OBA + separate implementation phase.

Bridge-only variables (`WHATSAPP_API_URL`, `WHATSAPP_API_KEY`, `WHATSAPP_SESSION`) are not used when `provider: meta`.

### Shared / optional

```env
# Application Base URL (required for webhooks and media delivery)
# Used for webhook generation and to resolve relative media URLs (e.g. TTS audio)
# to absolute URLs when the adapter fetches files for sending
JVAGENT_PUBLIC_BASE_URL=https://your-app.com

# Session Registration Timeout (optional, default: 120 seconds)
# HTTP client timeout for session registration during startup
# Allows time for slow WhatsApp API responses (registration can take 30-60+ seconds)
# Implemented at HTTP client level for Lambda compatibility (not asyncio.wait_for)
# Minimum value: 5 seconds
WHATSAPP_SESSION_REGISTER_TIMEOUT_SECONDS=120

# Media batch mode follows jvspatial is_serverless_mode() only (SERVERLESS_MODE or platform auto-detect).
# Deferred path: MongoDB + jvspatial create_task (Shape A); in-process path: in-memory batch + create_task (Shape B).

# Skip Startup Registration (optional, for Lambda; bridge providers only)
# Set to true to skip session registration on cold start; use POST /api/actions/{action_id}/session/register manually
WHATSAPP_SKIP_STARTUP_REGISTRATION=false

# Security Configuration
JVSPATIAL_JWT_SECRET_KEY=your_jwt_secret
```

### Action Configuration (agent.yaml)

```yaml
actions:
  - action: jvagent/whatsapp
    context:
      enabled: true
      provider: "wppconnect"
      api_url: "${WHATSAPP_API_URL}"
      api_key: "${WHATSAPP_API_KEY}"
      session: "${WHATSAPP_SESSION}"
      token: "${WHATSAPP_TOKEN}"
      base_url: "${JVAGENT_PUBLIC_BASE_URL}"
      request_timeout: 60
      chunk_length: 4000
      media_batch_window: 1.5
      stt_action: "DeepgramSTTAction"   # For transcribing voice messages (PTT)
      tts_action: "ElevenLabsTTSAction"   # For replying with voice when user sends PTT
```

## Voice Messages (PTT and TTS)

When `stt_action` and `tts_action` are configured, the action supports voice message flows. Agents register a concrete provider (e.g. jvagent/deepgram_stt, jvagent/elevenlabs_tts) and WhatsApp references it by class name (e.g. DeepgramSTTAction, ElevenLabsTTSAction):

- **Inbound PTT**: Voice messages are transcribed via the STT action. The adapter passes the correct audio format (WhatsApp voice uses OGG-Opus) to the generic STT layer.
- **Outbound voice reply**: When the user sends a PTT, the agent can respond with a synthesized voice message. ReplyAction applies voice-optimized formatting (short replies, no markdown), and the TTS action generates audio. The adapter sends the result as a voice message.
- **Media URLs**: TTS and other media may return relative URLs (e.g. `/api/files/...` under the default `JVSPATIAL_API_PREFIX`). The adapter prepends `base_url` (from `JVAGENT_PUBLIC_BASE_URL`) to produce absolute URLs before fetching for delivery. Ensure `JVAGENT_PUBLIC_BASE_URL` is set and publicly reachable.

## Images and Vision

The WhatsApp action supports image recognition so the agent can interpret images shared by users. Images flow through the media pipeline and are passed to the vision-capable language model.

### Image Flow

1. **Direct images**: When a user sends an image (with or without caption), media is saved via `MediaManager`, batched by `MediaBatchManager`, and passed to `visitor.data["image_urls"]` (vision-capable images) and `visitor.data["whatsapp_media"]` (all media).
2. **Quoted image replies**: When a user replies to an image using WhatsApp's reply feature, the webhook delivers the original image as base64 in `quoted_message`. The system extracts this via `_extract_quoted_image()` and injects it into `visitor.data["image_urls"]` before creating the walker, so the vision pipeline receives the image even when the current message is text-only (e.g. "What's in this?").
3. **Vision pipeline**: ReplyAction uses `build_prompt_for_vision()` to check `visitor.data["image_urls"]` and, when images are present, builds multimodal content for the LLM. An extensive image interpretation is generated behind the scenes and stored on the Interaction for follow-up questions (e.g., "What color was the car?"). The base persona includes the capability "Can view and interpret images shared by users."

### Data Keys

- **`image_urls`**: Standard key for vision-capable images (URLs or `{"base64": "..."}` dicts). Used by ReplyAction and the vision prompt builder. When populated (and not suppressed), an extensive interpretation is stored on the Interaction for follow-up questions.
- **`whatsapp_media`**: All media URLs (images, documents, video, audio). Preserved for backward compatibility with interview actions using `data_input_field: "whatsapp_media"`.
- **`image_interpretation`**: Set to `False` to suppress vision (e.g., document uploads). When suppressed, images are not passed to the model and no interpretation is stored.

### URL Accessibility

Media URLs are built as `whatsapp_action.base_url + media_url`. The LLM (e.g. OpenAI) must be able to fetch these URLs. Ensure `base_url` is publicly reachable or use a proxy/tunnel in development.

### File Storage on Lambda

On AWS Lambda, the deployment root (`/var/task`) is read-only. Local file storage with `root_dir: ./.files` or `.files` will fail. For Lambda deployments:

- **Recommended:** Configure `file_storage.provider: s3` (or `JVSPATIAL_FILE_STORAGE_PROVIDER=s3`) with an S3 bucket. Ensure `JVAGENT_PUBLIC_BASE_URL` and file proxy/URL generation serve media from S3.
- **Alternative:** Set `JVSPATIAL_FILES_ROOT_PATH=/tmp` for ephemeral storage (cleared between invocations; not suitable for media that must persist across requests).

### Persona Capabilities

When enabled, WhatsAppAction contributes capabilities to the agent via `get_capabilities()`: "Join WhatsApp groups and send messages to groups", "Send and receive voice notes over WhatsApp", "Send and receive images, documents, and other media over WhatsApp". These are aggregated automatically into the reply prompt; no manual sync in agent.yaml is needed. See [`jvagent/reply`](../reply/reply_action.py).

### Media Batch Mode

Batching mode follows **only** jvspatial `is_serverless_mode()` (config / `SERVERLESS_MODE` / platform auto-detect; see jvspatial `docs/md/serverless-mode.md`). Cloud-specific wiring (Lambda self-invoke, SQS, etc.) lives in jvspatial’s deferred-task factory, not in this module.

| Mode | Condition | Behavior |
|------|-----------|----------|
| **async** | Not serverless | In-memory batching with background timer. Media waits `media_batch_window` to coalesce; then one interact call with all items. |
| **deferred** | Serverless | Persistent batching in MongoDB + `jvspatial.create_task` (Shape A) for follow-up processing. Requires `JVSPATIAL_DB_TYPE=mongodb` for atomic updates. |

**Stale-batch flush (serverless only)**: `flush_pending_batch_if_stale` runs when the webhook continues to a **non-media** interaction (text, voice after STT, location)—see `whatsapp_interact` in `endpoints.py`. It does **not** run at the start of each image/document/video/audio webhook, so multiple files can remain in one `media_batches` document even when the provider delivers them more than `media_batch_window` seconds apart. If deferred dispatch is broken, a follow-up text (or voice/location) still drains an old pending batch.

#### AWS Lambda: self-invoke (typical setup)

The main Lambda can handle both HTTP (webhook) and batch events without a second function. The webhook invokes itself asynchronously; AWS Lambda Web Adapter (LWA) routes the direct-invoke payload to an internal HTTP endpoint.

**deploy.yaml configuration**:

```yaml
lambda:
  environment:
    # Set by AWS / jvdeploy; jvspatial uses it for default Lambda deferred dispatch
    AWS_LAMBDA_FUNCTION_NAME: "{{app.name}}"
    AWS_LWA_PASS_THROUGH_PATH: "/api/_internal/deferred"
```

- `AWS_LAMBDA_FUNCTION_NAME`: Set by AWS in Lambda environments (or by jvdeploy as `{{app.name}}`). Used for self-invoke; no extra config needed.
- `AWS_LWA_PASS_THROUGH_PATH`: Routes direct-invoke payloads to jvspatial’s deferred invoke router (`POST /api/_internal/deferred` by default). Without this, LWA defaults to `/events` and deferred tasks are never dispatched in-process.
- **IAM**: The Lambda role needs `lambda:InvokeFunction` on its own ARN (self-invoke).

**Flow**: Webhook persists media batches in the prime database (`media_batches`) → `create_task("jvagent.whatsapp.media_batch", {...}, run_at=...)` schedules work (Lambda async invoke and/or EventBridge when configured; see jvspatial serverless deferred-task docs) → LWA POSTs the JSON body to `/api/_internal/deferred` → `media_batch_manager.handle_whatsapp_media_batch_deferred_event` runs `process_persistent_batch`, which shares the same downstream walker path as in-memory batching (`_process_batch_internal`). MongoDB is the typical production store; other jvspatial adapters support the same `find_one_and_update` / work-claim APIs with RMW semantics (weaker under concurrent writers than native Mongo).

## Lambda Deployment

For AWS Lambda deployments, configure the following:

### Required Environment Variables

| Variable | Description |
|----------|-------------|
| `WHATSAPP_API_URL` | WhatsApp API endpoint URL |
| `WHATSAPP_API_KEY` | WhatsApp API authentication key |
| `JVAGENT_PUBLIC_BASE_URL` | Public base URL for webhooks and media (e.g. API Gateway URL) |
| `JVSPATIAL_JWT_SECRET_KEY` | JWT secret for webhook API key auth |

### Lambda-Specific Configuration

| Variable | Description |
|----------|-------------|
| `SERVERLESS_MODE` | Optional override; Lambda sets serverless automatically. See jvspatial serverless docs. |
| `JVSPATIAL_FILE_STORAGE_PROVIDER` | Set to `s3` for media storage. Local storage fails on Lambda (read-only `/var/task`). |
| `JVSPATIAL_DB_TYPE` | `mongodb` recommended for deferred media batching under concurrency; json/sqlite and other adapters work via jvspatial compound operations (best-effort RMW where not native) |
| `AWS_LWA_PASS_THROUGH_PATH` | Route direct-invoke payloads to deferred router: `/api/_internal/deferred` (or `{JVSPATIAL_API_PREFIX}/_internal/deferred`) |
| `WHATSAPP_SKIP_STARTUP_REGISTRATION` | Set to `true` to skip session registration on cold start; use manual endpoint instead |

### LWA (Lambda Web Adapter) Configuration

When using Lambda async invoke for deferred tasks, LWA must route direct-invoke payloads to the jvspatial deferred endpoint. Set `AWS_LWA_PASS_THROUGH_PATH=/api/_internal/deferred` in the Lambda environment (or match your `JVSPATIAL_API_PREFIX`). Without this, LWA defaults to `/events` and deferred handlers are never reached.

### IAM Permissions

For self-invoke: the Lambda role needs `lambda:InvokeFunction` on its own ARN.

### Serverless WhatsApp media batch – remediation checklist

Use this when logs show `Invoked deferred task jvagent.whatsapp.media_batch` but **no reply** until a follow-up text (typing may show on the webhook only). Work through in order.

| Step | Action | Verify |
|------|--------|--------|
| 1. LWA pass-through | Set `AWS_LWA_PASS_THROUGH_PATH` to **`{JVSPATIAL_API_PREFIX}/_internal/deferred`** (default **`/api/_internal/deferred`**). Must match jvspatial `APIRoutes.deferred_invoke_full_path()` for your `JVSPATIAL_API_PREFIX`. | CloudWatch / access logs show **`POST .../_internal/deferred`** on the invocation triggered by deferred scheduling—not only webhook URLs. |
| 2. App auth middleware | **`POST …/_internal/deferred` must bypass `AuthenticationMiddleware`** (no JWT on LWA pass-through). jvspatial **`PathMatcher` always exempts** `/_internal/deferred` so custom **`JVSPATIAL_AUTH_EXEMPT_PATHS`** cannot accidentally drop it. If you still see **`[401] path=/api/_internal/deferred has_auth_header=False`**, upgrade jvspatial to a version with that fix. Separately: if **`JVSPATIAL_DEFERRED_INVOKE_SECRET`** is set, the **deferred route** returns **401** unless you send `X-JVSPATIAL-Deferred-Authorize` / `Bearer` (LWA does not add these by default)—prefer unset for self-invoke. | No **401** from `auth_middleware` on `/_internal/deferred`; no **401** from deferred secret unless you intend to enforce it. |
| 3. Lambda timeout | Timeout must exceed **`media_batch_window`** + full interact (LLM) + WhatsApp send. Typical **30–120s+**; default **300s** in example deploy templates is safe. | REPORT for the deferred `RequestId` is not `Status: timeout`. |
| 4. IAM self-invoke | Execution role allows **`lambda:InvokeFunction`** on **this** function’s ARN. | No `AccessDeniedException` on invoke in logs. |
| 5. Shared media storage | Webhook and deferred run may be **different instances**. Local files under instance-only disk are invisible to the deferred worker. Use **S3** (`JVSPATIAL_FILE_STORAGE_PROVIDER=s3`) or **EFS** mounted at the same path for **all** invocations (e.g. `/mnt/jvspatial/.files`). | `GET` the saved media URL succeeds from a cold invocation / different instance. |

After routing works, deferred logs should include **`Deferred whatsapp media batch invoked for sender ...`** and either **`Processed media batch ...`** or **`processed=False`** (INFO) with a hint for claim races / empty batch.

## Migration Notes

### Breaking Changes

- Configuration now uses typed attributes instead of dictionaries
- Error handling now raises specific exception types
- Path handling requires proper sanitization

### Backward Compatibility

- Existing webhook URLs remain functional
- API endpoints maintain same interface
- Configuration keys unchanged (only validation added)

## Troubleshooting

### Session Registration Issues

#### Problem: Session registration fails or times out on startup

**Symptoms**:
- Log message about session registration failing or timing out on startup
- Webhooks not working after deployment

**Solutions**:
1. **Check WhatsApp API availability**: Verify the API server at `WHATSAPP_API_URL` is reachable
2. **Increase timeout**: Set `WHATSAPP_SESSION_REGISTER_TIMEOUT_SECONDS` to a higher value (e.g., 180 or 240)
3. **Use manual registration**: Call `POST /api/actions/{action_id}/session/register` after deployment

#### Problem: Session registered but webhooks not working

**Symptoms**:
- Health check shows `session_registered: true`
- Webhooks still not arriving

**Solutions**:
1. **Check webhook URL**: Verify `JVAGENT_PUBLIC_BASE_URL` is set correctly and publicly accessible
2. **Check adapter initialization**: Health check should show `adapter_initialized: true`
3. **Verify API key**: Webhook URL includes API key - ensure it's valid and not expired
4. **Check provider configuration**: Verify WhatsApp provider has the correct webhook URL registered

#### Problem: Voice or media messages fail to send ("Failed to fetch or encode file")

**Symptoms**:
- TTS voice replies or other media fail with fetch errors
- Log shows relative URL path (e.g. `/api/files/...`)

**Solutions**:
1. **Set JVAGENT_PUBLIC_BASE_URL**: The adapter needs an absolute URL to fetch media. Ensure `JVAGENT_PUBLIC_BASE_URL` is set (e.g. `https://your-app.com`) and is publicly reachable.
2. **Configure base_url on action**: In agent.yaml, set `base_url: "${JVAGENT_PUBLIC_BASE_URL}"` for the WhatsApp action.

#### Problem: Lambda batch mode - media not processed until follow-up message

**Symptoms**:
- No errors in logs, but batched media is not processed automatically
- Sending a follow-up **text** message (or a voice message that yields a transcript, or a location message) triggers processing via `flush_pending_batch_if_stale` on that non-media path

**Cause**: LWA sends direct-invoke payloads to `/events` by default. The deferred router is at `/api/_internal/deferred`, so the request never reaches it.

**Solution**: Set `AWS_LWA_PASS_THROUGH_PATH: "/api/_internal/deferred"` in the Lambda environment (deploy.yaml). Redeploy so the env var is applied.

#### Problem: ResourceNotFoundException for batch processor function

**Symptoms**:
- `Function not found: arn:aws:lambda:...:function:...`

**Cause**: `AWS_LAMBDA_FUNCTION_NAME` (set by AWS or deploy config) points to a non-existent or misconfigured Lambda.

**Solution**: Ensure the Lambda function exists and `AWS_LAMBDA_FUNCTION_NAME` matches the deployed function name. Set `AWS_LWA_PASS_THROUGH_PATH: "/api/_internal/deferred"` so LWA routes direct-invoke payloads to the deferred router.

### Health Check

Check the action's health status to diagnose registration issues:

```bash
GET /api/actions/{action_id}/healthcheck
```

**Response fields**:
- `healthy`: Overall health status
- `session_registered`: Whether session is currently registered (at startup or via manual endpoint)
- `adapter_initialized`: Whether WhatsAppAdapter is initialized

**Example response**:
```json
{
  "healthy": true,
  "configured": true,
  "status": "active",
  "session_registered": true,
  "adapter_initialized": true
}
```

### Manual Registration Endpoint

Force session registration manually:

```bash
POST /api/actions/{action_id}/session/register
Authorization: Bearer YOUR_TOKEN
```

**When to use**:
- Startup registration timed out or failed
- Need to re-register after configuration changes
- Want to verify registration

**Response**:
```json
{
  "status": "CONNECTED",
  "ok": true,
  "message": "Session registered successfully",
  "session": "your_session_name"
}
```

## Best Practices

1. **Always validate user input**:

   ```python
   if not sender:
       logger.debug("No sender information in WhatsApp message")
       return {"status": "ignored", "response": "No sender information"}
   ```

2. **Use proper error handling**:

   ```python
   try:
       result = await risky_operation()
   except SpecificError as e:
       logger.error(f"Specific error: {e}")
       # Handle specifically
   except Exception as e:
       logger.error(f"Unexpected error: {e}", exc_info=True)
       # Handle generically
   ```

3. **Sanitize all paths**:

   ```python
   safe_path = PathSanitizer.sanitize_path(user_input)
   ```

4. **Validate configuration**:
   ```python
   health_result = await self.healthcheck()
   if isinstance(health_result, dict) and not health_result.get("healthy", True):
       raise ValidationError(f"Configuration errors: {'; '.join(health_result.get('errors', []))}")
   ```

5. **Monitor registration status**:
   ```python
   health = await whatsapp_action.healthcheck()
   if not health.get("session_registered"):
       logger.warning("WhatsApp session not registered; use manual session/register endpoint.")
   ```

## Security Checklist

- ✅ Path traversal prevention implemented
- ✅ Input validation for all user inputs
- ✅ Proper error handling with specific exceptions
- ✅ Secure file handling with validation
- ✅ XSS prevention in message processing
- ✅ API key security for webhooks
- ✅ Timeout handling for external requests
- ✅ Logging without sensitive data exposure

## Performance Checklist

- ✅ Efficient chunking algorithm
- ✅ Proper async/await usage
- ✅ Minimal blocking operations
- ✅ Optimized database queries
- ✅ Reduced memory allocations
- ✅ Connection pooling for HTTP requests

This update brings the WhatsApp action into full compliance with jvspatial and jvagent coding standards while addressing all identified security vulnerabilities.
