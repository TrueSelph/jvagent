# App scaffolding CLI

jvagent ships commands to generate a **git-ready application directory** (`app.yaml`, `agents/`, `profiles/`, `.env.example`, docs, and optional deploy stubs) and to **add agents** to an existing app. Scaffolding uses YAML **action profiles** (built-in and app-local) to populate each agent’s `agent.yaml` `actions:` list.

**Runtime note:** The server reads **`app.yaml`** and **`agents/<namespace>/<agent_id>/agent.yaml`**. The `profiles/` tree is for **authoring** when you run the CLI; it is not loaded automatically at runtime unless you add that behavior in your own tooling.

## Commands overview

| Command | Purpose |
|---------|---------|
| `jvagent app create` | Create a new app directory from scratch |
| `jvagent app profile new` | Add `profiles/<name>.yaml` under the **current app root** |
| `jvagent agent create` | Add `agents/<ns>/<id>/` and register the agent in `app.yaml` |
| `jvagent skill add` | Create app-local `SKILL.md` bundle skeleton for an agent |
| `jvagent skill list` | List reusable built-in and/or app-local skill bundles |
| `jvagent skill show` | Inspect one skill bundle's metadata and SOP content |

Run `jvagent` with no args (or invalid args) for general usage, or `jvagent app` with no subcommand for app-specific help text.

**App root:** Most commands assume you are in the app directory or pass it as the first positional path, e.g. `jvagent /path/to/my_app agent create ...`. The `app` subcommands use the same resolved app root as the rest of the CLI (`cwd` by default).

---

## `jvagent app create`

Creates the output directory and writes:

- `app.yaml`, `.env.example`, `.gitignore`, `LICENSE`, root `README.md`
- `docs/architecture.md`
- `profiles/README.md`, `profiles/examples/custom.yaml`
- Optionally `profiles/builtin/*.yaml` (copies of packaged built-in profiles)
- `agents/<namespace>/<agent_id>/agent.yaml` and agent `README.md` for each `--agent`
- If `--deployment aws-lambda`: appends serverless-oriented hints to `.env.example`, and adds `deploy.example.yaml` and a starter `Dockerfile`
- If `--deployment azure-functions`: adds a short note in `.env.example` about serverless detection vs deferred tasks on non-AWS platforms
- Unless `--no-git`: runs `git init` in the output directory

### Flags

| Flag | Description |
|------|-------------|
| `--dir PATH` | Output directory (default: current working directory) |
| `--app-id ID` | Value for `app:` in `app.yaml` and typical `JVAGENT_APP_ID` |
| `--title`, `--description`, `--author` | Metadata (required with `--yes`) |
| `--email` | Admin email in generated `.env.example` (`JVAGENT_ADMIN_EMAIL`) |
| `--version`, `--license`, `--homepage` | App metadata defaults |
| `--jvagent-version SPEC` | `jvagent:` semver in YAML (default `~<installed version>`) |
| `--deployment` | `local` (default), `aws-lambda`, or `azure-functions` |
| `--profile NAME` | Default action profile when an agent spec has no `@profile` (default `minimal`) |
| `--agent SPEC` | Repeatable. See **Agent spec** below |
| `--action ID` | Repeatable. Extra stock action id (e.g. `jvagent/foo`); merged into every scaffolded agent in that run |
| `--no-copy-builtin-profiles` | Skip copying built-in profile YAML into `profiles/builtin/` |
| `--no-git` | Do not run `git init` |
| `--force` | Allow writing into a non-empty `--dir` |
| `--yes` | Non-interactive; requires `--app-id`, `--title`, `--description`, `--author`, and at least one `--agent` |

Without `--yes`, missing values are prompted when stdin is a TTY.

### Examples

Non-interactive:

```bash
jvagent app create --yes \
  --dir ./my_app \
  --app-id my_app \
  --title "My App" \
  --description "Production deployment" \
  --author "My Org" \
  --agent jvagent/bot@minimal \
  --profile minimal
```

AWS-oriented defaults:

```bash
jvagent app create --yes --dir ./my_app --app-id my_app --title "My App" \
  --description "Lambda deployment" --author "My Org" \
  --deployment aws-lambda \
  --agent jvagent/bot@minimal
```

Interactive (omit `--yes`):

```bash
jvagent app create --dir ./my_app
```

---

## Agent spec (`SPEC`)

Used with `jvagent app create --agent ...` and as the positional argument to `jvagent agent create`.

| Form | Meaning |
|------|---------|
| `namespace/agent_id` | Use the default profile from `--profile` (`app create`) or `--profile` (`agent create`) |
| `namespace/agent_id@profile_key` | Use profile `profile_key` for that agent only |

`profile_key` resolves, in order:

1. `profiles/<key>.yaml` (or `profiles/<key>` as a file)
2. `profiles/builtin/<key>.yaml`
3. Built-in packaged profiles: `orchestrator` (default for new agents), `minimal`, `conversational`, `whatsapp_voice`, `research`

Built-in profiles can **extend** other profiles (`extends:`) and pull in more YAML via `include:` (see **Profile YAML** below).

---

## `profiles/` and profile YAML

Generated apps include `profiles/README.md` with the same resolution rules. Profile files are ordinary YAML with:

| Key | Description |
|-----|-------------|
| `extends` | Optional string: name of another profile to merge first (parent before child; duplicate `action` ids are overridden by the child) |
| `include` | Optional list of paths under `profiles/` to merge in order |
| `actions` | List of maps with `action` and optional `context` / `config` (same shape as in `agent.yaml`) |

Use **`jvagent app profile new`** to create a starter file:

```bash
cd /path/to/my_app
jvagent app profile new my_stack --extends minimal
```

Then create an agent with `@my_stack` (after editing `profiles/my_stack.yaml`).

---

## `jvagent app profile new`

| Usage | Description |
|-------|-------------|
| `jvagent app profile new <name> [--extends PROFILE]` | Writes `profiles/<name>.yaml` under the **current app root** (the same directory the CLI treats as app root for other commands) |

Must be run from inside an existing app (or with app root as the first path argument to `jvagent` so `default_cwd` is the app). The file is rejected if it already exists.

---

## `jvagent agent create`

Adds a new agent under `agents/<namespace>/<agent_id>/`, writes `agent.yaml` and `README.md`, and appends `namespace/agent_id` to `app.yaml`’s `agents:` list if it is not already present.

### Flags

| Flag | Description |
|------|-------------|
| `SPEC` | Positional (optional if TTY prompts): `namespace/agent` or `namespace/agent@profile` |
| `--profile NAME` | Used when `SPEC` has no `@...` (default `minimal`) |
| `--action ID` | Repeatable; extra actions merged into the resolved profile |
| `--force` | Overwrite existing `agent.yaml` / refresh files; see CLI messages for duplicate `app.yaml` registration |
| `--version`, `--author`, `--jvagent-version` | Override defaults written into `agent.yaml` |

### Examples

```bash
cd /path/to/my_app
jvagent agent create acme/support@conversational
```

```bash
jvagent /path/to/my_app agent create acme/bot --profile minimal
```

After adding an agent, reload the graph:

```bash
jvagent bootstrap --update
# or
jvagent --update
```

---

## Skill catalogs and custom skills

Orchestrator agents (skills come in two specs — JV + Claude; see [`jvagent/skills/README.md`](../jvagent/skills/README.md)) discover skills from several tiers. **Placement standard (ADR-0023):** put every agent skill in `agents/<ns>/<id>/skills/<name>/` unless it is (a) a base action SOP at `<action_dir>/SKILL.md` (not a skill), or (b) bundled with a custom/core action under `<action_dir>/skills/<name>/`.

Discovery merge order:

1. Built-in library skills (`jvagent/skills/*`)
2. Core action skills (`<action_dir>/skills/*` for actions on the agent)
3. App-local agent skills (`agents/<ns>/<id>/skills/*`) — **default drop zone**
4. App action overlays (`agents/.../actions/<ns>/<action>/skills/*`) — action-bundled only

App-local overrides built-in / core by name. `jvagent skill add` writes to tier 3.

Runtime exposure is controlled per agent in `agents/<ns>/<id>/agent.yaml` on
the `jvagent/orchestrator` action via:

- `skills`: `-all` or a list of names/globs (default unset = expose none)
- `denied_skills`: optional subtractive names/globs
- `skills_source`: `library`, `app`, or `both`

### Skill commands

```bash
# create a custom app-local skill
jvagent /path/to/app skill add acme/bot bug_triage --description "App-specific triage SOP"

# list built-in reusable skills
jvagent /path/to/app skill list

# list merged view for an agent (builtin + app-local overrides)
jvagent /path/to/app skill list --agent acme/bot --builtin

# inspect one skill
jvagent /path/to/app skill show research --agent acme/bot --builtin
```

---

## Deployment presets

| Preset | Effect |
|--------|--------|
| `local` | JSON DB defaults in `app.yaml`; standard `.env.example` |
| `aws-lambda` | DynamoDB-oriented `database` block in `app.yaml`; extra serverless env comments in `.env.example`; `deploy.example.yaml` and `Dockerfile` stubs |
| `azure-functions` | Notes in `.env.example`; jvspatial may detect serverless mode, but deferred task scheduling on non-AWS providers may require a custom setup—see jvspatial docs |

For Lambda, LWA, and deferred HTTP behavior, see [jvspatial serverless-mode](https://github.com/trueselph/jvspatial/blob/main/docs/md/serverless-mode.md) (or your checkout of `docs/md/serverless-mode.md`).

---

## Suggested workflow

1. `jvagent app create ...` (or copy `examples/jvagent_app` if you prefer a hand-curated demo)
2. `cp .env.example .env` and set `JVAGENT_ADMIN_PASSWORD`, `JVSPATIAL_JWT_SECRET_KEY`, and any keys required by your profile (e.g. `OPENAI_API_KEY`)
3. `jvagent bootstrap`
4. `jvagent run`
5. To add agents later: `jvagent agent create ...` then `jvagent bootstrap --update`

Configuration and env mapping: [configuration.md](configuration.md).
