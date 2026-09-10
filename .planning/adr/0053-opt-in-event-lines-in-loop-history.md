# ADR 0053 — `[EVENT]` lines in loop history are opt-in, not forbidden

**Status**: Accepted
**Date**: 2026-09-09
**Relation**: Amends the "Loop history" row of [ADR-0041](0041-gearing-and-cost-policy-in-core.md) §2 (`[EVENT]` lines never included), the same way [ADR-0050](0050-planning-gear-escalation.md) amends its gearing row. The rest of ADR-0041's policy table stands.

---

## 1. Context

ADR-0041 collapsed a seven-knob cost panel into hard-coded policy. Two of the
knobs it deleted concerned loop history: `include_history_events` and
`history_max_statement_length`. The measured default was the right law in both
cases, so the dial went away and the behaviour was fixed in core: history is
untruncated, and interaction `[EVENT]` lines are never fed back to the loop.

Half of that has already been walked back once. Untruncated history turned out
to be unaffordable when the same history is re-sent on every tick, so
`history_statement_max_chars` (default 4000) came back as sizing config — the
per-tick multiplier, not the policy, was the thing ADR-0041 had mis-measured.

The event half has the same shape. `Interaction.events` are log annotations an
action writes about what the agent *did* out of band — "Report form was sent to
the user", "Handoff to a human was requested". For a turn-based responder they
are noise: the reply already says what happened. For an agent whose actions
have side effects the user can see, dropping them means the loop model has no
record that the side effect occurred, and it re-offers or re-performs work that
a prior turn already completed. There is no way to express that from YAML,
because ADR-0041 removed the only knob that could.

The cost objection to events is real and is the reason ADR-0041 ruled them out:
an event line is free-form text with no length contract, and history is replayed
on every tick, so one long event is billed once per step per turn.

## 2. Decision

**`with_event` returns as opt-in product config, default `false`, and events are
capped like every other replayed statement.**

1. `OrchestratorInteractAction.with_event` (new attribute, default `false`)
   selects whether `[EVENT]` lines from **prior** interactions enter loop
   history. It resolves through `_channel_cfg`, so a channel may differ from the
   action-level value, and it joins the documented `channel_overrides`
   whitelist.
2. The **current** interaction is excluded from its own history, as before, so
   a turn never sees the events it is in the middle of writing.
3. `history_statement_max_chars` now bounds each `[EVENT]` line as well as each
   replayed utterance/response. `Conversation.get_interaction_history` gains an
   explicit `max_event_length` for this; `max_statement_length` deliberately
   still does not apply to events, so no existing caller's output changes.

Default-off keeps ADR-0041's law as the shipped behaviour: an operator who never
touches the knob gets exactly the prompt they get today.

## 3. Consequences

- The default prompt is byte-identical to pre-0053. Nothing is billed until an
  operator opts in.
- Opting in costs, per tick, the sum of the capped event lines across
  `history_limit` interactions — bounded, and bounded by a knob that already
  exists rather than a new one.
- ADR-0041's argument against the knob (proliferation, no production need) is
  answered by a production need, not by relaxing the principle: this is the
  second and last of the two history knobs it deleted, and both came back only
  where the per-tick replay made the fixed law wrong.
- `Conversation.get_interaction_history`'s contract is now explicit that events
  ignore `max_statement_length` and honour `max_event_length`. Callers that pass
  neither (handoff, `call_model`, `InteractAction.get_history`) are unaffected.

## 4. Verification

`tests/action/orchestrator/test_history_events.py`: events omitted by default;
included when the knob is on; the channel override wins; each line capped at
`history_statement_max_chars` and uncapped at `0`.
`tests/memory/test_history_event_cap.py`: `max_event_length` caps formatted
`[EVENT]` content and raw `events` entries, leaves them alone when unset, and
does not mutate the stored event list.
