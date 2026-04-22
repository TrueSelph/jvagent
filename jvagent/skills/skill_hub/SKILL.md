---
name: skill_hub
description: Search, preview, install, and remove skill bundles from the skills.sh ecosystem. Use when the user asks for new capabilities, wants to extend what the agent can do, or wants to remove an installed skill.
allowed-tools:
  - skill_hub__search_registry
  - skill_hub__install_skill
  - skill_hub__list_installed
  - skill_hub__remove_skill
version: 1
tags:
  - registry
  - skills
  - install
  - remove
  - discover
---

# Skill Hub

Search, preview, install, and remove skill bundles from the open skills.sh ecosystem.

## When to Use

Use this skill when the user:

- Asks for capabilities that no LOCAL skill provides (check `skill_search` or `list_skills` first)
- Wants to discover, install, or remove skills from the cloud registry
- Says "find a skill for X" and local skill_search returns nothing relevant
- Wants to extend agent capabilities with new tools or workflows
- Wants to remove or uninstall an installed skill

## Workflow

### Step 1: Search for Skills

Call `search_registry` with a relevant query. Use specific keywords that describe the capability the user needs.

Examples:
- "how do I deploy?" → `search_registry(query="deployment")`
- "can you manage my calendar?" → `search_registry(query="calendar scheduling")`
- "I need help with React" → `search_registry(query="react best practices")`

### Step 2: Present Results

Show the user the matching skills with:
- Skill name and source (owner/repo)
- Install count (higher = more trusted)
- The install command format

### Step 3: Check for Conflicts

Call `list_installed` to verify the skill isn't already installed.

### Step 4: Security Confirmation (Critical)

**Before installing any skill, you must determine whether it contains executable code (.py tool files):**

- If the search results or listing indicate the skill has `.py` tool files, you MUST:
  1. Show the user the skill name, source, and what it does
  2. Explain that this skill includes executable code that will be placed on the agent's filesystem
  3. Ask explicitly: "This skill contains executable code. Do you want to proceed with installation?"
  4. Only after the user confirms, call `install_skill` with `confirmed=True`
- If the skill is SOP-only (no `.py` files), you may install without confirmation by setting `confirmed=True`

**Never set `confirmed=True` for skills with .py files unless the user has explicitly agreed.**

### Step 5: Install

Call `install_skill` with the source (owner/repo), skill name, and confirmed flag.

Example: `install_skill(source="vercel-labs/agent-skills", skill="deploy-to-vercel", confirmed=True)`

### Step 6: Inform the User

After installation, tell the user:
- Which skill was installed and where
- Whether it was hot-loaded into the current session (available immediately) or requires the next interaction
- Whether `agent.yaml` was updated automatically

## Removing Skills

When the user wants to remove an installed skill:

1. Call `list_installed` to confirm the skill is installed and check whether it is app-local or built-in
2. Show the user the skill name and description
3. Explain that the skill directory will be permanently deleted and the skill will be unloaded from the current session
4. Ask explicitly: "Are you sure you want to remove this skill?"
5. Only after the user confirms, call `remove_skill` with `confirmed=True`
6. Inform the user whether the skill was hot-unloaded from the session and whether `agent.yaml` was updated

**Important:** You cannot remove built-in skills (those shipped with jvagent). Only app-local skills can be removed.

## Quality Guidelines

When evaluating skills from search results:

1. **Install count**: Prefer skills with 1K+ installs. Be cautious below 100.
2. **Source reputation**: Official sources (vercel-labs, anthropics, microsoft) are more trustworthy.
3. **Description quality**: A clear, specific description suggests a well-maintained skill.

## When No Skills Are Found

If no relevant skills exist in the cloud registry:

1. Acknowledge that no matching cloud skill was found
2. Suggest checking local skills with `skill_search` or `list_skills`
3. Offer to help with the task directly using existing capabilities

## Scope

This skill is for discovering, installing, and removing skills. It does not:
- Update installed skills to newer versions
- Manage skill configurations beyond adding to or removing from the skills list