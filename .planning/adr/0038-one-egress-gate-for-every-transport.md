# ADR-0038 — One egress gate, identical on every transport

- **Status:** Accepted
- **Date:** 2026-07-26
- **Relates to:** [ADR-0024](0024-single-per-turn-egress.md) (single per-turn egress), [ADR-0025](0025-replyaction-single-output-contract.md) (ReplyAction output contract), [ADR-0037](0037-parameters-and-directives-as-the-only-customization-surfaces.md) (parameters own their enforcement)
- **Supersedes in part:** ADR-0037 §3's claim that streaming egress "cannot be scrubbed"

---

## 1. Context

ADR-0037 made a parameter own its enforcement, including a deterministic
`scrub` applied at egress. It also recorded, as a known limit, that streaming
could not be scrubbed because "the tokens have left."

That limit was then observed live. The demo agent, asked "hello", replied:

> Hello! I am Orchestrator Agent, here to help you with your needs. **Hello!**
> How can I assist you today?

`voice.single_greeting` exists precisely to stop this, and its detector fixes
the string when applied. It was not applied. The same agent, same text, over the
REST API, replied correctly with one greeting.

So the rule held or not depending on **which transport the user happened to be
on** — and the transport where it did not hold is the messenger, the
customer-facing one.

### The actual cause

Governance was applied by callers, and there were several:

| Path | Scrubbed? |
|---|---|
| `ReplyAction._pipe_response` | yes |
| `InteractAction.publish` | yes (again) |
| `ResponseBus` streaming chunks | **no** |
| `ResponseBus.commit_pending_adhoc` | **no** |
| `SuggestionsInteractAction` chips | only after ADR-0037 |

Two implementations that agreed, one path with none, and a second accumulator
exit nobody had noticed. "Streaming can't be scrubbed" also turned out to be
false for the most common shape: `ResponseBus.publish(stream=True,
streaming_complete=True)` receives the **whole text** and chops it into
pseudo-chunks for display. Nothing had left. It simply was not scrubbed.

---

## 2. Decision

**The ResponseBus is the single egress gate.** Every user-facing byte leaves
through `ResponseBus.publish`, so the response rules are applied there and
nowhere else. Callers stop scrubbing.

And the property that makes it meaningful:

> What the user receives is a function of the reply text and the active
> parameters. It is never a function of how the text was transported.

`EgressGate` ([`jvagent/action/egress_gate.py`](../../jvagent/action/egress_gate.py))
is the one implementation. Non-streaming scrubbing is the degenerate case of it
(`feed` everything, then `close`), so the two cannot drift — there is no second
code path to keep in sync.

### 2.1 How streaming stays equivalent

The detectors are whole-text functions and not all are causal: a closer is
removed only when *trailing*, which is unknowable until the text ends. So the
gate does not scrub chunk-by-chunk. It re-scrubs the accumulated prefix and
releases only what can no longer change:

- **Unterminated text is never judged.** Mid-stream, `"I am an A"` does not match
  the rule that `"I am an AI"` matches one character later. Only text up to the
  last sentence terminator is settled. (This was a real bug in the first draft,
  caught by the split-point tests below — the gate emitted `"I am an A"`.)
- **A trailing run of closers is withheld**, because a later sentence would make
  it non-trailing and therefore keepable.
- **Nothing leaves while every sentence so far has been dropped**, because the
  "don't blank a reply that is only a leak" fallback can still change the answer.
- **`close()` recomputes from the whole text** and emits the remainder.

Because `close()` always recomputes from the full text, the concatenation of
everything emitted equals `vet_egress(full_text)`.

### 2.2 The invariant is tested, not argued

`tests/action/test_egress_gate.py` asserts, for every sample text, that feeding
it through the gate broken at **every single split point**, at every **pair** of
split points, and **one character at a time**, produces exactly the whole-text
scrub. Equivalence is a test result, not a claim in a docstring.

### 2.3 Cost

A reply containing no sentence terminator is not released until end of stream.
In practice model replies are punctuated; the pathological case is a long
unpunctuated block, which arrives at once instead of progressively. Correctness
was preferred to progressive rendering. `strict_buffering=True` opts out of
progressive emission entirely.

---

## 3. Consequences

**Good.** One place to look and one place to change. A rule cannot hold on one
channel and not another, because there is only one gate. The streaming
limitation in ADR-0037 §3 is closed rather than documented.

**Contract change.** An incremental `stream_chunk` may now be empty: the gate is
holding that text until it settles. Anything reading chunks must accumulate
rather than assume each chunk is displayable text. The end-of-stream tail is
emitted as a real chunk so progressive clients still receive the last sentence.

**Not solved.** A registered scrub detector that rewrites text globally rather
than per sentence could break the prefix property. The gate detects that at
`close()` — emitted text would no longer be a prefix of the final text — and
reports it instead of silently shipping unsanctioned output. All built-in
detectors are per-sentence or the one known trailing rule.

**Residual.** `ReplyAction` still scrubs on the no-bus path, because there it
*is* the egress — the text goes straight into `interaction.response`, which the
caller returns and which replays as history. It calls the same gate, so this is
one implementation used in two places, not two implementations.
