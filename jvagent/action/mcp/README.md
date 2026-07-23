# MCPAction

MCPAction is a core singleton gateway action that manages **one or more named MCP servers** and exposes `fulfill(natural_language_command: str)` so other actions (e.g. InteractActions) can pass a natural language request and receive the MCP tool result.

## File I/O vs MCP

For **read/write/list confined to the per-user workspace**, use the built-in **file storage** via `App.get_file_interface()` (in-process calls with no MCP subprocess or `npx` dependency). Use **MCPAction** when you need **additional** MCP servers (HTTP or stdio tools from the ecosystem) or LLM-driven tool dispatch across those servers.

## Requirements

- **LanguageModelAction**: The agent must have a LanguageModelAction (e.g. `jvagent/openai`) for mapping natural language to tool name + arguments. If none is configured, `get_model_action(required=True)` will raise at runtime.

## Configuration

Configure MCP servers in `context.servers` (one MCPAction instance can host many providers). All attributes can be overridden via `context` in `agent.yaml`.

Top-level context attributes:

| Attribute | Description | Default |
|-----------|-------------|---------|
| `model_action_type` | LanguageModelAction type for NL→tool mapping | `OpenAILanguageModelAction` |
| `model` | Model name for tool selection | `gpt-4o-mini` |
| `servers` | List of MCP server configs | `[]` |

Per-server config (`servers[]`):

| Attribute | Description | Default |
|-----------|-------------|---------|
| `name` | Logical server name used by callers (`tool_servers`) | required |
| `enabled` | Whether this server is available | `true` |
| `transport` | `stdio` or `streamable_http` | `streamable_http` |
| `command` | For stdio: executable to run | `""` |
| `args` | For stdio: command line arguments | `[]` |
| `env` | For stdio: optional environment dict | `null` |
| `url` | For streamable_http: endpoint URL | `""` |
| `mcp_connect_timeout` | Connect + initialize timeout (seconds) | `10.0` |
| `mcp_call_timeout` | Tool call timeout (seconds) | `30.0` |
| `tools` | Tool selector: `"-all"` or list of names/globs | `"-all"` |
| `denied_tools` | Subtractive tool filter (supports globs) | `[]` |
| `sandbox_mode` | Confine stdio filesystem MCP to ``<files_root>/<agentId>/<userId>/`` | **`true`** (per-server can override) |
| `sandbox_user_scoped` | Separate MCP subprocess per ``user_id`` when not the default user (see env) | **`true`** (per-server can override) |
| `sandbox_root` | Optional override for files root (else ``JVSPATIAL_FILES_ROOT_PATH``) | unset |
| `type` | Optional. For sandboxed stdio filesystem: ``jvspatial_fs`` (Python ``jvspatial_fs_server``), ``npx_filesystem`` (force ``npx`` + npm server). If omitted, S3 storage auto-selects ``jvspatial_fs``; local storage defaults to ``npx``. | unset |

**Per-user file scoping is on by default.** Filesystem MCP servers (any
stdio entry that runs ``@modelcontextprotocol/server-filesystem`` or the
built-in ``jvspatial_fs_server``) are confined to
``<files_root>/<agent_id>/<user_id>/`` out of the box, and a separate
subprocess is spawned per real ``user_id``. Calls from the LLM-driven
``fulfill()`` and the skill-system ``ToolExecutor`` both pass the visitor's
``user_id`` so each user gets an isolated workspace folder named for their
``user_id``. Set ``sandbox_mode: false`` (or
``MCP_FILESYSTEM_SANDBOX_MODE=false``) to opt out of confinement, and
``sandbox_user_scoped: false`` to share one subprocess across users
(rooted at the default-user folder, ``MCP_FILESYSTEM_SANDBOX_DEFAULT_USER``,
default ``_default``).

Top-level MCP action context may set ``sandbox_mode`` / ``sandbox_user_scoped`` / ``sandbox_root`` as overrides; per-server entries can override again.

When ``JVSPATIAL_FILE_STORAGE_PROVIDER=s3`` and ``sandbox_mode`` is true, the agent uses ``python -m jvagent.action.mcp.jvspatial_fs_server`` (stdio) instead of ``npx`` + ``@modelcontextprotocol/server-filesystem``, unless ``type: npx_filesystem`` is set (not recommended for S3).

Env fallbacks: ``MCP_FILESYSTEM_SANDBOX_MODE``, ``MCP_FILESYSTEM_SANDBOX_USER_SCOPED``, ``MCP_FILESYSTEM_SANDBOX_ROOT``.

## Agent wiring (agent.yaml)

The agent must have a LanguageModelAction. Add one MCP action with all desired MCP servers:

```yaml
actions:
  - action: jvagent/openai
    # ... your LLM config ...

  - action: jvagent/mcp
    context:
      model_action_type: OpenAILanguageModelAction
      model: gpt-4o-mini
      servers:
        - name: weather
          transport: streamable_http
          url: "http://localhost:8000/mcp"
          tools: "-all"
          denied_tools: []
        - name: filesystem
          transport: stdio
          command: npx
          args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
          mcp_connect_timeout: 10.0
          mcp_call_timeout: 30.0
          tools: ["read_*", "list_*"]
          denied_tools: ["list_secrets*"]
```

## Caller usage

From an **InteractAction** (or any action that can call `get_action`):

- `mcp = await self.get_action(MCPAction)` then `result = await mcp.fulfill("What's the weather in Kansas tomorrow?", user_id=self.user_id)`.
- `fulfill()` aggregates tools across all enabled configured servers and asks the model to choose `{server_name, tool_name, arguments}`. Pass `user_id` so the per-user sandbox folder is used instead of the shared `anonymous` default.

From ReasoningHelm / `ToolExecutor` integration:

- `tool_servers` still references server names (`servers[].name`).
- `ToolExecutor` registers tools with collision-safe names like `mcp_<server>_<tool>` internally and dispatches to the owning server.

Use `result.text` and optionally `result.structured` or `result.error_kind` (e.g. `no_tool`, `tool_failed`, `gateway_error`) for branching.

## Recommended servers

- **Development / examples**: Use the [MCP Python SDK example server](https://github.com/modelcontextprotocol/python-sdk) (e.g. quickstart or minimal tool server). Run it with streamable HTTP (e.g. `uv run mcp run examples/snippets/servers/mcpserver_quickstart.py` or equivalent) so it serves at `http://localhost:8000/mcp`. No API keys; good for testing the full flow.
- **First real integration**: [Open-Meteo MCP Server](https://mcpservers.org/servers/cmer81/open-meteo-mcp) (weather), or official reference servers (e.g. filesystem, fetch) from [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers). See the [MCP registry](https://prod.registry.modelcontextprotocol.io/) for more.

## Session and tool list

- **Tool list**: Cached per server session; invalidated on reconnect/disconnect. No TTL or live refresh during a session.
- **Stdio**: One long-lived subprocess session per configured server.
- **Streamable HTTP**: One shared reusable session per configured server.

## Security model

> **`npx`-based stdio servers execute arbitrary npm packages with the
> running process's privileges.** There is no signature check on the npm
> package, no sandbox boundary beyond the OS-level filesystem allowlist
> set by `@modelcontextprotocol/server-filesystem`, and no out-of-the-box
> verification that the package's published version matches an expected
> digest. AUDIT-actions MED.
>
> If you ship MCPAction in a multi-tenant deployment:
>
> 1. **Pin** every npm package to an exact version (no `latest`).
> 2. **Pre-bake** packages into the container image so cold starts do
>    not pull from the npm registry at runtime.
> 3. **Run agents as a low-privilege user** with no write access outside
>    the configured workspace root.
> 4. **Prefer `jvspatial_fs`** (`type: jvspatial_fs`) for filesystem
>    access — it stays in-process, no `npx` dependency, and inherits
>    `App.get_file_interface()` permissions.
> 5. **Allowlist `command` values** at deploy time. Reject any agent
>    config whose stdio `command` is not in the allowlist before the
>    bootstrap loader saves the agent.
>
> When in doubt, use Streamable HTTP MCP servers running in their own
> network namespace (separate container, separate IAM identity).
