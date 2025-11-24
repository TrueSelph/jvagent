# Application Architecture

This document provides an overview of the jvagent application architecture.

## Overview

jvagent applications are built on the jvspatial graph database framework, providing a structured way to define and manage agents and their associated actions.

## Core Components

### App Node

The root application node that manages:
- Application-wide configuration
- File storage operations
- Global settings

### Agents Node

The manager node for all agents in the application. Each agent is:
- A Node in the graph
- Connected to the Agents manager
- Has its own Actions and Memory nodes

### Agent Nodes

Individual agent instances that:
- Process requests
- Execute assigned actions
- Manage conversations and memory

### Actions

Pluggable components that:
- Extend agent functionality
- Can be enabled/disabled per agent
- Have lifecycle hooks
- Maintain their own state

### Memory

Manages agent memory including:
- User data
- Conversations
- Collections

## Graph Structure

```
Root
└── App
    └── Agents
        └── Agent (example_agent)
            ├── Actions
            │   └── Action (example_action)
            └── Memory
```

## Data Flow

1. **Request** → Agent receives request
2. **Processing** → Agent executes assigned actions
3. **Response** → Agent returns result
4. **Memory** → Conversation/interaction stored in Memory

## Configuration Hierarchy

1. **Environment Variables** (.env) - Application-wide
2. **Agent Configuration** (agent.yaml) - Agent-specific
3. **Action Configuration** (info.yaml + agent.yaml actions section) - Action-specific

## File Storage

Files are stored via the App node:
- Local storage: `./.files/`
- S3 storage: Configurable S3 bucket
- Access via proxy URLs for security

## Security

- Authentication via JWT tokens
- File access via secure proxy URLs
- Action isolation and sandboxing

