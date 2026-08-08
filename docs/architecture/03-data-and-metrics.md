# Canonical data and metric governance

## Schema ownership

The v2 serving database has three private schemas:

- `app` contains application truth owned by the eight domain modules.
- `ops` contains durable jobs, outbox, idempotency and redacted audit events.
- `runtime` contains the four vendor-owned LangGraph checkpoint tables with
  retention rules.

Legacy rows and objects stay in an encrypted, access-restricted archive or
isolated migration source. No legacy table remains in the serving schema. The
logical ERDs are in `diagrams/data/`; a physical appendix must be regenerated
from the final Alembic/ORM state before release.

## Canonical row families

| Family | Canonical grain |
|---|---|
| Identity/onboarding | Internal user; mutable profile; immutable consent decision; guest session; validated assessment |
| Plans | Plan revision per user/local date; item per slot; typed variant per item |
| History/review | Immutable action event; Daily Review per plan; answer per reviewed item; accepted refresh operation |
| Engagement | Adjudicated streak day; immutable reward-ledger movement |
| Conversation/check-in | Typed conversation; ordered message; versioned summary; weekly check-in/question/response |
| Symptoms | Timestamped structured user-owned observation |
| Media/evidence/quality | Immutable media version; research source; claim-source link; provider invocation metadata; plan evaluation |
| Operations/runtime | Durable job; outbox event; idempotency result; redacted audit event; vendor checkpoint row |

The table catalog gives all 31 application/operations tables plus the four
vendor-owned runtime tables, and maps every one of the 35 legacy ORM tables.

The target count is a consequence of normalized row grains, not a target by
itself. A table is allowed only when its records have a distinct identity,
cardinality, retention/deletion rule, authorization boundary, or immutable
history requirement. A screen, button, chart, or AI prompt is never sufficient
reason to create a table. Conversely, unrelated facts are not forced into one
JSON document merely to make the table count smaller.

## Why these are separate tables

| Boundary | Why it is not merged | Competing truth that is forbidden |
|---|---|---|
| `users` / `user_profiles` | Authentication identity has a stricter lifecycle than editable display and locale fields. | Email, Firebase subject, or account status copied into feature tables. |
| onboarding session / assessment | A short-lived guest authorization credential is not health-answer content. Assessment revisions remain auditable while ownership is claimed without copying answers. | A second response table or device-ID authorization path. |
| plan / item / variant | One plan has four ordered items and each item has three independently addressed variants. Foreign keys and uniqueness constraints express that one-to-many structure. | Assignments, schedules, recommendations, or JSON arrays acting as another plan. |
| action event / Daily Review | Events are immutable facts at interaction time; a review is one normalized end-of-day decision over every item in one plan. Their lifecycle and grain differ. | Completion flags on plan rows, review JSON arrays, or a separate feedback counter. |
| streak day / reward ledger / refresh | Each is a distinct adjudicated or financial-style fact. Current values are derived, never edited counters. | `current_streak`, `freeze_balance`, date arrays, or refresh-count columns. |
| conversation / message / summary | Threads own ordered messages; summaries are replaceable derived versions that explicitly record the message boundary they cover. | Raw-message arrays or chatbot memory on the profile. |
| weekly check-in / question / response | Definition versions are reusable, a due check-in is user/week state, and each response has one question grain. | A second weekly session record containing duplicate answers. |
| media / source / citation | Stored bytes, authoritative publications, and claim-to-source relationships have independent identity and retention. | Image-usage arrays, ephemeral provider URLs, or cached search results used as evidence truth. |
| invocation / evaluation | Provider cost/latency is operational evidence; product safety/quality scoring is a separately versioned judgment. | One overloaded AI log or raw health prompts in telemetry. |
| job / outbox / idempotency | Durable execution, reliable publication, and request replay solve different failure modes. | In-memory tasks, Redis business truth, or workflow flags on the device. |

Any proposed merge or split must state the row grain before discussing columns.
If two records can be created, retained, authorized, retried, or deleted
independently, a forced merge is rejected. If two tables answer the same
business question from the same facts, the duplicate is rejected or classified
as a documented, reconciled projection.

## Enforced invariants

1. Every user-owned row has an internal user foreign key with explicit deletion
   behavior.
2. UTC `TIMESTAMPTZ` records instants; daily policy also stores immutable IANA
   timezone and local date.
3. An assessment has one canonical row. Before claim, the guest session is its
   authorization owner; after claim, `user_id` becomes its authorization owner
   while `session_id` remains immutable provenance. Answers are never copied.
4. Plan `(user_id, local_date, revision)` and one-current-plan constraints are
   explicit; revisions are preserved.
5. Item slot is unique per plan and variant type is unique per item.
6. `READY` publication atomically proves four actions and sixteen reachable
   permanent images.
7. One Daily Review exists per plan and one answer per plan item.
8. Client operation/message identifiers and idempotency keys make retries safe.
9. Refresh count, reward balance and streak values are queries over immutable
   facts, never second counters.
10. No health-data row is ownerless or lacks an approved archival class.

Some invariants require deferred database constraints or publication-domain
checks because they span multiple rows and external object reachability. They
must still be transactionally fail closed and covered by reconciliation tests.

## Metric ownership

Metrics are definitions over canonical fields, not columns copied into unrelated
tables. `catalogs/metrics.json` records formula, grain, timezone, exclusions,
source fields, owner, freshness and consumers for:

- plan-generation success;
- ready-plan completeness;
- action completion and daily adherence;
- current streak and refreshes used;
- weekly check-in completion;
- AI cost per ready plan;
- core API availability; and
- Daily Review persistence.

A projection/cache is allowed only with a named owner, documented lineage,
refresh policy and a reconciliation test against canonical facts. The current day
may show a clearly labeled provisional streak/adherence value but cannot be
finalized before the user-local day closes.

## Health-data lifecycle

Collection must be purpose-limited and consent-versioned. Encryption, access
control, production telemetry redaction, export and complete erasure apply to
answers, messages, summaries, observations and derived results. Exact retention
durations and any legally required consent-record retention are unresolved owner/
legal decisions; the application must fail release, not invent a duration.

## Migration proof

For each of the known 1,022 legacy application rows (plus the separate migration
version row), the v2 migrator produces exactly one of:

- a target table and stable legacy-ID-to-UUID mapping;
- an approved archive class; or
- a quarantine record with reason and no serving import.

Object migration records content SHA-256 and classifies referenced, duplicate,
orphaned and missing objects. Two clean target rehearsals must produce identical
counts/hashes, zero orphan foreign keys, zero duplicate business keys and zero
broken retained assets.

Current evidence does not satisfy that gate. The supplied storage ZIP is a valid
empty 22-byte archive, so there are no recoverable legacy media objects; images
must be regenerated and stored permanently. A disposable PostgreSQL 17.7 target
has proved clean v2 bootstrap, selected cross-row invariants and rollback paths.
The isolated legacy-dump restore and two identical source-to-target rehearsals
remain unverified.
