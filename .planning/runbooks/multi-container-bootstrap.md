# Runbook — Multi-container bootstrap (Lambda / replicas)

> **When to use:** AWS Lambda with provisioned concurrency, ECS/Fargate with
> `replicas > 1`, or any deployment where **multiple processes cold-start at
> once** and each runs `pre_startup_bootstrap` → `bootstrap_application_graph`.
>
> **Symptom without this:** duplicate graph nodes — especially
> `AccessControlAction` — multiple `(namespace, label)` rows for the same agent,
> `"Multiple AccessControlAction nodes"` in logs.
>
> **Related:** [ADR-0033](../adr/0033-identity-and-locking-substrate.md),
> [`jvagent/core/distributed_lease.py`](../../jvagent/core/distributed_lease.py),
> PR enforcing singleton registration via raw-record upsert.

---

## Root cause (one paragraph)

Graph bootstrap is **check-then-create**. The default JSON adapter does not
enforce unique indexes. Without a **cluster-wide lease**, each Lambda container
(or uvicorn worker) races through `install_agent` → `register_action` in
parallel. jvagent now **converges** duplicates via raw-record upsert and boot
dedupe, but prevention requires serializing bootstrap across containers.

---

## Required: distributed lease backend

Bootstrap uses the **same Redis/DynamoDB backend as the conversation turn-lock**
([`distributed_lease`](../../jvagent/core/distributed_lease.py)). Configure **one**
of:

### Option A — Redis (recommended)

```env
# Conversation turn-lock AND bootstrap lease (shared backend)
JVAGENT_CONVERSATION_LOCK_REDIS_URL=redis://your-elasticache-host:6379/0

# Optional: lease TTL seconds (default 45; bootstrap renews while held)
JVAGENT_CONVERSATION_LOCK_TTL_SECONDS=120
```

Requirements:

- All Lambda functions / replicas reach the same Redis endpoint (VPC + security group).
- `redis` Python package installed in the deployment image (already in jvagent deps when using Redis elsewhere).

### Option B — DynamoDB

```env
JVAGENT_CONVERSATION_LOCK_DYNAMODB_TABLE=jvagent-locks
JVAGENT_CONVERSATION_LOCK_DYNAMODB_TTL_SECONDS=120
```

Table schema: partition key `lock_key` (String). Used for both
`jvagent:conversation:*` and `jvagent:lease:bootstrap:*` keys.

---

## Verify lease is active

1. **Cold-start two containers simultaneously** (scale Lambda to 2+ concurrent executions or hit two replicas).
2. **CloudWatch / bootstrap logs:** one container should log full bootstrap; others should block briefly on the lease then complete idempotently (no long duplicate registration spam).
3. **Graph UI / DB query:** exactly one `AccessControlAction` (and one node per singleton archetype) per agent.

Without Redis/DynamoDB, logs may show:

- In-process lease only — **no cross-container protection**
- Duplicate action nodes after traffic bursts

---

## Heal existing duplicates (one-time)

After deploying singleton upsert + lease:

```bash
# From app root — reconciles stale/duplicate action nodes
jvagent /path/to/app --update
```

Or restart containers (boot dedupe runs every install):

- `_dedupe_actions_by_identity`
- `_dedupe_singleton_actions_by_archetype`

For large graphs, the background **graph repair** job also removes duplicate
singleton actions (`duplicate_singleton_actions_removed` metric).

---

## Lambda checklist

| Item | Action |
|------|--------|
| Redis or DynamoDB lock | Set env vars above on the Lambda function |
| Shared graph DB | All containers must use the **same** `JVSPATIAL_*` backend (S3+Dynamo, Mongo, etc.) — not local `/tmp` JSON per container |
| Provisioned concurrency burst | Expect many cold starts on deploy; lease prevents duplicate graph writes |
| Post-deploy | Run `--update` once or verify dedupe logs: `Removed N duplicate action node(s)` |
| Monitor | Alert on `Multiple AccessControlAction nodes` — should stay at zero |

---

## Local / single-process dev

No Redis required. Bootstrap uses an **in-process lock** — sufficient for one
uvicorn worker or `jvagent` CLI. Multi-worker local testing:

```bash
# Terminal 1 — Redis
docker run -p 6379:6379 redis:7

# Terminal 2
export JVAGENT_CONVERSATION_LOCK_REDIS_URL=redis://127.0.0.1:6379/0
uvicorn ... --workers 2
```

To qualify the Redis conversation lease across independent Python processes,
install the Redis client and run the opt-in test against a disposable Redis
instance. It holds one lease beyond its five-second TTL, verifies a second
process cannot enter early, and checks that a cooperatively suspended async
owner is cancelled after it resumes past lease expiry:

```bash
uv pip install --python .venv/bin/python 'redis>=5.0.0'
docker run --rm -d --name jvagent-lock-test \
  -p 127.0.0.1:16379:6379 redis:7-alpine
JVAGENT_TEST_REDIS_URL=redis://127.0.0.1:16379/0 \
  .venv/bin/pytest tests/memory/test_redis_lock_multiprocess.py -q
docker stop jvagent-lock-test
```

This qualifies Redis lock contention and lease renewal for active and
cooperatively suspended async workers only. Cancellation cannot fence a
process paused inside synchronous or cancellation-suppressing work, so this
does not qualify arbitrary process suspension, multi-worker graph persistence,
DynamoDB, or a deployed application topology.

To qualify signed host-context nonce replay against one shared graph database
and Redis lock, start disposable PostgreSQL and Redis services, then run the
opt-in cross-process test. Both workers load the same Conversation before a
barrier, contend through the production mutation-lock wrapper, and attempt to
consume the same signed nonce. The test requires exactly one promotion and
reads the single persisted nonce entry back from PostgreSQL:

```bash
uv pip install --python .venv/bin/python 'asyncpg>=0.29' 'redis>=5.0.0'
docker run --rm -d --name jvagent-host-context-pg \
  -e POSTGRES_USER=jvagent -e POSTGRES_PASSWORD=jvagent \
  -e POSTGRES_DB=jvagent_lock_test \
  -p 127.0.0.1:16381:5432 postgres:16-alpine
docker run --rm -d --name jvagent-host-context-redis \
  -p 127.0.0.1:16382:6379 redis:7-alpine
docker exec jvagent-host-context-pg \
  pg_isready -U jvagent -d jvagent_lock_test
JVAGENT_TEST_POSTGRES_DSN=postgresql://jvagent:jvagent@127.0.0.1:16381/jvagent_lock_test \
JVAGENT_TEST_REDIS_URL=redis://127.0.0.1:16382/0 \
  .venv/bin/pytest tests/action/orchestrator/test_host_context_distributed.py -q
docker stop jvagent-host-context-redis jvagent-host-context-pg
```

This qualifies concurrent nonce consumption for PostgreSQL plus Redis in this
local topology. It does not qualify other graph backends, database failover,
lease fencing for synchronous effects, or a deployed multi-worker rollout.

---

## Environment reference

Full key list: [`docs/environment-keys-reference.md`](../../docs/environment-keys-reference.md)
(section **Distributed locking**).

Keys used by bootstrap lease:

| Key | Purpose |
|-----|---------|
| `JVAGENT_CONVERSATION_LOCK_REDIS_URL` | Redis lease backend (bootstrap + turn-lock) |
| `JVAGENT_CONVERSATION_LOCK_TTL_SECONDS` | Lease TTL / renewal baseline |
| `JVAGENT_CONVERSATION_LOCK_DYNAMODB_TABLE` | DynamoDB alternative |
| `JVAGENT_CONVERSATION_LOCK_DYNAMODB_TTL_SECONDS` | DynamoDB lease TTL |

Bootstrap lease key format: `jvagent:lease:bootstrap:{app_id}` (see
[`bootstrap.py`](../../jvagent/cli/bootstrap.py)).
