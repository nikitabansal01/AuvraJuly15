# ADR-003: Proof-bound guest onboarding and versioned consent

- **Status:** Proposed - written owner/legal approval pending
- **Date:** 2026-08-01
- **Owners:** Onboarding, consent and profiles

## Context

Guest sessions contain health-related answers before authentication. Session or
device identifiers alone are not authorization, and copying answers during claim
creates duplication.

## Decision

Create an expiring guest session with a high-entropy proof returned once and
stored only as a hash. One validated/versioned assessment is owned by exactly one
guest session or user. Claim atomically transfers ownership after Firebase and
proof verification. Consent decisions are immutable and reference document
version, jurisdiction/purpose metadata and time.

## Consequences and verification

Expired, guessed, replayed and concurrent claims fail safely. Assessment answers
are not copied. Exact consent wording, jurisdiction and retention remain release-
blocking owner/legal decisions. Tests cover proof rotation/expiry, simultaneous
claim, orphan prevention and deletion/export.

