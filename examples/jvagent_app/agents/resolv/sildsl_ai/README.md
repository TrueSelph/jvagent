# SILDSL AI Agent

A production-ready agent for the Strengthening Instructional Leadership at the District and School Levels (SILDSL) Project, delivering updates and stakeholder support through the Resolv Incident Management System.

## Overview

The SILDSL AI Agent provides:
- Interview-based actions for structured data collection (reports and feedback)
- Intent-based routing with conversation history management
- Multi-channel support (web interface)
- Integration with Resolv IMS for SILDSL Project
- Speech-to-text and text-to-speech capabilities
- Project-specific information and support

## Project Context

The SILDSL Project, also known as the "Leadership Project", aims to strengthen school and district leadership and improve sector management within Guyana's education system. This agent serves as the virtual assistant providing:
- Updates and progress reports on the Leadership Project
- Recording and filing project-related inquiries or feedback from stakeholders
- Answering questions about project objectives, timelines, and implementation partners
- Sharing official information from the Ministry of Education and the IDB

## Structure

```
sildsl_ai/
├── actions/              # Shared actions from resolv namespace
│   └── resolv/          # Namespace directory (shared with other agents)
│       ├── feedback_interview_interact_action/
│       ├── report_interview_interact_action/
│       ├── resolv_api_action/
│       └── resolv_onboarding_interact_action/
├── agent.yaml           # Agent configuration and action assignments
└── README.md           # This file
```

## Configuration

### Agent Configuration (agent.yaml)

The `agent.yaml` file contains both agent configuration and action assignments:

```yaml
agent: resolv/sildsl_ai
version: 1.0.0
author: V75 Inc.
jvagent: ~0.0.1

context:
  alias: SILDSL AI
  description: SILDSL AI is the virtual assistant for the Resolv IMS System, delivering updates and stakeholder support for the Strengthening Instructional Leadership at the District and School Levels (SILDSL) "Leadership Project"
  enabled: true

actions:
  # Core routing and model actions
  - action: jvagent/interact_router
  - action: jvagent/openai_embedding
  - action: jvagent/openai_lm
  - action: jvagent/persona
  
  # Custom Resolv actions
  - action: resolv/report_interview_interact_action
  - action: resolv/feedback_interview_interact_action
  - action: jvagent/converse_interact_action
  - action: resolv/onboarding_interact_action
  - action: resolv/resolv_api_action
  
  # Integration and utility actions
  - action: jvagent/access_control_action
  - action: jvagent/agent_utils
  - action: jvagent/tts_action
  - action: jvagent/stt_action
```

## Actions

### Core Actions

#### InteractRouter
Intent-based routing action that analyzes utterances and routes to appropriate InteractActions.

**Configuration:**
- Model: `gpt-4.1-mini`
- History limit: 3 interactions
- Analyzes conversation context for intelligent routing

#### OpenAI Language Model
Provides LLM integration with GPT-4.1-mini for natural language processing.

**Configuration:**
- Model: `gpt-4.1-mini`
- Temperature: 0.2
- Max tokens: 4096
- Vision support enabled

#### OpenAI Embedding
Generates vector embeddings for semantic search and context retrieval.

**Configuration:**
- Model: `text-embedding-3-small`
- Dimensions: 1536
- Timeout: 30 seconds

#### Persona Action
Conversational agent with SILDSL Project-specific personality and capabilities.

**Configuration:**
- Persona name: "SILDSL"
- Model: `gpt-4.1-mini`
- Temperature: 0.1
- Max tokens: 8192

**Persona Description:**
Virtual assistant for the Strengthening Instructional Leadership at the District and School Levels (SILDSL) Project, also known as the "Leadership Project". Provides informational support and updates on the Leadership Project, which aims to strengthen school and district leadership and improve sector management within Guyana's education system.

**Capabilities:**
- Provide updates and progress reports on the Leadership Project
- Record and file project-related inquiries or feedback from stakeholders
- Answer questions about project objectives, timelines, and implementation partners
- Share official information from the Ministry of Education and the IDB

### Custom Resolv Actions

#### Report Interview InteractAction
Structured interview action for creating incident reports in the Resolv IMS.

**Features:**
- Multi-step interview flow with validation
- Custom directive passing for user guidance
- Media file attachment support
- Location-based report matching
- Privacy-aware logging for sensitive reports

**Configuration:**
- Weight: -50 (runs before fallback actions)
- Model: `gpt-4.1`
- History limit: 3 interactions
- Auto-confirm: false

**Interview Fields:**
- Reporter name
- Reporter phone
- Reporter address
- Reporting on behalf (yes/no)
- Stakeholder name (conditional)
- Stakeholder phone (conditional)
- Stakeholder address (conditional)
- Incident location
- Incident description
- Media attachments (optional)

#### Feedback Interview InteractAction
Structured interview action for submitting feedback on existing reports or projects.

**Features:**
- Report selection with matching
- Media file attachment support
- Feedback content validation
- Integration with Resolv API

**Configuration:**
- Weight: -50 (runs before fallback actions)
- Model: `gpt-4.1`
- History limit: 3 interactions
- Auto-confirm: false

**Interview Fields:**
- Project details (for report matching)
- Feedback content
- Selected report ID
- Media files (optional)

#### Converse InteractAction
Fallback action for smalltalk and casual conversation.

**Configuration:**
- Weight: 100 (runs last as a safety net)
- Handles general conversation when no specific action is triggered

#### Resolv Onboarding InteractAction
User registration and group subscription action for new users.

**Features:**
- User registration with Resolv API
- Automatic group subscription
- Session tracking
- Access control integration

**Configuration:**
- Weight: -100 (runs early in routing)

#### Resolv API Action
Central configuration and API client for Resolv IMS integration.

**Features:**
- Centralized API credentials
- Report creation and retrieval
- Feedback submission
- File upload management
- Organization and project context

**Configuration:**
- User UUID: `${RESOLV_SILDSL_USER_UUID}`
- Secret token: `${RESOLV_SILDSL_SECRET_TOKEN}`
- API URL: `${RESOLV_SILDSL_API_URL}`
- Organization slug: `${RESOLV_SILDSL_ORGANIZATION_SLUG}`
- Project ID: `${RESOLV_SILDSL_PROJECT_ID}`
- Agent identifier: `${RESOLV_SILDSL_AGENT_IDENTIFIER}`

### Integration Actions

#### Access Control Action
Role-based access control with session tracking and permission validation.

**Configuration:**
- Enabled: false (disabled by default)
- Channel-based permissions (default, whatsapp)
- User and group-based access control

#### Agent Utils Action
Power user controls for agent management and debugging.

**Features:**
- Agent status monitoring
- Configuration inspection
- Debug utilities

#### Text-to-Speech Action
Converts text responses to speech audio.

**Configuration:**
- Provider: `elevenlabs`
- Model: `eleven_turbo_v2`
- Voice: "Sarah"

#### Speech-to-Text Action
Converts audio messages to text.

**Configuration:**
- Provider: `deepgram`
- Model: `nova-2`

## Usage

### Starting the Agent

When jvagent starts from the app directory:

1. The agent configuration is loaded from `agent.yaml` and environment variables are resolved
2. An Agent node is created/updated in the graph
3. Actions are discovered from `actions/resolv/` subdirectories (shared with other agents)
4. Each action's `info.yaml` is read and environment variables are resolved
5. Action classes are loaded and `__init__.py` modules are imported for endpoint discovery
6. Actions are registered with their configuration from the `actions` section in `agent.yaml`
7. The agent is ready to process requests

### Interacting with the Agent

**Web Interface:**
```http
POST /api/agents/{agent_id}/interact
Content-Type: application/json

{
  "utterance": "I want to report a leadership training issue",
  "user_id": null,
  "session_id": null,
  "channel": "default",
  "data": {}
}
```

### Example Workflows

#### Creating a Report
1. User: "I want to report an issue with the leadership training program"
2. Agent routes to `report_interview_interact_action`
3. Agent collects: reporter info, incident details, location, media
4. Agent creates report in Resolv IMS for SILDSL Project
5. Agent provides confirmation with report ID

#### Submitting Feedback
1. User: "I want to give feedback on the district leadership workshop"
2. Agent routes to `feedback_interview_interact_action`
3. Agent matches existing reports
4. Agent collects feedback content and media
5. Agent submits feedback to Resolv IMS
6. Agent provides confirmation

#### Project Information
1. User: "What is the SILDSL Project about?"
2. Agent routes to `persona` action
3. Agent provides information about SILDSL Project objectives and focus areas
4. Agent offers to help with reports or feedback

## Environment Variables

This agent uses environment variables for configuration:

**Required:**
- `${OPENAI_API_KEY}` - OpenAI API key for LLM and embeddings

**SILDSL Project Resolv API:**
- `${RESOLV_SILDSL_USER_UUID}` - Resolv API user UUID for SILDSL Project
- `${RESOLV_SILDSL_SECRET_TOKEN}` - Resolv API secret token for SILDSL Project
- `${RESOLV_SILDSL_API_URL}` - Resolv API URL for SILDSL Project
- `${RESOLV_SILDSL_ORGANIZATION_SLUG}` - Organization slug in Resolv IMS
- `${RESOLV_SILDSL_PROJECT_ID}` - SILDSL Project ID in Resolv IMS
- `${RESOLV_SILDSL_AGENT_IDENTIFIER}` - Agent identifier for SILDSL

**Speech Services:**
- `${TTS_API_KEY}` - Text-to-speech API key (ElevenLabs)
- `${STT_API_KEY}` - Speech-to-text API key (Deepgram)

See the main [jvagent README](../../../../../README.md) for more information about environment variable resolution.

## Customization

### Modifying Persona

Update the persona description in `agent.yaml` to customize the agent's behavior:

```yaml
- action: jvagent/persona
  context:
    persona_name: "SILDSL"
    persona_description: |
      Your custom persona description here
    persona_capabilities:
      - "Custom capability 1"
      - "Custom capability 2"
```

### Adjusting Routing Weights

Action weights control execution order:
- Negative weights run earlier (e.g., -100, -50)
- Positive weights run later (e.g., 100 for fallback)
- Default weight is 0

Update weights in `agent.yaml` under each action's `context.weight`.

### Adding Project-Specific Actions

1. Create a new action directory under `actions/resolv/{action_name}/`
2. Implement the action class in `{action_name}.py`
3. Create `info.yaml` with action metadata
4. Add `endpoints.py` for API endpoints (if needed)
5. Update `agent.yaml` to assign the new action

## Testing

### Report Interview Testing
- Test all validation scenarios (name, phone, address formats)
- Test conditional fields (stakeholder info when reporting on behalf)
- Test media file attachments
- Test location-based report matching
- Verify privacy protection for sensitive reports

### Feedback Interview Testing
- Test report matching with various descriptions
- Test feedback content validation
- Test media file attachments
- Verify integration with Resolv API

### Persona Testing
- Test SILDSL Project-specific information queries
- Verify appropriate responses to stakeholder questions
- Test capability demonstrations
- Verify concise response style

## Known Issues and Fixes

See the main [resolv_demo README](../resolv_demo/README.md) for detailed information about recent fixes that apply to all Resolv agents.

## Future Enhancements

1. **Localization** - Support multiple languages for validation messages
2. **Advanced Privacy** - Configurable privacy levels for sensitive data
3. **Audit Trail** - Compliance-focused audit logging
4. **Smart Truncation** - Intelligent conversation history management
5. **Vector Search** - Enable Typesense integration for semantic search
6. **Project-Specific Workflows** - Custom workflows for SILDSL Project needs

## Support

For issues or questions:
- Review the [jvagent documentation](../../../../../README.md)
- Check the [architecture documentation](../../../docs/architecture.md)
- Review action-specific READMEs in the `actions/` directory
- Consult the [resolv_demo README](../resolv_demo/README.md) for shared action documentation
