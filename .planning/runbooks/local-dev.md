# Runbook — Local Development

> First-time setup → running agent → tailing logs. Target audience: AI agent or human contributor doing local-dev for the first time.

---

## 1. Prerequisites

- Python 3.10+ (3.8 minimum)
- `pip` and ideally `venv` / `virtualenv`
- Sibling `jvspatial` repo at `../jvspatial/` (or install via pip)
- For MongoDB: a running Mongo instance OR use the default JSON backend

---

## 2. Install

```bash
git clone <your-fork-of-jvagent>
cd jvagent
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install                  # one-time hook setup
```

---

## 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
# Required
JVAGENT_ADMIN_PASSWORD=change-me
JVSPATIAL_JWT_SECRET_KEY=$(openssl rand -hex 32)

# Default JSON backend (fine for dev)
JVSPATIAL_DB_TYPE=json
JVSPATIAL_JSONDB_PATH=./jvdb/dev

# Optional: model providers
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...

# Optional: log verbosity
JVSPATIAL_LOG_LEVEL=INFO
```

For Mongo:
```env
JVSPATIAL_DB_TYPE=mongodb
JVSPATIAL_MONGODB_URI=mongodb://localhost:27017
JVSPATIAL_MONGODB_DB_NAME=jvagent_dev
```

---

## 4. Run the bundled example

```bash
jvagent examples/jvagent_app --debug
```

Expected:

- Server starts on `http://127.0.0.1:8000`
- Swagger UI at `http://127.0.0.1:8000/docs`
- Bootstrap log lines for `App`, `Agents`, `Memory`, each `Action`

---

## 5. Authenticate

```bash
curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@jvagent.example","password":"change-me"}' \
  | jq -r '.access_token' > /tmp/jvtoken
export JV=$(cat /tmp/jvtoken)
```

---

## 6. Send an interact request

```bash
# Get an agent id
curl -s -H "Authorization: Bearer $JV" \
  http://localhost:8000/api/agents | jq

# Replace AGENT_ID below
AGENT_ID=...

curl -s -X POST -H "Authorization: Bearer $JV" \
  -H "Content-Type: application/json" \
  http://localhost:8000/agents/$AGENT_ID/interact \
  -d '{
    "utterance": "Hello",
    "user_id": "test_user_1",
    "session_id": "sess_abc",
    "channel": "web"
  }' | jq
```

---

## 7. Tail logs

```bash
# Live process logs
jvagent examples/jvagent_app --debug 2>&1 | tee /tmp/jvagent.log

# Query the logs DB for an agent
curl -s -H "Authorization: Bearer $JV" \
  "http://localhost:8000/logs/agents/$AGENT_ID?limit=50" | jq
```

---

## 8. Edit-test-restart loop

```bash
# Most code changes need a restart (the server doesn't hot-reload by default)
# Stop with Ctrl+C, then:
jvagent examples/jvagent_app --debug

# If you change agent.yaml or app.yaml:
jvagent examples/jvagent_app --update --debug   # merge mode
# OR to discard manual graph state and accept YAML truth:
jvagent examples/jvagent_app --update --source --debug
```

---

## 9. Test slice

```bash
pytest tests/                          # all (slow)
pytest tests/action/executive/ -v      # subsystem
pytest -k pruning                      # keyword
pytest --lf                            # last failed only
```

Before pushing:

```bash
pre-commit run --all-files
pytest tests/
```

---

## 10. Reset / start over

```bash
# Wipe local DB (DEV ONLY — gated by JVSPATIAL_ENVIRONMENT)
JVSPATIAL_ENVIRONMENT=development \
  jvagent examples/jvagent_app --purge

# OR delete JSON backend manually
rm -rf ./jvdb/dev
```

---

## 11. Scaffold a brand-new app

```bash
jvagent app create --yes \
  --dir ./my_app \
  --app-id my_app \
  --title "My App" \
  --description "demo" \
  --author "You" \
  --agent jvagent/main_bot@minimal \
  --profile minimal

cd my_app
cp .env.example .env
# edit .env (set JVAGENT_ADMIN_PASSWORD + ANTHROPIC_API_KEY)
jvagent .
```

---

## 12. Common dev-time errors

| Symptom | Likely cause | Fix |
|---|---|---|
| `KeyError: 'JVAGENT_ADMIN_PASSWORD'` | `.env` not loaded or var missing | Confirm `.env` is at app root; `cat .env \| grep JVAGENT_ADMIN_PASSWORD` |
| `RuntimeError: attached to different loop` | jvspatial entity cached from a prior event loop | Restart the process; this often resolves on warm path |
| `Unknown argument: --foo` | Flag not recognized by `cli/main.py` | `jvagent --help` or read [`jvagent/cli/CLAUDE.md`](../../jvagent/cli/CLAUDE.md) |
| `--source and --merge require --update` | Misused flags | Add `--update` or drop the modifier |
| `--purge` exits with "only allowed in development mode" | `JVSPATIAL_ENVIRONMENT` not `development` | `export JVSPATIAL_ENVIRONMENT=development` |
| `action package not found: namespace/foo` | dir layout mismatch with `info.yaml` name | Match `info.yaml:package.name` to dir path |
| Endpoints missing after action update | `endpoints.py` not imported in `__init__.py` | Add `from . import endpoints` |
| Mongo connection refused | jvspatial config still points to JSON, or Mongo not running | Check `JVSPATIAL_DB_TYPE` + `JVSPATIAL_MONGODB_URI` |

---

## 13. Reading list (after this runbook)

- [`/CLAUDE.md`](../../CLAUDE.md) — agent guide
- [`.planning/architecture.md`](../architecture.md) — diagrams
- [`/docs/ORCHESTRATOR.md`](../../docs/ORCHESTRATOR.md) — Executive pattern deep dive
- [`/docs/scaffolding.md`](../../docs/scaffolding.md) — `jvagent app create` reference
