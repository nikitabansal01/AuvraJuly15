# Delivery sequence and acceptance evidence

## Current program state

This initial package completes part of the Phase 1 alignment material. It does
not assert that Phase 0 evidence preservation, credential rotation, infrastructure
restriction, target implementation, migration or staging gates have passed.
Owner approval remains pending.

## Phase gates

| Phase | Exit evidence required | Status at this release |
|---|---|---|
| 0 Preserve/contain | Tags, checksummed DB/storage/config/flow evidence, verified restore, rotated credentials, restricted legacy | **Blocked:** legacy-dump restore and credential rotation not verified; supplied storage ZIP is empty |
| 1 Align | Approved feature/table/metric catalogs, ERDs, API and ADRs; no P0/P1 decision open | Package produced; approval pending |
| 2 Foundation | Blank DB bootstrap/rollback, UoW, auth, idempotency, worker restart and transaction tests | Not evidenced complete |
| 3 Backend slices | Contract, ownership, migration, failure, telemetry and domain tests per slice | Not evidenced complete |
| 4 Mobile integration | Retained iOS/Android journeys, generated client, no raw fetch/plaintext password/cross-account cache | Not evidenced complete |
| 5 Migration | Two identical clean rehearsals, 1,022-row disposition, regenerated asset inventory, zero FK/key/asset defects | **Blocked:** blank v2 PG17 proof exists, but no two source-to-target rehearsals and no restorable legacy media |
| 6 Staging readiness | Exact artifacts, full tests, 72-hour soak, independent runbook exercise, zero Critical/High or P0/P1 | Not evidenced complete |
| 7 Cutover/hypercare | Final reconciliation, smoke tests, two-hour watch, seven days without P0/P1, restore evidence | Not evidenced complete |

## Automated evidence suites

- Domain property/state tests for plan invariants, idempotency, timezone/DST,
  streak/freeze, review and metric formulas.
- PostgreSQL/Redis integration; blank/upgrade/rollback; schema drift.
- OpenAPI/runtime/generated-client drift.
- Authentication and cross-user authorization for every private object.
- Worker kill/restart, lease expiry, timeout, retry and dead-letter behavior.
- React Native components and Maestro E2E on physical/simulator iOS and Android.
- Secret, dependency, SAST, container, API and mobile-storage scans.
- Export/deletion verification across Firebase, DB, objects, summaries,
  checkpoints and cache.

## Initial targets - not current claims

| SLI | Initial acceptance target |
|---|---|
| Core synchronous API availability | At least 99.9% |
| Non-AI latency | p95 under 750 ms; p99 under 2 s |
| Current-plan retrieval | p95 under 1 s |
| Valid plan generation | At least 98%; p95 under 120 s |
| READY plan completeness | 100% four actions and sixteen reachable permanent images |
| Daily Review persistence | At least 99.9%; zero duplicate keys |
| Authorization/data integrity | Zero cross-user access and zero orphan records |
| Capacity floor | 25 interactive users plus five generation jobs for 30 minutes within SLOs |
| Recovery | Tested RPO <= 1 hour; RTO <= 2 hours |
| Coverage | Critical domain/auth/deletion >= 90% branch; backend >= 80%; mobile >= 75% |

OpenTelemetry provides vendor-neutral traces, metrics and logs. User-relevant SLI/
SLO design follows Google SRE principles. Health answers, prompts, emails, tokens
and conversation contents are forbidden from telemetry. An SLO without
instrumentation, an alert without a runbook or a production-ready claim without
dated evidence fails release review.

## Cutover and rollback

Freeze legacy writes, take final backups, migrate/reconcile, deploy the exact
staging-proven API/worker artifact, smoke-test authenticated flows and switch the
stable company hostname. Watch continuously for two hours, then through seven
days of hypercare. After v2 writes, rollback only to compatible v2 code/schema or
a verified v2 restore; never direct traffic back to the insecure legacy service.
