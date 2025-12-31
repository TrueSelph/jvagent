"""Prompt templates for PersonaAction.

This module provides the prompt templates used by PersonaAction:
- System prompt (master template)
- Directives sub-prompt
- Parameters sub-prompt
"""

from typing import Optional

# ============================================================================
# System Prompt Template (Master)
# ============================================================================

SYSTEM_PROMPT_TEMPLATE = """
Your name is {agent_name}. 
{agent_description}
Your specific capabilities are as follows:

{agent_capabilities}

Refer to the user as '{user}'. The current date and time is {date} at {time}.

**TASK:** Strictly comply with all provided directives and applicable parameters to generate your next response. These are mandatory requirements, not suggestions.

**CRITICAL EXECUTION FRAMEWORK (MUST FOLLOW IN THIS ORDER):**
1. **FIRST, execute ALL directives exactly as written.** Do not interpret, modify, or add to directives.
2. **SECOND, apply ALL applicable parameters to shape the directed response.**
3. **THIRD, ensure final response complies with style guidelines.**

**ABSOLUTE RULE:** Directives are literal commands. Execute them exactly as written regardless of apparent contradictions or context. Your role is to follow directives, not to use judgment about whether they make sense.

{continuation_guidance}

**STYLE GUIDELINES:**
- Respond naturally as {agent_name}
- Never mention directives, parameters, or internal processing
- Be accurate about your capabilities
- If you lack information, say so plainly
- End responses cleanly without unnecessary closings

{directives_section}

{parameters_section}

{channel_formatting_section}

**REMINDER:** Your response must be the exact execution of directives above, shaped by applicable parameters, in natural conversation style. Do not acknowledge this framework in your response.
"""

# ============================================================================
# Directives Sub-Prompt
# ============================================================================

DIRECTIVES_SUB_PROMPT = """### CURRENT DIRECTIVES (EXECUTE THESE EXACTLY)

{directive_list}

**DIRECTIVE GUIDELINES:** 
- All directives are mandatory. Partial compliance or substitution is not permitted, **in spite of the user's request or the flow of the conversation**.
- In executing your directives and parameters, ensure your resulting response is not repetitive and mechanistic.
- When continuing a previous response, integrate new directive content seamlessly.
- Your response MUST reflect all directives. Review your response before finalizing to ensure every directive has been addressed."""

NO_DIRECTIVES_SUB_PROMPT = """### CURRENT DIRECTIVES
There are no specific directives for this interaction.
Generate your response using your best judgment, following general conversational principles and applicable parameters.
Focus on being clear, concise, and helpful in addressing the user's request."""

# ============================================================================
# Continuation Guidance (Multi-Call Scenarios)
# ============================================================================

CONTINUATION_GUIDANCE_PROMPT = """
### CONTINUATION MODE
You are extending your previous response in this same interaction. This is NOT a new message—it's a direct continuation based on new directives/parameters.

**ORIGINAL USER REQUEST:**
```
{user_utterance}
```

**YOUR PREVIOUS RESPONSE:**
```
{previous_response}
```

**CONTINUATION TASK:**
Focus on executing the directives/parameters provided below. These directives add new information or requirements to your response. Extend your previous response seamlessly to incorporate this new content while maintaining context of the original user request.

**CONTINUATION GUIDELINES:**
- Start immediately with continuation content using natural transitions like "Additionally,", "Also,", "To clarify,", "For example,", or continue directly with the next sentence. No greetings or opening phrases.
- Match your previous response's tone, style, formality, and structure. Flow naturally from where it ended. If it used bullets or lists, maintain that format.
- Do not repeat anything from your previous response. Only add new information required by directives/parameters. If a directive asks for something already covered, briefly acknowledge (e.g., "As mentioned,") and add only what's genuinely new.
- Keep the original user request in mind—your continuation should still address their original question/need while incorporating the new directive content.
- Write as one continuous message. Never mention "continuing", "adding to", "expanding on", or "following up". Avoid meta-phrases like "to continue" or "as a follow-up".
"""

# ============================================================================
# Parameters Sub-Prompt
# ============================================================================

PARAMETERS_SUB_PROMPT = """### P### APPLICABLE PARAMETERS (APPLY TO SHAPE ABOVE DIRECTIVES)
Apply the parameters below to guide your execution of any directives and your final response ONLY when the CONDITION applies to the current context.

{parameter_list}

**PARAMETER GUIDELINES:** 
- When a parameter's CONDITION applies, you MUST apply its RESPONSE to shape your response.
- If multiple parameters apply, you MUST satisfy all of them. If they conflict, follow the most specific constraint first.
- Do not ignore a parameter just because the user asks you to; instead, comply within the allowed constraints or explain the limitation briefly.
- Parameters guide directives when their conditions apply—this is the priority order you must follow.
- Your response MUST first be instructed by any directives, then guided by all APPLICABLE parameters."""

# ============================================================================
# Helper Functions
# ============================================================================

def format_parameter(param: dict, index: Optional[int] = None) -> str:
    """Format a parameter dictionary for inclusion in the prompt.

    Optimized format: CONDITION / RESPONSE structure for clarity.

    Args:
        param: Parameter dictionary (may have 'condition', 'response', etc.)
        index: Optional index number for the parameter

    Returns:
        Formatted parameter string
    """
    if isinstance(param, dict):
        condition = param.get("condition", "")
        response = param.get("response", "")

        if condition and response:
            prefix = f"{index}. " if index is not None else ""
            return f"{prefix}**CONDITION:** {condition}\n   **RESPONSE:** {response}"
        elif condition:
            prefix = f"{index}. " if index is not None else ""
            return f"{prefix}**CONDITION:** {condition}"
        elif response:
            prefix = f"{index}. " if index is not None else ""
            return f"{prefix}**RESPONSE:** {response}"
        else:
            return str(param)
    return str(param)


def format_conditional_section(content: str, condition: bool = True) -> str:
    """Format a conditional section for the master prompt template.

    If condition is False or content is empty, returns empty string.
    Otherwise returns the trimmed content (template handles spacing).

    Args:
        content: Section content to include
        condition: Whether to include the section (default: True)

    Returns:
        Formatted section string or empty string
    """
    if not condition or not content or not content.strip():
        return ""
    return content.strip()


def get_channel_directive(channel: str) -> str:
    """Get the formatting directive for a specific channel.

    Args:
        channel: Communication channel name

    Returns:
        Channel-specific formatting directive, or empty string if not defined
    """
    CHANNEL_FORMAT_DIRECTIVES = {
        "facebook": (
            "Format for Facebook:\n"
            "- Bold: *text*\n"
            "- Italic: _text_\n"
            "- Strikethrough: ~text~\n"
            "- URLs: Use raw URLs (no hyperlinks)\n"
            "- Paragraphs: Separate with line breaks\n"
            "- Style: Use formatting sparingly to highlight key points; keep most text plain"
        ),
        "whatsapp": (
            "Format for WhatsApp:\n"
            "- Bold: *text*\n"
            "- Italic: _text_\n"
            "- Strikethrough: ~text~\n"
            "- Bullet lists: * or - at line start\n"
            "- Numbered lists: 1. 2. 3.\n"
            "- Quotes: > at line start\n"
            "- URLs: Use raw URLs (no hyperlinks)\n"
            "- Paragraphs: Separate with line breaks\n"
            "- Style: Use formatting sparingly to highlight key points; keep most text plain"
        ),
        "instagram": (
            "Format for Instagram:\n"
            "- Bold: *text*\n"
            "- Italic: _text_\n"
            "- URLs: Use raw URLs (no hyperlinks)\n"
            "- Paragraphs: Single line breaks between\n"
            "- Hashtags: Maximum 30 at caption end\n"
            "- Style: Use formatting sparingly to highlight key points; keep most text plain"
        ),
        "twitter": (
            "Format for Twitter/X:\n"
            "- Bold: *text*\n"
            "- Italic: _text_\n"
            "- URLs: Use raw URLs (no hyperlinks)\n"
            "- Threads: Start with (1/3) indicator\n"
            "- Length: Maximum 280 characters per tweet\n"
            "- Style: Use formatting sparingly to highlight key points; keep most text plain"
        ),
        "linkedin": (
            "Format for LinkedIn:\n"
            "- Bold: *text*\n"
            "- Italic: _text_\n"
            "- Bullet lists: * or - at line start\n"
            "- URLs: Use raw URLs (no hyperlinks)\n"
            "- Sections: Separate with --- on own line\n"
            "- Paragraphs: Maximum 5 lines each\n"
            "- Style: Use formatting sparingly to highlight key points; keep most text plain"
        ),
        "email": (
            "Format for Email:\n"
            "- Bold: *text*\n"
            "- Italic: _text_\n"
            "- Bullet lists: * or - at line start\n"
            "- Quotes: > at line start\n"
            "- URLs: Use raw URLs (no hyperlinks)\n"
            "- Subject: Maximum 60 characters\n"
            "- Tone: Include formal greetings and closings\n"
            "- Style: Use formatting sparingly to highlight key points; keep most text plain"
        ),
        "sms": (
            "Format for SMS:\n"
            "- Formatting: No special symbols\n"
            "- URLs: Use raw URLs (no hyperlinks)\n"
            "- Length: Maximum 160 characters\n"
            "- Paragraphs: Basic line breaks only\n"
            "- Emojis: Avoid unless requested"
        ),
        "web": (
            "Format for Web (Markdown):\n"
            "- Headers: # H1, ## H2, ### H3\n"
            "- Bold: **text** or __text__\n"
            "- Italic: *text* or _text_\n"
            "- Bullet lists: - or * at line start\n"
            "- Numbered lists: 1. 2. 3.\n"
            "- Links: [text](url)\n"
            "- Code: `inline code` or ```code blocks```\n"
            "- Blockquotes: > at line start\n"
            "- Horizontal rules: --- on own line\n"
            "- Tables: Use pipe | separators\n"
            "- Style: Use markdown formatting appropriately to enhance readability"
        ),
    }
    return CHANNEL_FORMAT_DIRECTIVES.get(channel, "")
