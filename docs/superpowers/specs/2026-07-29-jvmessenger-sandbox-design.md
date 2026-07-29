# jvmessenger sandbox — design spec

**Date:** 2026-07-29  
**Status:** approved → implementing

---

## Problem

`jvagent messenger` serves `demo.html` with a hardcoded `agentId`. After `--purge` the id
is stale and must be hand-edited in the query string. No in-browser agent picker exists.
Developers cannot quickly stand up a live jvmessenger UI against a running instance without
manual URL surgery.

---

## Goal

One command stands up a jvmessenger sandbox UI that:
1. Shows a login form (host URL + username + password, mirroring jvchat).
2. Authenticates against the running jvagent server (`POST /auth/login` or `/api/auth/login`).
3. Fetches the agent list (`GET /api/agents`) using the JWT.
4. Presents a host-bar agent picker at the top of a fake customer page.
5. Embeds `loader.js` with the selected agent's id + server URL on selection.
6. Allows switching agents by re-selecting in the host bar (reloads the script tag).

---

## Chosen approach — `--sandbox` flag on `jvagent messenger`

Command:

```
jvagent messenger --sandbox --url http://127.0.0.1:8000
```

- `--sandbox` activates sandbox mode; without it the existing `demo.html` flow is unchanged.
- `--url` is the jvagent server URL (defaults to `http://127.0.0.1:8000` when `--sandbox`).
- `--port`, `--host`, `--frame-ancestors`, `--no-browser` flags unchanged.

No new CLI subcommand; fits existing dispatch and handler convention.

---

## Implementation scope

### 1. `jvagent/messenger/server.py` — sandbox HTML generation

Add `_sandbox_html(agent_url: str) -> str` — returns a self-contained HTML page as a
Python string. No Node rebuild required. The page:

- Is served by the existing `ThreadingHTTPServer` at `GET /sandbox` (and `GET /` when
  sandbox mode is active).
- Is **not** embeddable (sends `X-Frame-Options: DENY` like the chat server).
- Has `Cache-Control: no-store`.

**Page sections (pure HTML/JS, no framework):**

1. **Login panel** (shown until JWT obtained):
   - Fields: Agent server URL (pre-filled from `--url`), Username, Password.
   - On submit: `POST {url}/api/auth/login` (fallback `/auth/login`), stores JWT in
     `sessionStorage["jvmessenger_sandbox_token"]`.
   - On 401: show error inline; do not redirect.

2. **Host bar** (shown after login):
   - `GET {url}/api/agents?per_page=50` with `Authorization: Bearer {token}`.
   - Renders agent name pills. Disabled agents shown greyed-out, not clickable.
   - "Switch" replaces the current `<script>` tag (remove old, insert new) then
     re-opens the messenger panel if it was open.
   - Logout button clears `sessionStorage` and returns to login panel.

3. **Fake customer body** (below host bar, always visible after login):
   - Static "Acme support desk" copy (matches existing `demo.html` tone).
   - Messenger bubble injected via `loader.js` with selected agent params.

**Messenger config injected into `loader.js` src:**
- `agentId`, `agentUrl`, `theme=auto`, `attachments=true`, `fullscreen=true`
- `data-greeting` and `data-title` sourced from agent's profile fetch
  (`GET {url}/api/agents/{id}/profile`) — best-effort; falls back to agent name.

**Security notes:**
- Sandbox HTML is only served when `--sandbox` is active.
- All credentials stay in `sessionStorage` (not `localStorage`); cleared on tab close.
- JWT never touches `document.cookie`.
- No CORS header added to sandbox route (same origin as loader.js).
- `frame-ancestors` CSP still applies to `loader.js` and `app.html` unchanged.

### 2. `jvagent/messenger/server.py` — serve integration

In `_build_handler`:
- Accept new `sandbox_mode: bool` and `agent_url: str` params.
- When `sandbox_mode=True`, route `GET /` and `GET /sandbox` to `_send_sandbox()`.
- `_send_sandbox()` serializes `_sandbox_html(agent_url)` to bytes, sends with
  `no-store` cache and `X-Frame-Options: DENY`.
- All other routes (`loader.js`, `assets/`, `app.html`) unchanged.

In `serve()`:
- Add `sandbox_mode: bool = False` and `agent_url: str = "http://127.0.0.1:8000"` params.
- Pass through to `_build_handler`.

### 3. `jvagent/cli/messenger.py` — CLI surface

Add to `handle_messenger_command`:

```
--sandbox           Serve the sandbox agent-switcher page instead of demo.html.
--url URL           jvagent server the sandbox authenticates against.
                    Default: http://127.0.0.1:8000. Implies --sandbox intent
                    but does not activate sandbox alone.
```

When `--sandbox` is set, call `serve(..., sandbox_mode=True, agent_url=ns.url)`.  
When `--sandbox` is not set, existing `serve()` call unchanged (no regression).

### 4. `tests/messenger/test_messenger_server.py` — tests

Add:
- `test_sandbox_route_returns_html` — `GET /` in sandbox mode returns 200 HTML containing
  `jvmessenger-sandbox`.
- `test_sandbox_not_served_without_flag` — `GET /sandbox` without sandbox mode returns the
  normal demo or 404 (not the sandbox page).
- `test_sandbox_html_contains_agent_url` — `_sandbox_html("http://example.com:9000")` output
  contains the URL string.
- `test_demo_route_unaffected` — existing demo route test still passes.

### 5. `docs/jvmessenger.md` — documentation

Add a "Sandbox / dev mode" section covering:
- Command syntax.
- Login flow.
- Agent switching.
- Security posture (dev-only, sessionStorage, no production use).

---

## File change summary

| File | Change |
|---|---|
| `jvagent/messenger/server.py` | Add `_sandbox_html()`, `_send_sandbox()`, `sandbox_mode` + `agent_url` params to `_build_handler` / `serve()` |
| `jvagent/cli/messenger.py` | Add `--sandbox` + `--url` args; pass to `serve()` |
| `tests/messenger/test_messenger_server.py` | 4 new tests |
| `docs/jvmessenger.md` | New "Sandbox / dev mode" section |

No changes to: jvspatial, jvmessenger npm source, DISPATCH table, other CLI files.

---

## Non-goals

- No production deployment of the sandbox page.
- No `--with-agent` flag (attach-only; agent must already be running).
- No persistence of login state across server restarts.
- No sandbox SPA rebuild step.

---

## Open questions (resolved)

| Question | Decision |
|---|---|
| Process model | Attach-only (agent already running) |
| In-page switcher | Host bar (top of fake customer page) |
| Agent discovery | Browser fetch with JWT from login form |
| Auth | Username + password → JWT, same pattern as jvchat |
| CLI surface | `--sandbox` flag on existing `messenger` subcommand |
