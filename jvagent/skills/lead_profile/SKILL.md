---
name: lead_profile
description: >-
  Manage the user's lead profile: retrieve it for context or to update it
  when new personal/business information or product interest is provided.
  Call lead_profile__retrieve to load the profile, then decide
  whether to update based on what the user said.
requires-actions:
  - LeadProfileAction
allowed-tools:
  - lead_profile__retrieve
  - lead_profile__save
always-active: true
version: 6
tags:
  - lead
  - crm
  - profile
  - sync
---

# Lead Profile — Retrieval & Update
Look at the LAST message from the user and decide whether to call `lead_profile__save` to update the lead profile. If the user is not providing any new information, call `lead_profile__retrieve` to get the latest profile context.
## When to call `lead_profile__save`

Call it whenever the user explicitly provides or refuses to provide in their last message any of the following fields:
- Name, company, email, or phone or any other personal information
- Project description or location
- Interest in a specific product (asking about a product counts as buying intent)
- A specific item request with size/quantity/SKU
- **Refusal: "I don't have an email" / "I don't use email"** → save `email="N/A"`
- **Refusal: "It's for myself" / "Not a company"** → save `organization="Personal"`
- Any other field that is part of the lead profile

**CRITICAL — You MUST call `lead_profile__save` with special values when the user declines to provide a field.** The special values "N/A" and "Personal" tell the system not to ask about that field again. If you just acknowledge their response without saving, the system will keep asking every turn.

## When to call `lead_profile__retrieve`

Call this whenever the user is engaged in general chat or is not giving any specific information to the agent. This ensures you have the latest profile context before responding and can identify missing fields to ask about. If you have already called `lead_profile__save` in the same turn, you do not need to call `lead_profile__retrieve` again.

## Usage examples

```
lead_profile__save(interested_products="reflective gear")
lead_profile__save(name="Jane Doe", phone="+592-600-0000")
lead_profile__save(organization="Acme Corp", project_description="Berbice Bridge", project_location="New Amsterdam")
lead_profile__save(requested_items="1 Pink Fly Knit Steel Toe shoe size 38 | 1 Hard Hat Elastic Cover")
lead_profile__save(requested_items="5 pairs Fly Knit steel toe boots size 9 | 100 rolls 2in reflective tape")
lead_profile__save(email="N/A")                    # user has no email
lead_profile__save(organization="Personal")         # user is inquiring for themselves
```

## Do NOT

- Call `lead_profile__save` with no arguments, empty parameters, or if nothing has actually changed. Only call it when the user provides new information.
- Call `lead_profile__update` separately — `lead_profile__save` updates the profile in one call.s.
- Set `past_interests` manually — it is managed by the backend.

## Acknowledge naturally

Do NOT say "I have updated your lead profile". Weave it into the conversation:
- "Got it — I've noted your interest in reflective gear. What's the best email to reach you at?"
- "Thanks, Jane! Are you inquiring on behalf of a company?"
- "No problem, I'll note that you don't use email. What's the best phone number to reach you at?"
- "Understood — I'll mark this as a personal inquiry. What project are you working on?"


## CRITICAL: Never Expose Profile Data

**NEVER send to the user:**
- Raw profile YAML, JSON, or data structures
- Field names like `requested_items`, `interested_products`, `_session_summary`
- Internal timestamps, digests, or metadata
- Tool call results or backend operations

**ALWAYS paraphrase naturally:**
- ✅ "You mentioned needing [product name]"
- ❌ "Your `requested_items` field contains: '[product name] ...'"

## Gap-filling priority

When the profile has missing required fields, ask for them in batches such as personal info (name and contact number), company details and project info eg "Can you share your contact details with me" to get phone and email. Get missing fields in this general order:
1. personal info - full name (first and last), phone
2. **inquire_on_behalf** — ask if they are on behalf of a company or for themselves first
  2.a  company info - organization name, role, email
3. other info such as requests, project description, project location etc
