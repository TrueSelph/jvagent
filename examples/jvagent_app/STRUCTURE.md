# Project Structure

> **Interview skills:** Structured interviews use [`jvagent/interview`](../../jvagent/action/interview/README.md) + app-local skills under `agents/.../skills/`. See `agents/jvagent/orchestrator_agent/skills/signup_interview/` for the reference implementation.

This document describes the complete structure of the jvagent demo app boilerplate.

```
jvagent_app/
├── app.yaml                  # Application descriptor (metadata & agent list)
├── .env.example              # Example environment configuration
├── .gitignore                # Git ignore rules
├── CHANGELOG.md              # Project changelog
├── LICENSE                   # Project license
├── README.md                 # Main project documentation
├── SETUP.md                  # Setup guide
├── STRUCTURE.md              # This file
│
├── agents/                   # Custom agent packages (namespace / agent_id)
│   └── jvagent/
│       ├── leadgen_agent/   # Lead generation demo agent
│       │   ├── agent.yaml
│       │   └── README.md
│       └── orchestrator_agent/ # Orchestrator demo agent
│           ├── skills/signup_interview/  # Current signup interview (skills-v2)
│           ├── agent.yaml
│           └── README.md
│
└── docs/                     # Application documentation
    └── architecture.md       # Architecture overview
```

## Directory Descriptions

### Root Directory

- **app.yaml**: Application descriptor with metadata and agent list
- **README.md**: Main project documentation with quick start guide
- **.env.example**: Template for environment configuration
- **.gitignore**: Git ignore rules for common files
- **LICENSE**: Project license file
- **CHANGELOG.md**: Project version history

### agents/

Contains custom agent packages. Each agent is in its own subdirectory and includes:

- **actions/**: Subdirectory containing actions packaged with this agent
  - Actions are organized by namespace (e.g., `jvagent/`, `contrib/`, `custom/`)
  - Each action is in its own subdirectory with:
    - **`__init__.py`**: Package initialization file that imports the action class and endpoints module (standard pattern)
    - **{action_name}.py**: Main action implementation extending `Action` (business logic)
    - **endpoints.py**: HTTP API endpoints for this action (standard pattern)
    - **info.yaml**: Action metadata, dependencies, and configuration
    - **requirements.txt**: Python package dependencies
    - **README.md**: Action-specific documentation
- **agent.yaml**: Agent configuration (name, status, description, etc.) and action assignments
- **README.md**: Agent-specific documentation

**Note**: Actions are now packaged within each agent folder, allowing agents to be self-contained packages with their own actions.

### docs/

Contains application documentation:

- **architecture.md**: Overview of application architecture

## File Purposes

### Configuration Files

- **app.yaml**: Application descriptor with metadata and agent list
- **.env.example**: Template for environment variables
- **agent.yaml**: Agent configuration and action assignments (in each agent folder)
- **info.yaml**: Action metadata and configuration (in each agent's actions/{action_name}/ folder)

### Code Files

- **{action_name}.py**: Action implementation (Python) in each action package
- **requirements.txt**: Python dependencies (per action, optional)

### Documentation

- **README.md**: Project and component documentation
- **architecture.md**: Architecture documentation
- **CHANGELOG.md**: Version history

