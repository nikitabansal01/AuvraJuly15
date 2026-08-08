# ADR-010: Versioned metric catalog over canonical facts

- **Status:** Proposed - formula owner approval pending
- **Date:** 2026-08-01
- **Owners:** Domain metric owners; Data/observability steward

## Context

Duplicate counters, arrays and scattered functions allow multiple values to
claim the same product or reliability concept.

## Decision

Every metric record includes formula, grain, timezone, exclusions, canonical
source fields, owner, freshness and consumers. Current-day daily metrics are
provisional until local day close. Caches/projections require lineage, refresh
policy and reconciliation tests. SLOs reference catalog IDs and telemetry.

## Consequences and verification

Metric changes become reviewed architecture changes rather than hidden code
edits. Formula property tests, canonical-vs-projection reconciliation and catalog
consumer checks gate release. Initial thresholds remain targets until dated
production-readiness evidence exists.

