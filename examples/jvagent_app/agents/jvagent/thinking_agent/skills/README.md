# Local Skills

This folder demonstrates app-local SKILL.md bundles for `ThinkingInteractAction`.

Layout:

```text
skills/<skill_name>/SKILL.md
skills/<skill_name>/<tool>.py   # optional
```

App-local bundles can be selected via `thinking_interact_action.context.skills`
and can override built-in bundles when names collide.
