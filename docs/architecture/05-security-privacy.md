# Security, privacy and trust boundaries

## Boundary model

The mobile device, public internet, API/worker runtime, managed data stores and
external providers are separate trust zones. The trust-boundary diagram is
`diagrams/c4/trust-boundary.mmd`. The API validates Firebase tokens and maps the
subject to one internal user before any private query. Workers accept only
server-issued job IDs and re-read authoritative state from PostgreSQL.

## Data classes

| Class | Examples | Production handling |
|---|---|---|
| Credentials/secrets | Firebase tokens, provider keys, DB URLs, signing keys | Secret manager/environment only; never logs, Git or catalogs; rotate on exposure |
| Direct identifiers | Email, provider subject, internal user ID | Least privilege; internal ID in operational joins; redact external identifiers from telemetry |
| Health-related content | Assessment answers, symptoms, messages, summaries, plan context | Purpose/consent limited, encrypted, owner-bound, omitted from telemetry, export/erasure covered |
| Derived health content | Plans, citations, evaluations, adherence/streak facts | Same lifecycle protection as source health data unless approved otherwise |
| Operational metadata | Job state, latency, tokens/cost, error code, correlation ID | Redacted; no prompt, email, answer, token or conversation content |
| Public evidence/media | Verified research metadata, approved category fallback | Integrity/version checks; still no user identifiers in provider prompts |

## Top threat paths and controls

| Threat | Prevent/detect control | Release evidence |
|---|---|---|
| Cross-user object access | Token-derived internal user plus repository owner predicate | Authorization matrix for every private object and hostile ID substitution |
| Guest-session takeover | High-entropy proof stored hashed, short TTL, rate limit, single claim | Guess/replay/expired/concurrent claim tests |
| Mutation replay/race | Request hash + Idempotency-Key + unique client operation + revision ETag | Duplicate tap, timeout retry and conflicting payload tests |
| Worker loss/duplication | PostgreSQL job authority, leases, heartbeats, bounded retry, idempotent publication | Kill/restart, lease expiry and dead-letter tests |
| Unsafe/unverifiable plan | Structured output, authoritative citations, deterministic and model safety gates | Clinical red-team fixtures and fail-closed publication tests |
| Sensitive logs/traces | Allowlisted telemetry attributes and logger redaction | Canary-secret/health-string scan across logs/traces/errors |
| Mobile account bleed | UID-scoped cache plus complete logout/account-switch purge | Two-account device E2E and storage inspection |
| Incomplete erasure | Deletion step ledger across identity, DB, objects, checkpoint/cache/summary | Post-deletion enumerator proves zero user-scoped remnants |
| Supply-chain compromise | Locked dependencies, SBOM, SAST/dependency/container/mobile scans | Zero open Critical/High findings and signed immutable image evidence |

## Account erasure

`DELETE /api/v2/me` uses recent authentication and returns a durable deletion job.
The worker marks the account deletion-pending, revokes/blocks new access, removes
objects and runtime/checkpoint/cache state, deletes or legally isolates database
rows, deletes the Firebase identity, and writes a content-free completion receipt.
Each step is idempotent and restartable. Partial failure remains visible and
alerted; the API never claims completion until the verification enumerator finds
no disallowed remnants.

The exact retention exception policy is unresolved and requires legal/owner
approval. A retained legal record must be minimal, access-restricted and detached
from serving use; this handbook does not declare that any exception applies.

## Release security gate

Use NIST SSDF, OWASP API Security Top 10 and OWASP MASVS as control frameworks,
plus a repository threat-model review. Production requires zero open Critical or
High security findings, no unresolved P0/P1 defect, rotated credentials and a
dated account-export/erasure test. These are gates, not current claims.

