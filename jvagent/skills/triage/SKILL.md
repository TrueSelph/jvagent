---
name: triage
description: Rapidly triage issues by severity, impact, and next action.
allowed-tools:
  - prioritize_findings
version: 1
tags:
  - triage
  - incident
---

## Workflow

1. Gather all known signals and symptoms.
2. Classify severity and impacted surfaces.
3. Identify the smallest safe next action.
4. Escalate clearly when confidence is low.

## Scope

This skill is for rapid issue triage: severity ranking, impact framing, and immediate next actions. Use it for incident/problem intake and prioritization. Do not use it for deep root-cause claims without supporting evidence.

## Grounding

- Assign severity only from provided evidence; do not inflate or downplay risk without support.
- If inputs are incomplete, explicitly state what is missing and what assumptions (if any) were made.
- Recommend conservative, reversible next steps when confidence is low.
