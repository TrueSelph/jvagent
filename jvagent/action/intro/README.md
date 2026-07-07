# IntroInteractAction

Welcomes a first-time visitor by folding a short self-introduction into their
first reply.

## What it does

On a new user's first message, `IntroInteractAction` contributes its intro text
as a **response-shaping parameter** (a "HOW" rule), not a standalone directive.
The single reply egress (`ReplyAction`) then weaves the greeting into the *same*
reply as whatever the rest of the turn produced — so a new visitor who opens with
a real question gets **one coherent answer that also introduces the agent**,
rather than a canned greeting stacked on top of a second, separate answer.

- **First-message only** — runs when `visitor.new_user` is true; skips returning
  users.
- **Coexists with the orchestrator** — because the greeting is a phrasing rule,
  the orchestrator's answer stays the single unit of content; the intro just
  makes the reply open with a brief self-introduction.
- **Runs before the executive** — `weight = -300`, `always_execute = True`, so its
  parameter is on the interaction before any downstream action queues a reply.

## Configure (agent.yaml)

```yaml
actions:
  - action: jvagent/intro_interact_action
    context:
      enabled: true
      # Phrasing rule applied to the first reply. Written as a lead-in ("open
      # your reply by …, then continue"), not a standalone message.
      directive: >-
        This is the visitor's first message: open your reply by briefly
        introducing yourself by name and what you help with (one short
        sentence), then continue naturally into the rest of your reply.
```

## Attributes

| Attribute        | Type | Default | Description |
|------------------|------|---------|-------------|
| `directive`      | str  | built-in lead-in intro | First-message self-introduction, applied as a response parameter |
| `weight`         | int  | `-300`  | Runs before the router/executive (`-200`) |
| `always_execute` | bool | `True`  | Executes regardless of routing (gated internally by `new_user`) |

## Flow

```
new user's first message
  → IntroInteractAction (weight -300): visitor.add_parameter({response: directive})
  → orchestrator / other actions queue the answer directive
  → ReplyAction: renders ONE reply — greeting woven in per the parameter, answer per the directive
returning user
  → IntroInteractAction: skips (visitor.new_user is False)
```

## Files

- `intro_interact_action.py` — the action class
- `__init__.py` — package init
- `info.yaml` — package metadata
