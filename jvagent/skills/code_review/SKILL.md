---
name: code_review
description: Review code for correctness, security, and maintainability.
version: 1
tags:
  - quality
  - security
  - engineering
---

## Workflow

1. Identify the files and scope impacted by the user request.
2. Read critical implementation paths before proposing changes.
3. Prioritize correctness and security issues ahead of style concerns.
4. Return concrete findings and actionable recommendations.

## Scope

This skill is for code quality analysis: correctness, regressions, security risk, and maintainability findings. Use it when the user asks for review, audit, or risk analysis. Do not use it as a substitute for implementation or non-code research tasks.

## Grounding

- Base findings on concrete code evidence from inspected files and tool output.
- Do not claim vulnerabilities, regressions, or behaviors without a supporting code path or observable output.
- If evidence is incomplete, call out uncertainty explicitly and recommend the minimum next verification step.
