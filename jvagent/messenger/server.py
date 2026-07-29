"""Static file server for the embeddable jvmessenger popup chat (``jvagent messenger``).

Serves the pre-built messenger bundle from ``jvagent/messenger/dist/`` on its own port.
Stdlib only; no runtime Node dependency. Two artifacts are served:

* ``loader.js`` — a tiny framework-less script the customer embeds with a single
  ``<script>`` tag on their site. It injects a launcher button and, on open, the
  chat ``<iframe>``.
* ``app.html`` + hashed JS/CSS — the React chat app that runs *inside* the iframe.

Unlike the standalone jvchat SPA (``jvagent/webui/server.py``), this server MUST
allow its pages to be framed by third-party customer sites. It therefore does
**not** send ``X-Frame-Options: DENY``; instead it sends a configurable
``Content-Security-Policy: frame-ancestors`` allowlist. ``loader.js`` is served
with permissive CORS so it can be fetched cross-origin from any host page.

All messenger configuration (agent URL, agent id, theme, avatar, feature toggles)
is supplied by the host page via the loader's ``data-*`` attributes and passed
to the iframe over an origin-checked ``postMessage`` handshake — never via the
URL — so there is no runtime config injection here.

Sandbox mode (``--sandbox``):
When ``sandbox_mode=True`` the server also serves ``GET /`` and ``GET /sandbox``
as a self-contained developer sandbox page. The sandbox page lets you log in with
username + password, fetches the agent list from the running jvagent server, and
shows an agent-switcher host bar so you can try any agent without touching the URL.
The sandbox page is **not** embeddable and is intended for local development only.
"""

from __future__ import annotations

import html as _html
import logging
import mimetypes
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default frame-ancestors allowlist. ``*`` permits embedding on any site, which
# is convenient for local development but should be tightened to the customer's
# explicit origins in production (see docs/jvmessenger.md).
DEFAULT_FRAME_ANCESTORS = "*"


def dist_dir() -> Path:
    """Absolute path to the bundled jvmessenger ``dist/`` directory."""
    return Path(__file__).resolve().parent / "dist"


def is_built() -> bool:
    """True when the messenger has been built/bundled (``dist/loader.js`` exists)."""
    return (dist_dir() / "loader.js").is_file() and (dist_dir() / "app.html").is_file()


def _sandbox_html(agent_url: str, sandbox_origin: str) -> str:
    """Return a self-contained developer sandbox page as a UTF-8 HTML string.

    The page:
    - Shows a login form (host URL + username + password).
    - On successful login stores the JWT in ``sessionStorage`` and fetches the
      agent list from the jvagent server.
    - Renders a host-bar with agent pills; clicking one injects ``loader.js``
      with the selected agent into the fake customer body below.
    - All credentials stay in ``sessionStorage`` (cleared on tab close).
    - The page sends ``X-Frame-Options: DENY``; it is never embeddable.

    Args:
        agent_url: jvagent server URL pre-filled into the host-URL field.
        sandbox_origin: ``scheme://host:port`` of *this* static server, used to
            construct the ``loader.js`` src so the iframe postMessage handshake
            resolves to the correct origin.
    """
    safe_agent_url = _html.escape(agent_url, quote=True)
    safe_origin = _html.escape(sandbox_origin, quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>jvmessenger sandbox</title>
  <style>
    :root {{
      --bg: #f0f2f7;
      --ink: #1a1f2e;
      --muted: #5c6578;
      --card: #ffffff;
      --line: #d8dde8;
      --accent: #1f4b99;
      --accent-hover: #173a7a;
      --danger: #9b1c1c;
      --success: #166534;
      --pill-bg: #e8edf8;
      --pill-active-bg: #1f4b99;
      --pill-active-ink: #ffffff;
      --pill-disabled-opacity: 0.45;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: "Segoe UI", ui-sans-serif, system-ui, sans-serif;
      font-size: 14px;
      color: var(--ink);
      background: var(--bg);
      min-height: 100vh;
    }}

    /* ── login panel ── */
    #login-panel {{
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 2rem;
    }}
    .login-card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 2rem;
      width: 100%;
      max-width: 420px;
    }}
    .login-card h1 {{
      font-size: 1.35rem;
      letter-spacing: -0.02em;
      margin-bottom: 0.35rem;
    }}
    .login-card .sub {{
      color: var(--muted);
      margin-bottom: 1.5rem;
      font-size: 0.88rem;
    }}
    .field {{ margin-bottom: 1rem; }}
    .field label {{
      display: block;
      font-size: 0.8rem;
      font-weight: 600;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.04em;
      margin-bottom: 0.3rem;
    }}
    .field input {{
      width: 100%;
      padding: 0.5rem 0.65rem;
      border: 1px solid var(--line);
      border-radius: 7px;
      font-size: 0.95rem;
      color: var(--ink);
      background: var(--bg);
      outline: none;
      transition: border-color 0.15s;
    }}
    .field input:focus {{ border-color: var(--accent); }}
    .login-btn {{
      width: 100%;
      padding: 0.6rem;
      background: var(--accent);
      color: #fff;
      border: none;
      border-radius: 7px;
      font-size: 0.95rem;
      font-weight: 600;
      cursor: pointer;
      margin-top: 0.5rem;
      transition: background 0.15s;
    }}
    .login-btn:hover {{ background: var(--accent-hover); }}
    .login-btn:disabled {{ opacity: 0.6; cursor: not-allowed; }}
    #login-error {{
      margin-top: 0.75rem;
      color: var(--danger);
      font-size: 0.88rem;
      display: none;
    }}
    .sandbox-badge {{
      display: inline-block;
      background: var(--pill-bg);
      color: var(--accent);
      font-size: 0.72rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      padding: 0.15rem 0.55rem;
      border-radius: 20px;
      margin-bottom: 0.9rem;
    }}

    /* ── host bar ── */
    #host-bar {{
      display: none;
      background: var(--card);
      border-bottom: 1px solid var(--line);
      padding: 0.55rem 1rem;
      position: sticky;
      top: 0;
      z-index: 100;
    }}
    #host-bar.visible {{ display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; }}
    .bar-label {{
      font-size: 0.78rem;
      font-weight: 600;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      flex-shrink: 0;
    }}
    #agent-pills {{ display: flex; gap: 0.4rem; flex-wrap: wrap; flex: 1; }}
    .agent-pill {{
      background: var(--pill-bg);
      color: var(--ink);
      border: none;
      border-radius: 20px;
      padding: 0.25rem 0.75rem;
      font-size: 0.82rem;
      font-weight: 500;
      cursor: pointer;
      transition: background 0.15s, color 0.15s;
    }}
    .agent-pill:hover {{ background: #d4dff5; }}
    .agent-pill.active {{
      background: var(--pill-active-bg);
      color: var(--pill-active-ink);
    }}
    .agent-pill:disabled {{
      opacity: var(--pill-disabled-opacity);
      cursor: not-allowed;
    }}
    #bar-meta {{
      margin-left: auto;
      display: flex;
      align-items: center;
      gap: 0.5rem;
      flex-shrink: 0;
    }}
    .server-badge {{
      font-size: 0.75rem;
      color: var(--muted);
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    }}
    .logout-btn {{
      background: none;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0.2rem 0.6rem;
      font-size: 0.78rem;
      color: var(--muted);
      cursor: pointer;
      transition: border-color 0.15s, color 0.15s;
    }}
    .logout-btn:hover {{ border-color: var(--accent); color: var(--accent); }}
    #bar-status {{
      font-size: 0.75rem;
      color: var(--danger);
      display: none;
    }}

    /* ── fake customer body ── */
    #customer-body {{
      display: none;
      max-width: 720px;
      margin: 0 auto;
      padding: 4rem 1.5rem 8rem;
    }}
    #customer-body.visible {{ display: block; }}
    #customer-body h1 {{
      font-size: clamp(1.75rem, 4vw, 2.5rem);
      letter-spacing: -0.03em;
      margin-bottom: 0.6rem;
    }}
    #customer-body p {{ color: var(--muted); line-height: 1.55; max-width: 38rem; }}
    .hint-card {{
      margin-top: 1.5rem;
      padding: 1rem 1.1rem;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 10px;
      font-size: 0.88rem;
    }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.85em; word-break: break-all; }}
    #hint-text {{ color: var(--muted); }}
  </style>
</head>
<body>

<!-- ── login panel ── -->
<div id="login-panel">
  <div class="login-card">
    <div class="sandbox-badge">sandbox</div>
    <h1>jvmessenger sandbox</h1>
    <p class="sub">Log in to browse agents on a running jvagent instance.</p>
    <div class="field">
      <label for="f-url">Agent server URL</label>
      <input id="f-url" type="url" value="{safe_agent_url}" placeholder="http://127.0.0.1:8000" autocomplete="off" />
    </div>
    <div class="field">
      <label for="f-user">Username</label>
      <input id="f-user" type="text" placeholder="admin" autocomplete="username" />
    </div>
    <div class="field">
      <label for="f-pass">Password</label>
      <input id="f-pass" type="password" placeholder="" autocomplete="current-password" />
    </div>
    <button class="login-btn" id="login-btn" type="button">Sign in</button>
    <div id="login-error"></div>
  </div>
</div>

<!-- ── host bar ── -->
<div id="host-bar">
  <span class="bar-label">agent</span>
  <div id="agent-pills"></div>
  <div id="bar-meta">
    <span class="server-badge" id="server-badge"></span>
    <span id="bar-status"></span>
    <button class="logout-btn" id="logout-btn" type="button">Logout</button>
  </div>
</div>

<!-- ── fake customer body ── -->
<div id="customer-body">
  <h1>Acme support desk</h1>
  <p>Stand-in customer site for the jvmessenger sandbox.
     The chat bubble (bottom-right) is injected by <span class="mono">loader.js</span> —
     open it to talk to the selected agent.</p>
  <div class="hint-card">
    <div id="hint-text">Select an agent in the bar above to start.</div>
  </div>
</div>

<script>
(function () {{
  'use strict';

  var SANDBOX_ORIGIN = '{safe_origin}';
  var SESSION_KEY = 'jvmessenger_sandbox_token';
  var URL_KEY = 'jvmessenger_sandbox_url';

  var loginPanel = document.getElementById('login-panel');
  var hostBar = document.getElementById('host-bar');
  var customerBody = document.getElementById('customer-body');
  var loginBtn = document.getElementById('login-btn');
  var logoutBtn = document.getElementById('logout-btn');
  var loginError = document.getElementById('login-error');
  var agentPillsEl = document.getElementById('agent-pills');
  var serverBadge = document.getElementById('server-badge');
  var barStatus = document.getElementById('bar-status');
  var hintText = document.getElementById('hint-text');

  var currentAgentId = null;
  var currentAgentName = null;

  // ── helpers ──────────────────────────────────────────────────────────────

  function getToken() {{ return sessionStorage.getItem(SESSION_KEY); }}
  function getUrl() {{ return sessionStorage.getItem(URL_KEY); }}

  function setError(msg) {{
    loginError.textContent = msg;
    loginError.style.display = msg ? 'block' : 'none';
  }}

  function setBarStatus(msg) {{
    barStatus.textContent = msg;
    barStatus.style.display = msg ? 'block' : 'none';
  }}

  function normalizeUrl(u) {{ return (u || '').replace(/\\/+$/, ''); }}

  // ── auth ─────────────────────────────────────────────────────────────────

  async function login() {{
    var url = normalizeUrl(document.getElementById('f-url').value.trim());
    var username = document.getElementById('f-user').value.trim();
    var password = document.getElementById('f-pass').value;
    if (!url || !username || !password) {{
      setError('All fields are required.');
      return;
    }}
    loginBtn.disabled = true;
    setError('');
    try {{
      var token = await doLogin(url, username, password);
      sessionStorage.setItem(SESSION_KEY, token);
      sessionStorage.setItem(URL_KEY, url);
      await enterLoggedIn(url, token);
    }} catch (err) {{
      setError(err.message || 'Login failed.');
    }} finally {{
      loginBtn.disabled = false;
    }}
  }}

  async function doLogin(url, username, password) {{
    // Try /api/auth/login first, then /auth/login (same pattern as jvchat).
    var endpoints = [url + '/api/auth/login', url + '/auth/login'];
    var lastErr = null;
    for (var i = 0; i < endpoints.length; i++) {{
      try {{
        var r = await fetch(endpoints[i], {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ username: username, password: password }})
        }});
        if (r.status === 401 || r.status === 403) {{
          throw new Error('Invalid username or password.');
        }}
        if (!r.ok) {{
          throw new Error('Server error (' + r.status + ').');
        }}
        var data = await r.json();
        var tok = data.access_token || data.token;
        if (!tok) throw new Error('No token in response.');
        return tok;
      }} catch (err) {{
        lastErr = err;
        if (err.message === 'Invalid username or password.' ||
            err.message.startsWith('Server error')) {{
          throw err; // don't retry 401/5xx on second endpoint
        }}
      }}
    }}
    throw lastErr || new Error('Could not reach agent server.');
  }}

  function logout() {{
    sessionStorage.removeItem(SESSION_KEY);
    sessionStorage.removeItem(URL_KEY);
    currentAgentId = null;
    currentAgentName = null;
    removeLoader();
    hostBar.classList.remove('visible');
    customerBody.classList.remove('visible');
    loginPanel.style.display = 'flex';
  }}

  // ── agent list ────────────────────────────────────────────────────────────

  async function fetchAgents(url, token) {{
    var endpoints = [url + '/api/agents?per_page=50', url + '/agents?per_page=50'];
    for (var i = 0; i < endpoints.length; i++) {{
      try {{
        var r = await fetch(endpoints[i], {{
          headers: {{ 'Authorization': 'Bearer ' + token }}
        }});
        if (r.ok) {{
          var d = await r.json();
          return d.agents || [];
        }}
      }} catch (_) {{}}
    }}
    throw new Error('Could not fetch agent list.');
  }}

  function renderPills(agents) {{
    agentPillsEl.innerHTML = '';
    if (!agents.length) {{
      agentPillsEl.textContent = 'No agents found.';
      return;
    }}
    agents.forEach(function (a) {{
      var btn = document.createElement('button');
      btn.className = 'agent-pill';
      btn.type = 'button';
      btn.textContent = a.alias || a.name || a.id;
      btn.title = a.id;
      if (!a.enabled) {{
        btn.disabled = true;
        btn.title = a.id + ' (disabled)';
      }} else {{
        btn.addEventListener('click', function () {{ selectAgent(a); }});
      }}
      agentPillsEl.appendChild(btn);
    }});
  }}

  // ── loader injection ──────────────────────────────────────────────────────

  function removeLoader() {{
    var old = document.getElementById('jvmessenger-loader-script');
    if (old) old.remove();
    // Also remove any launcher/iframe the previous loader injected.
    var btn = document.getElementById('jvmessenger-launcher');
    if (btn) btn.remove();
    var frame = document.getElementById('jvmessenger-frame');
    if (frame) frame.remove();
  }}

  async function selectAgent(agent) {{
    var url = getUrl();
    var token = getToken();
    if (!url || !token) return;

    // Update pill active state.
    document.querySelectorAll('.agent-pill').forEach(function (p) {{
      p.classList.toggle('active', p.title === agent.id || p.title === agent.id + ' (disabled)' ? false : p.title === agent.id);
    }});
    document.querySelectorAll('.agent-pill').forEach(function (p) {{
      p.classList.toggle('active', p.title === agent.id);
    }});

    currentAgentId = agent.id;
    currentAgentName = agent.alias || agent.name || agent.id;
    setBarStatus('');
    hintText.innerHTML = 'Active agent: <span class="mono">' + escHtml(currentAgentName) + '</span> &mdash; open the chat bubble.';

    // Best-effort profile fetch for greeting / title.
    var greeting = 'Hi! How can I help?';
    var title = currentAgentName;
    try {{
      var pr = await fetch(url + '/api/agents/' + encodeURIComponent(agent.id) + '/profile');
      if (!pr.ok) pr = await fetch(url + '/agents/' + encodeURIComponent(agent.id) + '/profile');
      if (pr.ok) {{
        var pd = await pr.json();
        if (pd && pd.name) title = pd.name;
      }}
    }} catch (_) {{}}

    removeLoader();

    var s = document.createElement('script');
    s.id = 'jvmessenger-loader-script';
    s.src = SANDBOX_ORIGIN + '/loader.js' +
      '?agentId=' + encodeURIComponent(agent.id) +
      '&agentUrl=' + encodeURIComponent(url);
    s.dataset.theme = 'auto';
    s.dataset.title = title;
    s.dataset.greeting = greeting;
    s.dataset.attachments = 'true';
    s.dataset.fullscreen = 'true';
    s.dataset.showReasoning = 'false';
    document.body.appendChild(s);
  }}

  function escHtml(str) {{
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }}

  // ── flow ──────────────────────────────────────────────────────────────────

  async function enterLoggedIn(url, token) {{
    var agents;
    try {{
      agents = await fetchAgents(url, token);
    }} catch (err) {{
      setError(err.message);
      return;
    }}
    loginPanel.style.display = 'none';
    serverBadge.textContent = normalizeUrl(url).replace(/^https?:\\/\\//, '');
    renderPills(agents);
    hostBar.classList.add('visible');
    customerBody.classList.add('visible');
    hintText.textContent = 'Select an agent in the bar above to start.';
    setBarStatus('');
  }}

  // ── event wiring ──────────────────────────────────────────────────────────

  loginBtn.addEventListener('click', login);
  logoutBtn.addEventListener('click', logout);

  document.getElementById('f-pass').addEventListener('keydown', function (e) {{
    if (e.key === 'Enter') login();
  }});

  // Resume session if token still in sessionStorage (same tab, page refresh).
  var savedToken = getToken();
  var savedUrl = getUrl();
  if (savedToken && savedUrl) {{
    enterLoggedIn(savedUrl, savedToken).catch(function () {{
      // Token stale; fall through to login panel.
      sessionStorage.removeItem(SESSION_KEY);
      sessionStorage.removeItem(URL_KEY);
    }});
  }}
}})();
</script>
</body>
</html>"""  # noqa: E501


def _build_handler(
    root: Path,
    frame_ancestors: str,
    sandbox_mode: bool = False,
    agent_url: str = "http://127.0.0.1:8000",
) -> type:  # noqa: E501
    csp = f"frame-ancestors {frame_ancestors}"

    class _Handler(BaseHTTPRequestHandler):
        server_version = "jvagent-messenger/1.0"

        def _headers(
            self,
            status: int,
            content_type: str,
            length: int,
            *,
            cache: bool,
            embeddable: bool,
            cors: bool = False,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            # Hashed assets are immutable; HTML/loader must stay fresh so config
            # and framing changes propagate without a stale cache.
            self.send_header(
                "Cache-Control",
                "public, max-age=31536000, immutable" if cache else "no-store",
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            # Embeddable pages use CSP frame-ancestors (allowlist) INSTEAD of
            # X-Frame-Options: DENY — the latter would break iframe embedding.
            if embeddable:
                self.send_header("Content-Security-Policy", csp)
            else:
                self.send_header("X-Frame-Options", "DENY")
            # loader.js is fetched cross-origin from the customer's site.
            if cors:
                self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

        def _send_file(self, target: Path) -> None:
            name = target.name
            is_loader = name == "loader.js"
            is_html = target.suffix == ".html"
            # loader.js + the iframe app page must be framable and uncached;
            # fingerprinted assets under assets/ are immutable.
            embeddable = is_loader or is_html
            cache = not (is_loader or is_html)
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            data = target.read_bytes()
            self._headers(
                HTTPStatus.OK,
                ctype,
                len(data),
                cache=cache,
                embeddable=embeddable,
                cors=is_loader,
            )
            if self.command != "HEAD":
                self.wfile.write(data)

        def _send_app_index(self) -> None:
            """SPA fallback: serve app.html for unknown (client-routed) paths."""
            target = root / "app.html"
            if not target.is_file():
                self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Messenger not built")
                return
            self._send_file(target)

        def _resolve(self, path: str) -> Optional[Path]:
            rel = path.split("?", 1)[0].split("#", 1)[0].lstrip("/")
            if not rel:
                return None
            target = (root / rel).resolve()
            # Path-traversal guard: must stay under root.
            if target != root and root not in target.parents:
                return None
            return target if target.is_file() else None

        def _send_demo(self) -> None:
            """Host-page demo that embeds loader.js (not the iframe app itself)."""
            target = root / "demo.html"
            if not target.is_file():
                self.send_error(
                    HTTPStatus.NOT_FOUND,
                    "demo.html missing - rebuild with scripts/build_jvmessenger.py",
                )
                return
            self._send_file(target)

        def _send_sandbox(self) -> None:
            """Developer sandbox page with login form + agent-switcher host bar."""
            host_header = self.headers.get("Host", "127.0.0.1:3100")
            scheme = "http"
            sandbox_origin = f"{scheme}://{host_header}"
            body = _sandbox_html(agent_url, sandbox_origin).encode("utf-8")
            self._headers(
                HTTPStatus.OK,
                "text/html; charset=utf-8",
                len(body),
                cache=False,
                embeddable=False,
            )
            if self.command != "HEAD":
                self.wfile.write(body)

        def _serve(self) -> None:
            path = self.path.split("?", 1)[0]
            # `/` and `/demo` are the customer-site stand-in. Opening `/app.html`
            # alone shows "Connecting…" forever — config arrives via postMessage
            # from the loader on a host page, never from the URL.
            if sandbox_mode and path in ("/", "/sandbox"):
                self._send_sandbox()
                return
            if path in ("/", "/demo", "/demo.html"):
                self._send_demo()
                return
            if path == "/app.html":
                self._send_app_index()
                return
            target = self._resolve(self.path)
            if target is None:
                self._send_app_index()
                return
            self._send_file(target)

        def do_GET(self) -> None:  # noqa: N802
            self._serve()

        def do_HEAD(self) -> None:  # noqa: N802
            self._serve()

        def log_message(self, fmt: str, *args) -> None:
            logger.debug("jvmessenger %s - %s", self.address_string(), fmt % args)

    return _Handler


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 3100,
    frame_ancestors: str = DEFAULT_FRAME_ANCESTORS,
    open_browser: bool = True,
    sandbox_mode: bool = False,
    agent_url: str = "http://127.0.0.1:8000",
) -> None:
    """Serve the bundled jvmessenger assets until interrupted.

    Args:
        host: Bind address (default localhost — bind ``0.0.0.0`` deliberately).
        port: Port to listen on (default 3100, distinct from ``jvagent chat``).
        frame_ancestors: CSP ``frame-ancestors`` allowlist controlling which host
            origins may embed the messenger iframe. ``*`` (default) allows any; set
            to the customer's explicit origins in production.
        open_browser: Open the default browser at the served URL on startup.
        sandbox_mode: When ``True``, serve the developer sandbox page at ``GET /``
            and ``GET /sandbox`` instead of ``demo.html``. The sandbox page lets
            developers log in, browse agents, and switch agents via a host bar.
            Intended for local development only; do not expose publicly.
        agent_url: jvagent server URL pre-filled in the sandbox login form.
            Only used when ``sandbox_mode=True``.

    Raises:
        FileNotFoundError: If the messenger has not been built (no bundled ``dist/``).
    """
    root = dist_dir()
    if not is_built():
        raise FileNotFoundError(
            "jvmessenger is not bundled (no jvagent/messenger/dist/). This wheel was "
            "built without the messenger, or you are in a source checkout — build "
            "it with `python scripts/build_jvmessenger.py`."
        )

    handler = _build_handler(
        root, frame_ancestors, sandbox_mode=sandbox_mode, agent_url=agent_url
    )
    httpd = ThreadingHTTPServer((host, port), handler)
    actual_port = httpd.server_address[1]
    url = f"http://{host}:{actual_port}"
    logger.info("jvmessenger serving at %s", url)
    print(f"jvmessenger: {url}  (Ctrl+C to stop)")
    if sandbox_mode:
        print(f"  → sandbox page: {url}/  (login → agent picker → embed)")
        print(f"  → agent server: {agent_url}")
    else:
        print(f"  → demo host page: {url}/  (or {url}/demo)")
    print(f"  → loader: {url}/loader.js")
    print(f"  → iframe app: {url}/app.html")
    print(f"  → frame-ancestors: {frame_ancestors}")

    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping jvmessenger.")
    finally:
        httpd.shutdown()
        httpd.server_close()
