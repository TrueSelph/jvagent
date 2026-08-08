---
name: smoke_frontmatter
description: Regression fixture - declares its tools with underscore keys behind a UTF-8 BOM.
spec: jv
allowed_tools:
  - file_interface__list_directory
  - file_interface__read_file
requires_actions:
  - FileInterfaceAction
tags:
  - smoke
---

# Frontmatter Smoke - Standard Operating Procedure

> **This file is deliberately malformed. Do not "fix" it.**
>
> It is written the way a Windows or Office editor writes one: the first three
> bytes are a UTF-8 BOM (`EF BB BF`, invisible in every editor), and the
> frontmatter keys use underscores rather than the canonical hyphens. Both
> spellings once produced the same silent failure - the frontmatter parsed as
> body, so the skill owned no tools *and* `allowed-tools:` leaked into the
> rendered PROCEDURE. `tests/scaffold/test_skill_resolve.py` asserts on this
> exact file; normalising it would delete the regression it guards.

Use this procedure when the user asks to list or read their stored files.

1. Call `file_interface__list_directory` to see what the user has.
2. If they named a file, read it with `file_interface__read_file`.
3. Answer with what the files actually contain. Do not invent filenames.
