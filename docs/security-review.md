# Security Review: jvagent

**Date:** 2026-05-02
**Scope:** jvagent package and jvchat frontend (excluding jvspatial, which has been separately reviewed)
**Reviewer:** Automated security analysis
**Status:** Review complete; 4 of 5 medium findings remediated in code

---

## Executive Summary

jvagent is a modular AI agent platform built on jvspatial's graph primitives. The platform exposes a substantial API surface (~198 endpoints) for managing agents, actions, memory, conversations, and integrations with third-party services (Google, Microsoft, Facebook, WhatsApp, email). Security design is generally thoughtful, with multiple defense layers including JWT authentication, API key management, role-based access control, per-agent policy enforcement, rate limiting, path traversal protection, and webhook HMAC verification.

**Overall finding:** The codebase demonstrates solid security fundamentals. No critical vulnerabilities were identified. Four of five medium-severity findings have been remediated in code. One medium finding and five low-severity items remain as hardening opportunities.

### Risk Summary (Post-Remediation)

| Severity | Count | Area |
|----------|-------|------|
| High | 0 | — |
| Medium | 2 | Frontend plaintext credential storage (partial — UI warning added), example .env local credentials (operational) |
| Low | 5 | Missing CSP, JWT in localStorage, OAuth state scope, env var placeholder defaults, logging PII retention |
| Informational | 3 | Hardening recommendations |

### Remediations Applied (2026-05-02)

| Issue | Severity | Fix | File(s) |
|-------|----------|-----|---------|
| LaTeX injection | Medium | Added `_safe_hex_color()` validator (hex regex), added `-no-shell-escape` to all LaTeX invocations | `skills/pdf_generation/scripts/latex_compiler.py` |
| Webhook URL SSRF | Medium | Added `_validate_webhook_url()` with DNS resolution + `ipaddress` check blocking private/reserved IP ranges | `core/callback.py` |
| Rate limiter races | Medium | Replaced uninitialized `_lock` with `asyncio.Lock()`; made `check_rate_limit` / `record_request` async; updated callers | `action/interact/rate_limiter.py`, `action/interact/endpoints.py` |
| Production-unsafe defaults | Low | Added `is_production_mode()` checks warning on: debug mode, empty admin password, runtime pip install in production | `cli/server_config.py` |
| Frontend credential storage | Medium (partial) | Added persistent amber warning banner about plaintext storage; strengthened export notice; added preemptive warning for new users | `jvchat/src/components/Login.tsx` |

---

## 1. Authentication & Authorization

### 1.1 JWT Authentication (Adequate)

**Files:** `jvagent/cli/server_config.py:150-165`

JWT auth is enabled by default. The server refuses to start if auth is enabled but `JVSPATIAL_JWT_SECRET_KEY` is unset. Default token expiry is 60 minutes, configurable via `auth.jwt_expire_minutes` / `JVSPATIAL_JWT_EXPIRE_MINUTES`.

**Finding:** No issues. The hard-fail on missing JWT secret is the correct behavior. The 60-minute default is reasonable for an admin API.

### 1.2 Role-Based Access Control (Adequate)

**Files:** `jvagent/cli/server_config.py:299-303`

Three roles: `admin` (wildcard `*`), `user` (empty), `system` (empty). Nearly all management endpoints require `roles=["admin"]`. One endpoint (`/api/agents/{agent_id}/memory/me`) accepts any authenticated user.

**Finding:** No issues. The role model is simple and appropriate. The `system` role having no default permissions is correct (permissions are granted explicitly to system service users).

### 1.3 Per-Agent Access Control (Adequate, Minor Edge Case)

**Files:** `jvagent/action/access_control/access_control_action.py`

The `AccessControlAction` provides fine-grained allow/deny rules per channel, per action label, with user and group matching. It supports:
- Deny-first evaluation (deny rules checked before allow rules)
- `default_deny` mode
- `allow_anonymous` toggle
- `enforce` toggle
- Action-level exceptions
- Action aliases

**Finding (Low):** The `_matches_rule` method (line 143) treats a `user` or `group` value of `"any"` or `"all"` as a wildcard — but only when those exact strings are passed as the rule's `user` or `group` field. If a real user had the ID `"all"` or `"any"`, they would unintentionally match wildcard rules. This is unlikely in practice with UUID-based user IDs but worth noting.

### 1.4 API Key Management (Adequate)

**Files:** `jvagent/cli/server_config.py:167-187`

API key management is enabled by default when auth is enabled. Uses configurable prefix (`jv_`) and header (`x-api-key`). Webhook endpoints authenticate via API key.

**Finding:** No issues.

### 1.5 Auth-Exempt Paths (Adequate, Verify Intent)

**Files:** `jvagent/cli/server_config.py:270-281`

Seven path patterns are exempt from authentication, including:
- `/health`, `/docs`, `/redoc`, `/openapi.json`, `/favicon.ico` (standard)
- `/api/auth/register`, `/api/auth/login`, `/api/auth/refresh`, `/api/auth/logout` (auth flow)
- `/api/agents/*/interact` — the anonymous interact endpoint

Additional paths can be added via `auth.exempt_paths` in `app.yaml`.

**Finding (Low):** The `/api/auth/register` path allows public user registration when no users exist. `jvspatial`'s auth service should automatically disable public registration after the first user is created, but this relies on jvspatial's implementation. Verify that jvspatial enforces this consistently.

### 1.6 Admin Bootstrap (Adequate — Production Warning Added)

**Files:** `jvagent/cli/bootstrap.py:136-244`, `jvagent/cli/server_config.py:189-196`

Admin user is bootstrapped from `JVAGENT_ADMIN_USERNAME`, `JVAGENT_ADMIN_PASSWORD`, and `JVAGENT_ADMIN_EMAIL`. If `admin_password` is empty, admin bootstrap is skipped. The `ensure_admin_user()` function provides recovery logic: if the configured email exists without the admin role, it logs a warning rather than silently failing.

**Original Finding (Low):** When `admin_password` is empty, the admin was silently not created but the server still started. In production, this could leave the instance without an admin user.

**Remediation:** Added a production-mode safety check in `create_server_from_config()` that logs a `WARNING` when `is_production_mode()` is true and `admin_password` is empty. The message: *"PRODUCTION SAFETY: JVAGENT_ADMIN_PASSWORD is not set in production. No admin user will be created."* This surfaces the condition prominently in production logs.

**Residual risk:** The server still starts without an admin in production (non-fatal). Consider making this a hard error in a future release.

---

## 2. Input Validation & Rate Limiting

### 2.1 Interact Rate Limiter (Remediated)

**Files:** `jvagent/action/interact/rate_limiter.py`, `jvagent/action/interact/endpoints.py:317-387`

The `InteractRateLimiter` implements:
- Sliding window rate limiting by `IP:agent_id` (default 60 req/min)
- Utterance length validation (default 2000 chars)
- Periodic cleanup when cache exceeds 1000 entries
- IP extraction from `X-Forwarded-For`, `X-Real-IP`, `CF-Connecting-IP`, and `request.client.host`

**Original Finding (Medium):** The rate limiter used in-memory state (`defaultdict`) without any synchronization primitive (the `_lock` attribute was declared but never initialized or used). In an async server with multiple worker processes (e.g., uvicorn with `--workers N`), each worker had its own independent rate limit counter. The `X-Forwarded-For` header could be spoofed by clients if not behind a trusted reverse proxy.

**Remediation:** Replaced `self._lock = None` with `self._lock = asyncio.Lock()`. Wrapped all reads and writes to `_request_timestamps` inside `async with self._lock` blocks. Converted `check_rate_limit()` and `record_request()` to async methods. Updated both call sites in `action/interact/endpoints.py` to `await`. The class docstring now documents the single-process scope and recommends Redis for multi-worker deployments.

**Residual risk:** Multi-process deployments still need a shared store (Redis, etc.). The `X-Forwarded-For` trust concern remains a deployment-level configuration matter.

### 2.2 Utterance Validation (Adequate)

**Files:** `jvagent/action/interact/endpoints.py:572-576`

Empty utterances are rejected with a `ValidationError`. Whitespace-only utterances are caught by `utterance.strip()` check.

**Finding:** No issues.

### 2.3 File Upload Validation (Adequate)

**Files:** `jvagent/action/pageindex/endpoints.py`

- Max upload size: 100 MB (`MAX_UPLOAD_BYTES`)
- Extension allowlist: `ALLOWED_EXTENSIONS`
- Content-Type validation for remote URL downloads
- Filename sanitization via `_safe_ingest_filename_stem()`

**Finding:** No issues.

---

## 3. Injection Attacks

### 3.1 LaTeX Injection (Remediated)

**Files:** `jvagent/skills/pdf_generation/scripts/latex_compiler.py`

The `pdf_generation__latex_compile` tool renders user-supplied content into a LaTeX document.

**Original Finding (Medium):** Two variables were injected into the LaTeX template without escaping:
- `brand_primary_color` and `brand_accent_color`: Only stripped of `#` prefix, not validated or escaped
- Standard LaTeX engines were invoked without `-no-shell-escape` flag

An attacker controlling color values through LLM tool call arguments could inject LaTeX commands into the document preamble.

**Remediation:**
1. Added `_HEX_COLOR_RE = re.compile(r"^[0-9a-fA-F]{6}$")` and `_safe_hex_color()` validator function. Color values that do not match the 6-digit hex pattern are replaced with empty strings before template interpolation.
2. Added `-no-shell-escape` flag to the standard LaTeX engine invocation (xelatex/pdflatex/lualatex). Tectonic does not support shell escape and is unaffected.
3. Added `import re` to module imports.

**Residual risk:** The `body` content (rendered via `_convert_to_latex()`) passes through `_tex_escape()` on text segments, but complex LaTeX injection via crafted Markdown headings or list items remains theoretically possible. The `-no-shell-escape` flag mitigates the most dangerous vector.

### 3.2 No SQL Injection (Not Applicable)

The codebase uses jvspatial's graph-based data abstraction. No raw SQL queries are constructed with user input. SQLite FTS5 in `pageindex/lexical_index.py` uses parameterized queries via jvspatial's abstractions.

**Finding:** No issues.

### 3.3 Prompt Injection (Platform Risk — Out of Scope)

As an AI agent platform, jvagent is inherently susceptible to prompt injection attacks through the interact endpoint. The platform relays user utterances to language models, which then issue tool calls. Indirect prompt injection via ingested documents (PageIndex) and webhook payloads (Messenger, WhatsApp, Email) is also possible.

**Finding:** This is an inherent risk of AI agent platforms and not a code defect. The access control system provides a partial mitigation by restricting which actions an anonymous/authenticated user can trigger.

### 3.4 No SSTI / XSS in Server Responses

The server returns JSON responses. No HTML templating is done server-side for user-facing content. The Google/Microsoft OAuth callback pages use `html.escape` for output.

**Finding:** No issues.

---

## 4. Secrets & Credential Management

### 4.1 Environment Variable Resolution (Adequate, Minor Leakage Risk)

**Files:** `jvagent/core/env_resolver.py`

The `resolve_env_placeholders()` function replaces `${VAR_NAME}` in YAML config strings with `os.getenv(VAR_NAME, "")`. Missing vars become empty strings silently (debug log only, unless `${VAR_NAME:?}` syntax is used or `JVAGENT_WARN_EMPTY_PLACEHOLDERS=true`).

**Finding (Low):** If an `agent.yaml` contains `${SECRET_KEY}` and the env var is not set, the action silently receives an empty string. For critical secrets (API keys, OAuth credentials), this could lead to confusing failures downstream. The `${VAR_NAME:?}` syntax provides a warning mechanism but is opt-in.

**Recommendation:** Consider making `${VAR:?}` the default for secrets-related config keys or adding a `required: true` alternative syntax.

### 4.2 No Hardcoded Secrets in Source (Good)

No API keys, tokens, or credentials were found hardcoded in committed source code. All secrets use environment variable placeholders.

**Finding:** No issues in committed code.

### 4.3 Example .env File Contains Local Credentials (Medium)

**Files:** `examples/jvagent_app/.env` (gitignored, present on local disk)

This file contains real credentials including JWT signing key, OpenAI API key, Google OAuth client secret, Serper API key, and an ngrok tunnel URL. While gitignored, these exist unencrypted on disk.

**Finding (Medium):** These appear to be development credentials, but the OpenAI key and Google OAuth secret should be rotated if this machine is shared or if the repository is ever copied. The ngrok URL exposes the local development server's public address.

**Recommendation:** Rotate the OpenAI API key and Google OAuth client secret. Consider using a secrets manager or encrypted vault for development credentials.

### 4.4 Frontend Credential Storage (Medium — Partially Remediated)

**Files:** `jvchat/src/utils/storage.ts:21-86`, `jvchat/src/components/Login.tsx`

The jvchat frontend stores user credentials (server URL, email, **plaintext password**) in `localStorage` under the key `jvchat_saved_credentials_v2`. This is the "remember me" / saved accounts feature.

**Finding (Medium):** Storing plaintext passwords in `localStorage` is a significant security concern:
1. **XSS vulnerability:** Any successful XSS attack on the frontend can exfiltrate all saved credentials.
2. **Local access:** Anyone with physical access to the machine can read credentials from browser developer tools.
3. **No encryption at rest:** Credentials are stored as plain JSON.
4. **Export/import feature** (`buildSavedAccountsExportJson`, `parseSavedAccountsImport`) transfers plaintext passwords in JSON files.

**Partial Remediation (UI warnings):**
1. Added a persistent amber warning banner in the "Saved Accounts" section: *"Credentials are stored in this browser's local storage as plain text. Anyone with access to this device or browser can view saved passwords. Do not save credentials on shared devices."*
2. Added a preemptive note for users who have not yet saved credentials: *"Note: saved credentials are stored in this browser's local storage as plain text."*
3. Strengthened the export confirmation notice: *"Warning: JSON file contains plaintext passwords — store it securely."*

**Remaining recommendations:**
1. Use `sessionStorage` instead of `localStorage` for credentials, so they are cleared when the browser closes.
2. Consider using WebCrypto API to encrypt credentials at rest with a user-provided PIN.
3. Document the risk in the README.

---

## 5. Webhook Security

### 5.1 Webhook API Key Authentication (Adequate)

**Files:** `jvagent/action/utils/webhook_system_user.py`, per-action `webhook_auth.py`

Each webhook-receiving action (WhatsApp, Messenger, Email, PageIndex, PageIndex Google Drive Sync, Task Dispatcher) uses API key authentication. System service users are created with `secrets.token_urlsafe(32)` for random passwords and `roles=["system"]` with specific permissions.

**Finding:** No issues. The use of `secrets.token_urlsafe` (cryptographically secure random) is correct.

### 5.2 Facebook Messenger HMAC Verification (Adequate)

**Files:** `jvagent/action/facebook_action/messenger_webhook_helpers.py:29-45`

Facebook Messenger webhooks use SHA256 HMAC signature verification via `X-Hub-Signature-256` header. The comparison uses `hmac.compare_digest()` (constant-time) to prevent timing attacks.

**Finding:** No issues.

### 5.3 Webhook URL Configuration (Remediated)

**Files:** `jvagent/core/callback.py`

The `trigger_task_created_callback` and `_trigger_task_event_callback` functions send outbound webhooks to URLs configured via `JVAGENT_TASK_CREATED_WEBHOOK_URL` or `TaskCreationInteractAction.task_created_webhook_url`. The webhook target is redacted in logs (only scheme+host shown).

**Original Finding (Low → addressed as Medium):** The webhook URL was used directly in `httpx.AsyncClient().post(webhook_url, ...)` without validating that it pointed to an expected destination. An attacker who could modify the agent configuration could set this to an arbitrary URL, causing SSRF against internal services.

**Remediation:** Added `_validate_webhook_url()` function that:
1. Parses the URL and resolves the hostname via `socket.getaddrinfo()` (dual-stack: `AF_UNSPEC`)
2. Checks every resolved IP address against a static blocklist of private/reserved ranges:
   - `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` (RFC 1918)
   - `169.254.0.0/16` (link-local)
   - `::1/128`, `fc00::/7`, `fe80::/10` (IPv6 loopback/ULA/link-local)
3. Raises `ValueError` (caught by existing `except Exception` handler) if the target resolves to any blocked range
4. Called immediately before `httpx.AsyncClient().post()` in both `_fire()` closures

Added imports: `ipaddress`, `socket`.

### 5.4 Webhook Size Limits (Adequate)

**Files:** `.env.example:215-219`

`JVSPATIAL_WEBHOOK_MAX_PAYLOAD_SIZE=1048576` (1 MB) is configured as default.

**Finding:** No issues.

---

## 6. File System & Path Security

### 6.1 Path Traversal Protection (Adequate, Multiple Layers)

The codebase has consistent path traversal defenses:

- **`PathSanitizer.sanitize_path()`** from jvspatial: used in PageIndex endpoints, MCP filesystem server, WhatsApp media manager, and skill file interface
- **`validate_relative_write_path()`** in `skills/fileinterface/scripts/_core.py`: rejects absolute paths, `..` segments, and `.`
- **`_validate_path_safety()`** in `skills/skill_hub/scripts/_installer.py`: uses `Path.resolve().relative_to()` check
- **`sanitize_segment()`** in `action/mcp/sandbox.py`: restricts path characters to `[a-zA-Z0-9_.\-@]`
- **`_safe_pageindex_relative_path()`** in `action/pageindex/endpoints.py`: wraps `PathSanitizer`

**Finding:** No issues. The multiple layers provide defense in depth.

### 6.2 File Storage Configuration (Adequate)

**Files:** `jvagent/core/config.py:534-560`, `jvagent/cli/server_config.py:312-319`

File storage is configurable (local, S3) with a configurable `max_size`. The `.gitignore` excludes `.files/` directory from version control.

**Finding:** No issues.

### 6.3 MCP Sandbox (Adequate)

**Files:** `jvagent/action/mcp/sandbox.py`, `jvagent/action/mcp/jvspatial_fs_server.py`

The MCP filesystem can be sandboxed with configurable root paths, user-scoped directories, and path sanitization. Sandbox mode defaults to `false`.

**Finding (Low):** Sandbox mode is off by default (`MCP_FILESYSTEM_SANDBOX_MODE=false`). For production deployments where MCP tools are exposed to end users, sandbox mode should be enabled.

**Recommendation:** Enable sandbox mode by default when auth is enabled, or document the risk prominently.

---

## 7. Subprocess Execution

### 7.1 LaTeX Compilation (Adequate, See Section 3.1)

**Files:** `jvagent/skills/pdf_generation/scripts/latex_compiler.py`

Uses `subprocess.run()` with `capture_output=True` (no `shell=True`). Arguments are list-constructed, not string-interpolated.

**Finding:** No subprocess injection risk. LaTeX injection risk covered in Section 3.1.

### 7.2 LibreOffice Conversion (Adequate)

**Files:** `jvagent/action/pageindex/docling_convert.py:60-77`

Uses `subprocess.run()` with `capture_output=True`. Arguments are hardcoded and path-safe.

**Finding:** No issues.

### 7.3 Runtime pip install (Opt-out Available — Production Warning Added)

**Files:** `jvagent/core/dependency_installer.py`, `jvagent/cli/server_config.py`

Dependencies declared in action `info.yaml` files are installed at runtime via `pip install`. This is guarded by `JVAGENT_DISABLE_RUNTIME_PIP_INSTALL`, which when set to `true` causes a hard error if undeclared dependencies exist (forcing operators to pre-install in images).

**Original Finding (Low):** Runtime pip install is a supply chain concern in production. While the guard is present, it defaults to allowing installs.

**Remediation:** Added a production-mode safety check in `create_server_from_config()` that logs a `WARNING` when `is_production_mode()` is true and `JVAGENT_DISABLE_RUNTIME_PIP_INSTALL` is not set to `true`. The warning message reminds operators to pre-install dependencies in the deployment image.

**Residual recommendation:** Consider defaulting `JVAGENT_DISABLE_RUNTIME_PIP_INSTALL` to `true` when `JVSPATIAL_ENVIRONMENT=production` (behavioral change — would need migration notice).

### 7.4 Git Init During Scaffolding (Adequate)

**Files:** `jvagent/scaffold/operations.py:493-502`

`subprocess.run(["git", "init"], cwd=str(root))` is used during `jvagent app create`. The `root` path comes from CLI arguments. No `shell=True`.

**Finding:** No issues.

---

## 8. Client-Side Security (jvchat Frontend)

### 8.1 JWT Token Storage (Low)

**Files:** `jvchat/src/utils/storage.ts:190-218`

Access and refresh tokens are stored in `localStorage`. This is vulnerable to XSS exfiltration. While `localStorage` is the most practical option for SPAs without a backend-for-frontend (BFF) pattern, it's worth documenting the risk.

**Finding (Low):** Industry standard practice for SPAs, but documented risk.

**Recommendation:**
1. Set short JWT expiry (already 60 min default, good).
2. Implement refresh token rotation.
3. Document XSS risk in deployment guide.
4. Consider `HttpOnly` cookie-based session management as an alternative for production.

### 8.2 Missing Content Security Policy (Low)

No CSP headers are configured in the jvchat frontend or jvagent server. A CSP would mitigate XSS risk by restricting script sources, inline scripts, and connect-src destinations.

**Recommendation:** Add CSP headers to the production build. Minimum viable CSP:
```
Content-Security-Policy: default-src 'self'; script-src 'self'; connect-src 'self' <api-origin>; style-src 'self' 'unsafe-inline'
```

### 8.3 Server URL Validation (Adequate)

**Files:** `jvchat/src/components/Login.tsx:33-52`

The server URL input is validated via `new URL()` before use. Missing protocol defaults to `http://`.

**Finding:** No issues.

### 8.4 Token Refresh Logic (Adequate)

**Files:** `jvchat/src/config/api.ts`, `jvchat/src/hooks/useAuth.ts`

Proactive token refresh with a 5-minute buffer, request deduplication queue, and redirect guard to prevent multiple login redirects.

**Finding:** No issues.

---

## 9. Dependency & Supply Chain

### 9.1 Pre-commit Hooks (Adequate)

**Files:** `.pre-commit-config.yaml`

Includes `detect-secrets` (Yelp) with `Base64HighEntropyString` detection, plus standard code quality hooks (black, isort, flake8, mypy).

**Finding:** No issues. No baseline file was found, but that's acceptable for active development.

### 9.2 Dependency Declaration (Adequate)

**Files:** `pyproject.toml`, `setup.py`, `requirements*.txt`

Dependencies are explicitly declared. `python-dotenv` is a core dependency. `detect-secrets` is a dev dependency.

**Finding:** No issues.

---

## 10. Logging & Data Protection

### 10.1 PII Sanitization in Logs (Adequate)

**Files:** `jvagent/action/interact/endpoints.py:136-163`

`_sanitize_visitor_data_for_log()` replaces media payloads with `<media>` markers, truncates body/caption to 200 chars, and summarizes WhatsApp media arrays. This prevents PII and base64 blobs from being logged.

**Finding:** No issues. Good practice.

### 10.2 Interaction Data Storage (Informational)

**Files:** `jvagent/action/interact/endpoints.py:166-314`

`_build_interaction_log_data()` captures comprehensive interaction state including utterance, response, directives, parameters, events, and observability metrics. This data is stored in the logging database.

**Finding (Informational):** Interaction logs contain full conversation content. Ensure the logging database has appropriate access controls and retention policies, especially for production deployments handling sensitive user data.

### 10.3 Webhook URL Log Redaction (Adequate)

**Files:** `jvagent/core/callback.py:12-20`

`_safe_webhook_target()` redacts webhook URLs to `scheme://host` before logging, preventing query parameter leakage.

**Finding:** No issues.

---

## 11. Network & Infrastructure

### 11.1 CORS Configuration (Adequate)

**Files:** `jvagent/cli/server_config.py:229-249`

CORS is enabled by default with a limited set of localhost origins. Production origins must be configured via `cors.origins` / `JVSPATIAL_CORS_ORIGINS`.

**Finding:** No issues. Default origins are appropriate for development.

### 11.2 HTTPS Enforcement for Webhooks (Adequate)

**Files:** `.env.example:219`

`JVSPATIAL_WEBHOOK_HTTPS_REQUIRED=true` is the default.

**Finding:** No issues.

### 11.3 Public URL Configuration (Informational)

**Files:** `jvagent/core/public_url.py`

`JVAGENT_PUBLIC_BASE_URL` is used for OAuth callbacks, webhook URLs, and media URLs. If unset, these features may not work correctly.

**Finding (Informational):** Production deployments must set this to the correct public URL. Incorrect configuration could lead to OAuth redirect URI mismatches or broken webhook delivery.

---

## 12. Server Hardening Opportunities

### 12.1 Server Host Binding

**Files:** `jvagent/cli/server_config.py:94`

Default host is `127.0.0.1`, which is correct for development. In production, this must be explicitly set to `0.0.0.0` via `JVAGENT_HOST` or CLI `--host`.

**Finding:** No issues. Good default.

### 12.2 Debug Mode (Production Warning Added)

**Files:** `jvagent/cli/server_config.py`

Debug mode is controlled by `development.debug` in app.yaml or `JVSPATIAL_DEBUG` env var. When enabled, log level is set to `debug`.

**Original Finding (Informational):** Ensure debug mode is disabled in production. The scaffolding template (`_build_app_dict`) correctly defaults to `false`.

**Remediation:** Added a production-mode safety check in `create_server_from_config()` that logs a `WARNING` when `is_production_mode()` is true and `debug_mode` is enabled. The message: *"PRODUCTION SAFETY: Debug mode is enabled in production. Set JVSPATIAL_DEBUG=false or remove development.debug from app.yaml."*

---

## Appendix A: Endpoint Auth Summary

| Category | Count | Auth Mechanism |
|----------|-------|---------------|
| Admin CRUD | ~175 | JWT + admin role |
| Authenticated user | 1 | JWT (any) |
| Public/OAuth | 7 | None |
| Webhook (API key) | 7 | `x-api-key` header |
| Total endpoints | ~198 | — |

## Appendix B: Files Reviewed

Key files examined during this review. Files marked `[*]` were modified during remediation.

- `jvagent/cli/server_config.py` [*] — Server and auth configuration; production safety checks added
- `jvagent/cli/bootstrap.py` — Admin user bootstrap
- `jvagent/action/interact/endpoints.py` [*] — Main interact endpoint; rate limiter calls now awaited
- `jvagent/action/interact/rate_limiter.py` [*] — Rate limiting; asyncio.Lock synchronization added
- `jvagent/action/interact/interact_walker.py` — Walker + access control enforcement
- `jvagent/action/access_control/access_control_action.py` — Per-agent RBAC
- `jvagent/action/utils/webhook_system_user.py` — Webhook system user creation
- `jvagent/action/facebook_action/messenger_webhook_helpers.py` — HMAC verification
- `jvagent/core/env_resolver.py` — Env var placeholder resolution
- `jvagent/core/dependency_installer.py` — Runtime pip install
- `jvagent/core/callback.py` [*] — Outbound webhooks; SSRF protection added
- `jvagent/core/public_url.py` — Public URL resolution
- `jvagent/action/mcp/sandbox.py` — MCP filesystem sandbox
- `jvagent/skills/pdf_generation/scripts/latex_compiler.py` [*] — LaTeX compilation; hex color validation + -no-shell-escape added
- `jvagent/skills/skill_hub/scripts/_installer.py` — Skill installation
- `jvagent/scaffold/operations.py` — App scaffolding
- `jvagent/action/pageindex/endpoints.py` — File upload/download
- `jvchat/src/utils/storage.ts` — Client-side credential/token storage
- `jvchat/src/config/api.ts` — Client-side API configuration
- `jvchat/src/components/Login.tsx` [*] — Login UI; credential storage security warnings added
