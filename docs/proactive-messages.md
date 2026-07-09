# Proactive Messages

A **proactive message** is text the agent emits *without* an inbound user webhook — for example, a scheduled reminder, an integration callback, an admin-triggered notification, or any code path that pushes a message out to a specific user/session/channel.

This page documents the canonical programmatic API for **canned** proactive delivery: **`Agent.send_proactive_message(...)`**.

For **agentic** follow-ups (the model must reason, use tools/skills, and compose a reply from conversation state), queue a `PROACTIVE` task and let **`TaskMonitor`** dispatch it through the full Orchestrator — see [task-tracking.md](task-tracking.md).

| Need | API / action |
|------|----------------|
| Pre-formed text, deliver now | `Agent.send_proactive_message(...)` (this page) |
| LLM-generated reply from context | `enqueue_proactive` / `queue_task` / `TaskCreationInteractAction` → `TaskMonitor` |
| Keyword/mood trigger on user turn | `TaskTriggerInteractAction` + Orchestrator finalizer |

---

## At a glance

```python
agent = await Agent.get(agent_id)

interaction = await agent.send_proactive_message(
    user_id="<user / phone / external id>",
    content="Heads up — your reminder is due.",
    channel="whatsapp",                    # any channel with a registered adapter
    source_action="MySchedulerJob",        # optional — defaults to "ProactiveDispatch"
    metadata={"job_id": "j-123"},          # optional — merged into the proactive tag
)
```

Single call. The bus dispatches `content` to the channel adapter (so the user receives the message) and records the result on a new `Interaction` node (so the agent has context on the user's next reply).

---

## What it does

1. **Resolves the `User`** via `Memory.get_user(user_id, create_if_missing=True)` ([`memory/manager.py:76`](../jvagent/memory/manager.py)). Lock-guarded to avoid duplicate creation.
2. **Resolves the `Conversation`** in this order:
   - If `session_id` is provided → `User.get_conversation_by_session(session_id)`.
   - Else → `User.get_active_conversation()`.
   - Else → `User.create_conversation(session_id=session_id or "", channel=channel)`.
3. **Creates an `Interaction`** via `Conversation.add_interaction(utterance="", channel=..., session_id=...)`. The empty utterance flags this entry as proactive.
4. **Tags origin** on `Interaction.parameters` (see [Metadata](#metadata-on-the-interaction) below).
5. **Publishes** via `ResponseBus.publish(category="user", interaction=..., content=..., channel=..., user_id=..., metadata=...)`:
   - the registered **channel adapter** receives the message and delivers it to the channel transport (WhatsApp, Messenger, email, SSE, etc.);
   - `interaction.response` is set to `content` and the Interaction is saved;
   - any session subscribers (SSE clients) are notified.

The return value is the persisted `Interaction`, or `None` if the call was skipped (missing inputs, no memory attached, user lookup failed).

Source: [`jvagent/core/agent.py:271-358`](../jvagent/core/agent.py).

---

## API

```python
async def send_proactive_message(
    self,
    *,
    user_id: str,
    content: str,
    channel: str,
    session_id: Optional[str] = None,
    source_action: str = "ProactiveDispatch",
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional["Interaction"]:
```

| Param | Required | Meaning |
|---|---|---|
| `user_id` | yes | Target user identifier (the same string used by inbound webhooks for that user — e.g. phone number, external account id). |
| `content` | yes | Text to deliver. Pre-formed; no LLM generation happens here. |
| `channel` | yes | Channel key that matches a `ChannelAdapter` registered on this agent's `ResponseBus` (e.g. `"whatsapp"`, `"default"`). |
| `session_id` | no | Pin to a specific conversation. If omitted, the user's active conversation is used (or a new one is created on demand). |
| `source_action` | no | Action-name tag for the proactive parameter entry. Defaults to `"ProactiveDispatch"`. Useful for downstream filters and post-hoc analytics. |
| `metadata` | no | Arbitrary dict merged into the proactive tag entry (e.g. `{"job_id": "...", "trigger": "..."}`). |

Empty `user_id` / `content` / `channel` short-circuits and returns `None`.

---

## What the resulting Interaction looks like

After the call, the new `Interaction` node has:

| Field | Value |
|---|---|
| `utterance` | `""` (empty — the proactive marker) |
| `response` | The `content` you passed (set by the bus, may grow if you publish more chunks with the same `interaction=`). |
| `channel` | The `channel` argument. |
| `session_id` | The resolved session id. |
| `conversation_id`, `user_id` | Set by `add_interaction`. |
| `parameters` | Includes a flat dict: `{"is_proactive": True, "action_name": <source_action>, "executed": False, ...metadata}`. |

Chain semantics are identical to inbound interactions — bidirectional edge to the prior `Interaction`, count bumped on the `Conversation`, pruning honored when `Agent.interaction_limit > 0`.

### Metadata on the Interaction

The `metadata` dict you pass and the implicit `{"is_proactive": True}` flag are merged together and stored on `Interaction.parameters` via [`Interaction.add_parameter`](../jvagent/memory/interaction.py). The merged dict is **flat** — there is no `"data"` sub-key:

```python
await agent.send_proactive_message(
    user_id="u1", content="Hi", channel="whatsapp",
    source_action="SchedulerJob",
    metadata={"job_id": "j-123", "reason": "reminder"},
)

# Interaction.parameters now contains:
# [
#   {
#     "is_proactive": True,
#     "job_id": "j-123",
#     "reason": "reminder",
#     "action_name": "SchedulerJob",
#     "executed": False,
#   }
# ]
```

To find proactive entries later:

```python
proactive = [
    p for p in interaction.parameters
    if p.get("is_proactive") is True
]
# Or by source:
by_source = [
    p for p in interaction.parameters
    if p.get("action_name") == "SchedulerJob"
]
```

`add_parameter` deduplicates: identical `(action_name, content)` pairs collapse and `executed` flips back to `False` ([`interaction.py:323-336`](../jvagent/memory/interaction.py)).

---

## How it appears in LLM history

`Conversation._format_interactions` ([`conversation.py:674`](../jvagent/memory/conversation.py)) **skips** the `role: "user"` entry when the utterance is empty/whitespace. A proactive interaction therefore renders as a standalone `assistant` turn in the model's history, sitting cleanly between the surrounding user/assistant pairs.

Example transcript fed to the LLM:

```jsonc
[
  {"role": "user",      "content": "Hey, can you remind me later?"},
  {"role": "assistant", "content": "Sure — I'll ping you tonight."},
  // ── time passes; scheduler fires Agent.send_proactive_message ──
  {"role": "assistant", "content": "Heads up — your reminder is due."},
  // ── user replies; normal inbound webhook records this one ──
  {"role": "user",      "content": "Thanks!"},
  {"role": "assistant", "content": "Anytime."}
]
```

---

## When to use it (and when not)

**Use `Agent.send_proactive_message` when:**

- You have pre-formed text to deliver (no LLM call needed).
- You want both delivery AND history recording in a single call.
- The message originates outside the inbound webhook pipeline (scheduled job, integration callback, admin tool).
- You want it to work uniformly across channels — same code for WhatsApp, Messenger, email, SSE.

**Do not use it when:**

- The message must be LLM-generated based on conversation state. Queue a `PROACTIVE` task (`enqueue_proactive`, `queue_task`, or `TaskCreationInteractAction`) and let `TaskMonitor` dispatch it through the full Orchestrator pipeline. See [`docs/task-tracking.md`](task-tracking.md) and ADR-0022.
- You want to record a message that another system already delivered (e.g. the human owner of a WhatsApp account typed a reply directly via the WhatsApp UI). Publishing through this method would re-send the text. A record-only sibling helper is out of scope for now.
- You are already inside an `InteractAction.execute(visitor)` and just want to send a reply for the current turn. Use `await self.publish(visitor, content)` / `await self.respond(visitor, ...)` — the canonical in-pipeline path ([`interact/base.py:293`](../jvagent/action/interact/base.py)).

---

## Channel requirements

The `channel` argument must match a `ChannelAdapter` that has registered itself with the agent's `ResponseBus` (typically during the channel action's `on_startup` lifecycle hook). If no adapter is registered for the channel:

- the `Interaction` is still created and `interaction.response` is still set to `content` (the bus' record path runs regardless);
- adapter dispatch is a no-op — no external delivery happens.

This means recording survives misconfiguration, but external delivery silently doesn't happen. Always verify the channel action is enabled and the adapter is registered before relying on proactive sends for that channel.

---

## Concurrency

`Conversation.add_interaction` acquires the distributed `conversation_mutation_lock` ([`conversation.py:277`](../jvagent/memory/conversation.py)). Proactive sends serialize against inbound webhooks on the same conversation, so the chain never forks.

---

## Examples

### Scheduled reminder

```python
# inside a cron-style worker or an APScheduler job
from jvagent.core.agent import Agent

agent = await Agent.get(agent_id)
await agent.send_proactive_message(
    user_id=reminder.user_id,
    content=f"Reminder: {reminder.title}",
    channel="whatsapp",
    source_action="ReminderScheduler",
    metadata={"reminder_id": reminder.id, "trigger": "due_at"},
)
```

### Integration webhook callback

```python
# inside an HTTP endpoint that handles a third-party callback
@endpoint("/integrations/calendly/booked", methods=["POST"])
async def calendly_booked(payload: dict) -> dict:
    agent = await Agent.get(payload["agent_id"])
    await agent.send_proactive_message(
        user_id=payload["invitee_phone"],
        content=f"Confirmed for {payload['event_time']}.",
        channel="whatsapp",
        source_action="CalendlyWebhook",
        metadata={"event_uri": payload["event_uri"]},
    )
    return {"ok": True}
```

### Admin broadcast (one user at a time)

```python
agent = await Agent.get(agent_id)
for user_id in target_users:
    await agent.send_proactive_message(
        user_id=user_id,
        content=announcement_text,
        channel="default",
        source_action="AdminBroadcast",
        metadata={"campaign_id": campaign.id},
    )
```

(For multi-channel fan-out to a single user, iterate the channels yourself.)

### Queued agentic follow-up (TaskMonitor)

When the agent must **generate** the message using tools and conversation context:

```python
from jvagent.memory.task_proactive import ProactiveTaskSpec

spec = ProactiveTaskSpec(
    directive="Check whether the user finished scheduling and offer to continue",
    context="User said they were busy; follow up in 10 minutes",
    not_before="2026-06-08T10:10:00+00:00",
    trigger_on="schedule",
)
await agent.enqueue_proactive_task(
    user_id=user_id,
    spec=spec,
    channel="whatsapp",
)
```

`TaskMonitor` (native scheduler or `GET /api/proactive/tick/{agent_id}`) claims the task and runs a full Orchestrator turn. Do **not** call `send_proactive_message` for this path unless you already have final text.

---

## Tests

- `tests/core/test_agent_proactive.py` — end-to-end behavior (Interaction shape, bus kwargs, User/Conversation bootstrap, session routing, validation, parameter tagging).
- `tests/memory/test_history_empty_utterance.py` — LLM history serializer skips blank `user` role for empty-utterance entries.

---

## See also

- [SPEC §7.1](../.planning/SPEC.md) — normative semantics.
- [GLOSSARY: Proactive message / Proactive interaction](../.planning/GLOSSARY.md).
- [Architecture §6.1 — Proactive (out-of-walker) sends](../.planning/architecture.md).
- [`docs/task-tracking.md`](task-tracking.md) — `PROACTIVE` queue, `TaskMonitor`, scheduler setup.
- [ADR-0022](../.planning/adr/0022-proactive-task-monitor.md) — architecture and dispatch model.
- [`docs/ORCHESTRATOR.md`](ORCHESTRATOR.md) — in-walker response emission (the inbound counterpart).
