# API and database incident runbook

**Status:** TARGET PLANNED. Execute only in an approved environment with an
identified incident owner.

1. Record deploy image digest, UTC time, request IDs and affected endpoint
   class. Redact health content, authorization headers and tokens.
2. Read readiness and dependency health; compare error/latency to the approved
   baseline. Verify database connection saturation, migration revision and
   Redis availability without exposing data. Confirm whether the deployment is
   in explicit `direct` or `pooler` database mode; never change one pool mode
   variable without the matching pool budget settings.
3. For `429 rate_limit_exceeded`, preserve the `Retry-After` and request ID,
   then inspect only aggregate expiring-counter availability and proxy trust
   configuration. Do not inspect keys or disable the limit. A protected-route
   `503 rate_limit_unavailable` is fail-closed by design; restore the isolated
   TLS Redis dependency and verify with a non-sensitive request.
4. For `413 request_body_too_large`, confirm the client payload contract. The
   v2 API accepts at most the configured `V2_MAX_REQUEST_BODY_BYTES`; raising
   it requires capacity review, a contract change and a release test.
5. If a migration/deploy correlates with the incident, stop promotion and use
   the tested compatible v2 rollback/restore path. Legacy is never a post-write
   rollback target.
6. For database pressure, capture read-only lock/connection evidence. Do not
   kill sessions or alter schema without DBA/owner approval.
7. Verify recovery with an authenticated, non-sensitive smoke test and record
   the exact evidence. Create follow-up for missing alert/runbook/test coverage.
