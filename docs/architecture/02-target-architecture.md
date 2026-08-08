# TARGET architecture

## Governing pattern

AUVRA uses a modular monolith because the domain boundaries need to stabilize
before operationally independent services would be justified. One application
image produces two deployables: the synchronous FastAPI API and a durable worker.
This follows the readiness tradeoff described in Microsoft's
[microservices assessment guidance](https://learn.microsoft.com/en-us/azure/architecture/guide/technology-choices/microservices-assessment).

The system context, container, backend component, mobile component and deployment
views live under `diagrams/c4/`. Mermaid is the editable diagram source; the
handbook builder embeds vector renderings or a vector fallback and records the
renderer in the manifest.

## Implementation boundary observed on 2026-08-08

The current workspace contains `20260801_0002_auvra_v2_foundation.py` and
`20260808_0003_v2_engagement_governance.py`. Together they declare the current
catalogued application/operations foundation, including engagement, conversation,
research, AI governance and deletion-request tables. `0003` uses PostgreSQL DDL;
its `app.audit_events` location corrects the earlier intended `ops` placement.
The LangGraph runtime tables remain vendor-managed planned contract, not created
by these migrations. No isolated PostgreSQL 17 upgrade/rollback evidence, API
implementation evidence or contract freeze is recorded. Therefore the physical
schema is **TARGET PARTIAL**: migration code is present, but it is not a deployed
or verified foundation. The catalog uses **TARGET PLANNED** for the full intended
contract; the physical-schema appendix reports present versus planned separately.

## Backend dependency rule

Every module follows:

`API adapter -> application command/query -> domain policy -> repository/provider port`

Only infrastructure adapters implement SQLAlchemy repositories, provider SDKs,
Redis transport, storage or telemetry. The same SQLAlchemy 2.x session factory
and Unit of Work are used across modules. Only the Unit of Work opens, commits or
rolls back a transaction. A provider call occurs outside database transactions;
state is first committed as a durable job/outbox fact and completed through a
later short publication transaction.

## Module ownership

| Module | Owns | May consume through |
|---|---|---|
| Identity and access | Internal user mapping, authentication policy | Firebase identity port |
| Onboarding, consent and profiles | Guest workflow, assessment, consent, profile | Identity application query |
| Plans, actions and Daily Review | Plans, items, variants, events, reviews, refresh operations | Profile snapshot, evidence and media ports |
| Progress, streaks, rewards and refreshes | Streak and reward ledgers, progress queries | Immutable plan/action/review facts |
| Conversations and check-ins | Typed threads, messages, summaries, weekly check-ins | AI gateway, profile/plan read models |
| Symptom observations | Structured symptom facts | Conversation commands and profile identity |
| Evidence, AI safety and evaluation | Sources, citations, invocation metadata, evaluation policy | Provider gateway ports |
| Media, jobs and operational infrastructure | Media, durable jobs, outbox, idempotency and audits | Provider/storage/queue adapters |

No module imports another module's SQLAlchemy models/repositories. A cross-domain
change uses an application interface and an outbox event when temporal decoupling
is useful.

## Runtime responsibilities

PostgreSQL is authoritative for application and operations data. Redis transports
job identifiers, caches recomputable results and supports locks; eviction cannot
erase business truth. LangGraph's four checkpoint tables live in `runtime`, are
vendor-owned, have explicit retention and never substitute for messages,
summaries or observations.

The worker claims jobs with `FOR UPDATE SKIP LOCKED` or an equivalently tested
lease algorithm, increments attempt state, heartbeats, releases to `retry_wait`
with bounded backoff, and ends exhausted work in `dead_letter`. Redis delivery is
at least once; handlers are idempotent.

## Configuration and promotion

- Local/CI, staging and production use separate Supabase, Firebase, Redis,
  storage and provider credentials.
- Production typed configuration is fail closed. Placeholder/missing identity or
  database credentials, wildcard origins/hosts or development authentication
  prevent startup.
- Database migrations run once before deploy, never on every API/worker startup.
- The identical immutable image is promoted from staging to production.
- Supabase Data API exposure excludes private application schemas; mobile reads
  health data only through the backend.
- Company infrastructure and signing accounts have company ownership and at
  least two administrators.

## Architecture fitness rules

CI rejects provider SDK imports outside adapters, transaction calls outside the
Unit of Work, request-handler fire-and-forget work, module-to-module database
imports, raw mobile fetch, direct AsyncStorage health data, unauthenticated
private routes and catalog/ORM/Alembic/OpenAPI drift.

Handwritten files above 800 lines, functions above 100 lines or cyclomatic
complexity above 15 fail unless an ADR documents a narrowly scoped exception.
Generated source and vendor-owned migrations are classified separately.

## Status

This is a **TARGET PLANNED** architecture. Presence in this handbook does not
prove the rules are implemented. Verification requires the phase gates in
`06-delivery-and-acceptance.md` and dated evidence in the build manifest/release
record.
