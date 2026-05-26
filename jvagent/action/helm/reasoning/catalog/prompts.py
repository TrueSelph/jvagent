"""Skill-catalog prompt templates rendered into the cockpit system prompt.

Two intros, picked at runtime by :class:`SkillCatalog.render_*`:

- ``SKILL_INDEX_INTRO`` — used when the catalog is small enough to inline
  (count ≤ ``skill_index_inline_max_skills``).
- ``SKILL_INDEX_SEARCH_MODE_INTRO`` — used when the catalog is large; the
  engine is told to call ``skill_search`` / ``list_skills`` instead of
  reading a full inline list.
"""

from __future__ import annotations

SKILL_INDEX_INTRO = """You have access to the following Claude-style skill bundles.
Each skill is a specialized workflow with dedicated tools and an SOP.

When to use a skill:
- Call `read_skill` with the exact `skill_name` when the request clearly matches the skill's scope.

When NOT to use a skill:
- If the request is general conversation, can be answered without a skill workflow,
  or does not match any skill's scope, answer directly without calling `read_skill`.
- Do not activate a skill "just in case."

Multi-skill orchestration:
- Some requests span multiple skills (e.g., "review this code AND search for CVEs").
- If you identify a multi-skill task, plan the sequence, then activate skills ONE AT A TIME.
- Complete each skill's workflow before activating the next.
- Carry forward relevant results (file paths, IDs, findings) between skills.

Disambiguation:
- If multiple skills appear relevant, pick the one whose tools and scope most directly match intent.
- Prefer activating only one skill per interaction unless multiple are clearly needed.

Available skills:"""


SKILL_INDEX_SEARCH_MODE_INTRO = """You have access to {n_skills} Claude-style skill bundles
(specialized workflows with dedicated tools and an SOP each).

The full per-skill index (name, description, tags) is not listed here to keep the system
prompt small. Before choosing a skill:

- Call `skill_search` with a short query derived from the user's request (and optional
  `plan_skills` if the task may span multiple skills), or `list_skills` to see the whole
  local catalog.
- Then call `read_skill` with the exact `skill_name` for the SOP and tool rules.

When NOT to use a skill: general conversation, or requests that do not match any
skill's scope—answer directly without `read_skill`. Do not activate a skill "just in case."
For multi-skill work, activate skills ONE AT A TIME and complete each workflow before
the next."""


SKILL_INDEX_ENTRY_TEMPLATE = (
    "- {name}: {description}{tag_suffix}{requires_suffix}{tools_suffix}"
)


__all__ = [
    "SKILL_INDEX_INTRO",
    "SKILL_INDEX_SEARCH_MODE_INTRO",
    "SKILL_INDEX_ENTRY_TEMPLATE",
]
