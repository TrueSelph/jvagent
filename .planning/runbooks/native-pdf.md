# Native PDF skill

Use `library:pdf-generation` with the existing CodeExecutionAction and the
[native sandbox profile](native-script-sandbox.md). The canonical package folder
is now `jvagent/skills/pdf-generation`; update explicit old filesystem paths from
`pdf_generation`. The declared skill name remains `pdf-generation`, and the
renderer script is unchanged. Official Skills admission requires directory and
declared name to match. Sources: `jvagent/harness/skill_catalog.py:60`,
`jvagent/skills/pdf-generation/SKILL.md:1`.

The optional [example image](../../examples/native_pdf/Dockerfile) installs
WeasyPrint, Markdown, Pango and a basic font inside the sandbox image. These do
not become JV Agent host requirements. Its package recipe follows the
[official WeasyPrint Debian installation requirements](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#debian-11).
Build locally, then obtain the full immutable image identity:

```bash
docker build -t jvagent-native-pdf:reviewed examples/native_pdf
docker image inspect jvagent-native-pdf:reviewed --format '{{.Id}}'
```

Set `CodeExecutionAction.context.native_image` to that full `sha256:...` identity
or to a reviewed repository digest. Mutable tags and short image IDs are refused.
The runtime still applies the existing non-root, read-only-root, network-free,
bounded-filesystem and resource policies. Native image policy source:
`jvagent/harness/script_sandbox.py:23`. A locally built image must already be
available to the Docker daemon; copying its identity does not distribute it.

Set the NativeHarnessAction primary `skill_directory: library:pdf-generation`,
or include it in the explicit catalog. Grant the named `code_execution__bash`
permission through AccessControl. For web download links also configure
FileInterfaceAction, its named `file_interface__share_file` permission and the
[artifact download profile](native-artifact-downloads.md). The skill renders
without requiring FileInterface, and only offers publication through the available,
independently authorized tool. Source: `jvagent/skills/pdf-generation/SKILL.md:8`.

The script reports a nonzero failure when no rendering engine is installed;
the SOP prohibits inventing a PDF or a download URL. It does not install packages
at runtime. Source: `jvagent/skills/pdf-generation/scripts/render_pdf.py:120`.
The recipe pins the base image and direct Python dependencies. The tested image
identity and installed versions are recorded; reproducible system/transitive
locking and production supply-chain qualification remain separate release gates.

See the [browser/model qualification](../plans/2026-11-06-native-pdf.md) for tested
formatting, package delivery, accounting, restart and remaining limits.
