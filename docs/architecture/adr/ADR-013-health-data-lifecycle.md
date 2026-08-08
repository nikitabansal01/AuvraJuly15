# ADR-013: Purpose-limited health-data lifecycle and complete erasure

- **Status:** Proposed - legal/clinical/owner decisions pending
- **Date:** 2026-08-01
- **Owners:** Privacy owner; Onboarding, consent and profiles

## Context

Assessments, symptoms, conversations, plans and summaries are health-related and
span identity, DB, objects, runtime/cache and providers. Exact jurisdiction,
retention and product classification are unresolved.

## Decision

Collect only purpose/consent-versioned data, encrypt and owner-bind it, omit it
from production telemetry and include it in export/erasure. Deletion is a durable,
restartable job covering Firebase, PostgreSQL, storage, conversations, summaries,
checkpoints and caches, followed by a verification enumerator. No compliance or
diagnostic claim is made without approved evidence.

## Consequences and verification

Public release blocks on recorded legal/clinical decisions. Tests prove consent
versioning, access, export, partial-failure recovery and zero disallowed remnants.
Any legal retention exception must be minimal and explicitly approved.

