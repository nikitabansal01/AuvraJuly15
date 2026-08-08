# ADR-007: PostgreSQL-authoritative jobs, outbox and idempotency

- **Status:** Proposed - written owner approval pending
- **Date:** 2026-08-01
- **Owners:** Media, jobs and operational infrastructure

## Context

In-process background tasks can disappear during restart and at-least-once queue
delivery can duplicate business effects.

## Decision

Every asynchronous business operation starts with a committed
`ops.generation_jobs` row. Redis transports identifiers only. Workers use leases,
heartbeats, bounded attempts, retry wait and dead-letter states. Domain changes
and outbox rows commit together. Every mutation uses an actor/operation/
Idempotency-Key record with request hash and replayable result.

## Consequences and verification

Redis loss delays work without losing truth; duplicate delivery is safe. Provider
calls happen outside transactions. Worker kill/restart, lease stealing, timeout,
duplicate message, conflicting request hash and dead-letter tests are mandatory.

