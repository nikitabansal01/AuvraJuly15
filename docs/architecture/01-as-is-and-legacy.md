# AS-IS evidence and legacy disposition

## Evidence boundary

AS-IS statements in this package are tied to committed objects, not a mutable
working tree:

| Repository | Frozen commit | Measured scope |
|---|---|---|
| Backend | `c02892a8e1d5ec6bc76040e25df4e93d8fb60cfc` | Tracked source at commit, primarily `app/**/*.py` |
| Mobile | `923e09c234c7b105d9f9ec15347ea555fa45a104` | Tracked TS/TSX/JS/JSX excluding native build trees and dependencies |

The exact counts, largest-file list, table list, route scan methodology and
selected source hashes are in `evidence/repository_snapshot.json`. The route
authentication count is a static signal only: it detects recognized guards
inside operation functions and cannot prove all dependency injection or object
authorization behavior. Every private object still needs an explicit cross-user
test.

## Observed architecture

The backend is one FastAPI process exposing `/api/v1`, with endpoint modules that
call a large set of services and SQLAlchemy models. All 35 application ORM tables
are declared in one 1,614-line database file. Direct transaction calls are
distributed throughout application code. Firebase initialization can continue
after failures, production host defaults allow `*`, CORS allows all origins, and
some background generation uses in-process `asyncio.create_task`. Redis appears
for rate limiting/cache behavior, while LangGraph checkpoint configuration is
mixed into the same runtime.

The mobile application starts from `App.tsx` with React Navigation, but still
declares Expo Router and web dependencies/configuration. API calls, token logic
and URL selection are duplicated across services and screens. AsyncStorage holds
workflow state, assessment/review drafts and, in the legacy auth service,
password-related values. Large screens and temporary copies compete as
implementation references.

## Systemic risks evidenced by source

| Risk | Evidence | v2 control |
|---|---|---|
| Lost asynchronous work on process restart | Four committed `asyncio.create_task` call sites | Durable PostgreSQL job record, Redis transport, worker lease/heartbeat/dead letter |
| Ambiguous identity/authorization | Static scan finds 52 operations without a recognized local guard | Token-derived internal user plus repository owner predicates and cross-user tests |
| Partial or broken plans | Generation and image work span large services and mutable status | Atomic READY publication after four-action/sixteen-image validation |
| Competing business truth | 35-table model contains recommendation, assignment and plan families plus counters/arrays | Canonical v2 ledgers and plan family; legacy archive is non-serving |
| Transaction drift | 146 direct `.commit()` calls found in tracked Python | One SQLAlchemy 2.x Unit of Work owns begin/commit/rollback |
| Sensitive device storage | Password, assessment and Daily Review references in AsyncStorage | Firebase session truth, SecureStore only for necessary credentials, server-side/time-limited drafts |
| Mobile contract drift | 61 raw fetch calls and 12 API URL implementations | Generated OpenAPI client is the sole network/token/URL layer |
| Unverified behavior | No detected mobile test file | Component and Maestro device tests on both platforms |

## Legacy database boundary

`docs/DATABASE_RECOVERY.md` remains the authoritative historical recovery
procedure for the 35-table public-schema baseline. The supplied backup's COPY
sections contain the following exact application row counts:

| Table | Rows | Table | Rows |
|---|---:|---|---:|
| `daily_assignments` | 145 | `question_sessions` | 4 |
| `recommendation_advices` | 498 | `recommendation_completions` | 1 |
| `recommendation_records` | 169 | `recommendation_redistributions` | 0 |
| `recommendation_schedules` | 141 | `schedule_redistributions` | 5 |
| `session_processing_status` | 8 | `user_profiles` | 25 |
| `user_responses` | 25 | `user_schedules` | 1 |
| **Application total** | **1,022** | `alembic_version` (separate) | 1 |

Its safe principle is retained: restore the cluster dump only into an isolated
PostgreSQL 17 source, bootstrap a blank target through Alembic, and copy only
explicitly reviewed application columns.

That runbook bootstraps the legacy 35-table schema; it is **not** the v2 serving
schema and its existing copier must not be presented as a completed v2 migration.
The v2 migration requires UUID mapping, row-level disposition, quarantine reports
and object hashing against the new `app`/`ops` schemas. PostgreSQL 17.7 is now
available locally, and a disposable blank v2 database has exercised bootstrap,
selected cross-row invariants and rollback paths. **Legacy restore rehearsal is
still not verified:** the supplied cluster dump has not yet completed an isolated
source restore followed by two identical fresh-target rehearsals. Those remain
release blockers.

The supplied storage ZIP is a valid but empty 22-byte archive (`unzip -t` reports
an empty archive). No legacy media can be restored from it. Required plan media
must be regenerated through approved providers/fallbacks, permanently persisted,
hashed and reconciled before any plan is published READY.

## Disposition summary

- `question_sessions` and `user_responses` become one guest workflow plus one
  canonical validated assessment.
- Legacy recommendation/scheduling/assignment tables are archived. Only
  completion/skip history with deterministic user, item and time mapping may
  become action events.
- `user_profiles` splits identity from mutable profile; chatbot memory, counters
  and scheduling fields lose source-of-truth status.
- Chat, care-plan and symptom threads consolidate into typed conversations,
  normalized messages and versioned summaries. `raw_messages` arrays disappear.
- The action-plan family is rebuilt with UUIDs, revisions, constraints and
  permanent media foreign keys. Feedback and reviews become immutable events and
  normalized review rows.
- Mutable streak/reward/refresh counters and date arrays become ledgers.
- Mood tracking is archived by default because it is inactive; mock, test,
  backup and unreachable features are removed unless the owner explicitly
  retains them.

Every individual legacy table, target table, row grain and mapping is recorded in
`catalogs/tables.json`. Ambiguous data is archived or quarantined; it is never
guessed into a canonical v2 row.

## Legacy rollback statement

Before v2 receives writes, a cutover can be abandoned while the legacy system
remains restricted. After meaningful v2 writes, rollback is only to a compatible
v2 application/schema or a tested v2 restore. The insecure legacy deployment is
not an approved production rollback target.
