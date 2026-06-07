# Interview SOP assets

Framework-owned procedure templates for skills-v2 interviews. **Not** how-to documentation — see [`../docs/`](../docs/) for guides.

| File | Role |
|------|------|
| [`standard_procedure.md`](standard_procedure.md) | **Runtime SOP** — loaded by `procedure.py`, prepended to every interview skill's `SkillDoc.body` at discovery |
| [`skill_custom_instructions.md`](skill_custom_instructions.md) | **Authoring template** — what to write in `SKILL.md` body (custom rules only; do not copy the standard procedure) |

Per-skill copies of the standard procedure are never checked into app `skills/` folders — composition happens in `discover_skill_docs`.
