"""The single gate every user-facing byte leaves through.

Egress governance used to be applied by whichever caller happened to remember
it: ``ReplyAction`` scrubbed its composed text, ``InteractAction.publish``
scrubbed again, and the streaming path scrubbed not at all. Whether a rule held
therefore depended on the transport — a reply that was clean over the REST API
came out dirty over the messenger, because the messenger streams. That is not a
policy anyone chose; it is three code paths disagreeing.

This module states the property directly:

    What the user receives is a function of the reply text and the active
    parameters. It is never a function of how the text was transported.

``EgressGate`` is the one implementation. Non-streaming scrubbing is the
degenerate case of it (``feed`` everything, then ``close``), so the two cannot
drift apart — there is nothing to keep in sync.

## How streaming stays equivalent

The detectors are whole-text functions and some are not causal: a closer is only
removed when it is *trailing*, which is unknowable until the text ends. So the
gate does not try to scrub chunk-by-chunk. It re-scrubs the accumulated prefix
on every feed and emits only the part that cannot still change:

* text already emitted is never retracted — it cannot be, so it is never
  emitted until it is safe;
* a trailing run of closer sentences is withheld, because a later sentence may
  yet make it non-trailing;
* while every sentence so far has been dropped, nothing is emitted, because the
  "don't blank a reply that is only a leak" fallback (see ``vet_egress``) can
  still change the answer;
* ``close()`` recomputes from the *whole* text and emits the remainder.

Because ``close()`` always recomputes from the full text, the concatenation of
everything emitted equals ``vet_egress(full_text)`` exactly. That is asserted
directly in the tests, over every possible chunk splitting of the sample texts,
rather than argued for here.

## Custom detectors

The reasoning above holds for detectors that are causal per sentence (drop this
sentence, rewrite this sentence) plus the one trailing rule the gate knows about.
A registered detector that reorders or rewrites text globally could break it.
The gate detects that case at ``close()`` — the emitted text would no longer be
a prefix of the final text — and reports it rather than silently shipping
something the rules did not sanction. ``strict_buffering`` opts out of
progressive emission entirely for deployments that would rather wait than
reason about it.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from jvagent.action.parameters import (
    _CLOSER_PATTERNS,
    _SENTENCE_RE,
    vet_egress,
)

logger = logging.getLogger(__name__)

__all__ = ["EgressGate", "scrub_text"]


def _split_sentences(text: str) -> List[str]:
    return [m.group(0) for m in _SENTENCE_RE.finditer(text)]


def _is_closer_sentence(sentence: str) -> bool:
    return any(p.search(sentence) for p in _CLOSER_PATTERNS)


def _settled_prefix(text: str) -> str:
    """The part of *text* whose sentences are complete.

    A trailing fragment is not a sentence yet and must never be judged as one:
    mid-stream, ``"I am an A"`` does not match the self-disclosure rule that
    ``"I am an AI"`` matches one character later. Emitting on the strength of an
    unfinished sentence is how a leak escapes a gate that is otherwise correct.
    """
    cut = max(text.rfind("."), text.rfind("!"), text.rfind("?"))
    return text[: cut + 1] if cut >= 0 else ""


def _strip_trailing_closers(text: str) -> str:
    """Drop a trailing run of closer sentences.

    Withheld rather than emitted, because a sentence that follows would make
    them non-trailing and therefore keepable. Mirrors ``_detect_peel_closers``'s
    shape, but here it decides what is *not yet safe to send*, not what to
    delete.
    """
    sentences = _split_sentences(text)
    while sentences and _is_closer_sentence(sentences[-1]):
        sentences.pop()
    return "".join(sentences)


class EgressGate:
    """Accumulates outgoing text and releases only what can no longer change.

    Usage is the same whether or not the transport streams::

        gate = EgressGate(parameters)
        for chunk in chunks:
            send(gate.feed(chunk))
        send(gate.close())

    ``feed`` and ``close`` return the text to emit now, possibly ``""``.
    """

    def __init__(
        self,
        parameters: Optional[List[Any]] = None,
        *,
        allow_empty: bool = False,
        strict_buffering: bool = False,
    ) -> None:
        self._parameters = parameters
        self._allow_empty = allow_empty
        self._strict = strict_buffering
        self._buffer = ""
        self._emitted = ""
        self._closed = False

    @property
    def emitted(self) -> str:
        """Everything released so far. Read-only; the gate cannot retract."""
        return self._emitted

    def feed(self, chunk: str) -> str:
        """Accept more text; return the portion that is now safe to send."""
        if self._closed:
            raise RuntimeError("EgressGate.feed() after close()")
        if not chunk:
            return ""
        self._buffer += chunk
        if self._strict:
            return ""

        # Scrub the prefix with the blank-guard OFF. With it on, an all-leak
        # prefix would come back unchanged and we would emit a leak that the
        # full text is going to delete.
        settled = _settled_prefix(self._buffer)
        if not settled:
            return ""
        candidate = vet_egress(settled, self._parameters, allow_empty=True)
        if not candidate:
            # Everything so far is a rule break. The fallback that keeps a
            # reply which is *only* a leak can still fire at close(), so the
            # answer is not settled and nothing may leave yet.
            return ""

        safe = _strip_trailing_closers(candidate)
        if not safe.startswith(self._emitted):
            # A detector rewrote text we already released. Nothing can be done
            # about the bytes already gone; stop making it worse.
            logger.warning(
                "egress gate: scrubbed prefix diverged from emitted text; "
                "holding the remainder until close()"
            )
            return ""
        delta = safe[len(self._emitted) :]
        self._emitted += delta
        return delta

    def close(self) -> str:
        """Finish the message; return the remaining text to send.

        Recomputes from the whole buffer, so the total emitted equals a
        straight ``vet_egress`` of the full text.
        """
        if self._closed:
            return ""
        self._closed = True
        final = vet_egress(
            self._buffer, self._parameters, allow_empty=self._allow_empty
        )
        if not final.startswith(self._emitted):
            logger.error(
                "egress gate: %d chars were emitted that the final scrub does "
                "not sanction (a non-causal scrub detector is registered); "
                "sending only the unambiguous remainder",
                len(self._emitted),
            )
            # Everything already sent is unretractable. Emit nothing further
            # rather than compounding it with text that assumes a prefix.
            return ""
        remainder = final[len(self._emitted) :]
        self._emitted = final
        return remainder


def scrub_text(
    text: str,
    parameters: Optional[List[Any]] = None,
    *,
    allow_empty: bool = False,
) -> str:
    """Whole-text scrub expressed through the gate.

    Exists so the non-streaming path is provably the same code as the streaming
    one rather than a second implementation that agrees today.
    """
    gate = EgressGate(parameters, allow_empty=allow_empty)
    return gate.feed(text) + gate.close()
