# ADR-009: Immutable permanent media before plan publication

- **Status:** Proposed - provider selection pending
- **Date:** 2026-08-01
- **Owners:** Media, jobs and operational infrastructure

## Context

Temporary/broken URLs and usage arrays cannot support the four-action/sixteen-
image product contract or reliable deletion/inventory.

## Decision

`media_assets` represents immutable object versions identified by content hash,
storage key, media type, dimensions, safety/approval state and lifecycle. Items
and variants reference assets by foreign key. Provider output is copied to
permanent storage and verified before publication. If both providers fail, use an
approved permanent category fallback or fail the job.

## Consequences and verification

Usage is derived from foreign keys; duplicate/orphan/missing objects can be
reconciled. Tests cover hash deduplication, reachability, provider outage,
fallback category, object deletion and the sixteen-image invariant.

