"""Interactive CLI for exercising the SentDMBroadcastAction HTTP endpoints.

This is a standalone tester — it does NOT import any jvagent code at runtime,
it just talks to a running jvagent server over HTTP.

Usage::

    python jvagent/action/sentdm_broadcast/test_cli.py
    python jvagent/action/sentdm_broadcast/test_cli.py --env-file path/to/.env

The script will:

1. Load a ``.env`` file: by default ``examples/jvagent_app/.env`` in this repo
   (resolved from this script's location), or override with ``--env-file``,
   with a CWD walk as a fallback if that file is missing.
2. Ask for the jvagent base URL (default derived from
   ``JVAGENT_BASE_URL`` / ``JVAGENT_PUBLIC_BASE_URL`` or
   ``JVAGENT_HOST`` + ``JVAGENT_PORT``).
3. Authenticate with either admin email/password (``POST /api/auth/login``
   — jvspatial's ``UserLogin`` model expects ``email`` + ``password``) or an
   existing jvagent API key (``x-api-key`` header). Defaults are taken from
   ``JVAGENT_ADMIN_EMAIL`` (falling back to ``JVAGENT_ADMIN_USERNAME``) /
   ``JVAGENT_ADMIN_PASSWORD`` / ``JVAGENT_API_KEY`` /
   ``JVAGENT_API_KEY_HEADER`` if present.
4. List agents and let you pick one.
5. Find the SentDMBroadcastAction registered on that agent.
6. Open a menu: send broadcast (few prompts; optional ``SENTDM_TEST_*`` .env
   defaults), reconcile webhook, show webhook URL, switch agent, quit.

Depends on the standard library + ``httpx`` + ``python-dotenv`` (both already
jvagent dependencies). The last successful base_url + auth method are cached
at ``~/.sentdm_test_cli.json`` so reruns are quick. **Passwords and API keys
are NEVER cached to disk.**
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import httpx
except ImportError:  # pragma: no cover - guidance for the user
    print(
        "This script requires the 'httpx' package. "
        "Install it with: pip install httpx",
        file=sys.stderr,
    )
    raise

try:
    from dotenv import dotenv_values, find_dotenv
except ImportError:  # pragma: no cover - python-dotenv is a jvagent dep
    print(
        "This script requires the 'python-dotenv' package "
        "(already a jvagent dependency). Install it with: pip install python-dotenv",
        file=sys.stderr,
    )
    raise


CONFIG_PATH = Path.home() / ".sentdm_test_cli.json"
ACTION_LABEL = "SentDMBroadcastAction"
ACTION_NAMES = {
    "sentdm_broadcast_action",
    "jvagent/sentdm_broadcast_action",
}
ACTION_ID_PREFIX = "n.SentDMBroadcastAction."

# Module-level flag toggled by --yes / -y. When True, _prompt and friends
# silently accept whatever default they were given instead of waiting for input.
AUTO_MODE: bool = False

# Default .env for local dev: examples/jvagent_app/.env (repo root is three
# levels above this file: jvagent/action/sentdm_broadcast/test_cli.py).
_DEFAULT_EXAMPLE_APP_DOTENV = (
    Path(__file__).resolve().parents[3] / "examples" / "jvagent_app" / ".env"
)


# ---------------------------------------------------------------------------
# Pretty printing helpers
# ---------------------------------------------------------------------------


def _print_header(title: str) -> None:
    line = "=" * max(len(title), 32)
    print(f"\n{title}\n{line}")


def _print_kv(key: str, value: Any) -> None:
    print(f"  {key:<20s} {value}")


def _dump_json(obj: Any) -> None:
    try:
        print(json.dumps(obj, indent=2, default=str))
    except (TypeError, ValueError):
        print(repr(obj))


def _prompt(label: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default else ""
    if AUTO_MODE and default is not None:
        print(f"{label}{suffix}: {default}  (auto)")
        return default
    while True:
        raw = input(f"{label}{suffix}: ").strip()
        if raw:
            return raw
        if default is not None:
            return default
        print("  (required — please enter a value)")


def _prompt_choice(label: str, default: str, options: List[str]) -> str:
    opts = "/".join(options)
    if AUTO_MODE:
        print(f"{label} ({opts}) [{default}]: {default}  (auto)")
        return default
    while True:
        raw = input(f"{label} ({opts}) [{default}]: ").strip().lower()
        if not raw:
            return default
        if raw in options:
            return raw
        print(f"  Invalid choice; expected one of {options}.")


def _prompt_bool(label: str, default: bool) -> bool:
    return _prompt_choice(label, "y" if default else "n", ["y", "n"]) == "y"


def _prompt_optional(label: str) -> Optional[str]:
    raw = input(f"{label} (blank to skip): ").strip()
    return raw or None


# ---------------------------------------------------------------------------
# Config cache (base_url + auth method only — never secrets)
# ---------------------------------------------------------------------------


def load_cached_defaults() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def save_cached_defaults(data: Dict[str, Any]) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"(warning: could not cache defaults: {exc})")


# ---------------------------------------------------------------------------
# .env loading — values feed prompt defaults but NEVER get cached to disk.
# ---------------------------------------------------------------------------


def load_env_file(explicit_path: Optional[str]) -> Dict[str, str]:
    """Load environment variables from a ``.env`` file.

    Resolution order:
      1. ``--env-file`` path if provided (errors if missing).
      2. ``examples/jvagent_app/.env`` under the repository root (path derived
         from this script — same file the example app uses).
      3. ``find_dotenv()`` walking up from the current working directory.
      4. ``./.env`` as a final fallback.

    Returns a flat ``{KEY: VALUE}`` dict (empty if nothing is found).
    Values found in ``os.environ`` are merged on top so real env vars win.
    """
    candidate: Optional[Path] = None
    if explicit_path:
        candidate = Path(explicit_path).expanduser()
        if not candidate.is_file():
            raise SystemExit(f"--env-file not found: {candidate}")
    else:
        if _DEFAULT_EXAMPLE_APP_DOTENV.is_file():
            candidate = _DEFAULT_EXAMPLE_APP_DOTENV
        else:
            discovered = find_dotenv(usecwd=True)
            if discovered:
                candidate = Path(discovered)
            elif Path(".env").is_file():
                candidate = Path(".env")

    merged: Dict[str, str] = {}
    if candidate:
        print(f"Loading defaults from {candidate}")
        for key, value in dotenv_values(candidate).items():
            if value is not None:
                merged[key] = value
    for key in os.environ:
        if key.startswith(_PREFIXES):
            merged[key] = os.environ[key]
    return merged


_PREFIXES: Tuple[str, ...] = ("JVAGENT_", "JVSPATIAL_", "SENTDM_")

# Optional .env defaults for a shorter broadcast flow (see do_send_broadcast).
_SENTDM_ENV_TEST_TO = "SENTDM_TEST_TO"
_SENTDM_ENV_TEST_TEMPLATE_ID = "SENTDM_TEST_TEMPLATE_ID"
_SENTDM_ENV_TEST_PARAMETERS = "SENTDM_TEST_PARAMETERS_JSON"


def _env_value(env: Dict[str, str], *keys: str) -> Optional[str]:
    for key in keys:
        val = env.get(key)
        if val and str(val).strip():
            return str(val).strip()
    return None


def derive_default_base_url(env: Dict[str, str]) -> str:
    """Pick the best default base URL from env values.

    Preference:
      1. ``JVAGENT_BASE_URL`` (explicit CLI override; not standard).
      2. ``JVAGENT_PUBLIC_BASE_URL`` (the canonical public origin).
      3. ``http://{JVAGENT_HOST or localhost}:{JVAGENT_PORT or 8000}``.
    """
    explicit = _env_value(env, "JVAGENT_BASE_URL", "JVAGENT_PUBLIC_BASE_URL")
    if explicit:
        return explicit.rstrip("/")

    host = _env_value(env, "JVAGENT_HOST") or "localhost"
    if host in ("0.0.0.0", "127.0.0.1"):
        host = "localhost"
    port_raw = _env_value(env, "JVAGENT_PORT") or "8000"
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        port = 8000
    return f"http://{host}:{port}"


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


class JvAgentClient:
    """Thin wrapper around the jvagent REST API."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        cli_env: Optional[Dict[str, str]] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout)
        self._headers: Dict[str, str] = {"accept": "application/json"}
        self.cli_env: Dict[str, str] = dict(cli_env or {})

    def close(self) -> None:
        self._client.close()

    # ---- auth helpers ----

    def login(self, identifier: str, password: str) -> None:
        """POST /api/auth/login. ``identifier`` should be an email — jvspatial's
        UserLogin model is ``{email: EmailStr, password: str}``. We try a few
        payload shapes in case the server has been customized.
        """
        url = f"{self.base_url}/api/auth/login"
        candidates: List[Tuple[Dict[str, Any], str]] = [
            ({"json": {"email": identifier, "password": password}}, "json/email"),
            (
                {"json": {"username": identifier, "password": password}},
                "json/username",
            ),
            (
                {"data": {"username": identifier, "password": password}},
                "form/username",
            ),
        ]
        last_response: Optional[httpx.Response] = None
        for kwargs, label in candidates:
            try:
                resp = self._client.post(url, **kwargs)
            except httpx.HTTPError as exc:
                raise RuntimeError(f"Could not reach {url}: {exc}") from exc
            last_response = resp
            if resp.status_code == 200:
                body = _safe_json(resp)
                token = _find_token(body)
                if not token:
                    raise RuntimeError(
                        f"Login {label} succeeded but no access token in response: {body}"
                    )
                self._headers["authorization"] = f"Bearer {token}"
                return
            if resp.status_code not in (400, 401, 415, 422):
                break
        msg = "Login failed"
        if last_response is not None:
            msg += f" (HTTP {last_response.status_code}): {last_response.text}"
        raise RuntimeError(msg)

    def use_api_key(self, api_key: str, *, header: str = "x-api-key") -> None:
        self._headers[header.lower()] = api_key

    # ---- generic request ----

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Any = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        try:
            resp = self._client.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=self._headers,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"{method} {path} transport error: {exc}") from exc
        body = _safe_json(resp)
        if not resp.is_success:
            detail = body if isinstance(body, (dict, list)) else resp.text
            raise RuntimeError(
                f"{method} {path} failed (HTTP {resp.status_code}): {detail}"
            )
        return body


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text


def _find_token(body: Any) -> Optional[str]:
    """Best-effort extract an access token from a login response body."""
    if not isinstance(body, dict):
        return None
    for key in ("access_token", "token", "jwt"):
        val = body.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    data = body.get("data")
    if isinstance(data, dict):
        return _find_token(data)
    return None


def _unwrap_data(body: Any) -> Any:
    """jvspatial success_response wraps payloads as ``{"data": {...}, "success": true}``."""
    if isinstance(body, dict) and "data" in body and "success" in body:
        return body["data"]
    return body


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def list_agents(client: JvAgentClient) -> List[Dict[str, Any]]:
    body = client.request("GET", "/api/agents", params={"page": 1, "per_page": 100})
    data = _unwrap_data(body)
    if isinstance(data, dict):
        agents = data.get("agents")
        if isinstance(agents, list):
            return agents
    if isinstance(body, dict) and isinstance(body.get("agents"), list):
        return body["agents"]
    if isinstance(body, list):
        return body
    return []


def list_agent_actions(client: JvAgentClient, agent_id: str) -> List[Dict[str, Any]]:
    body = client.request(
        "GET",
        f"/api/agents/{agent_id}/actions",
        params={"page": 1, "per_page": 200},
    )
    data = _unwrap_data(body)
    if isinstance(data, dict):
        actions = data.get("actions")
        if isinstance(actions, list):
            return actions
    if isinstance(body, dict) and isinstance(body.get("actions"), list):
        return body["actions"]
    if isinstance(body, list):
        return body
    return []


def pick_agent(client: JvAgentClient) -> Optional[Dict[str, Any]]:
    agents = list_agents(client)
    if not agents:
        print("No agents found on this server.")
        return None
    # When in auto-mode, prefer the first agent that actually has a SentDM
    # action registered instead of just agents[0].
    if AUTO_MODE:
        for agent in agents:
            agent_id = str(agent.get("id") or _agent_field(agent, "id") or "")
            if not agent_id:
                continue
            try:
                actions = list_agent_actions(client, agent_id)
            except RuntimeError:
                continue
            if find_sentdm_action(actions):
                name = agent.get("name") or _agent_field(agent, "name") or "(unnamed)"
                print(
                    f"(auto-selected agent {name} ({agent_id}) "
                    f"— has SentDMBroadcastAction)"
                )
                return agent
        # Fall through to listing if none matched.
    print("\nAvailable agents:")
    for idx, agent in enumerate(agents, start=1):
        name = agent.get("name") or _agent_field(agent, "name") or "(unnamed)"
        agent_id = agent.get("id") or _agent_field(agent, "id") or "?"
        print(f"  {idx}) {name}  (id={agent_id})")
    while True:
        raw = _prompt("Pick agent number", "1")
        try:
            choice = int(raw)
        except ValueError:
            print("  Please enter a number.")
            continue
        if 1 <= choice <= len(agents):
            return agents[choice - 1]
        print(f"  Choose between 1 and {len(agents)}.")


def _agent_field(agent: Dict[str, Any], key: str) -> Any:
    """Look in the top-level dict and in the nested ``context`` block."""
    if key in agent:
        return agent[key]
    ctx = agent.get("context")
    if isinstance(ctx, dict) and key in ctx:
        return ctx[key]
    return None


def find_sentdm_action(
    actions: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Locate the SentDMBroadcastAction in a list of action dicts.

    The action registry exposes different fields depending on serializer.
    Matches on (in order):
      1. ``archetype`` exactly equals ``SentDMBroadcastAction``.
      2. ``label`` equals the configured action name (``sentdm_broadcast_action``
         or ``jvagent/sentdm_broadcast_action``).
      3. ``id`` starts with ``n.SentDMBroadcastAction.`` — the node id always
         embeds the archetype, so this is the most reliable signal.
    """
    for action in actions:
        archetype = action.get("archetype") or _agent_field(action, "archetype") or ""
        if str(archetype).strip() == ACTION_LABEL:
            return action

        label = str(action.get("label") or _agent_field(action, "label") or "").strip()
        if label in ACTION_NAMES:
            return action

        action_id = str(action.get("id") or _agent_field(action, "id") or "").strip()
        if action_id.startswith(ACTION_ID_PREFIX):
            return action

    return None


# ---------------------------------------------------------------------------
# Interactive actions on a chosen SentDMBroadcastAction
# ---------------------------------------------------------------------------


def _action_id(action: Dict[str, Any]) -> str:
    return str(action.get("id") or _agent_field(action, "id") or "")


def do_send_broadcast(client: JvAgentClient, action: Dict[str, Any]) -> None:
    """Minimal prompts: to, optional template id, optional parameters JSON, sandbox.

    Set ``SENTDM_TEST_TO``, ``SENTDM_TEST_TEMPLATE_ID``, and/or
    ``SENTDM_TEST_PARAMETERS_JSON`` in ``.env`` to pre-fill defaults (fewer keystrokes).
    """
    _print_header("Send broadcast")

    env_defaults = client.cli_env
    env_to = _env_value(env_defaults, _SENTDM_ENV_TEST_TO)
    env_tid = _env_value(env_defaults, _SENTDM_ENV_TEST_TEMPLATE_ID)
    env_params = _env_value(env_defaults, _SENTDM_ENV_TEST_PARAMETERS)

    recipients_raw = _prompt(
        "recipient phone(s), comma-separated (E.164)",
        env_to or "",
    )
    recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]
    if not recipients:
        print("  No recipients provided; aborting.")
        return

    template_id = (
        _prompt(
            "template id (UUID, blank = action default_template_*)", env_tid or ""
        ).strip()
        or None
    )

    params_raw = _prompt(
        'parameters JSON (blank = omit), e.g. {"var_1":"123456"}',
        env_params or "",
    ).strip()
    parameters: Optional[Dict[str, Any]] = None
    if params_raw:
        try:
            parsed = json.loads(params_raw)
        except json.JSONDecodeError as exc:
            print(f"  Could not parse parameters JSON: {exc}; sending without.")
        else:
            if isinstance(parsed, dict):
                parameters = parsed
            else:
                print("  parameters must be a JSON object; ignoring.")

    sandbox = _prompt_bool("sandbox mode? (no real carrier send)", True)

    body: Dict[str, Any] = {"to": recipients, "sandbox": sandbox}
    if template_id:
        body["template"] = {"id": template_id}
    if parameters:
        body["parameters"] = parameters

    print("\nRequest body:")
    _dump_json(body)

    resp = client.request(
        "POST",
        f"/api/actions/{_action_id(action)}/broadcast",
        json_body=body,
    )
    _print_header("Response")
    _dump_json(_unwrap_data(resp))

    if sandbox:
        print(
            "\n(Graph) SentDMBroadcastRecord is not created for sandbox sends unless "
            "the action has persist_sandbox_sends: true in agent.yaml — "
            "otherwise only non-sandbox sends are persisted."
        )


def do_reconcile_webhook(client: JvAgentClient, action: Dict[str, Any]) -> None:
    _print_header("Reconcile webhook")
    body = client.request(
        "POST",
        f"/api/actions/{_action_id(action)}/webhook/register",
    )
    _dump_json(_unwrap_data(body))


def do_show_webhook(client: JvAgentClient, action: Dict[str, Any]) -> None:
    _print_header("Webhook URL (currently registered)")
    body = client.request(
        "GET",
        f"/api/actions/{_action_id(action)}/webhook",
    )
    _dump_json(_unwrap_data(body))


# ---------------------------------------------------------------------------
# Top-level loop
# ---------------------------------------------------------------------------


MENU = """
SentDM Broadcast Tester
=======================
  1) Send broadcast
  2) Reconcile webhook
  3) Show webhook URL
  4) Pick a different agent / action
  0) Quit
"""


def authenticate(
    client: JvAgentClient,
    cached: Dict[str, Any],
    env: Dict[str, str],
) -> str:
    env_password = _env_value(env, "JVAGENT_ADMIN_PASSWORD")
    env_api_key = _env_value(env, "JVAGENT_API_KEY")

    if not cached.get("auth_method"):
        if env_api_key:
            default_method = "api_key"
        elif env_password:
            default_method = "login"
        else:
            default_method = "login"
    else:
        default_method = cached["auth_method"]

    method = _prompt_choice("Auth method", default_method, ["login", "api_key", "none"])
    if method == "login":
        # jvspatial's /api/auth/login expects an email (EmailStr) + password.
        # Prefer JVAGENT_ADMIN_EMAIL; fall back to USERNAME only if it's
        # already a valid-looking email.
        default_email = (
            cached.get("email")
            or _env_value(env, "JVAGENT_ADMIN_EMAIL")
            or _env_value(env, "JVAGENT_ADMIN_USERNAME")
            or ""
        )
        email = _prompt("Admin email", default_email or "admin@jvagent.example")
        if "@" not in email:
            print(
                "  (warning: the auth endpoint expects an email — "
                "this value will likely 422)"
            )
        if env_password:
            print("  (using JVAGENT_ADMIN_PASSWORD from .env)")
            password = env_password
        else:
            password = getpass.getpass("Password (input hidden): ")
        if not password:
            raise RuntimeError(
                "Empty password. Set JVAGENT_ADMIN_PASSWORD in your .env or "
                "type the password at the prompt."
            )
        client.login(email, password)
        cached["auth_method"] = "login"
        cached["email"] = email
        return "login"
    if method == "api_key":
        default_header = (
            cached.get("api_key_header")
            or _env_value(env, "JVAGENT_API_KEY_HEADER")
            or "x-api-key"
        )
        header = _prompt("API key header", default_header)
        if env_api_key:
            print("  (using JVAGENT_API_KEY from .env)")
            api_key = env_api_key
        else:
            api_key = getpass.getpass(
                "API key (input hidden, or set JVAGENT_API_KEY in .env): "
            )
        if not api_key:
            raise RuntimeError("An API key is required for api_key auth.")
        client.use_api_key(api_key, header=header)
        cached["auth_method"] = "api_key"
        cached["api_key_header"] = header
        return "api_key"
    # method == "none": rely on server-side auth being disabled
    cached["auth_method"] = "none"
    return "none"


def select_action(
    client: JvAgentClient,
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Pick an agent and locate its SentDMBroadcastAction."""
    agent = pick_agent(client)
    if not agent:
        return None
    agent_id = str(agent.get("id") or _agent_field(agent, "id") or "")
    if not agent_id:
        print("Selected agent has no id; aborting.")
        return None

    actions = list_agent_actions(client, agent_id)
    if not actions:
        print(f"Agent {agent_id} has no registered actions.")
        return None

    action = find_sentdm_action(actions)
    if not action:
        print(f"\nNo {ACTION_LABEL} found on this agent. Available actions:")
        for a in actions:
            print(
                f"  - {a.get('archetype') or _agent_field(a, 'archetype')}"
                f"  (label={a.get('label') or _agent_field(a, 'label')},"
                f"   id={a.get('id') or _agent_field(a, 'id')})"
            )
        return None

    _print_header(f"{ACTION_LABEL} found")
    _print_kv("agent_id", agent_id)
    _print_kv("agent_name", _agent_field(agent, "name"))
    _print_kv("action_id", _action_id(action))
    _print_kv("action_label", _agent_field(action, "label"))
    _print_kv("enabled", _agent_field(action, "enabled"))
    return agent, action


def menu_loop(client: JvAgentClient) -> None:
    selection = select_action(client)
    if not selection:
        return
    _, action = selection
    while True:
        print(MENU)
        choice = _prompt("choose", "1")
        try:
            if choice == "1":
                do_send_broadcast(client, action)
            elif choice == "2":
                do_reconcile_webhook(client, action)
            elif choice == "3":
                do_show_webhook(client, action)
            elif choice == "4":
                new_sel = select_action(client)
                if new_sel:
                    _, action = new_sel
            elif choice == "0":
                return
            else:
                print(f"Unknown choice: {choice}")
        except RuntimeError as exc:
            print(f"\n[error] {exc}")
        except KeyboardInterrupt:
            print()
            return


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Interactive CLI for the SentDMBroadcastAction. Reads defaults from "
            "examples/jvagent_app/.env in this repo by default, or --env-file."
        )
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help=(
            "Path to a .env file. If omitted, defaults to examples/jvagent_app/.env "
            "in this repository (next to the example app); if that file is missing, "
            "walks up from the current working directory for a .env."
        ),
    )
    parser.add_argument(
        "--no-env",
        action="store_true",
        help="Skip .env discovery entirely (still honors real os.environ values).",
    )
    parser.add_argument(
        "-y",
        "--yes",
        dest="auto",
        action="store_true",
        default=None,
        help=(
            "Auto-mode: accept all defaults without prompting (auto-login when "
            "the .env provides credentials, auto-pick the single matching "
            "SentDMBroadcastAction, etc.). This is the default whenever the env "
            "fully supplies credentials; use --prompt to force interactive auth."
        ),
    )
    parser.add_argument(
        "--prompt",
        dest="auto",
        action="store_false",
        help="Force interactive prompts even when env values are available.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.no_env:
        env = {k: v for k, v in os.environ.items() if k.startswith(_PREFIXES)}
    else:
        env = load_env_file(args.env_file)

    cached = load_cached_defaults()

    has_password = bool(_env_value(env, "JVAGENT_ADMIN_PASSWORD"))
    has_email = bool(_env_value(env, "JVAGENT_ADMIN_EMAIL", "JVAGENT_ADMIN_USERNAME"))
    has_api_key = bool(_env_value(env, "JVAGENT_API_KEY"))
    env_can_auth = has_api_key or (has_password and has_email)

    global AUTO_MODE
    if args.auto is None:
        AUTO_MODE = env_can_auth
    else:
        AUTO_MODE = args.auto
    if AUTO_MODE:
        print("(auto-mode: accepting defaults; use --prompt to force prompts)")

    default_base = cached.get("base_url") or derive_default_base_url(env)
    base_url = _prompt("jvagent base URL", default_base)

    client = JvAgentClient(base_url, cli_env=env)
    try:
        try:
            authenticate(client, cached, env)
        except RuntimeError as exc:
            print(f"\n[auth error] {exc}")
            return 1

        cached["base_url"] = base_url
        save_cached_defaults(cached)
        if AUTO_MODE:
            print(
                "\n(auto-mode complete — interactive menu starts now; "
                "press a number to act, 0 to quit)"
            )
            AUTO_MODE = False
        menu_loop(client)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
