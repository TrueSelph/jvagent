# Interview Action (legacy)

> **Superseded by [`../interview_action/`](../interview_action/README.md).** New structured interviews use skills-v2 + `InterviewAction` (`interview__*` tools, frontmatter `interview:` contract, standard procedure composed via extends). This package (`InterviewInteractAction`) remains for existing Rails-style flows only.

A reusable, extensible interview system for gathering structured information from users through multi-turn conversations with validation, revision, and confirmation flows.

## Overview

The Interview Action provides a reusable way to collect responses from users in a coordinated, multi-turn conversation. It manages the interview lifecycle from initialization through completion or cancellation using a unified orchestrator pattern.

**Key Design**: `InterviewInteractAction` is an abstract base class that developers extend to create multiple interview flows (e.g., registration, onboarding) within the same agent. Each interview maintains its own per-user session attached to Conversation nodes with type identification.

### Key Features

- **Abstract Base Class Pattern**: Extend `InterviewInteractAction` to create custom interview flows
- **Multiple Interviews Per Agent**: Run registration, onboarding, and other interviews simultaneously
- **Per-User Session Isolation**: Sessions attached to Conversation nodes for user separation
- **Unified Classification**: Single LLM call detects intent and extracts field values
- **Branch Functions**: Custom Python functions for complex branching logic
- **State Handlers**: `@on_interview_complete`, `@on_interview_review`, `@on_interview_cancelled`
- **Interaction-Only**: No REST/API endpoints—operates through InteractWalker and conversation flow
- **Task Tracking**: Automatically registers active tasks when session is ACTIVE/REVIEW; removes on completion/cancellation (see [Task Tracking](../../../docs/task-tracking.md))

## Quick Start

```python
from jvagent.action.interview import InterviewInteractAction
from jvspatial.core.annotations import attribute
from typing import Any, Dict, List

class RegistrationInterviewAction(InterviewInteractAction):
    description: str = "User registration interview flow"

    anchors: List[str] = attribute(
        default_factory=lambda: [
            "User wants to register",
            "User is providing registration information",
        ]
    )

    question_graph: List[Dict[str, Any]] = attribute(
        default_factory=lambda: [
            {
                "name": "user_name",
                "question": "What's your full name?",
                "constraints": {"description": "User's full name", "type": "string"},
                "required": True,
            },
            {
                "name": "user_email",
                "question": "What is your email?",
                "constraints": {"description": "Email address", "type": "string", "format": "email"},
                "required": True,
            },
        ]
    )
```

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/architecture.md) | Module layout, core components, state machine |
| [Configuration](docs/configuration.md) | Context-only overrides, agent.yaml, auto_confirm |
| [Question Schema](docs/question-schema.md) | Question config, branching, branch functions |
| [Decorators & Handlers](docs/decorators.md) | Input handlers, validators, review override, directive overrides |
| [Classification](docs/classification.md) | Intent detection, extraction modes, troubleshooting |
| [Session & State](docs/session-and-state.md) | Session management, completion/cancellation/review handlers |
| [Troubleshooting](docs/troubleshooting.md) | Common issues and solutions |
| [Examples](docs/examples.md) | Full examples |

## File Structure

```
interview/
├── interview_interact_action.py   # Abstract base class
├── info.yaml
├── README.md
├── docs/                           # Detailed documentation
└── core/
    ├── foundation/                 # Enums, config, decorators, prompts
    ├── graph/                      # QuestionNode, InterviewWalker, branching
    ├── classification/            # ClassificationHandler
    ├── processing/                 # DirectiveBuilder
    ├── session/                    # InterviewSession
    └── utils/                     # Session helpers, cache
```
