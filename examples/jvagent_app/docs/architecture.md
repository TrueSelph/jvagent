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

### Memory System

The memory system manages agent memory with an optimized architecture:

**Components:**
- **User nodes**: Represent end-users interacting with the agent
- **Conversation nodes**: Represent conversation sessions with rolling window pruning
- **Interaction nodes**: Chained chronologically for efficient traversal

**Interaction Chaining Architecture:**
- Interactions are stored as a chronological chain using bidirectional edges
- Pattern: `Interaction1 <-> Interaction2 <-> Interaction3`
- Conversation connects only to the first interaction
- Enables efficient forward and backward traversal
- Optimized access to last interaction via cached reference

**Rolling Window Pruning:**
- Conversations can automatically prune old interactions when a limit is set
- Configure at agent level (default for all conversations) or per-conversation
- Set via `agent.interaction_limit` (agent-level default) or `conversation.interaction_limit` (per-conversation override)
- When limit is exceeded, oldest interactions are automatically removed
- Maintains conversation continuity while managing memory usage
- Useful for long-running conversations with memory constraints

## Graph Structure

```
Root
└── App
    └── Agents
        └── Agent (example_agent)
            ├── Actions
            │   └── Action (example_action)
            └── Memory
                └── User
                    └── Conversation
                        └── Interaction1 <-> Interaction2 <-> Interaction3
                            (bidirectional chain)
```

## Data Flow

1. **Request** → Agent receives request
2. **Processing** → Agent executes assigned actions
3. **Response** → Agent returns result
4. **Memory** → Interaction created and chained to conversation
5. **Pruning** → If interaction limit exceeded, oldest interactions auto-pruned

## Configuration Hierarchy

1. **Environment Variables** (.env) - Application-wide
2. **Agent Configuration** (agent.yaml) - Agent-specific
3. **Action Configuration** (info.yaml + agent.yaml actions section) - Action-specific

## File Storage

Files are stored via the App node:
- Local storage: `./.files/`
- S3 storage: Configurable S3 bucket
- Access via proxy URLs for security

## Performance Optimizations

The architecture includes several performance optimizations:

**Query Optimizations:**
- Uses `count()` for efficient record counting without loading data
- Uses `find_one()` for optimized single-record retrieval
- Uses `node()` for direct single-node graph traversal
- Database-level operations instead of client-side filtering

**Indexing:**
- Automatic database indexes on frequently queried fields
- Indexed fields: `user_id`, `session_id`, `status`, `conversation_id`
- Compound indexes for multi-field queries
- Transparent index creation on first use

**Memory Management:**
- Rolling window pruning for conversation interactions
- Cached reference to last interaction for O(1) access
- Efficient chain traversal for interaction history
- Automatic cleanup of old interactions

## Security

- Authentication via JWT tokens
- File access via secure proxy URLs
- Action isolation and sandboxing

