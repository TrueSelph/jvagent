#!/usr/bin/env bash
#
# End-to-end check that a jvagent app actually runs on PostgreSQL.
#
# The unit tests never touch a real Postgres, and the two failure modes that
# bit us are both integration-shaped: the Server rejecting `db_type=postgres`
# outright, and the asyncpg pool not surviving the bootstrap-loop -> uvicorn-loop
# handoff. Neither shows up until a real process talks to a real database, and
# the second only shows up across a *restart*. Hence this script.
#
# What it proves, in order: the graph bootstraps onto Postgres, the server
# serves against it, auth resolves a Postgres-stored user, an agent turn writes
# the memory subgraph, and — the important one — conversation state read back
# after a full server restart, which is what exercises pool loop-affinity.
#
#   scripts/smoke_postgres.sh                       # spins up its own container
#   scripts/smoke_postgres.sh examples/jvagent_app  # explicit app root
#   JVSPATIAL_POSTGRES_DSN=postgresql://... scripts/smoke_postgres.sh --no-docker
#
# Requires: docker (unless --no-docker), curl, and the asyncpg extra
# (`pip install asyncpg`). Agent-turn checks need a model key in the app's
# .env; without one they are skipped and the persistence checks still run.
#
# The jvagent entrypoint is derived from $PYTHON (default python3) so the run
# exercises the same environment as PYBIN, not whatever `jvagent` PATH happens
# to resolve to. Container name and ports are unique per run, and the server is
# tracked by PID, so concurrent runs do not kill each other.
#
# Exits non-zero with the number of failed checks. The temp workdir (bootstrap
# and server logs) is kept when any check fails. See docs/postgres.md.

set -u

APP_ROOT="examples/jvagent_app"
USE_DOCKER=1
for arg in "$@"; do
    case "$arg" in
        --no-docker) USE_DOCKER=0 ;;
        -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) APP_ROOT="$arg" ;;
    esac
done

WORKDIR="$(mktemp -d)"
PYBIN="${PYTHON:-python3}"

# Run the *same* install PYBIN belongs to. A bare `jvagent` off PATH can be a
# different environment entirely (different jvspatial, missing asyncpg), which
# turns an environment problem into a wall of bogus check failures.
PYBIN_PATH="$(command -v "$PYBIN" 2>/dev/null || printf '%s' "$PYBIN")"
if [ -x "$(dirname "$PYBIN_PATH")/jvagent" ]; then
    JVAGENT_CMD=("$(dirname "$PYBIN_PATH")/jvagent")
else
    JVAGENT_CMD=("$PYBIN" -m jvagent)
fi

# Everything externally visible is per-run unique so two overlapping runs do not
# fight over a container name, a port, or each other's server process.
RUN_ID=$$
port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1; }
pick_port() { local p=$1; while port_in_use "$p"; do p=$((p + 1)); done; printf '%s' "$p"; }

CONTAINER="${JVAGENT_SMOKE_CONTAINER:-jvagent-smoke-pg-$RUN_ID}"
PGPORT="${JVAGENT_SMOKE_PGPORT:-$(pick_port $((55432 + RUN_ID % 1000)))}"
PORT="${JVAGENT_SMOKE_PORT:-$(pick_port $((8123 + RUN_ID % 1000)))}"
BASE="http://127.0.0.1:$PORT"
SERVER_PID=""

pass=0
fail=0
skip=0
step() { printf '\n=== %s ===\n' "$1"; }
ok()   { printf 'PASS  %s\n' "$1"; pass=$((pass + 1)); }
bad()  { printf 'FAIL  %s\n' "$1"; fail=$((fail + 1)); }
note() { printf 'SKIP  %s\n' "$1"; skip=$((skip + 1)); }

start_server() {
    nohup "${JVAGENT_CMD[@]}" "$APP_ROOT" > "$WORKDIR/server_$1.log" 2>&1 &
    SERVER_PID=$!
    for _ in $(seq 1 30); do
        curl -s -m 2 "$BASE/health" >/dev/null 2>&1 && return 0
        kill -0 "$SERVER_PID" 2>/dev/null || return 1  # died before serving
        sleep 3
    done
    return 1
}
# Kill by PID, never `pkill -f jvagent <app root>` — that pattern matches every
# concurrent run's server too. The trailing redirect swallows bash's
# "Terminated: 15" job report, which is expected here, not a failure.
stop_server() {
    [ -n "$SERVER_PID" ] || return 0
    pkill -P "$SERVER_PID"
    kill "$SERVER_PID"
    for _ in $(seq 1 15); do
        kill -0 "$SERVER_PID" 2>/dev/null || break
        sleep 1
    done
    kill -9 "$SERVER_PID"
    wait "$SERVER_PID"
    SERVER_PID=""
} 2>/dev/null

cleanup() {
    stop_server
    [ "$USE_DOCKER" = "1" ] && docker rm -f "$CONTAINER" >/dev/null 2>&1
    if [ "$fail" -gt 0 ]; then
        printf '\nlogs kept: %s\n' "$WORKDIR"
    else
        rm -rf "$WORKDIR"
    fi
}
trap cleanup EXIT

if [ "$USE_DOCKER" = "1" ]; then
    step "0. postgres container"
    docker rm -f "$CONTAINER" >/dev/null 2>&1
    if docker run -d --name "$CONTAINER" \
        -e POSTGRES_USER=jvagent -e POSTGRES_PASSWORD=jvagent -e POSTGRES_DB=jvagent_smoke \
        -p "$PGPORT:5432" postgres:16-alpine >/dev/null 2>&1; then
        for _ in $(seq 1 30); do
            docker exec "$CONTAINER" pg_isready -U jvagent -d jvagent_smoke >/dev/null 2>&1 && break
            sleep 1
        done
        ok "postgres:16-alpine listening on $PGPORT"
    else
        bad "could not start container $CONTAINER"
        exit 1
    fi
    export JVSPATIAL_POSTGRES_DSN="postgresql://jvagent:jvagent@localhost:$PGPORT/jvagent_smoke"
fi

: "${JVSPATIAL_POSTGRES_DSN:?set JVSPATIAL_POSTGRES_DSN or drop --no-docker}"

export JVSPATIAL_DB_TYPE=postgres
# jvspatial has no postgres branch for the log DB and silently falls back to a
# json file log; pin it so the fallback is a decision, not a surprise.
export JVSPATIAL_LOG_DB_TYPE=json
export JVSPATIAL_LOG_DB_PATH="$WORKDIR/logs"
export JVSPATIAL_JWT_SECRET_KEY="${JVSPATIAL_JWT_SECRET_KEY:-smoke-only-not-a-real-secret-0123456789}"
export JVAGENT_ADMIN_USERNAME="${JVAGENT_ADMIN_USERNAME:-admin}"
export JVAGENT_ADMIN_PASSWORD="${JVAGENT_ADMIN_PASSWORD:-smokepass123}"
export JVAGENT_ADMIN_EMAIL="${JVAGENT_ADMIN_EMAIL:-admin@jvagent.example}"
export JVAGENT_HOST=127.0.0.1
export JVAGENT_PORT="$PORT"
export JVSPATIAL_ENVIRONMENT=development

psql_q() { docker exec "$CONTAINER" psql -U jvagent -d jvagent_smoke -tA -c "$1" 2>/dev/null; }

login() {
    curl -s -m 15 -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' \
        -d "{\"email\":\"$JVAGENT_ADMIN_EMAIL\",\"password\":\"$JVAGENT_ADMIN_PASSWORD\"}" |
        "$PYBIN" -c 'import sys,json; print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null
}

say() {  # token agent utterance
    curl -s -m 120 -X POST "$BASE/api/agents/$2/interact" \
        -H "Authorization: Bearer $1" -H 'Content-Type: application/json' \
        -d "{\"utterance\":$("$PYBIN" -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$3"),\"session_id\":\"smoke\"}" |
        "$PYBIN" -c 'import sys,json; print(json.load(sys.stdin).get("response",""))' 2>/dev/null
}

step "1. bootstrap graph onto Postgres"
if "${JVAGENT_CMD[@]}" "$APP_ROOT" bootstrap > "$WORKDIR/bootstrap.log" 2>&1; then
    ok "jvagent bootstrap"
else
    bad "jvagent bootstrap — see $WORKDIR/bootstrap.log"
    grep -E "ValueError|Unsupported database type|ImportError" "$WORKDIR/bootstrap.log" | head -2
fi

if [ "$USE_DOCKER" = "1" ]; then
    tables=$(psql_q "select count(*) from information_schema.tables where table_schema='public' and table_name in ('node','edge','object');")
    [ "$tables" = "3" ] && ok "node/edge/object tables created" || bad "expected 3 tables, got '${tables:-none}'"
    nodes=$(psql_q "select count(*) from node;")
    [ "${nodes:-0}" -gt 20 ] && ok "graph persisted ($nodes nodes)" || bad "too few nodes: '${nodes:-0}'"
fi

step "2. serve"
start_server 1 && ok "server healthy" || bad "server did not become healthy"
curl -s -m 5 "$BASE/health" | grep -q '"database":"connected"' \
    && ok "health reports database connected" || bad "health did not report a connected database"
grep -q "Database: PostgresDB" "$WORKDIR/server_1.log" \
    && ok "lifecycle reports PostgresDB" || bad "lifecycle did not report PostgresDB"

step "3. auth against a Postgres-stored user"
TOK=$(login)
[ -n "$TOK" ] && ok "JWT login" || bad "login returned no token"

AGENT=$(curl -s -m 15 "$BASE/api/agents" -H "Authorization: Bearer $TOK" |
    "$PYBIN" -c 'import sys,json; a=json.load(sys.stdin).get("agents",[]); print(a[0]["id"] if a else "")' 2>/dev/null)
[ -n "$AGENT" ] && ok "agent listed ($AGENT)" || bad "no agents returned"

step "4. agent turn"
TURNS=1
r1=$(say "$TOK" "$AGENT" "Remember the number 8675309.")
if [ -n "$r1" ]; then
    printf 'agent: %s\n' "$r1"
    ok "turn produced a reply"
else
    TURNS=0
    note "agent turn — no reply (model key missing?); persistence checks continue"
fi

if [ "$USE_DOCKER" = "1" ] && [ "$TURNS" = "1" ]; then
    mem=$(psql_q "select count(*) from node where id like 'n.Interaction%';")
    [ "${mem:-0}" -ge 1 ] && ok "Interaction persisted ($mem)" || bad "no Interaction rows"
fi

step "5. restart (new process, new event loop)"
stop_server
start_server 2 && ok "server restarted" || bad "server failed to restart"

step "6. read state back after restart"
TOK=$(login)
[ -n "$TOK" ] && ok "login works post-restart" || bad "login failed post-restart"
if [ "$TURNS" = "1" ]; then
    r2=$(say "$TOK" "$AGENT" "What number did I ask you to remember?")
    printf 'agent: %s\n' "$r2"
    case "$r2" in
        *8675309*) ok "recalled pre-restart turn from Postgres" ;;
        *) bad "did not recall pre-restart state: $r2" ;;
    esac
else
    note "recall check — needs a model key"
fi

errs=$(cat "$WORKDIR"/server_*.log 2>/dev/null |
    grep -cE "ConnectionDoesNotExistError|another operation is in progress|Unsupported database type")
[ "${errs:-0}" = "0" ] && ok "no postgres/event-loop errors in server logs" \
    || bad "$errs postgres/event-loop error(s) in server logs"

step "summary"
[ "$USE_DOCKER" = "1" ] && docker exec "$CONTAINER" psql -U jvagent -d jvagent_smoke \
    -c "select (select count(*) from node) nodes, (select count(*) from edge) edges, (select count(*) from object) objects;" 2>/dev/null
printf '\n%d passed, %d failed, %d skipped\n' "$pass" "$fail" "$skip"
exit "$fail"
