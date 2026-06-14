# jvchat — the bundled web UI

[jvchat](../jvchat/README.md) is jvagent's reference chat UI (React/Vite). The
built static bundle ships inside the `jvagent` wheel and is served by the
`jvagent chat` command on **its own port**, as a process and origin separate
from the agent server.

## Running it

```bash
jvagent chat                                   # serves on http://127.0.0.1:3000, opens a browser
jvagent chat --url https://my-agent.example.com  # point the UI at a remote agent
jvagent chat --port 8080 --no-browser          # custom port, no auto-open
jvagent chat --host 0.0.0.0                     # expose on the network (see security note)
```

| Flag | Default | Meaning |
|---|---|---|
| `--url` | built-in / login screen | Agent server URL, injected at runtime (no rebuild) |
| `--jvforge-url` | — | Optional jvforge service URL |
| `--port` | `3000` | Port to serve the UI on |
| `--host` | `127.0.0.1` | Bind address (`0.0.0.0` to expose) |
| `--no-browser` | off | Don't open a browser on startup |

`--url` is injected into the served `index.html` as
`window.__JVCHAT_RUNTIME_CONFIG__`, so one pre-built bundle targets any agent.
Resolution priority for the agent URL: **login screen (localStorage) → `--url`
injection → build-time `VITE_JVAGENT_URL` → built-in default**.

## Why a separate origin (not mounted on the agent server)

Serving the UI from the agent server itself is simpler but widens the agent's
public attack surface in a cloud deployment. Keeping jvchat on its own
port/origin avoids that:

- **No same-origin token exposure.** jvchat holds its JWT in `localStorage`. If
  the UI shared an origin with the API, an XSS in the bundle could read that
  token and call the API directly. A separate origin keeps the UI out of the
  API's trust boundary.
- **Smaller API surface.** The agent server keeps serving only the API; it
  doesn't also advertise an HTML/login surface or a debug viewer (`/debug`)
  next to admin endpoints.
- **Independent deploy/scale.** The UI can go to a CDN/static host, version and
  scale on its own, and be disabled in production without touching the API.

### Production guidance

- Keep the API behind a **strict CORS allowlist** that names the UI's origin.
- Turn on `JVAGENT_INTERACT_PUBLIC_AUTH=required` (session capability tokens,
  [ADR-0020](../.planning/adr/0020-public-interact-endpoint-auth.md)) and the
  rate limiter so the public `interact` endpoint isn't trivially abusable.
- Serve everything over TLS.
- For a hardened deployment, build jvchat and host the static `dist/` on a CDN
  (S3+CloudFront, Netlify, …) pointed at the API via `--url` / the login
  screen, rather than running `jvagent chat` on a public host.

The static server itself sets `no-store` on HTML (so injected config is always
fresh), `immutable` caching on hashed assets, and `X-Content-Type-Options`,
`X-Frame-Options: DENY`, and `Referrer-Policy: no-referrer` on every response.

## How it's built

The bundle is produced from `jvchat/` by
[`scripts/build_jvchat.py`](../scripts/build_jvchat.py) (`npm ci && npm run
build`, then staged into `jvagent/webui/dist/`). That directory is a git-ignored
build artifact, run at release time by the publish workflow. `jvagent chat`
prints a build hint if the UI isn't bundled (e.g. a source checkout that hasn't
been built).
