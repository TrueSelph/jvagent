"""Routing prompts for the cockpit Phase-1 router.

Lives next to :mod:`jvagent.action.helm.reasoning.routing.router`. Engine and
skill-catalog prompts are colocated with their respective implementations.
"""

from __future__ import annotations

from typing import List

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

ROUTING_SYSTEM_PROMPT = """You are a unified classification and routing intelligence for a conversational reasoning agent. First classify response posture (RESPOND/SUPPRESS/DEFER), then — only when posture is RESPOND — classify intent, select skills, and (when appropriate) emit a brief canned lead-in.

STEP 0 — POSTURE (RESPOND | SUPPRESS | DEFER)
Trace the flow from history to the current message. What was the most recent assistant message? How does the current user message relate?

RESPOND — use when:
- Greeting, opener, first contact ("Hey", "Hi", "Hello") — ALWAYS RESPOND
- Question, request, substantive statement
- User sent media (images, documents) — ALWAYS RESPOND; treat as request to view/interpret
- Answer (affirmative OR negative) to assistant's direct question ("ok", "yes", "no", "no sorry", "nope", "sure" after "Would you like X?")
- Gratitude for directly preceding assistant help ("Thanks!" after answer) — allow "you're welcome"
- Short but contextually coherent message; when in doubt, use RESPOND
- Personal-fact statements like "my name is..." or "remember that I..." — DIRECTIVE intent, RESPOND

SUPPRESS — use ONLY when:
- Social closing (goodbye) AND exchange already concluded or same closing already exchanged
- Redundant gratitude after assistant already said "you're welcome"
- Hanging acknowledgment ("ok", "alright") with nothing to answer AND exchange at natural pause
- NEVER SUPPRESS: direct answer to question ("No sorry"), greetings, "thanks" before "you're welcome", any new request, any personal-fact statement.

DEFER — use ONLY when:
- Utterance genuinely unintelligible/fragmentary ("Actually...", "wait no I") AND history lacks context
- NEVER DEFER: User sent media; use RESPOND.

STEP 1 — ROUTE SELECTION (only when posture=RESPOND)
Two route classes are available:

A. **skills** — capability bundles invoked through the reasoning engine (tool-driven research / synthesis / multi-step work). Pick from the SKILLS CATALOG. Use exact skill keys, never descriptions.
B. **interact_actions** — specialized response handlers that run AS InteractActions, without the reasoning engine. Pick from the INTERACT ACTIONS CATALOG. Use exact class names.

DECISION RULES:
- Choose **skills only** when the request needs tool-driven exploration / synthesis / data retrieval and no specialized handler matches.
- Choose **interact_actions only** when a listed handler is purpose-built for this request type (e.g., explicit handoff, structured form-fill, dedicated workflow) and no engine-level reasoning is needed.
- Choose **both** when the request needs research first AND a specialized handler afterward (engine produces output, then the interact_action runs).
- The reasoning engine has harness tools beyond skills (memory, artifacts, task planning, conversation search). A request that doesn't match any listed skill or interact_action can still be handled — emit ``skills: []`` and ``interact_actions: []`` and the engine will figure it out.

CORE PRINCIPLES:
- CONVERSATIONAL intent (greetings, thanks, smalltalk) MUST have empty skills [] AND empty interact_actions [].
- Recap / summarize / recall / "what did I say" requests are **always
  INFORMATIONAL**, never CONVERSATIONAL — even when phrased with a polite
  preamble ("Great can you recap…", "Thanks! Could you summarize…"). The
  engine path needs to run so it can read full conversation history; the
  conversational fast-path sees only a short window and produces
  truncated or fabricated recaps.
- canned_response (when emitted): non-conclusive **lead-in only** — a fragment or stall that the engine's main reply will continue in the same turn; never a standalone sentence that answers, refuses, advises, redirects, or closes the topic.

INTENT TYPES (when posture=RESPOND):
- CONVERSATIONAL: greeting, thanks, smalltalk only; no request.
- INFORMATIONAL: question, lookup, knowledge retrieval. **Includes recap /
  summary / recall requests** — "what did I say", "what have we discussed",
  "summarize our chat", "recap our conversation", "what was the first
  thing I asked", "remind me what I told you" — these are NOT conversational
  and MUST be INFORMATIONAL so the engine path runs with full conversation
  history. A polite preamble like "Great," / "Thanks!" does not downgrade a
  recap request to CONVERSATIONAL.
- INTERACTIVE: multi-turn (interview / form-fill / back-and-forth).
- DIRECTIVE: direct command, imperative ("search for X", "remember that...", "save Z").
- UNCLEAR: cannot determine.

GROUNDING:
- Use this prompt, history, the skill catalog, and any tool output as admissible evidence for posture, intent, and interpretation.
- Do not treat general pretrained world knowledge as authoritative; when unsure, lower confidence.
"""


# ---------------------------------------------------------------------------
# User-prompt template (formatted per-call by the router)
# ---------------------------------------------------------------------------

ROUTING_USER_PROMPT_TEMPLATE = """CONVERSATION STATE:
{active_tasks_section}{history_section}{prior_fragments_section}
CURRENT USER MESSAGE:
{utterance}

SKILLS CATALOG (JSON keys = only valid "skills" array entries):
{skills_json}

INTERACT ACTIONS CATALOG (JSON keys = only valid "interact_actions" array entries):
{interact_actions_json}

TASK: 1) Classify posture (RESPOND/SUPPRESS/DEFER). 2) If posture=RESPOND, classify intent and fill skills + interact_actions; otherwise use skills=[], interact_actions=[], canned_response="", intent_type="UNCLEAR".

POSTURE RULES (recap):
- RESPOND: greeting (always), question, request, answer to question, gratitude for help, personal-fact statement, contextually coherent message. When in doubt, RESPOND.
- SUPPRESS: closing after exchange concluded; redundant thanks; hanging "ok" with nothing to answer. NEVER for direct answers, greetings, or new requests.
- DEFER: genuinely unintelligible fragment AND no context. NEVER for media attachments.

RULES:
1. The ">>> USER RESPONDS NOW <<<" marker in history indicates the transition to the current user message.
2. Output posture first; then interpretation, intent_type, skills, interact_actions, confidence (and canned_response when posture=RESPOND).
3. CONVERSATIONAL intent MUST have empty skills [] AND empty interact_actions [].
4. Each skills array entry MUST be an exact SKILLS CATALOG key, NOT a description or tag.
5. Each interact_actions array entry MUST be an exact INTERACT ACTIONS CATALOG key (class name), NOT a description.
6. Use interact_actions ONLY when a listed handler is purpose-built for this request and no tool-driven engine work is needed.
7. Use both skills AND interact_actions when engine work must precede a specialized handler.
8. If the assistant's most recent message was a question and the user answers, use INTERACTIVE.
9. Lower confidence if ambiguous{optional_instructions}

INTERPRETATION: Brief synopsis of user intent and why this posture applies. Target one sentence, ~15-30 words.

OUTPUT (JSON only):
{{
  "posture": "RESPOND|SUPPRESS|DEFER",
  "interpretation": "Brief synopsis of user intent and why this posture applies.",
  "intent_type": "CONVERSATIONAL|INFORMATIONAL|INTERACTIVE|DIRECTIVE|UNCLEAR",
  "skills": ["SkillName1"],
  "interact_actions": ["ClassName1"],
  "confidence": 0.0-1.0{entity_field}{canned_field}
}}"""


# ---------------------------------------------------------------------------
# Optional fragments
# ---------------------------------------------------------------------------

ROUTING_CANNED_INSTRUCTIONS_TEMPLATE = """
7. canned_response: use "" when intent_type is one of: {skip_intents}. Otherwise same language as the CURRENT USER MESSAGE; ≤{max_words} words; vary wording across turns.{persona_tone_hint}

   STRICT — lead-in acknowledgement ONLY (must sound incomplete; the real reply follows immediately after in the same turn):
   - ALLOWED: hesitation, filler, or a short fragment with no full thought (e.g. "Hmm…", "One sec…", "Let me see…", "On it…", "Looking that up…" in the user's language). Reference the topic when natural ("Hmm… looking into Silvies Online…").
   - FORBIDDEN — **no conclusive or substantive content whatsoever**: no answers, explanations, outcomes, reasons, advice, instructions to the user, refusals, limits, policy, apologies-for-limits, workarounds, redirects, or any string that could read as a finished message. If it could stand alone in chat, it is wrong.
   - FORBIDDEN patterns (illustrative, not exhaustive): two clauses that resolve or pivot ("…, but you can…"; "…, so …"); "I can't …" / "I'm unable …" / "You should …" / "Try …" / anything that addresses the user's request without an obvious follow-on in the same bubble. Also forbidden: pre-emptive "Here's what I found…" / "Got it, here's…" — those imply the answer is already coming.
   - BAD: "I can't check the time, but you can look at your device." — explains and concludes; belongs in the main reply only, never in canned_response.
   - BAD: "Here's what I found about Silvies Online." — pre-empts the answer.
   - GOOD: "Hmm…" / "Just a moment…" / "On it — pulling up Silvies Online…" — acknowledges processing only; carries zero standalone substance.
"""


ROUTING_CLARIFICATION_FALLBACK_MESSAGES: List[str] = [
    "Could you tell me more about what you need?",
    "I'd like to help — could you rephrase that?",
    "Can you provide more details about your request?",
]


ROUTING_CLARIFICATION_USER_PROMPT_TEMPLATE = """\
The user said: "{utterance}"
Our initial interpretation: {interpretation}
Intent type: {intent_type}
Confidence: {confidence}
Issues: {issues}

Please provide a clarification question to ask the user.
"""


ROUTING_CLARIFICATION_PARAPHRASE_PROMPT_TEMPLATE = """\
Rephrase this clarification question naturally and concisely: "{template}"
"""


__all__ = [
    "ROUTING_SYSTEM_PROMPT",
    "ROUTING_USER_PROMPT_TEMPLATE",
    "ROUTING_CANNED_INSTRUCTIONS_TEMPLATE",
    "ROUTING_CLARIFICATION_FALLBACK_MESSAGES",
    "ROUTING_CLARIFICATION_USER_PROMPT_TEMPLATE",
    "ROUTING_CLARIFICATION_PARAPHRASE_PROMPT_TEMPLATE",
]
