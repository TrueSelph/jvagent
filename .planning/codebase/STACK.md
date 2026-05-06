# Technology Stack

**Analysis Date:** 2026-05-06

## Languages

**Primary:**
- Python 3.8+ - Core platform and all application logic
  - Supports Python 3.8, 3.9, 3.10, 3.11, 3.12
  - Specified in `pyproject.toml`: `requires-python = ">=3.8"`

**Secondary:**
- YAML - Application configuration and action descriptors
- HTML/CSS/JavaScript - Frontend and web components (via jvspatial)

## Runtime

**Environment:**
- Python interpreter (3.8+)
- Uvicorn ASGI server - production web server (referenced in `jvagent/cli/server_config.py`)
- MCP (Model Context Protocol) - Python 3.10+ for full functionality; Python 3.8-3.9 supported with limited MCP support

**Package Manager:**
- pip - Primary package management
- Lockfile: `requirements.txt` and `requirements-dev.txt` present
  - Core dependencies: `jvspatial>=0.0.6`, `aiohttp>=3.9.0`, `pyyaml>=6.0.0`, `httpx>=0.27.0`

## Frameworks

**Core:**
- **jvspatial** (>=0.0.6) - Graph database and spatial primitives framework
  - Provides: `Node`, `Root`, `Server`, authentication, CORS, database abstraction
  - Database backends: JSON (default), SQLite, or custom via jvspatial
  - Located: Used throughout `jvagent/core/`, `jvagent/memory/`, `jvagent/action/`

- **FastAPI** - Web framework for HTTP API endpoints
  - Used in action endpoints (`jvagent/action/endpoints.py`)
  - Integrated via jvspatial Server
  - Decorators: `@endpoint` (custom jvagent/jvspatial annotation)

**Testing:**
- **pytest** (>=7.0) - Test runner
- **pytest-asyncio** (>=0.21.0) - Async test support
- **pytest-cov** (>=4.0.0) - Coverage reporting
- Config: `pyproject.toml` with `asyncio_mode = "auto"`, testpaths = `["tests"]`

**Build/Dev:**
- **black** (>=23.9.0) - Code formatter
  - Line length: 88 characters
- **isort** (>=6.0.0) - Import sorter
  - Profile: `black`
- **flake8** (>=6.0.0) - Linter with plugins:
  - pep8-naming
  - flake8-docstrings
  - flake8-comprehensions
  - flake8-bugbear
  - flake8-annotations
  - flake8-simplify
- **mypy** (>=1.6.0) - Type checker
  - Python version: 3.9
  - Strict mode: Not enforced globally, but enforced for core modules
- **pre-commit** (>=3.0.0) - Git hooks framework
- **detect-secrets** (>=1.5.0) - Secret detection

## Key Dependencies

**Critical Infrastructure:**
- **aiohttp** (>=3.9.0) - Async HTTP client library
  - Used for: WhatsApp, Facebook, web search, callback handlers
  - Location: `jvagent/core/callback.py`, `jvagent/action/whatsapp/info.yaml`

- **httpx** (>=0.27.0) - Modern async/sync HTTP client
  - Widely used across actions: email, facebook, web search (Brave), web requests
  - Used for: API calls, webhook handling, external service communication
  - Location: Core usage in `jvagent/core/callback.py`

- **pyyaml** (>=6.0.0) - YAML parser
  - Application configuration (`app.yaml`)
  - Action descriptor files (`info.yaml`)
  - Location: `jvagent/core/app_yaml_validator.py`, `jvagent/core/app_loader.py`

- **python-dotenv** (>=1.0.0) - Environment variable loading
  - `.env` file support with dotenv_values (child process safe)
  - Used via `jvagent/env.py`

- **Jinja2** (>=3.1.0) - Template engine
  - Used for: Action templates, prompt generation
  - Location: Core framework for action generation

- **pymupdf** (>=1.24.0) - PDF processing
  - PDF document handling
  - Used in document indexing and analysis

- **mcp** (>=1.0.0) - Model Context Protocol
  - Conditional: Python 3.10+ (full support)
  - Partial support: Python 3.8-3.9 without mcp package
  - Location: `jvagent/action/mcp/` for MCP server integration

**Language Model & Embeddings:**
- **openai** (>=1.0.0) - OpenAI SDK
  - Language models (GPT-4, GPT-3.5-turbo, etc.)
  - Embeddings (text-embedding-3-small, text-embedding-3-large, ada)
  - Location: `jvagent/action/model/language/openai/`, `jvagent/action/model/embedding/openai/`

- **litellm** (>=1.82.0) - LLM abstraction layer
  - Supports multiple LM providers
  - Used in PageIndex for token counting and model routing
  - Location: `jvagent/action/pageindex/pageindex_action/info.yaml`

- **tiktoken** (>=0.5.0) - Token counting for OpenAI models
  - Used in PageIndex and response generation
  - Location: Tests and PageIndex action

**Search & Vector Storage:**
- **typesense** - Vector search engine (optional)
  - Conditional import in `jvagent/action/vectorstore/typesense/typesense.py`
  - Vector dimensions: 384 (default for sentence-transformers)

**Document Processing:**
- **PyPDF2** (>=3.0.1) - PDF processing library
  - PDF reading and analysis
  - Location: PageIndex tests and document processing

- **docling** (>=2.0.0) - Document layout understanding
  - Advanced document parsing and layout analysis
  - Used in PageIndex action for document indexing
  - Location: `jvagent/action/pageindex/pageindex_action/info.yaml`

- **tabulate** (>=0.9.0) - Table formatting
  - Used with docling for structured data extraction

- **rapidocr** (>=3.3,<4) - OCR engine
  - Optional: For document indexing with OCR
  - Location: PageIndex optional dependencies

**Speech/Audio:**
- **elevenlabs** (>=1.13.0) - Text-to-Speech API
  - ElevenLabs TTS integration
  - Location: `jvagent/action/tts_action/elevenlabs/info.yaml`

- **deepgram-sdk** (>=6.0.0) - Speech-to-Text SDK
  - Deepgram STT service
  - Location: `jvagent/action/stt_action/deepgram/info.yaml`

**Social & Communication:**
- **filetype** (>=1.2.0) - File type detection
  - Used in WhatsApp media handling
  - Location: `jvagent/action/whatsapp/info.yaml`

- **requests** (>=2.28.0) - HTTP library
  - Facebook Graph API integration
  - Location: `jvagent/action/facebook_action/info.yaml`

- **google-api-python-client** (>=2.192.0) - Google API client
  - Gmail, Drive, Sheets, Docs, Calendar APIs
  - Location: `jvagent/action/google/google_gmail_action/info.yaml`

- **google-auth-httplib2** (>=0.2.0) - Google Auth for httplib2
  - OAuth2 authentication for Google services

- **google-auth-oauthlib** (>=1.2.0) - Google OAuth library
  - OAuth flow for Google services

**Distributed Locking (Optional):**
- **redis** (>=5.0.0) - Redis client (optional)
  - Distributed conversation locks across workers
  - Environment: `JVAGENT_CONVERSATION_LOCK_REDIS_URL`
  - Location: `jvagent/memory/distributed_conversation_lock.py`

- **boto3** (>=1.28.0) - AWS SDK (optional)
  - DynamoDB for distributed conversation locks
  - Environment: `JVAGENT_CONVERSATION_LOCK_DYNAMODB_TABLE`
  - Location: `jvagent/memory/distributed_conversation_lock.py`

## Configuration

**Environment:**
- `.env` file support via `python-dotenv`
- Environment variable resolution in `jvagent/env.py`
- Critical env vars:
  - `JVAGENT_APP_ID` - Application identifier
  - Database: `JVSPATIAL_DB_TYPE`, `JVSPATIAL_DB_PATH`
  - Server: `JVAGENT_HOST`, `JVAGENT_PORT`, `JVAGENT_TITLE`
  - Locks: `JVAGENT_CONVERSATION_LOCK_REDIS_URL`, `JVAGENT_CONVERSATION_LOCK_DYNAMODB_TABLE`

**Build:**
- `pyproject.toml` - Python project metadata and dependencies
- `setup.py` - Setuptools configuration
- Entry point: `jvagent = "jvagent.cli:main"`
- Package data: Built-in YAML profiles and static files in `jvagent/scaffold/`

## Platform Requirements

**Development:**
- Python 3.8+ (tested 3.8-3.12)
- pip with setuptools>=45 and wheel
- Pre-commit hooks for formatting and linting
- Git for version control and pre-commit

**Production:**
- Python 3.8+ runtime
- Uvicorn or compatible ASGI server
- Optional: Redis for distributed locking
- Optional: DynamoDB for distributed locking
- File storage: Local filesystem or cloud (S3, etc. via jvspatial)
- Database: JSON, SQLite, or other jvspatial-supported backends

**Deployment Targets:**
- Standalone Python application (CLI: `jvagent`)
- Docker container (Dockerfile generation via `jvagent bundle` command)
- AWS Lambda (with distributed locking support)
- Any ASGI-compatible hosting

---

*Stack analysis: 2026-05-06*
