# ADR-004: Revisioned plans and immutable action history

- **Status:** Proposed - written owner approval pending
- **Date:** 2026-08-01
- **Owners:** Plans, actions and Daily Review

## Context

Legacy recommendation, assignment and plan systems compete, while feedback and
reviews mix mutable flags and JSON arrays.

## Decision

One plan revision exists per user/local date/revision, with one current revision.
Each plan has unique slots, typed variants and replacement lineage. Completion,
skip and feedback are immutable events with client operation IDs. One Daily
Review header per plan has one normalized row per item. A short publication
transaction marks READY only after four actions and sixteen permanent reachable
images are validated.

## Consequences and verification

History remains explainable and replay-safe; current state is derived. The READY
invariant spans rows and object reachability and therefore needs domain validation
plus reconciliation. Property tests cover revision races, duplicate events,
replacement lineage, partial image failure and review uniqueness.

