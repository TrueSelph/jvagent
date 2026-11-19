# IntroInteractAction

Welcomes a first-time visitor by folding a short self-introduction into their
first reply.

## What it does

On a new user's first message, `IntroInteractAction` contributes its intro text
as **two parameters** with the same `directive` copy:

- **`scope: orchestration`** (`key: intro.first_message`) — so the executive that
  authors the reply directive sees the custom intro in its system prompt
- **`scope: response`** (`key: intro.first_message`) — so ReplyAction can still
  weave the greeting if a bare directive slips through

Neither is a standalone directive. The single reply egress (`ReplyAction`) then
weaves the greeting into the *same* reply as whatever the rest of the turn
produced — so a new visitor who opens with a real question gets **one coherent
answer that also introduces the agent**, rather than a canned greeting stacked
on top of a second, separate answer.

- **First engagement only** — runs when `visitor.new_user` is true (first time this
  user appears under the agent's Memory). Skips returning users, session resumes,
  and new conversations for an already-known `user_id`.
- **Coexists with the orchestrator** — because the greeting is a phrasing rule,
  the orchestrator's answer stays the single unit of content; the intro just
  makes the reply open with a brief self-introduction.
- **Runs before the executive** — `weight = -300`, `always_execute = True`, so its
  parameters are on the interaction before any downstream action queues a reply.

## Configure (agent.yaml)

Put the full custom intro in `directive` only. Do **not** duplicate it into
`orchestrator.system_prompt_extra` or reply custom parameters — the action
publishes the text into both scopes automatically.

```yaml
actions:
  - action: jvagent/intro_interact_action
    context:
      enabled: true
      # Full custom intro + when to skip. Written as a lead-in ("open your
      # reply by …, then continue"), not a standalone message.
      directive: >-
        This is the visitor's first message: a greeting or small talk with no
        request opens the reply with who you are by name and company, two or
        three concrete examples of what you help with, and an invitation to
        ask. A product, price, stock, policy, quote, or order message is not a
        greeting: skip the introduction and answer directly. Never mention a
        cutoff, model, or provider.
```

## Attributes

| Attribute        | Type | Default | Description |
|------------------|------|---------|-------------|
| `directive`      | str  | built-in lead-in intro | First-message self-introduction, applied as orchestration + response parameters |
| `weight`         | int  | `-300`  | Runs before the Orchestrator (`-200`) |
| `always_execute` | bool | `True`  | Executes regardless of routing (gated internally by `new_user`) |

## Flow

```
new user's first message
  → IntroInteractAction (weight -300):
      add_parameter({key: intro.first_message, scope: orchestration, response: directive})
      add_parameter({key: intro.first_message, scope: response, response: directive})
  → orchestrator sees orchestration param → authors reply that includes the intro
  → ReplyAction: renders ONE reply — greeting woven in per the parameter, answer per the directive
returning user
  → IntroInteractAction: skips (visitor.new_user is False)
```

## Files

- `intro_interact_action.py` — the action class
- `__init__.py` — package init
- `info.yaml` — package metadata
