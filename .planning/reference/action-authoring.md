# Action Authoring Guide

> **Contract for building a new jvagent action.** This is the single source of truth — `README.md` and `docs/` may link here. If you're an AI agent dropped into the repo to add a new capability, you're in the right file.

Cross-refs: [`SPEC.md`](../SPEC.md) for invariants, [`GLOSSARY.md`](../GLOSSARY.md) for terms, [`actions-catalog.md`](actions-catalog.md) for existing actions, [`jvspatial-integration.md`](jvspatial-integration.md) for `@attribute` / `@endpoint`, [`thin-harness.md`](../../docs/thin-harness.md) for the platform-wide harness contract (actions expose capabilities; skills add judgment — no server-side turn steering).

---

## 1. Decide what kind of action you need

```
Need to wrap an LLM provider?
└─ Subclass LanguageModelAction
   (jvagent/action/model/language/base.py:24)

Need to expose an embedding model?
└─ Subclass BaseModelAction
   (jvagent/action/model/base.py:26)

Need to participate in the interact pipeline (run during a /interact call)?
└─ Subclass InteractAction
   (jvagent/action/interact/base.py:32)
   - Use weight to control top-tier order (lower = earlier)
   - Use run_in_background=True for analytics / post-response work
   - Use always_execute=True for actions that must run regardless of routing
   - Set manifest.routable_by_anchor=False if the IA is chain-internal
     (reachable only via DELEGATE from a parent IA)

Need to integrate with an external service the LLM should call?
└─ Subclass Action
   (jvagent/action/base.py:48)
   - Implement get_tools() so the Orchestrator tool surface picks it up
   - Optionally add channel-adapter behaviour via on_startup() + ResponseBus

Need a vector store / web-search / STT / TTS?
└─ Use the corresponding base: VectorStore, BaseWebSearchAction,
   BaseSTTAction, BaseTTSAction (each lives under its own subdir).
```

---

## 2. Directory layout

```
jvagent/action/{namespace}/{dir_name}/         ← shipped in the core repo
or
custom/{dir_name}/                             ← in an external app
├── __init__.py                                # exports + endpoints import
├── {module_name}.py                           # Action subclass implementation
├── endpoints.py                               # @endpoint-decorated HTTP routes
└── info.yaml                                  # package metadata (REQUIRED)
```

**Canonical identifier**: the **action's canonical name** is `info.yaml` →
`package.name` (e.g., `jvagent/whatsapp_action`). The loader at
[`jvagent/action/loader/info_yaml.py:42-44`](../../jvagent/action/loader/info_yaml.py)
parses that string and uses the post-slash part (`whatsapp_action`) as the
in-graph `Action.label`. The **directory name** is a presentational
convention — it commonly drops a `_action` / `_interact_action` suffix to
keep paths short (so `package.name=jvagent/whatsapp_action` lives in
`jvagent/action/whatsapp/`). Two consequences:

- `agent.yaml` always references actions by their `package.name`, never by
  directory.
- Two valid spellings can exist (the catalog and the YAML name); the
  authoritative one is whatever `info.yaml` says.

**Naming**: snake_case dir + snake_case Python file. The Python file may be
`{name}.py` or `{name}_interact_action.py` (the latter is convention for
`InteractAction`s — see `interview_interact_action.py`,
`converse_interact_action.py`, etc.).

---

## 3. `info.yaml` schema

Minimal viable example:

```yaml
package:
  name: namespace/action_name              # REQUIRED — must match dir layout
  author: your_org_or_username             # REQUIRED
  archetype: MyActionClassName             # REQUIRED — exact Python class name
  version: 0.1.0                           # semver

  meta:
    title: My Action
    description: One-line summary used in catalogs and prompts
    group: jvagent                         # or contrib/custom
    type: action                           # action | interact_action

  config:                                  # optional
    singleton: true                        # only one instance per agent
    order:
      weight: 0                            # for top-tier InteractActions

  dependencies:
    jvagent: ~0.0.1                        # jvagent version constraint
    actions:                               # other actions this one needs
      - jvagent/persona
    pip:                                   # PyPI packages (auto-installed unless
      - requests>=2.28                     # JVAGENT_DISABLE_RUNTIME_PIP_INSTALL=true)

  manifest:                                # optional — pattern-agnostic metadata
    purpose: One-line tool-surface summary the model sees
    activates_on:                          # intent phrases that route to this IA (first entry)
      - "user wants to enroll in training"
    terminates_when:                       # conditions that end the flow
      - "all questions answered"
    latency_class: quick                   # fast | quick | deliberate | long
    routable_by_anchor: true               # may the Orchestrator route to it by intent?
    pattern_orchestrator: false            # true ONLY for the -200 orchestrator
```

### Pattern compatibility

The `manifest:` block surfaces metadata that is consumed by deployment patterns at runtime. It is informational for **Rails** and orchestration-relevant for the **Orchestrator**:

- `purpose` — the tool-surface summary the model sees when the IA is exposed as a routable tool.
- `activates_on` — intent-explicit entry triggers; the Orchestrator routes to this IA's tool when the turn matches one (first entry). Falls back to the static class `anchors` when no manifest is declared.
- `terminates_when` — conditions that end the IA's flow (documentation/ops signal).
- `latency_class` — `deliberate` / `long` warrant a transient acknowledgement on a slow tool call (`manifest.is_ack_eligible()`).
- `routable_by_anchor` — whether the Orchestrator may route to this IA by intent (`false` = chain-internal / engine tool).
- `pattern_orchestrator` — `true` only for the single `-200` orchestrator (the Orchestrator); excludes the IA from the routable tool surface.

If your action is meant for any pattern, list all that apply. The harness does not enforce this — it is consumed by tooling only.

Schema and defaults live at [`jvagent/action/manifest.py`](../../jvagent/action/manifest.py). Access at runtime via `await action.get_manifest()`.

Real examples to copy from:

| Action | File | Notes |
|---|---|---|
| Persona | [`jvagent/action/persona/info.yaml`](../../jvagent/action/persona/info.yaml) | `singleton: true`, no deps |
| Orchestrator | [`jvagent/action/orchestrator/info.yaml`](../../jvagent/action/orchestrator/info.yaml) | `weight: -200`, `pattern_orchestrator: true`, unified tool surface |
| Router | [`jvagent/action/router/info.yaml`](../../jvagent/action/router/info.yaml) | `weight: -200`, no deps (Rails) |
| Email | [`jvagent/action/email_action/info.yaml`](../../jvagent/action/email_action/info.yaml) | pip deps |

---

## 4. `__init__.py` pattern

```python
"""My Action package."""
from .my_action import MyActionClassName  # noqa: F401

# Import endpoints module for side-effect registration of @endpoint decorators
from . import endpoints  # noqa: F401
```

This is the discovery hook the loader uses. Both the class export and the `endpoints` import must be present.

---

## 5. Action class skeleton

### 5.1 Generic `Action`

```python
"""My Action — does X."""
from typing import Annotated, Any, Dict, List

from jvspatial.core.annotations import attribute
from jvagent.action.base import Action
from jvagent.tooling.tool_decorator import tool


class MyActionClassName(Action):
    """One-line purpose. Multi-line detail goes here.

    Lifecycle: explain anything unusual about register/enable/startup ordering.
    """

    # Config attributes — surface for agent.yaml `context:` overrides
    api_url: str = attribute(
        default="https://api.example.com",
        description="API endpoint URL",
    )
    timeout: int = attribute(default=30, description="Request timeout (seconds)")
    api_key: str = attribute(
        default="",
        description="API key. Prefer setting via env var ${MYACTION_API_KEY}.",
    )

    async def on_register(self) -> None:
        # First-time setup. Validate config, prep external resources.
        pass

    async def on_enable(self) -> None:
        # Open connections, start workers.
        pass

    async def on_disable(self) -> None:
        # Close connections, stop workers. Action remains registered.
        pass

    async def healthcheck(self) -> Dict[str, Any]:
        # Return {"healthy": bool, "details": ...} or True/False.
        return {"healthy": self.enabled}

    def get_capabilities(self) -> List[str]:
        # Strings shown on the Orchestrator tool surface so the model knows you exist.
        return ["Send X via Y", "Query Z"]

    @tool
    async def do_thing(self, target: Annotated[str, "what to act on"]) -> dict:
        """Trigger the thing."""  # docstring → tool description
        # Decorated methods are auto-published by the base get_tools(); no
        # override needed. See §10.
        return {"ok": True, "target": target}
```

### 5.2 `InteractAction` — pick a category first

IA dispatch falls into a handful of categories. Pick which one applies
BEFORE writing the class — the choice drives which fields you set on the
action and on its `manifest`. Under the Orchestrator, a routable IA is
**exposed to the loop as a tool** (its description = manifest `purpose` +
entry triggers); routing is the model selecting that tool. There are no
"centers" — every dispatch path is the one orchestrator over a unified tool
surface.

| Category | Trigger | Authoring | Dispatched by |
|---|---|---|---|
| **Pattern orchestrator** | `manifest.pattern_orchestrator: true` | Set the flag; supply `purpose`. The Orchestrator (`weight -200`) is the orchestrator today. Runs at its walker weight; excluded from the tool surface. | Walker weight. |
| **Always-execute (sidecar)** | `always_execute=True` on the class | Set the attribute; supply `purpose`. The Orchestrator runs it every turn via its curated walker queue. Use for telemetry, persona-directive deposits, audit logs. | Orchestrator curated walker queue, in weight order. |
| **Routable conversational** | `always_execute=False`; `manifest.routable_by_anchor` defaults to `True` | Supply `purpose` and 3–5 **intent-explicit** entry triggers via the manifest `activates_on` (`"User wants to enroll in training"`), not topic-explicit (`"something about training"`). The static class `anchors`/`get_anchors()` are the fallback when no manifest is declared. | Orchestrator selects the IA's tool by intent. |
| **Chain-internal** | `manifest.routable_by_anchor: false` | Set the flag in `info.yaml`. Skip triggers. Reachable only via explicit delegation from a parent IA (e.g. `confirm_payment` invoked by `checkout_interview`). | Parent IA delegate chain. |
| **Synchronous (engine tool)** | `manifest.routable_by_anchor: false`; declare return-value description in `purpose` | Treat `purpose` as the tool surface the model sees. Keep the action small and idempotent — it returns to the loop rather than emitting a user response. | Orchestrator engine tool call. |
| **Turn-locking (flow)** | a routable IA that opens a flow task and doesn't finish it in one turn | Supply `activates_on` for first-entry routing and create/track a flow task (TaskStore) the IA owns until it completes. The flow owns subsequent turns via the Orchestrator's deterministic continuation (`lock_active_flow: true`) or model-mediated continuation (`false`) — ADR-0013. No `turn_lock` manifest field. | First entry: Orchestrator tool selection. Subsequent turns: flow continuation. |

Authoring requirements per category:

- **Routable conversational AND Turn-locking (flow)** — entry triggers
  REQUIRED (`activates_on`, or the static `anchors` fallback). Without them
  the bootstrap WARNING fires at install time and the IA is invisible to the
  Orchestrator's intent routing for first entry. The `delegate_to_ia`
  recovery hatch can still reach them, but discoverability is materially worse.
- **Pattern orchestrator / always-execute / chain-internal / synchronous**
  — triggers NOT required. These categories reach IAs through paths
  that don't consult routing triggers.

Mixing categories (e.g. `always_execute=True` AND
`pattern_orchestrator=True`) is undefined behavior — the loader uses
the first matching filter and the rest become inert.

If you can't answer "which category am I?" up front, you're not ready
to write the IA yet — read [`adr/0012-skill-executive-architecture.md`](../adr/0012-skill-executive-architecture.md) and [`../docs/ORCHESTRATOR.md`](../../docs/ORCHESTRATOR.md).

### 5.2.1 `InteractAction` skeleton

```python
from typing import TYPE_CHECKING
from jvspatial.core.annotations import attribute
from jvagent.action.interact.base import InteractAction

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker


class MyInteractAction(InteractAction):
    weight: int = attribute(default=0, description="Top-tier ordering")
    description: str = attribute(default="Does X during /interact")

    async def execute(self, visitor: "InteractWalker") -> None:
        # 1. Evaluation check — return early if we shouldn't run
        if not self._applies(visitor):
            return

        # 2. Access interaction state
        interaction = visitor.interaction
        if interaction is None:
            return

        # 3. Do work
        result = await self._do_work(interaction)

        # 4. Emit response (one of: publish / publish_thought / respond)
        await self.publish(visitor, content=result)

        # 5. (Optional) Route to children
        # child = await self.node(node="ChildInteractAction")
        # if child:
        #     await visitor.visit(child)

    def _applies(self, visitor) -> bool:
        # Custom routing check
        return True

    async def _do_work(self, interaction) -> str:
        return "result"
```

### 5.3 `LanguageModelAction`

See `jvagent/action/model/language/anthropic/anthropic.py` for a reference implementation. Key concerns:
- Define `model_id`, `default_model`, `api_key` attributes.
- Override `query(messages, tools, ...)` (provider-agnostic signature).
- Honor inherited retry config (`max_retries`, `retry_initial_delay`, ...).

---

## 6. `endpoints.py` pattern

```python
"""HTTP endpoints for MyActionClassName."""
from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError

from jvagent.action.base import Action
from .my_action import MyActionClassName


@endpoint(
    "/actions/{action_id}/do-thing",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    description="Triggers the thing.",
)
async def do_thing(
    action_id: str,
    payload: dict = ResponseField(...),
) -> dict:
    action = await Action.get(action_id)
    if not action or not isinstance(action, MyActionClassName):
        raise ResourceNotFoundError(f"MyActionClassName not found: {action_id}")
    result = await action.execute_thing(payload)
    return success_response({"result": result})
```

**Path convention**: all action-owned endpoints SHOULD live under `/actions/{action_id}/...`. The deregister flow ([`base.py:354`](../../jvagent/action/base.py)) uses this prefix to discover and unregister endpoints automatically.

---

## 7. agent.yaml integration

Once shipped, an agent enables your action by adding:

```yaml
# agents/{namespace}/{agent_name}/agent.yaml
actions:
  - action: namespace/action_name
    context:                       # overrides attribute defaults
      api_url: ${MYACTION_API_URL}
      timeout: 60
```

`context:` keys MUST match `attribute(...)` field names on the class. `${...}` env placeholders are expanded by `core/env_resolver.py`.

---

## 8. Tests

Add a test directory matching the action path:

```
tests/action/{action_name}/
├── __init__.py
├── test_basic.py
└── ...
```

Conventions:
- Use `pytest-asyncio` (already configured).
- Mock external HTTP via `pytest-httpx` or `respx`.
- For walker-level tests, construct an `InteractWalker` directly (`tests/action/interact/` has examples).
- For full integration, see `tests/integration/`.

Run a slice:

```bash
pytest tests/action/{action_name}/ -v
```

---

## 9. Action-to-action communication

Inside your action, get another action by class:

```python
# By exact class type — O(1), uses cached index
from jvagent.action.persona.persona_action import PersonaAction
persona = await self.get_action(PersonaAction)

# By class-name string — O(1)
llm = await self.get_action("OpenAILanguageModelAction")

# Any LanguageModelAction (recommended for actions that need a model)
llm = await self.get_model_action(required=True)

# Any subclass of a base — O(n) isinstance scan
from jvagent.action.vectorstore.base import VectorStore
vs = await self.get_action_by_base_class(VectorStore)
```

Source: [`action/base.py:710-852`](../../jvagent/action/base.py).

---

## 10. Tool exposure (Orchestrator tool surface)

If your action's capabilities should be callable by the LLM, **decorate a method
with `@tool`** — the base `get_tools()` discovers it automatically (no override):

```python
from typing import Annotated
from jvagent.tooling.tool_decorator import tool

class MyAction(Action):
    @tool
    async def do_thing(self, target: Annotated[str, "what to act on"]) -> dict:
        """Trigger the thing."""
        return {"ok": True, "target": target}
```

The decorator derives everything from the function:

| Tool field | Source | Override |
|---|---|---|
| `name` | `{action_name}__{method}` (here `my_action__do_thing`) | `@tool(name="…")` |
| `description` | method docstring, first paragraph | `@tool(description="…")` |
| `parameters_schema` | the signature; `Annotated[T, "desc"]` supplies per-arg docs | — |

Supported arg types: `str`/`int`/`float`/`bool`, `List[T]`, `Dict`, `Literal`,
`Enum`, `Optional[T]` (→ not required). The generated schema is always the
portable subset enforced by `tool_schema_validator` — no hand-written JSON Schema
and no drift.

The Orchestrator collects `get_tools()` results from every enabled action and
registers them with an `action__` prefix on the unified tool surface. See
[`../docs/ORCHESTRATOR.md`](../../docs/ORCHESTRATOR.md) for the pattern overview.

**Manual `Tool()` still works.** Override `get_tools()` only when a tool can't be
a decorated method (dynamic tool sets, etc.); combine both by calling
`collect_tools(self)` and extending the result.

---

## 11. Channel adapters

If your action sends/receives over an external channel (Slack, Discord, Telegram, etc.):

1. Subclass `Action` (not `InteractAction`).
2. In `on_startup()` and `on_enable()`, register a channel adapter with the agent's `ResponseBus`:
   ```python
   from jvagent.action.response.channel_adapter import ChannelAdapter
   bus = await (await self.get_agent()).get_response_bus()
   await bus.register_adapter("slack", SlackAdapter(self))
   ```
3. Implement an inbound webhook endpoint (`endpoints.py`) that constructs an `InteractWalker` payload and spawns it on the agent (see `whatsapp/endpoints.py` or `facebook_action/endpoints.py` for examples).
4. The adapter's `send()` method translates `ResponseMessage` to your channel's API.

---

## 12. Cascade-delete safety

If your action creates child Nodes (caches, persistent state), connect them via outgoing edges:

```python
async def on_register(self):
    cache = await MyCacheNode.create(...)
    await self.connect(cache, direction="out")  # cascade-delete on action delete
```

`Action.delete()` ([`base.py:225`](../../jvagent/action/base.py)) walks outgoing edges and recursively deletes children.

---

## 13. Common pitfalls

| Mistake | Fix |
|---|---|
| Forgetting `from . import endpoints` in `__init__.py` | Endpoints don't register. Add the import. |
| Naming the class differently from `archetype` in `info.yaml` | Loader fails to find the class. Match exactly. |
| Top-level `InteractAction` not routing to children | Child actions never execute. Call `await visitor.visit(child)` from `execute()`. |
| Long sync work inside `execute()` | Blocks the response. Use `run_in_background=True` for non-critical work, or enqueue a `PROACTIVE` task via `TaskStore.enqueue_proactive` / `TaskMonitor`. |
| Mutating `self.metadata` directly | Lost on next load — `metadata` is rebuilt from `info.yaml`. Use `attribute(...)` fields for persistent state. |
| Swallowing exceptions in lifecycle hooks | Errors go silent. Let them propagate — the framework's `enable()`/`disable()` wrappers log them. |
| Hard-coding API keys | Use `attribute(default="")` + agent.yaml `${ENV_VAR}` indirection. |
| Skipping `info.yaml` | Loader skips the action package. Always ship one. |

---

## 14. Reference walkthroughs

| Want to ... | Read |
|---|---|
| See a minimal Action | [`persona`](../../jvagent/action/persona/persona_action.py) |
| See an InteractAction with children | [`interview_action`](../../jvagent/action/interview/interview_action.py) |
| See a LanguageModelAction | [`anthropic`](../../jvagent/action/model/language/anthropic/) |
| See a channel adapter | [`whatsapp`](../../jvagent/action/whatsapp/whatsapp_action.py) |
| See a background InteractAction | grep `run_in_background = attribute(default=True)` |
