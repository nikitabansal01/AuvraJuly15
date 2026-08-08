# ADR-011: OpenAPI v2, token-derived ownership and RFC 9457 errors

- **Status:** Proposed - written owner approval pending
- **Date:** 2026-08-01
- **Owners:** API architecture; Domain owners

## Context

The legacy client duplicates URL/token/DTO logic and v1 routes include user IDs,
inconsistent errors and mutation semantics.

## Decision

Check in OpenAPI 3.1.1 under `/api/v2` with stable operation IDs. Private identity
comes only from verified tokens. Every mutation requires `Idempotency-Key`; plan
and other versioned mutations use `If-Match`. Errors use RFC 9457 plus stable code
and correlation ID. v1 is absent from final production.

## Consequences and verification

The generated TypeScript client becomes the mobile network contract. Runtime,
OpenAPI, examples and generated-client hashes must match in CI. Authorization,
problem-details, idempotency and stale-revision contract tests cover every
operation.

