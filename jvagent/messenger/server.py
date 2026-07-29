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
email + password, fetches the agent list from the running jvagent server, and
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
    - Shows a login form (host URL + email + password).
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
    #agent-select {{
      flex: 1;
      min-width: 12rem;
      max-width: 28rem;
      padding: 0.35rem 0.6rem;
      border: 1px solid var(--line);
      border-radius: 7px;
      font-size: 0.88rem;
      color: var(--ink);
      background: var(--bg);
      outline: none;
      cursor: pointer;
    }}
    #agent-select:focus {{ border-color: var(--accent); }}
    #agent-select:disabled {{ opacity: 0.6; cursor: not-allowed; }}
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

    /* ── config panel ── */
    #config-panel {{
      display: none;
      background: var(--card);
      border-bottom: 1px solid var(--line);
      padding: 1rem 1.25rem 1.25rem;
      max-height: min(70vh, 560px);
      overflow-y: auto;
    }}
    #config-panel.visible {{ display: block; }}
    #config-panel h2 {{
      font-size: 0.95rem;
      margin: 0 0 0.75rem;
      letter-spacing: -0.02em;
    }}
    .cfg-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
      gap: 0.75rem 1rem;
    }}
    .cfg-field {{ display: flex; flex-direction: column; gap: 0.25rem; }}
    .cfg-field.wide {{ grid-column: 1 / -1; }}
    .cfg-field label {{
      font-size: 0.72rem;
      font-weight: 600;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .cfg-field input[type="text"],
    .cfg-field input[type="url"],
    .cfg-field input[type="number"],
    .cfg-field select,
    .cfg-field textarea {{
      padding: 0.4rem 0.55rem;
      border: 1px solid var(--line);
      border-radius: 6px;
      font-size: 0.88rem;
      color: var(--ink);
      background: var(--bg);
      font-family: inherit;
      outline: none;
    }}
    .cfg-field textarea {{ min-height: 3.2rem; resize: vertical; }}
    .cfg-field input:focus,
    .cfg-field select:focus,
    .cfg-field textarea:focus {{ border-color: var(--accent); }}
    .cfg-toggles {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.55rem 1.1rem;
      grid-column: 1 / -1;
      padding: 0.35rem 0;
    }}
    .cfg-toggle {{
      display: flex;
      align-items: center;
      gap: 0.4rem;
      font-size: 0.88rem;
      cursor: pointer;
      user-select: none;
    }}
    .cfg-toggle input {{ accent-color: var(--accent); }}
    .cfg-actions {{
      display: flex;
      gap: 0.5rem;
      margin-top: 1rem;
      align-items: center;
      flex-wrap: wrap;
    }}
    .cfg-apply {{
      background: var(--accent);
      color: #fff;
      border: none;
      border-radius: 6px;
      padding: 0.4rem 0.9rem;
      font-size: 0.88rem;
      font-weight: 600;
      cursor: pointer;
    }}
    .cfg-apply:hover {{ background: var(--accent-hover); }}
    .cfg-reset {{
      background: none;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0.4rem 0.75rem;
      font-size: 0.82rem;
      color: var(--muted);
      cursor: pointer;
    }}
    .cfg-reset:hover {{ border-color: var(--accent); color: var(--accent); }}
    .cfg-hint {{ font-size: 0.78rem; color: var(--muted); }}
    #config-toggle.active {{
      border-color: var(--accent);
      color: var(--accent);
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
      <label for="f-email">Email</label>
      <input id="f-email" type="email" placeholder="admin@example.com" autocomplete="email" />
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
  <select id="agent-select" aria-label="Select agent">
    <option value="">Select an agent…</option>
  </select>
  <div id="bar-meta">
    <span class="server-badge" id="server-badge"></span>
    <span id="bar-status"></span>
    <button class="logout-btn" id="config-toggle" type="button">Config</button>
    <button class="logout-btn" id="logout-btn" type="button">Logout</button>
  </div>
</div>

<!-- ── messenger config panel ── -->
<div id="config-panel">
  <h2>Messenger embed config</h2>
  <div class="cfg-grid">
    <div class="cfg-field">
      <label for="cfg-title">Title</label>
      <input id="cfg-title" type="text" placeholder="Chat (or agent name)" />
    </div>
    <div class="cfg-field">
      <label for="cfg-description">Description</label>
      <input id="cfg-description" type="text" placeholder="Shown under title" />
    </div>
    <div class="cfg-field">
      <label for="cfg-theme">Theme</label>
      <select id="cfg-theme">
        <option value="auto">auto</option>
        <option value="light">light</option>
        <option value="dark">dark</option>
      </select>
    </div>
    <div class="cfg-field">
      <label for="cfg-avatar">Avatar URL</label>
      <input id="cfg-avatar" type="url" placeholder="https://…" />
    </div>
    <div class="cfg-field wide">
      <label for="cfg-greeting">Greeting</label>
      <input id="cfg-greeting" type="text" placeholder="Hi! How can I help?" />
    </div>
    <div class="cfg-field wide">
      <label for="cfg-quick-replies">Quick replies (one per line)</label>
      <textarea id="cfg-quick-replies" placeholder="What can you do?&#10;Help me get started"></textarea>
    </div>
    <div class="cfg-field wide">
      <label for="cfg-notice">Notice banner</label>
      <input id="cfg-notice" type="text" placeholder="Responses may be slower…" />
    </div>
    <div class="cfg-field wide">
      <label for="cfg-consent">Consent text (empty = off)</label>
      <textarea id="cfg-consent" placeholder="We may use this chat to improve…"></textarea>
    </div>
    <div class="cfg-field wide">
      <label for="cfg-teaser">Teaser text (empty = off)</label>
      <input id="cfg-teaser" type="text" placeholder="Need help? Ask me anything." />
    </div>
    <div class="cfg-field">
      <label for="cfg-teaser-delay">Teaser delay (ms)</label>
      <input id="cfg-teaser-delay" type="number" min="0" step="100" />
    </div>
    <div class="cfg-field">
      <label for="cfg-teaser-cooldown">Teaser cooldown (days)</label>
      <input id="cfg-teaser-cooldown" type="number" min="0" step="1" />
    </div>
    <div class="cfg-field">
      <label for="cfg-teaser-triggers">Teaser triggers (CSV)</label>
      <input id="cfg-teaser-triggers" type="text" placeholder="delay,scroll,exit,idle" />
    </div>
    <div class="cfg-field">
      <label for="cfg-teaser-scroll">Teaser scroll %</label>
      <input id="cfg-teaser-scroll" type="number" min="0" max="100" step="1" />
    </div>
    <div class="cfg-toggles">
      <label class="cfg-toggle"><input id="cfg-attachments" type="checkbox" /> Attachments</label>
      <label class="cfg-toggle"><input id="cfg-voice" type="checkbox" /> Voice</label>
      <label class="cfg-toggle"><input id="cfg-fullscreen" type="checkbox" /> Fullscreen</label>
      <label class="cfg-toggle"><input id="cfg-sound" type="checkbox" /> Sound</label>
      <label class="cfg-toggle"><input id="cfg-show-reasoning" type="checkbox" /> Show reasoning</label>
      <label class="cfg-toggle"><input id="cfg-page-context" type="checkbox" /> Page context</label>
      <label class="cfg-toggle"><input id="cfg-proactive" type="checkbox" /> Proactive</label>
    </div>
  </div>
  <div class="cfg-actions">
    <button class="cfg-apply" id="cfg-apply" type="button">Apply &amp; reload messenger</button>
    <button class="cfg-reset" id="cfg-reset" type="button">Reset defaults</button>
    <span class="cfg-hint">Saved in sessionStorage · maps to loader data-* attrs</span>
  </div>
</div>

<!-- ── fake customer body ── -->
<div id="customer-body">
  <h1>jvmessenger sandbox</h1>
  <p>Stand-in host page for the embeddable messenger.
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
  var CONFIG_KEY = 'jvmessenger_sandbox_config';

  var DEFAULT_CONFIG = {{
    title: '',
    description: '',
    theme: 'auto',
    avatar: '',
    greeting: 'Hi! How can I help?',
    quickReplies: 'What can you do?\\nHelp me get started',
    notice: '',
    consent: '',
    teaser: '',
    teaserDelay: 4000,
    teaserCooldownDays: 7,
    teaserTriggers: 'delay',
    teaserScrollPercent: 55,
    attachments: true,
    voice: false,
    fullscreen: true,
    sound: true,
    showReasoning: false,
    pageContext: true,
    proactive: false
  }};

  var loginPanel = document.getElementById('login-panel');
  var hostBar = document.getElementById('host-bar');
  var configPanel = document.getElementById('config-panel');
  var configToggle = document.getElementById('config-toggle');
  var customerBody = document.getElementById('customer-body');
  var loginBtn = document.getElementById('login-btn');
  var logoutBtn = document.getElementById('logout-btn');
  var loginError = document.getElementById('login-error');
  var agentSelect = document.getElementById('agent-select');
  var serverBadge = document.getElementById('server-badge');
  var barStatus = document.getElementById('bar-status');
  var hintText = document.getElementById('hint-text');

  var currentAgentId = null;
  var currentAgentName = null;
  // id → {{id, name, alias}} for the dropdown change handler
  var agentById = {{}};
  var profileFallback = {{ title: '', greeting: '' }};

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

  // ── embed config ──────────────────────────────────────────────────────────

  function loadSavedConfig() {{
    try {{
      var raw = sessionStorage.getItem(CONFIG_KEY);
      if (!raw) return Object.assign({{}}, DEFAULT_CONFIG);
      return Object.assign({{}}, DEFAULT_CONFIG, JSON.parse(raw));
    }} catch (_) {{
      return Object.assign({{}}, DEFAULT_CONFIG);
    }}
  }}

  function readConfigFromForm() {{
    return {{
      title: document.getElementById('cfg-title').value.trim(),
      description: document.getElementById('cfg-description').value.trim(),
      theme: document.getElementById('cfg-theme').value || 'auto',
      avatar: document.getElementById('cfg-avatar').value.trim(),
      greeting: document.getElementById('cfg-greeting').value.trim(),
      quickReplies: document.getElementById('cfg-quick-replies').value,
      notice: document.getElementById('cfg-notice').value.trim(),
      consent: document.getElementById('cfg-consent').value.trim(),
      teaser: document.getElementById('cfg-teaser').value.trim(),
      teaserDelay: Number(document.getElementById('cfg-teaser-delay').value) || 0,
      teaserCooldownDays: Number(document.getElementById('cfg-teaser-cooldown').value) || 0,
      teaserTriggers: document.getElementById('cfg-teaser-triggers').value.trim(),
      teaserScrollPercent: Number(document.getElementById('cfg-teaser-scroll').value) || 0,
      attachments: document.getElementById('cfg-attachments').checked,
      voice: document.getElementById('cfg-voice').checked,
      fullscreen: document.getElementById('cfg-fullscreen').checked,
      sound: document.getElementById('cfg-sound').checked,
      showReasoning: document.getElementById('cfg-show-reasoning').checked,
      pageContext: document.getElementById('cfg-page-context').checked,
      proactive: document.getElementById('cfg-proactive').checked
    }};
  }}

  function writeConfigToForm(cfg) {{
    document.getElementById('cfg-title').value = cfg.title || '';
    document.getElementById('cfg-description').value = cfg.description || '';
    document.getElementById('cfg-theme').value = cfg.theme || 'auto';
    document.getElementById('cfg-avatar').value = cfg.avatar || '';
    document.getElementById('cfg-greeting').value = cfg.greeting || '';
    document.getElementById('cfg-quick-replies').value = cfg.quickReplies || '';
    document.getElementById('cfg-notice').value = cfg.notice || '';
    document.getElementById('cfg-consent').value = cfg.consent || '';
    document.getElementById('cfg-teaser').value = cfg.teaser || '';
    document.getElementById('cfg-teaser-delay').value = cfg.teaserDelay;
    document.getElementById('cfg-teaser-cooldown').value = cfg.teaserCooldownDays;
    document.getElementById('cfg-teaser-triggers').value = cfg.teaserTriggers || '';
    document.getElementById('cfg-teaser-scroll').value = cfg.teaserScrollPercent;
    document.getElementById('cfg-attachments').checked = !!cfg.attachments;
    document.getElementById('cfg-voice').checked = !!cfg.voice;
    document.getElementById('cfg-fullscreen').checked = !!cfg.fullscreen;
    document.getElementById('cfg-sound').checked = !!cfg.sound;
    document.getElementById('cfg-show-reasoning').checked = !!cfg.showReasoning;
    document.getElementById('cfg-page-context').checked = !!cfg.pageContext;
    document.getElementById('cfg-proactive').checked = !!cfg.proactive;
  }}

  function saveConfig(cfg) {{
    try {{ sessionStorage.setItem(CONFIG_KEY, JSON.stringify(cfg)); }} catch (_) {{}}
  }}

  function quickRepliesJson(raw) {{
    var lines = String(raw || '').split(/\\r?\\n/).map(function (l) {{
      return l.trim();
    }}).filter(Boolean);
    return lines.length ? JSON.stringify(lines) : '';
  }}

  function applyConfigToScript(s, cfg) {{
    var title = cfg.title || profileFallback.title || currentAgentName || 'Chat';
    var greeting = cfg.greeting || profileFallback.greeting || '';
    s.dataset.theme = cfg.theme || 'auto';
    s.dataset.title = title;
    if (cfg.description) s.dataset.description = cfg.description;
    if (greeting) s.dataset.greeting = greeting;
    if (cfg.avatar) s.dataset.avatar = cfg.avatar;
    var qr = quickRepliesJson(cfg.quickReplies);
    if (qr) s.dataset.quickReplies = qr;
    if (cfg.notice) s.dataset.notice = cfg.notice;
    if (cfg.consent) s.dataset.consent = cfg.consent;
    s.dataset.attachments = cfg.attachments ? 'true' : 'false';
    s.dataset.voice = cfg.voice ? 'true' : 'false';
    s.dataset.fullscreen = cfg.fullscreen ? 'true' : 'false';
    s.dataset.sound = cfg.sound ? 'true' : 'false';
    s.dataset.showReasoning = cfg.showReasoning ? 'true' : 'false';
    s.dataset.pageContext = cfg.pageContext ? 'true' : 'false';
    s.dataset.proactive = cfg.proactive ? 'true' : 'false';
    if (cfg.teaser) {{
      s.dataset.teaser = cfg.teaser;
      s.dataset.teaserDelay = String(cfg.teaserDelay);
      s.dataset.teaserCooldownDays = String(cfg.teaserCooldownDays);
      if (cfg.teaserTriggers) s.dataset.teaserTriggers = cfg.teaserTriggers;
      s.dataset.teaserScrollPercent = String(cfg.teaserScrollPercent);
    }}
  }}

  function reinjectMessenger() {{
    if (!currentAgentId) {{
      setBarStatus('Select an agent first.');
      return;
    }}
    var url = getUrl();
    if (!url) return;
    var cfg = readConfigFromForm();
    saveConfig(cfg);
    removeLoader();
    var s = document.createElement('script');
    s.id = 'jvmessenger-loader-script';
    s.src = SANDBOX_ORIGIN + '/loader.js' +
      '?agentId=' + encodeURIComponent(currentAgentId) +
      '&agentUrl=' + encodeURIComponent(url);
    applyConfigToScript(s, cfg);
    document.body.appendChild(s);
    setBarStatus('');
    hintText.innerHTML = 'Active agent: <span class="mono">' + escHtml(currentAgentName) +
      '</span> &mdash; open the chat bubble. Config applied.';
  }}

  // ── auth ─────────────────────────────────────────────────────────────────

  async function login() {{
    var url = normalizeUrl(document.getElementById('f-url').value.trim());
    var email = document.getElementById('f-email').value.trim();
    var password = document.getElementById('f-pass').value;
    if (!url || !email || !password) {{
      setError('All fields are required.');
      return;
    }}
    loginBtn.disabled = true;
    setError('');
    try {{
      var token = await doLogin(url, email, password);
      sessionStorage.setItem(SESSION_KEY, token);
      sessionStorage.setItem(URL_KEY, url);
      await enterLoggedIn(url, token);
    }} catch (err) {{
      setError(err.message || 'Login failed.');
    }} finally {{
      loginBtn.disabled = false;
    }}
  }}

  async function doLogin(url, email, password) {{
    // jvspatial UserLogin expects {{email, password}} — same as jvchat.
    // Try /api/auth/login first, then /auth/login.
    var endpoints = [url + '/api/auth/login', url + '/auth/login'];
    var lastErr = null;
    for (var i = 0; i < endpoints.length; i++) {{
      try {{
        var r = await fetch(endpoints[i], {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ email: email, password: password }})
        }});
        if (r.status === 401 || r.status === 403) {{
          throw new Error('Invalid email or password.');
        }}
        if (r.status === 422) {{
          throw new Error('Invalid login payload (email + password required).');
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
        if (err.message === 'Invalid email or password.' ||
            err.message.startsWith('Invalid login payload') ||
            err.message.startsWith('Server error')) {{
          throw err; // don't retry 401/422/5xx on second endpoint
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
    profileFallback = {{ title: '', greeting: '' }};
    removeLoader();
    hostBar.classList.remove('visible');
    configPanel.classList.remove('visible');
    configToggle.classList.remove('active');
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

  function renderAgentSelect(agents) {{
    agentById = {{}};
    agentSelect.innerHTML = '';
    var placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = agents.length ? 'Select an agent…' : 'No agents found';
    placeholder.disabled = true;
    placeholder.selected = true;
    agentSelect.appendChild(placeholder);

    if (!agents.length) {{
      agentSelect.disabled = true;
      return;
    }}
    agentSelect.disabled = false;
    agents.forEach(function (a) {{
      // jvspatial export nests attributes under context unless flat=True.
      // Match jvchat: context.enabled → enabled → default true.
      var enabled = true;
      if (a.context && a.context.enabled !== undefined) {{
        enabled = !!a.context.enabled;
      }} else if (a.enabled !== undefined) {{
        enabled = !!a.enabled;
      }}
      var agentId = a.id || '';
      if (!agentId) return;
      var label = a.alias || (a.context && a.context.alias) ||
                  a.name || (a.context && a.context.name) || agentId;
      var opt = document.createElement('option');
      opt.value = agentId;
      opt.textContent = enabled ? label : (label + ' (disabled)');
      opt.disabled = !enabled;
      agentSelect.appendChild(opt);
      if (enabled) {{
        agentById[agentId] = {{
          id: agentId,
          name: label,
          alias: a.alias || (a.context && a.context.alias) || null,
          enabled: true
        }};
      }}
    }});
  }}

  // ── loader injection ──────────────────────────────────────────────────────

  function removeLoader() {{
    var old = document.getElementById('jvmessenger-loader-script');
    if (old) old.remove();
    // Shadow-DOM launcher host (data-jvmessenger="launcher") + any iframe wrappers.
    document.querySelectorAll('[data-jvmessenger]').forEach(function (el) {{
      el.remove();
    }});
    // Loader guards against double-embed; clear so a re-inject boots.
    try {{ delete window.__jvmessengerLoaded; }} catch (_) {{
      window.__jvmessengerLoaded = false;
    }}
  }}

  async function selectAgent(agent) {{
    var url = getUrl();
    var token = getToken();
    if (!url || !token) return;
    if (!agent || !agent.id) {{
      setBarStatus('Agent has no id.');
      return;
    }}

    // Keep dropdown in sync with the active selection.
    if (agentSelect.value !== agent.id) {{
      agentSelect.value = agent.id;
    }}

    currentAgentId = agent.id;
    currentAgentName = agent.alias || agent.name || agent.id;
    setBarStatus('');
    hintText.innerHTML = 'Active agent: <span class="mono">' + escHtml(currentAgentName) + '</span> &mdash; open the chat bubble.';

    // Best-effort profile for fallbacks when config title/greeting empty.
    profileFallback = {{ title: currentAgentName, greeting: '' }};
    try {{
      var pr = await fetch(url + '/api/agents/' + encodeURIComponent(agent.id) + '/profile');
      if (!pr.ok) pr = await fetch(url + '/agents/' + encodeURIComponent(agent.id) + '/profile');
      if (pr.ok) {{
        var pd = await pr.json();
        if (pd && pd.name) profileFallback.title = pd.name;
        if (pd && pd.description) {{
          var cfg = readConfigFromForm();
          if (!cfg.description) {{
            document.getElementById('cfg-description').value = pd.description;
          }}
        }}
      }}
    }} catch (_) {{}}

    reinjectMessenger();
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
    renderAgentSelect(agents);
    hostBar.classList.add('visible');
    customerBody.classList.add('visible');
    hintText.textContent = 'Select an agent in the bar above to start.';
    setBarStatus('');
  }}

  // ── event wiring ──────────────────────────────────────────────────────────

  writeConfigToForm(loadSavedConfig());

  loginBtn.addEventListener('click', login);
  logoutBtn.addEventListener('click', logout);
  configToggle.addEventListener('click', function () {{
    var open = configPanel.classList.toggle('visible');
    configToggle.classList.toggle('active', open);
  }});
  document.getElementById('cfg-apply').addEventListener('click', reinjectMessenger);
  document.getElementById('cfg-reset').addEventListener('click', function () {{
    writeConfigToForm(Object.assign({{}}, DEFAULT_CONFIG));
    saveConfig(DEFAULT_CONFIG);
  }});
  agentSelect.addEventListener('change', function () {{
    var id = agentSelect.value;
    if (!id || !agentById[id]) return;
    selectAgent(agentById[id]);
  }});

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
