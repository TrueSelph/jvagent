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
