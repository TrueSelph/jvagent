# Resolv Demo Agent

A production-ready agent for the Resolv Incident Management System, demonstrating advanced jvagent capabilities including interview-based data collection, WhatsApp integration, and role-based access control.

## Overview

The Resolv Demo Agent demonstrates:
- Interview-based actions for structured data collection (reports and feedback)
- Intent-based routing with conversation history management
- Multi-channel support (web and WhatsApp)
- Role-based access control with session tracking
- Integration with external APIs (Resolv IMS)
- Speech-to-text and text-to-speech capabilities
- Vector-based semantic search (optional)

## Structure

```
resolv_demo/
├── actions/              # Actions packaged with this agent
│   └── resolv/          # Namespace directory
│       ├── feedback_interview_interact_action/
│       │   ├── __init__.py
│       │   ├── feedback_interview_interact_action.py
│       │   ├── endpoints.py
│       │   └── info.yaml
│       ├── report_interview_interact_action/
│       │   ├── __init__.py
│       │   ├── report_interview_interact_action.py
│       │   └── info.yaml
│       ├── resolv_api_action/
│       │   ├── __init__.py
│       │   ├── resolv_api_action.py
│       │   ├── endpoints.py
│       │   └── info.yaml
│       └── resolv_onboarding_interact_action/
│           ├── __init__.py
│           ├── resolv_onboarding_interact_action.py
│           ├── endpoints.py
│           └── info.yaml
├── agent.yaml           # Agent configuration and action assignments
└── README.md           # This file
```

## Configuration

### Agent Configuration (agent.yaml)

The `agent.yaml` file contains both agent configuration and action assignments:

```yaml
agent: resolv/resolv_demo
version: 1.0.0
author: V75 Inc.
jvagent: ~0.0.1

context:
  alias: Resolv Demo Agent
  description: A demo agent for Resolv
  enabled: true

actions:
  # Core routing and model actions
  - action: jvagent/interact_router
  - action: jvagent/openai_lm
  - action: jvagent/openai_embedding
  - action: jvagent/persona
  
  # Custom Resolv actions
  - action: resolv/report_interview_interact_action
  - action: resolv/feedback_interview_interact_action
  - action: resolv/resolv_onboarding_interact_action
  - action: resolv/resolv_api_action
  
  # Integration actions
  - action: jvagent/whatsapp_action
  - action: jvagent/tts_action
  - action: jvagent/stt_action
  - action: jvagent/access_control_action
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

**API Endpoints:**
- `POST /actions/{action_id}/query` - Query the language model
- `GET /actions/{action_id}/metrics` - Get usage metrics

#### OpenAI Embedding
Generates vector embeddings for semantic search and context retrieval.

**Configuration:**
- Model: `text-embedding-3-small`
- Dimensions: 1536
- Timeout: 30 seconds

**API Endpoints:**
- `POST /actions/{action_id}/embed` - Generate embedding for text
- `POST /actions/{action_id}/embed/batch` - Generate embeddings for multiple texts

#### Persona Action
Conversational agent with configurable personality and capabilities.

**Configuration:**
- Persona name: "Navi"
- Model: `gpt-4.1-mini`
- Temperature: 0.1
- Max tokens: 8192

**Capabilities:**
- Answer questions about jvagent
- Demonstrate action delegation
- Process user interactions with behavioral parameters
- Provide streaming and non-streaming responses

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
- Model: `gpt-4.1-mini`
- History limit: 3 interactions
- DSPy optimization enabled

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

**API Endpoints:**
- Standard InteractAction endpoints via InteractWalker

#### Feedback Interview InteractAction
Structured interview action for submitting feedback on existing reports or projects.

**Features:**
- Report selection with matching
- Media file attachment support
- Feedback content validation
- Integration with Resolv API

**Configuration:**
- Weight: -50 (runs before fallback actions)
- Model: `gpt-4.1-mini`
- History limit: 3 interactions
- DSPy optimization enabled

**Interview Fields:**
- Project details (for report matching)
- Feedback content
- Selected report ID
- Media files (optional)

**API Endpoints:**
- Standard InteractAction endpoints via InteractWalker

#### Resolv Onboarding InteractAction
User registration and group subscription action for new users.

**Features:**
- User registration with Resolv API
- Automatic group subscription
- Session tracking
- Access control integration

**Configuration:**
- Weight: -100 (runs early in routing)
- Default groups: ["28"]

**API Endpoints:**
- `POST /actions/{action_id}/onboard` - Onboard a new user
- `GET /actions/{action_id}/status` - Check onboarding status

#### Resolv API Action
Central configuration and API client for Resolv IMS integration.

**Features:**
- Centralized API credentials
- Report creation and retrieval
- Feedback submission
- File upload management
- Organization and project context

**Configuration:**
- Organization: `mopw-guyana`
- Project ID: `32`
- API URL: `https://app.resolv-ims.com/api`

**API Endpoints:**
- `POST /actions/{action_id}/reports` - Create a report
- `GET /actions/{action_id}/reports/{report_id}` - Get report details
- `POST /actions/{action_id}/feedback` - Submit feedback
- `POST /actions/{action_id}/upload` - Upload media files

### Integration Actions

#### WhatsApp Action
Multi-provider WhatsApp integration for messaging.

**Configuration:**
- Provider: `wwebjs` (supports wppconnect, ultramsg, wwebjs)
- Session management
- Media file handling
- Webhook support

**Features:**
- Send and receive messages
- Media file upload/download
- Session persistence
- Multi-provider support

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

#### Access Control Action
Role-based access control with session tracking and permission validation.

**Features:**
- Channel-based permissions (default, whatsapp)
- User and group-based access control
- Session tracking
- Dynamic permission validation

**Configuration:**
- Default channel: Allow all users
- WhatsApp channel: Restricted to specific users (5926431530)

**API Endpoints:**
- `POST /actions/{action_id}/validate` - Validate user permissions
- `GET /actions/{action_id}/permissions` - Get permission configuration

#### Agent Utils Action
Power user controls for agent management and debugging.

**Features:**
- Agent status monitoring
- Configuration inspection
- Debug utilities

## Usage

### Starting the Agent

When jvagent starts from the app directory:

1. The agent configuration is loaded from `agent.yaml` and environment variables are resolved
2. An Agent node is created/updated in the graph
3. Actions are discovered from `actions/resolv/` subdirectories
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
  "utterance": "I want to report a pothole on Main Street",
  "user_id": null,
  "session_id": null,
  "channel": "default",
  "data": {}
}
```

**WhatsApp:**
Send a message to the configured WhatsApp number. The agent will automatically route to the appropriate action based on intent.

### Example Workflows

#### Creating a Report
1. User: "I want to report a pothole"
2. Agent routes to `report_interview_interact_action`
3. Agent collects: reporter info, incident details, location, media
4. Agent creates report in Resolv IMS
5. Agent provides confirmation with report ID

#### Submitting Feedback
1. User: "I want to give feedback on my report"
2. Agent routes to `feedback_interview_interact_action`
3. Agent matches existing reports
4. Agent collects feedback content and media
5. Agent submits feedback to Resolv IMS
6. Agent provides confirmation

#### User Onboarding
1. New user interacts with agent
2. Agent routes to `resolv_onboarding_interact_action`
3. Agent registers user with Resolv API
4. Agent subscribes user to default groups
5. Agent confirms successful onboarding

## Environment Variables

This agent uses environment variables for configuration:

**Required:**
- `${OPENAI_API_KEY}` - OpenAI API key for LLM and embeddings
- `${APP_BASE_URL}` - Base URL for the application

**WhatsApp Integration:**
- `${WHATSAPP_API_URL}` - WhatsApp provider API URL
- `${WHATSAPP_API_KEY}` - WhatsApp provider API key
- `${WHATSAPP_SESSION}` - WhatsApp session identifier
- `${WHATSAPP_TOKEN}` - WhatsApp webhook token

**Speech Services:**
- `${TTS_API_KEY}` - Text-to-speech API key (ElevenLabs)
- `${STT_API_KEY}` - Speech-to-text API key (Deepgram)

**Optional:**
- `${TYPESENSE_API_KEY}` - Typesense API key for vector search (if enabled)

See the main [jvagent README](../../../../../README.md) for more information about environment variable resolution.

## Customization

### Adding New Actions

1. Create a new action directory under `actions/resolv/{action_name}/`
2. Implement the action class in `{action_name}.py`
3. Create `info.yaml` with action metadata
4. Add `endpoints.py` for API endpoints (if needed)
5. Update `agent.yaml` to assign the new action

### Modifying Interview Flows

Interview actions use the `InterviewInteractAction` base class. To modify:

1. Update field definitions in the action class
2. Add custom validators for field validation
3. Implement directive overrides for custom user guidance
4. Configure branch functions for conditional logic

### Adjusting Routing Weights

Action weights control execution order:
- Negative weights run earlier (e.g., -100, -50)
- Positive weights run later (e.g., 100 for fallback)
- Default weight is 0

Update weights in `agent.yaml` under each action's `context.weight`.

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

### WhatsApp Integration Testing
- Send messages and verify interaction completion
- Test media file upload/download
- Verify proper response formatting
- Test error handling and fallback responses

### Access Control Testing
- Test channel-based permissions (default vs whatsapp)
- Test user-based access restrictions
- Verify session tracking
- Test permission validation

## Known Issues and Fixes

See [TARGETED_ACTION Updates](../../../../../../../jvsproject/README.md) for detailed information about recent fixes:

1. **Report Interview Directive Passing** - Fixed directive override functions and validation messages
2. **Feedback Interview Fixes** - Fixed imports, validators, and directive overrides
3. **WhatsApp Interaction Completion** - Fixed interaction completion and response handling
4. **Conversation History Truncation** - Fixed routing context preservation
5. **Media File Access** - Added unauthenticated endpoints for WhatsApp media

## Future Enhancements

1. **Localization** - Support multiple languages for validation messages
2. **Advanced Privacy** - Configurable privacy levels for sensitive data
3. **Audit Trail** - Compliance-focused audit logging
4. **Smart Truncation** - Intelligent conversation history management
5. **Vector Search** - Enable Typesense integration for semantic search
6. **Multi-Project Support** - Support multiple Resolv projects per agent

## Support

For issues or questions:
- Review the [jvagent documentation](../../../../../README.md)
- Check the [architecture documentation](../../../docs/architecture.md)
- Review action-specific READMEs in the `actions/` directory
