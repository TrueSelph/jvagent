# Lead Agent

A B2B lead-development agent built on the cockpit pattern. It maintains structured
lead profiles per user, injects them into the conversation context, and applies
a post-conversation diff engine to keep profiles current.

## Architecture

The lead agent uses two complementary InteractActions:

1. **LeadRetrievalAction** (`weight=-90`)
   - Runs before the cockpit on every interaction
   - Loads the user's lead profile and injects it as a directive
   - Optionally indexes the profile to PageIndex for semantic search

2. **LeadInteractAction** (`weight=170`, `run_in_background=True`)
   - Runs after the response is sent
   - Sends the conversation transcript + current profile to an LLM
   - Receives a structured diff and applies it to the profile graph

## Profile Structure

Each lead is stored as a graph:

```
User --> LeadProfile --> LeadProfileNode (professional_history)
                  --> LeadProfileNode (behavior_and_preferences)
                  --> LeadProfileNode (project_active)
                  --> LeadProfileNode (conversation_summaries)
                  --> LeadProfileNode (<custom>)
```

The `LeadProfile` anchor node stores YAML frontmatter (structured fields).
Each `LeadProfileNode` stores markdown content for one section.

## Agent YAML

```yaml
extends: lead
```

Or inline:

```yaml
actions:
  - action: jvagent/lead
    context:
      enabled: true
      model: gpt-4o-mini
      update_frequency: 1

  - action: jvagent/lead_retrieval
    context:
      enabled: true
      max_profile_chars: 4096

  - action: jvagent/cockpit
    context:
      enabled: true
      model: gpt-4o-mini
      skills: -all
```

## Long Memory Integration

The lead agent can work alongside `UserLongMemoryInteractAction` for general
user memory, while `LeadInteractAction` focuses on sales-specific profile
fields. Both systems use the same graph-based Node architecture and can
coexist on the same user.
