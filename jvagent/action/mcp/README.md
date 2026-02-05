# MCPAction

MCPAction is a core action that pairs with a **named MCP server** and exposes `fulfill(natural_language_command: str)` so other actions (e.g. InteractActions) can pass a natural language request and receive the MCP tool result.

## Requirements

- **LanguageModelAction**: The agent must have a LanguageModelAction (e.g. `jvagent/openai`) for mapping natural language to tool name + arguments. If none is configured, `get_model_action(required=True)` will raise at runtime.

## Configuration

Configure one named server per MCPAction instance. All attributes can be overridden via `context` in agent.yaml.

| Attribute | Description | Default |
|-----------|-------------|---------|
| `server_name` | Logical name (logging, default label) | `mcp` |
| `transport` | `stdio` or `streamable_http` | `streamable_http` |
| **stdio** | | |
| `command` | Executable to run | `""` |
| `args` | Command line arguments | `[]` |
| `env` | Optional environment dict | `null` |
| **streamable_http** | | |
| `url` | Endpoint URL (e.g. `http://localhost:8000/mcp`) | `""` |
| **Model (NL→tool)** | | |
| `model_action_type` | LanguageModelAction type | `OpenAILanguageModelAction` |
| `model` | Model name for tool selection | `gpt-4o-mini` |
| **Timeouts** | | |
| `mcp_connect_timeout` | Connect + initialize timeout (seconds) | `10.0` |
| `mcp_call_timeout` | Tool call timeout (seconds) | `30.0` |

**Label uniqueness**: Each MCPAction on an agent must have a distinct `label`. If you omit `label`, it defaults to `MCP ({server_name})`. When adding a second MCP server, set an explicit unique label (e.g. `"Weather MCP"`, `"Files MCP"`).

## Agent wiring (agent.yaml)

The agent must have a LanguageModelAction. Add one or more MCP actions with distinct labels:

```yaml
actions:
  - action: jvagent/openai
    # ... your LLM config ...

  - action: jvagent/mcp
    context:
      label: "Weather MCP"
      server_name: weather
      transport: streamable_http
      url: "http://localhost:8000/mcp"
      model_action_type: OpenAILanguageModelAction
      model: gpt-4o-mini
      mcp_connect_timeout: 10.0
      mcp_call_timeout: 30.0
```

Multiple MCP servers = multiple `jvagent/mcp` entries with distinct `label` and connection config.

## Caller usage

From an **InteractAction** (or any action that can call `get_action`):

- **Single MCPAction**: `mcp = await self.get_action(MCPAction)` then `result = await mcp.fulfill("What's the weather in Kansas tomorrow?")`.
- **Multiple MCPAction instances**: Use label — e.g. `agent = await self.get_agent(); mcp = await agent.get_action("Weather MCP")`, then `result = await mcp.fulfill(...)`.

Use `result.text` and optionally `result.structured` or `result.error_kind` (e.g. `no_tool`, `tool_failed`, `gateway_error`) for branching.

## Recommended servers

- **Development / examples**: Use the [MCP Python SDK example server](https://github.com/modelcontextprotocol/python-sdk) (e.g. quickstart or minimal tool server). Run it with streamable HTTP (e.g. `uv run mcp run examples/snippets/servers/mcpserver_quickstart.py` or equivalent) so it serves at `http://localhost:8000/mcp`. No API keys; good for testing the full flow.
- **First real integration**: [Open-Meteo MCP Server](https://mcpservers.org/servers/cmer81/open-meteo-mcp) (weather), or official reference servers (e.g. filesystem, fetch) from [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers). See the [MCP registry](https://prod.registry.modelcontextprotocol.io/) for more.

## Session and tool list

- **Tool list**: Cached per session; invalidated on reconnect or disconnect. No TTL or live refresh during the session.
- **Stdio**: One long-lived session (one subprocess) per MCPAction instance. If the process dies, the next `fulfill()` will reconnect.
- **Streamable HTTP**: One shared session with connection reuse; same per-instance lock so only one logical request uses the client at a time.
