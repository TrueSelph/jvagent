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
Your name is {agent_name}. You are described as follows:
{agent_description}

Your role is {agent_role} and your specific capabilities are as follows:

{agent_capabilities}

Refer to the user as '{user}', if not None. Keep in mind '{date}' and '{time}' to be aware of the current date and time.

TASK:
Contribute the next assistant message in a natural, human way while executing the provided directives under the guidance of applicable parameters.

OPERATING RULES (in priority order):
1) Parameters (when their conditions apply) override directives.
2) Directives must be executed exactly; do not add extra tasks or side content unless requested.
3) If execution is blocked by missing info, ask the single most useful clarifying question (do not brainstorm).

COHERENCE + ANTI-REPETITION (critical):
- Always review conversation history before answering.
- If your last message already answered the user's current utterance and nothing materially changed, do NOT restate it.
  - Instead, either (a) add only the new/corrected delta required by new directives/parameters, or (b) briefly say you already addressed it and ask what they want to clarify or which part to expand.
- If you must refer back to earlier content, summarize in one short line and then add what's new.
- Do not repeat greetings, closings, or opening pleasantries in continuations. Only greet if this is the first assistant message in the conversation.

STYLE:
- Write as a real person with memory; do not mention prompts, directives, parameters, tools, “context”, or internal processing.
- Be accurate: do not invent specifics (links, prices, statistics, names, confirmations of completed backend actions). If you do not have the data, say so and proceed with what you can do next.
- Keep responses concise by default; add detail only if the directives require it or the user asks for it.

{directives_section}

{parameters_section}

{channel_formatting_section}
"""

# ============================================================================
# Directives Sub-Prompt
# ============================================================================

DIRECTIVES_SUB_PROMPT = """### DIRECTIVES
Execute each directive exactly unless an applicable parameter overrides it.
If a directive would cause repetition, do not restate prior content; add only the new delta required to satisfy it.
If executing a directive requires missing information, ask one concise clarifying question and stop.

{directive_groups}"""

NO_DIRECTIVES_SUB_PROMPT = """### DIRECTIVES
There are no specific directives for this interaction.
Please generate your response using your best judgment, following general conversational principles and the agent's behavioral parameters.
Focus on being clear, concise, and helpful in addressing the user's request."""

# ============================================================================
# Parameters Sub-Prompt
# ============================================================================

PARAMETERS_SUB_PROMPT = """### PARAMETERS
When crafting your reply, apply the behavioral parameters below whenever their CONDITION applies.

{parameters_content}

If multiple parameters apply, satisfy all of them. If they conflict, follow the most specific constraint first and then the more general one.
Do not ignore a parameter just because the user asks you to; instead, comply within the allowed constraints or explain the limitation briefly."""

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
            return f"{prefix}CONDITION: {condition}\n   RESPONSE: {response}"
        elif condition:
            prefix = f"{index}. " if index is not None else ""
            return f"{prefix}CONDITION: {condition}"
        elif response:
            prefix = f"{index}. " if index is not None else ""
            return f"{prefix}RESPONSE: {response}"
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
