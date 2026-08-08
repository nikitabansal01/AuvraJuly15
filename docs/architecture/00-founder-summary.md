# AUVRA v2 in fifteen minutes

## The outcome

AUVRA v2 is a controlled rebuild around one source of truth per business
concept. The target is a FastAPI **modular monolith** plus one separately
deployed durable worker, PostgreSQL 17 as the business authority, Redis only as
queue/cache/lock infrastructure, and an iOS/Android React Native client using a
single generated OpenAPI client.

This package is an **initial design and handover baseline**, not evidence that v2
is complete. Written owner approval is pending. Production release is blocked
until architecture, legal, clinical, privacy, migration and readiness gates are
recorded as passed.

## Why the rebuild is controlled

The frozen backend commit contains 69,102 application Python lines, 35 ORM
tables in `app/core/database.py`, 132 detected HTTP operations, four
`asyncio.create_task` call sites and 146 direct `.commit()` call sites. The
largest generator is 9,529 lines. Static route evidence identifies 52 operations
without a recognized in-function authentication guard, including 19 mutations;
this is a conservative lexical signal requiring manual review, not a complete
authorization proof.

The frozen mobile commit contains 56,708 tracked TypeScript/JavaScript lines, a
3,599-line chat screen, a 3,103-line home screen, 61 raw `fetch` calls across 14
files, 12 files implementing API URL logic, no detected test files, seven source
references to password persistence through AsyncStorage, and temporary/old
source copies. These measurements are reproducible in the evidence snapshot.

The existing deployment and schemas are therefore restricted reference evidence,
not an approved rollback destination once v2 writes begin.

## The target shape

```text
iOS / Android
      |
      | OpenAPI v2 + verified Firebase token
      v
FastAPI API process -----> PostgreSQL 17 (app + ops + runtime)
      |                              ^
      | enqueue identifier           | durable job state / atomic publication
      v                              |
Redis queue ----------------> Durable worker ----> AI/evidence/image adapters
                                      |
                                      +-----------> permanent object storage/CDN
```

The API and worker use one immutable image. The API owns synchronous adaptation;
the worker owns durable provider orchestration. Only PostgreSQL owns business
state. Provider calls never occur inside an open database transaction.

## Eight modules, one owner each

1. Identity and access.
2. Onboarding, consent and profiles.
3. Plans, actions and Daily Review.
4. Progress, streaks, rewards and refreshes.
5. Conversations and check-ins.
6. Symptom observations.
7. Evidence, AI safety and evaluation.
8. Media, jobs and operational infrastructure.

Each module follows `API adapter -> application command/query -> domain policy ->
repository/provider port`. Cross-module callers use application interfaces, not
another module's database implementation.

## Non-negotiable data rules

- Private identity comes only from a verified Firebase token mapped to internal
  `app.users.id`; a request-body `user_id` never authorizes access.
- Guest onboarding uses a hashed proof credential distinct from its session ID.
- All timestamps are UTC `TIMESTAMPTZ`; daily decisions also store the immutable
  IANA timezone and local business date used.
- A plan revision is `READY` only when one transaction publishes four valid
  actions and sixteen reachable permanent images: hero plus three variants for
  each action.
- Completion, skip and feedback facts are immutable and replay safe.
- Streak days and reward movements are ledgers; current values are calculated.
- LangGraph checkpoints are expiring runtime state, never conversation or
  health-data truth.
- Deletion covers Firebase, PostgreSQL, storage, conversations, checkpoints,
  caches and derived summaries.

## The critical journey

`validated assessment/profile -> context snapshot -> evidence retrieval ->
structured model output -> citation and safety validation -> image generation ->
permanent storage -> atomic plan publication -> asynchronous evaluation`

PostgreSQL records every durable job state. Redis only transports a job ID. A
worker claims a lease, heartbeats, retries within an attempt limit and dead
letters exhausted work. Blank/temporary images or unsafe/unverifiable content
cannot be published.

## Safe mobile mental model

Firebase owns login state. The generated OpenAPI client is the only place that
may resolve an API URL, retrieve a token or call `fetch`. TanStack Query owns
server state; small feature reducers/state machines own ephemeral workflow state.
SecureStore contains only necessary credentials, never passwords or durable
health drafts. Logout/account switching clears query state and all UID-scoped
local data. A plan remains hidden until all hero images are prefetched through
one `PlanImage` component built on `expo-image`.

## What is not decided or verified

- Owner approval of the retained-feature matrix, table mapping, metrics, API and
  ADRs is pending.
- Jurisdiction, consent wording, retention duration, health-product
  classification and clinical escalation text are pending legal/clinical owner
  decisions.
- No provider/model/image route is production-approved by this document;
  selection requires rotated credentials and staged safety/quality/cost/latency
  benchmarks.
- No production SLO, backup objective, security posture or performance target is
  claimed met. Listed values are initial acceptance targets.
- The backup contains exactly 1,022 application COPY rows plus one
  `alembic_version` row, but an isolated PostgreSQL 17 restore has **not** been
  rehearsed. PostgreSQL 17.7 now proves the clean v2 bootstrap and selected
  rollback/invariant paths; it does not prove restoration or migration of the
  legacy dump. This is a release blocker. The supplied storage ZIP is a valid
  empty 22-byte archive, so no legacy media can be restored; retained images must
  be regenerated and permanently persisted.

## How to make a change without creating another source of truth

Start at the feature record. Identify its owning module, API operation, canonical
table and governed metric. Amend the relevant ADR when a decision changes. Update
OpenAPI, ORM, Alembic, catalogs, ERD and tests in the same change. Validate blank
bootstrap and rollback. Add telemetry and a runbook before adding an alert. A
second active table, calculation, API DTO or cache claiming the same concept is a
release-blocking design defect.

When the system is under pressure, start with `runbooks/UNDER_PRESSURE.md` and
make read-only checks before intervention.
