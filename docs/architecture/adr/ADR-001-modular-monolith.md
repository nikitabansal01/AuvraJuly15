# ADR-001: Modular monolith with one durable worker

- **Status:** Proposed - written owner approval pending
- **Date:** 2026-08-01
- **Owners:** Architecture owner; Platform engineering

## Context

The legacy backend has intertwined plan, scheduling, conversation, AI and
engagement implementations. Splitting unstable boundaries into microservices
would add deployment, data-consistency and observability costs before ownership
is clear.

## Decision

Build one FastAPI modular monolith with eight enforced modules and one separately
deployed durable worker from the same immutable image. PostgreSQL is the business
authority. Modules expose application interfaces and cannot import another
module's database implementation. The API performs synchronous adaptation; the
worker performs durable asynchronous orchestration.

## Consequences and verification

Deployment remains simple while module boundaries become testable. A future
service extraction requires its own ADR and evidence that independent scaling or
ownership outweighs distributed-system cost. CI checks module imports, provider
SDK boundaries, transaction ownership and file/complexity limits. Staging proves
the same image can run API and worker and survives worker restart.

