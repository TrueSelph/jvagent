# Native DOCX creation profile

The existing app-local DOCX skill now declares the independently authorized SDK
shell prerequisite and optional FileInterface sharing tool. It uses the existing
script sandbox and unchanged authored OOXML validator. No document executor or
host Python/Node dependency is added to the harness. Source:
`examples/jvagent_app/agents/jvagent/orchestrator_agent/skills/docx/SKILL.md:6`.

Build the optional dependency image and use its full immutable local image ID as
`CodeExecutionAction.context.native_image`:

```bash
docker build -t jvagent-native-docx:qualification \
  -f examples/native_docx/Dockerfile examples/native_docx
docker image inspect jvagent-native-docx:qualification --format '{{.Id}}'
```

The image contains Node, locked docx-js dependencies and pinned Python XML
validation packages. It does not contain the app's skill assets or credentials.
Sources: `examples/native_docx/Dockerfile:1`,
`examples/native_docx/package.json:1`, `examples/native_docx/package-lock.json:1`.
Configure the app-local `skill_directory` under NativeHarnessAction, declare the
existing CodeExecutionAction/FileInterface services, and grant named shell/share
permissions. Follow [script isolation and storage admission](native-script-sandbox.md)
and [artifact delivery](native-artifact-downloads.md). An image alone does not
authorize execution, create a bounded filesystem or qualify deployment.

The skill checks the preinstalled module, writes its own JavaScript program in
the working directory, generates a DOCX, validates it through the staged support
scripts and shares the actual file. Script paths under
`staged_skills/docx/scripts/` refer to the authored support tools, not an existing
document-generator program. Offline sandboxes must not attempt runtime npm
installation. Source: the skill's `SKILL.md:21` and `:83`.

See [the actual-provider acceptance record](../plans/2026-11-06-native-docx.md)
for the retained failures, corrected creation, restart/re-sharing and limitations.
This profile qualifies a bounded creation/download route. It does not qualify all
features advertised by the original DOCX skill, arbitrary uploaded documents,
LibreOffice/pandoc conversions, other providers or production images.
