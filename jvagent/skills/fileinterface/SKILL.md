---
name: fileinterface
description: >
  In-process file I/O under jvspatial storage (local or S3), scoped per agent and user.
  Mandatory workflow: before any read, write, list, mkdir, existence check, or delete in
  a new task, call describe_write_workspace once to learn the sandbox layout and which
  relative prefixes are appropriate; then use the other tools only with sandbox-relative paths.
  Do not use absolute host paths or paths outside the described workspace.
allowed-tools:
  - describe_write_workspace
  - read_file
  - write_file
  - write_binary_file
  - list_directory
  - create_directory
  - delete_file
  - file_exists
version: 1
tags:
  - files
  - storage
  - sandbox
---

## When to use

- Reading or writing artifacts under the agent pipeline (e.g. `output/`, drafts, PDFs).
- Listing or creating directories in the user’s workspace.
- Any file operation that must respect **multi-user isolation** (`<agent_id>/<user_id>/…`).

**Skill authors:** imperative Python in other bundles should use
`jvagent.skills.fileinterface._core` (strict helpers or `*_with_local_fallback` for
optional tests/offline). Do not write user artifacts with raw `Path`/`open` to
relative paths unless you are only using a **process-local temp dir** (e.g. LaTeX
build) and then copying results into the sandbox.

Do **not** use these tools to access arbitrary host paths outside the sandbox. Storage backend (local vs S3) is configured at the jvagent/jvspatial level and is transparent here.

**Exceptions:** Bundles that intentionally manage the **app repository** (e.g. `skill_hub` installing under `agents/...`) or **cloud APIs** are not required to use this sandbox.

## File I/O protocol (binding for this skill)

Follow this order whenever this skill is active and you need to touch the user workspace:

1. **Discover first** — Call `describe_write_workspace` once at the **start of file-related work** in the current task (or after the user changes what they want from the filesystem). It summarizes what exists at the sandbox root, recommended relative prefixes (e.g. `output/`), and clarifies that all paths are **relative to the sandbox**, not the host OS.
2. **Plan paths** — Choose paths only under those prefixes or the sandbox root; do not invent absolute paths (`/…`, `C:\…`) or `..` segments.
3. **Then operate** — Use `list_directory` / `read_file` / `write_file` / `write_binary_file` / `create_directory` / `file_exists` / `delete_file` as needed.
4. **Re-discover when context shifts** — If the task pivots to a new area of the tree or you are unsure what exists, call `describe_write_workspace` again (or `list_directory` on a specific prefix you already validated).

Skipping step 1 risks writing to the wrong conceptual location or assuming directories that do not exist. The tool descriptions below point back to this protocol.

## Tool names (namespaced when active)

When the skill is activated, tools are registered as:

- `fileinterface__describe_write_workspace`
- `fileinterface__read_file`
- `fileinterface__write_file`
- `fileinterface__write_binary_file`
- `fileinterface__list_directory`
- `fileinterface__create_directory`
- `fileinterface__file_exists`
- `fileinterface__delete_file`

## Paths

All `path` arguments are **relative** to the sandbox root for the current interaction (same layout as MCP sandbox mode: sanitized `agent_id` and `user_id`). Use forward slashes (e.g. `output/proposal.pdf`).

## Grounding

- Parameters are authoritative; do not assume paths outside the allowed sandbox.
- If file storage is disabled or App is unavailable, tools return `ok: false` with an error message.
