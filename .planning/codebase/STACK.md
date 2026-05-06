# Technology Stack

**Analysis Date:** 2026-05-06

## Languages

**Primary:**
- Python 3.8+ (`>=3.8` per `pyproject.toml`) - Entire codebase. Targets Python 3.8 through 3.12. Production deployment image runs on Python 3.12 (`Dockerfile.base`).

**Secondary:**
- YAML - Declarative configuration for apps (`app.yaml`), agents (`agent.yaml`), action packages (`info.yaml`), and pre-commit configuration.
- Jinja2 templates - Used for prompt templating (e.g. `jvagent/scaffold/builtin_profiles/*.yaml`, persona prompt rendering).

## Runtime

**Environment:**
- CPython 3.8+ (recommended 3.10+ for full feature set, since the `mcp` SDK requires `python_version>='3.10'`)
- AsyncIO event loop (`asyncio_mode = "auto"` in pytest)
- AWS Lambda + Lambda Web Adapter (`Dockerfile.base` uses `public.ecr.aws/lambda/python:3.12` and `public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1`)

**Package Manager:**
- pip (>=22 recommended; `setuptools>=45`, `wheel` are required build dependencies)
- Lockfile: Not detected - dependencies pinned via version specifiers in `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`, `requirements-all.txt`

## Frameworks

**Core:**
- `jvspatial>=0.0.6` - Graph-based primitive framework providing `Node`, `Edge`, `Walker`, `@on_visit`, `Server`, auth, file storage, database abstraction, and serverless mode. All jvagent core types (`App`, `Agents`, `Agent`, `Action`, `Memory`, `User`, `Conversation`, `Interaction`) extend jvspatial nodes (`jvagent/core/`, `jvagent/action/base.py`).
- FastAPI (transitive via `jvspatial.api.Server`) - HTTP API surface; `@endpoint` decorators register routes (see `jvagent/core/endpoints.py`, `jvagent/action/endpoints.py`).
- Starlette (transitive via FastAPI) - ASGI primitives (`from starlette.datastructures` in actions).
- Uvicorn (transitive) - ASGI server invoked by `jvspatial`'s `Server.run()`.
- Pydantic (transitive via FastAPI/jvspatial) - Used for action attribute validation and request/response models. `pydantic[email]` (>=2.0) is bundled in `requirements-all.txt` for `EmailStr` support.

**Testing:**
- `pytest>=7.0` - Test runner. Config in `pyproject.toml` `[tool.pytest.ini_options]`.
- `pytest-asyncio>=0.21.0` - Async test support; `asyncio_mode = "auto"`.
- `pytest-cov>=4.0.0`, `coverage>=7.0.0` - Coverage reporting (`[tool.coverage]` in `pyproject.toml`).
- `httpx>=0.24.0` - HTTP client used for endpoint testing (and as runtime client).

**Build/Dev:**
- `setuptools>=45` + `wheel` - Build backend (`pyproject.toml` `[build-system]`).
- `black>=23.9.0` - Code formatter, line length 88, targets py38-py312 (`pyproject.toml` `[tool.black]`).
- `isort>=6.0.0` - Import sorter, profile=black (`[tool.isort]`).
- `flake8>=6.0.0` + plugins (`pep8-naming`, `flake8-docstrings`, `flake8-comprehensions`, `flake8-bugbear`, `flake8-annotations`, `flake8-simplify`) - Linting; config in `.flake8`.
- `mypy>=1.6.0` - Static type checking. Config at `pyproject.toml` `[tool.mypy]`; `examples/` and `tests/` are excluded; many submodules are listed under `ignore_errors = true`.
- `pre-commit>=3.0.0` - Hooks defined in `.pre-commit-config.yaml`.
- `detect-secrets>=1.5.0` - Pre-commit hook scanning for committed secrets.

## Key Dependencies

**Critical (always installed via `pyproject.toml` / `requirements.txt`):**
- `aiohttp>=3.9.0` - Async HTTP for WhatsApp action (`jvagent/action/whatsapp/`) and other channel adapters.
- `jvspatial>=0.0.6` - Graph runtime, server, auth, and storage backbone.
- `python-dotenv>=1.0.0` - Loads `.env` files at app root and CWD (`jvagent/env.py`, `jvagent/cli/main.py`).
- `pyyaml>=6.0.0` - Parsing of `app.yaml`, `agent.yaml`, action `info.yaml`, builtin profiles.
- `httpx>=0.27.0` - Primary async HTTP client used by most action HTTP integrations (Anthropic, OpenRouter, OpenAI passthrough, Microsoft Graph, Brave, ElevenLabs ext, Postiz, etc.).
- `jinja2>=3.1.0` - Prompt templating for persona/skill actions and scaffolding.
- `pymupdf>=1.24.0` - PDF rendering / extraction (used by PageIndex stack).
- `mcp>=1.0.0` (Python 3.10+ only) - Model Context Protocol stdio client. Required by `jvagent/action/mcp/` and `jvspatial_fs_server`.

**Runtime extras (`requirements-all.txt` - production-ready superset):**
- `typesense>=0.21.0` - Typesense vector search client (`jvagent/action/vectorstore/typesense/`).
- `openai>=1.0.0` - OpenAI / OpenAI-compatible client (`jvagent/action/model/language/openai`, `jvagent/action/model/embedding/openai`, PageIndex tree search).
- `pydantic[email]>=2.0` - `EmailStr` support for handoff and email actions.

**Optional extras (per-action, declared in `info.yaml` `dependencies.pip`):**
- Google APIs: `google-api-python-client>=2.192.0`, `google-auth-httplib2>=0.2.0`, `google-auth-oauthlib>=1.2.0` (Calendar, Gmail, Drive, Docs, Sheets, PageIndex Drive sync).
- `requests>=2.28.0` - Used by `jvagent/action/facebook_action/` (Graph API SDK).
- `filetype>=1.2.0` - MIME type detection in WhatsApp action.
- `elevenlabs>=1.13.0` - ElevenLabs TTS SDK (`jvagent/action/tts_action/elevenlabs/`).
- `deepgram-sdk>=6.0.0` - Deepgram STT SDK (`jvagent/action/stt_action/deepgram/`).
- `google-search-results>=2.4.2` - SerpAPI client (`jvagent/action/web_search/serpapi/`).
- `openpyxl>=3.1.0` - Excel workbook manipulation (`jvagent/action/microsoft/microsoft_excel_action/`).
- `litellm>=1.82.0`, `tiktoken>=0.5.0`, `PyPDF2>=3.0.1`, `pypdf>=4.0.0` - PageIndex tree-search / token counting / PDF parsing.
- `docling>=2.0.0`, `tabulate>=0.9.0` - PageIndex Docling document conversion (test extra and `pageindex` extra).
- `rapidocr>=3.3,<4` - OCR for the `pageindex` extra (manual / pre-baked images).

**Distributed-lock extras (`pyproject.toml` `distributed-lock`):**
- `redis>=5.0.0` - `redis.asyncio` for cluster-wide conversation locks.
- `boto3>=1.28.0` - DynamoDB-backed conversation lock (and AWS clients in serverless mode).

## Configuration

**Environment:**
- `.env` files loaded by `python-dotenv` at app root and CWD via `jvagent/env.py::get_jvagent_app_id()` and `jvagent/cli/main.py::load_app_env()`.
- `.env.example` enumerates ~100+ environment variables across server, database, auth, file storage, integrations, performance, logging.
- Two namespace prefixes: `JVAGENT_*` (jvagent-owned) and `JVSPATIAL_*` (framework keys; allowlisted - see `docs/environment-keys-reference.md`).
- Layered config resolution (`jvagent/cli/server_config.py::create_server_from_config`): env vars > `app.yaml` > hardcoded defaults.
- Action-level config: Pydantic `attribute(default=...)` defaults on the Action subclass, overridable via `agent.yaml` `context:` block, validated at runtime.

**Key configs required for production:**
- `JVSPATIAL_JWT_SECRET_KEY` - JWT signing secret (raises `ValueError` at startup if auth enabled and unset).
- `JVAGENT_ADMIN_PASSWORD` - Bootstraps initial admin user.
- Provider secrets (selected): `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OLLAMA_API_KEY`, `TYPESENSE_API_KEY`, `WHATSAPP_API_KEY`, `FACEBOOK_APP_SECRET`, `SENDGRID_API_KEY`, `ELEVENLABS_API_KEY`, `DEEPGRAM_API_KEY`, `SERPER_API_KEY`, `GOOGLE_CLIENT_SECRETS_JSON`, `MICROSOFT_CLIENT_ID`, `JVAGENT_JVFORGE_API_KEY` / `JVFORGE_API_KEY`.
- `JVAGENT_PUBLIC_BASE_URL` - Public origin for webhooks, OAuth callbacks, and absolute media URLs.
- `JVAGENT_DISABLE_RUNTIME_PIP_INSTALL=true` - Required in production to skip dynamic action `pip install` at load time.

**Build:**
- `pyproject.toml` - Primary build/tool configuration (PEP 517).
- `setup.py` - Mirrors `pyproject.toml` install_requires (legacy; pip uses `pyproject.toml`).
- `MANIFEST.in` - Packaging includes (`README.md`, `LICENSE`, `pyproject.toml`, `setup.py`, `.env.example`, scaffold YAMLs, skill markdown).
- `.flake8` - Lint config (line-length 88, extensive ignore list).
- `.pre-commit-config.yaml` - Pre-commit hooks (yaml/json check, black 24.8.0, isort 6.0.0, flake8 6.1.0, mypy v1.10.1, detect-secrets v1.5.0, manual pytest stage).
- `Dockerfile.base` - Base AWS Lambda image (`public.ecr.aws/lambda/python:3.12`) with Lambda Web Adapter.

## Platform Requirements

**Development:**
- macOS / Linux / Windows (any platform with CPython 3.8+).
- pip with `pip install -e ".[dev]"` for editable + dev dependencies.
- Optional: Ollama daemon for local LLM testing (`http://localhost:11434`).
- Optional: MongoDB (`mongodb://localhost:27017`) for non-JSON DB development.
- Optional: Typesense, Redis for vector search / distributed lock testing.

**Production:**
- Primary deployment target: AWS Lambda (containerized, Python 3.12 base image, Lambda Web Adapter at port 8080). See `Dockerfile.base`.
- Alternative: Long-running ASGI host via `jvagent` CLI (`uvicorn` under jvspatial's `Server`).
- Auto-detected serverless mode (`SERVERLESS_MODE`, `AWS_LAMBDA_FUNCTION_NAME`) toggles single-worker mode, deferred saves, EventBridge scheduler.
- Database options (set `JVSPATIAL_DB_TYPE`): `json` (file-backed default, `./jvagent_db`), `sqlite`, `mongodb`, `dynamodb` (serverless).
- Optional EventBridge scheduler (`JVSPATIAL_EVENTBRIDGE_SCHEDULER_ENABLED`) requires `JVSPATIAL_EVENTBRIDGE_ROLE_ARN`, `JVSPATIAL_EVENTBRIDGE_LAMBDA_ARN` (or auto-built from `AWS_LAMBDA_FUNCTION_NAME` + `AWS_REGION` + `AWS_ACCOUNT_ID`).
- File storage backends (`JVSPATIAL_FILE_STORAGE_PROVIDER`): `local` (default, `./.files`) or `s3` (`JVSPATIAL_S3_BUCKET_NAME`, `JVSPATIAL_S3_REGION`, `JVSPATIAL_S3_ACCESS_KEY`, `JVSPATIAL_S3_SECRET_KEY`).
- Cache backends (`JVSPATIAL_CACHE_BACKEND`): `memory` (default) or `redis` (`JVSPATIAL_REDIS_URL`).

---

*Stack analysis: 2026-05-06*
