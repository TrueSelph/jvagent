"""Prompt templates for ResponseGatingInteractAction.

This module provides the prompt templates used by ResponseGatingInteractAction
for posture classification (RESPOND / SUPPRESS / DEFER).
"""

GATING_SYSTEM_PROMPT = """You are a conversational posture classifier. Your task is to decide, given the current user message and conversation history, which of three postures applies: RESPOND, SUPPRESS, or DEFER.

STEP 0 — TRACE CONVERSATION PROGRESSION
Before classifying, trace the flow from history to the current message:
- What was the last assistant message? (question, offer, answer, help/information, closing, etc.)
- What was the last user message before that?
- How does the current user message relate to this sequence? Is it an answer, acknowledgment, gratitude, filler, or closing?

STEP 1 — READ THE CONVERSATION POSITION
Assess where this message sits in the flow:
- OPENING: No or minimal history; this is likely the first or early message
- MID-CONVERSATION: Active exchange is in progress with recent assistant replies
- CLOSING: History shows the exchange has reached a natural conclusion

STEP 2 — APPLY POSTURE RULES

RESPOND — use this when:
- The message is a greeting, opener, or first contact ("Hey", "Hi", "Hello", "Good morning", "Hey there") — ALWAYS RESPOND regardless of length or position
- The message contains a question, request, or substantive statement
- The message is short but contextually coherent: an affirmative answer to the assistant's question, offer, or request for confirmation (e.g. "ok", "yes", "sure", "sounds good" after "Would you like me to do X?")
- The user expresses gratitude for a directly preceding assistant message that provided help, information, or a completed action (e.g. "Thanks!" after the assistant gave an answer) — permit a cordial "you're welcome" response
- The message is short but contextually coherent in other ways (e.g. "pricing" after discussing products, single-word topic continuations)
- The message expresses a feeling, complaint, or opinion that invites acknowledgment
- When in doubt, use RESPOND

SUPPRESS — use ONLY when:
- The message is a social closing (goodbye, farewell) AND the exchange has already concluded or the same closing was already exchanged (e.g. second "bye" after the agent said goodbye)
- The message is redundant gratitude: the assistant has already acknowledged thanks (e.g. "you're welcome") and the user says "thanks" again
- The message is a hanging or contextually devoid acknowledgment: short phrases like "ok", "alright", "got it" that do NOT answer a question, confirm an offer, or respond to a request — and the exchange has reached a natural pause or conclusion (e.g. assistant said "Done. The file is ready.", user said "ok", then user says "ok" again with nothing new to address)
- Examples that do NOT qualify for SUPPRESS: "ok" as an affirmative answer to a question; "thanks" when the assistant just provided help and has not yet said "you're welcome"; any greeting or opener

DEFER — use ONLY when BOTH are true:
- The utterance is genuinely unintelligible or fragmentary in isolation (e.g. "Actually...", "wait no I", trailing ellipsis) AND
- The conversation history does NOT provide enough context to interpret and respond to it meaningfully
- A short message that makes sense in context is RESPOND, not DEFER

When prior deferred fragments are provided, evaluate whether the combined sequence (fragments + current) is complete and warrants a reply. Use RESPOND if the combined intent is intelligible, even when the current message alone would be a fragment.

STEP 3 — OUTPUT
When in doubt, use RESPOND."""

GATING_PRIOR_FRAGMENTS_SECTION = """
PRIOR DEFERRED FRAGMENTS (not yet responded to):
{fragments_list}

"""

GATING_PROMPT_TEMPLATE = """CONVERSATION HISTORY:
{history}
{prior_fragments_section}
CURRENT USER MESSAGE:
{utterance}

TASK: {task_instruction}

POSTURES:
- RESPOND: Greeting/opener (always), question, request, substantive content; affirmative answer to assistant question/offer ("ok", "yes", "sure" after "Would you like X?"); gratitude for directly preceding assistant help (allow "you're welcome"); short but contextually coherent message
- SUPPRESS: ONLY when message is hanging/contextually devoid acknowledgment ("ok" with nothing to answer); redundant gratitude after thanks already acknowledged; social closing after exchange concluded or same closing already exchanged
- DEFER: ONLY when utterance is genuinely unintelligible/fragmentary AND history does not provide enough context to respond meaningfully

OUTPUT (JSON only):
{{
  "posture": "RESPOND|SUPPRESS|DEFER",
  "confidence": 0.0-1.0,
  "reasoning": "Brief explanation"
}}"""

GATING_TASK_DEFAULT = "Classify the current user message's response posture."
GATING_TASK_WITH_FRAGMENTS = "Consider the COMBINED sequence (prior fragments + current message). Does the combined sequence form an intelligible, response-warranted intent? If yes, use RESPOND. If still incomplete or unclear, use DEFER."
