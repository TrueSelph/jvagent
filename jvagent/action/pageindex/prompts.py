"""Prompt templates for PageIndex retrieval.

Uses string.Template ($-substitution) to avoid KeyError when document content
contains literal curly braces (e.g. JSON excerpts).
"""

from string import Template

DIRECTIVE_TEMPLATE_PLAIN = Template(
    "Use this context to inform your response to the user's query:\n\n"
    "{results}\n\n"
    "[END OF CONTEXT]"
)

DIRECTIVE_TEMPLATE = Template(
    "Use the following excerpts to answer the user's query.\n"
    "Each excerpt is tagged with a reference number [N]. Multiple excerpts may share the same [N] "
    "when they come from the same source.\n\n"
    "Citation rules:\n"
    "- When you use information from an excerpt, cite its [N] inline.\n"
    "- At the end of your response, list ONLY the references you actually cited—copy each cited "
    "line verbatim from the block below. Do not modify, reorder, or paraphrase them.\n"
    "- If your response does not cite any source, do NOT include a references section at all.\n\n"
    "{results}\n\n"
    "Available references (include only those you cited):\n\n"
    "{references}\n\n"
    "[END OF CONTEXT]"
)

DIRECTIVE_TEMPLATE_NO_REFS = Template(
    "Use the following numbered excerpts to inform your response to the user's query.\n"
    "Cite sources using bracketed reference numbers (e.g. [1], [2]) where appropriate.\n\n"
    "{results}\n\n"
    "[END OF CONTEXT]"
)

# String form for Pydantic/JSON-serializable attribute defaults
DIRECTIVE_TEMPLATE_STR = DIRECTIVE_TEMPLATE.template
