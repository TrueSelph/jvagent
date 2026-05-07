---
name: converse
description: >-
  Reply to the user briefly and in character via the agent persona. Use for
  greetings, acknowledgments, smalltalk, off-task chat, brief follow-ups,
  and any utterance that does not require a tool, lookup, or research step.
  No tools, no engine loop — the cockpit hands the utterance straight to
  PersonaAction so the response is low-latency and persona-shaped.
always-active: true
response-mode: respond
version: 1
tags:
  - conversational
  - smalltalk
  - persona
  - low-latency
plan-steps:
  - Reply briefly in character via PersonaAction
---

## When to engage

Pick `converse` when the user's message is one of:

- **Greetings / pleasantries** — "hi", "hello", "good morning", "thanks", "you're welcome".
- **Acknowledgments** — "ok", "got it", "sounds good", "noted".
- **Smalltalk / off-task** — anything that doesn't request a lookup, an action,
  or evidence-backed information.
- **Brief follow-ups** — "and?", "what do you mean?", "go on" — when the
  preceding turn already supplied the substantive content.
- **Persona-only replies** — anything where the right answer is "respond as the
  agent would, briefly and in character" with no tool call needed.

## When NOT to engage

Do NOT pick `converse` when:

- The user asks a factual or knowledge question that needs evidence (use the
  `answer` skill or another retrieval skill instead).
- The user requests an action that requires a tool (search, send, fetch,
  ingest, generate). Pick the matching skill.
- The user references a specific document, file, URL, or external system that
  must be consulted before responding.
- The user has an active task that they're checking on — surface task status
  rather than chatting past it.

## Workflow

The cockpit handles dispatch structurally for this skill: when `converse` is
the only routed skill, the engine is bypassed entirely and the utterance is
delivered to PersonaAction directly. There is no tool to call. Keep the
reply short, in-character, and aligned with the user's tone.

## Constraints

- Never fabricate facts. If a brief reply would require knowledge you don't
  have, decline politely and suggest the user clarify or rephrase.
- Match the user's register (formal / casual / brief / playful).
- Do not append invitation closers ("let me know if…", "feel free to ask…").
- Do not narrate process ("I see you said hello, so I'll greet you back").
