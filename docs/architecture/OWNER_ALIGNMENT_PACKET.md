# AUVRA v2 owner alignment packet

## Purpose

This is the required decision checkpoint before AUVRA v2 is deployed to a
shared staging or production environment. It answers the two questions raised
by the owner: which product behavior remains, and which data structures and
metrics are authoritative. Approval of this packet authorizes implementation
of the agreed target; it does not by itself certify legal, clinical, security,
or production readiness.

## Recommended product disposition

| Product area | Recommendation | Reason |
|---|---|---|
| Firebase identity, profile, consent and guest onboarding | KEEP behavior; REBUILD contract and persistence | These are required entry flows, but guest proof, consent version and ownership must be explicit. |
| Four-action daily plan, action detail and sixteen permanent images | KEEP experience; REBUILD generation and publication | This is the core product. A plan is visible only after all content, evidence and media pass validation. |
| Completion, skip, feedback and Daily Review | KEEP; REBUILD as canonical events plus normalized review | Duplicate flags, arrays and counters are replaced by immutable facts and one review per plan. |
| Progress, streak, rewards and refresh | KEEP; REBUILD from ledgers | Displayed values are derived from one event/ledger source instead of competing counters. |
| General, care-plan and symptom conversations | KEEP; REBUILD into typed conversations | Raw message arrays and profile memory are removed. |
| Weekly check-in | KEEP; REBUILD as a versioned definition/check-in/response flow | This preserves the user journey without a duplicate weekly session truth. |
| Structured symptom observations | KEEP | They have a distinct timestamped clinical-observation grain and owner. |
| Insights | HOLD FOR OWNER DECISION | The current surface does not have an approved metric contract. |
| Legacy recommendation, schedule, assignment and redistribution system | ARCHIVE | It competes with Action Plans and is the largest source of duplicate plan/progress truth. |
| Mood tracking | ARCHIVE by default | The feature is inactive and not connected to the retained mobile journey. |
| Paywall, community, mock, example and test screens | REMOVE by default | They are not approved reachable product behavior. |
| Expo web/Router path | REMOVE | The approved product platforms are iOS and Android. |

The machine-readable feature decision record is
`catalogs/features.json`. The trace from retained screen to API, table and
metric is in `07-traceability.md`.

## Authoritative data decisions

The target contains 31 AUVRA application/operations tables and four
vendor-owned runtime checkpoint tables. This is not one table per screen or
feature. Each table is admitted only for a distinct row identity,
one-to-many relationship, authorization boundary, immutable history, or
retention/deletion lifecycle. The grain and owner of every table, plus the
disposition of all 35 legacy tables, are recorded in `catalogs/tables.json`.

The decisions that eliminate competing truth are:

1. `app.action_plans`, items and variants are the only serving plan model.
   Recommendation/schedule/assignment tables cannot serve the app.
2. `app.action_item_events` is the only completion/skip/feedback fact stream.
   Plan rows do not receive completion counters or boolean mirrors.
3. `app.daily_reviews` plus normalized review items are the only Daily Review
   records. Review answers are not copied into JSON arrays.
4. `app.streak_days`, `app.reward_ledger` and `app.plan_refreshes` are the only
   adjudication/reward/refresh facts. Balances and counts are calculated.
5. Conversations, messages and summaries replace raw-message arrays and
   chatbot memory on profiles.
6. `ops.generation_jobs` is authoritative for asynchronous state. Redis and
   mobile storage cannot claim business workflow truth.
7. `app.media_assets` records permanent stored objects; temporary provider
   URLs and image-usage arrays are not truth.
8. AI invocation telemetry and plan evaluation remain separate because cost/
   latency evidence and safety/quality judgments have different grains.

The complete rationale and anti-duplication test are in
`03-data-and-metrics.md` and ADR-004 through ADR-010.

## Authoritative metric decisions

Approve or amend the versioned formulas in `catalogs/metrics.json`:

- generation success is a terminal durable-job ratio;
- READY completeness is the four-action/sixteen-image database invariant;
- action completion counts each eligible item once using its latest canonical
  event;
- daily adherence uses the items presented for the closed user-local day;
- streak counts consecutive closed local dates adjudicated earned or frozen;
- refresh usage counts accepted refresh facts;
- reward/freeze balances sum ledger movements;
- weekly completion uses due check-ins under the approved schedule version;
- AI cost per READY plan sums linked invocation cost under one currency and
  pricing version.

No chart may introduce a private formula. A projection or cache needs named
lineage, freshness and a reconciliation test against these facts.

## Decisions still required from the owner

| Decision | Recommended default | Release impact |
|---|---|---|
| Retain Insights now? | No; hold until its questions and metrics are approved. | Unapproved Insights remains unreachable. |
| Retain mood tracking? | Archive. | No serving mood table or API. |
| Retain paywall/community? | Remove until there is an approved product and commercial contract. | Screens and mock data are deleted. |
| Consent text and versions | Owner/legal supplies released privacy and health-data documents. | Production startup is blocked without exact versions. |
| Jurisdiction, health-product classification and clinical escalation | Record legal/clinical decision and named owner. | Public release is blocked. |
| Retention periods and deletion exceptions | Define per health-data class and consent/audit obligation. | Automated retention and erasure cannot be certified. |
| Image visibility | Prefer private delivery unless generic public artwork is explicitly approved. | Storage policy and mobile URL strategy depend on this. |
| AI/evidence/image providers | Approve only after staging safety, quality, latency, cost and privacy evidence. | Provider credentials are not production-approved by architecture alone. |
| Isolated Supabase/Render staging spend | Approve an isolated database, API, worker and queue. | Live Render/device verification cannot use the legacy database. |

## Approval record

- Architecture/data direction: `PENDING`
- Retained-feature disposition: `PENDING`
- Metric catalog: `PENDING`
- Legal/privacy/retention: `PENDING`
- Clinical and safety language: `PENDING`
- Staging infrastructure and provider spend: `PENDING`
- Owner name/date/notes: `PENDING`

Implementation evidence may continue in isolated source and disposable test
environments. Shared staging creation, migration rehearsals, production
cutover and any claim of production readiness remain blocked until the
applicable approvals above are recorded.
