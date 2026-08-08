# ADR-014: Redacted OpenTelemetry and evidence-linked operations

- **Status:** Proposed - telemetry platform selection pending
- **Date:** 2026-08-01
- **Owners:** Platform engineering; Security/privacy owner

## Context

Production decisions require user-relevant SLIs without leaking health answers,
prompts, emails, tokens or conversation content.

## Decision

Emit vendor-neutral OpenTelemetry traces, metrics and structured logs through an
allowlisted attribute schema. Correlation IDs and stable error/job/provider codes
link signals. Each SLO maps to a metric catalog record; every alert maps to a
runbook. Audit events are content-free security/account facts. Release claims
require dated evidence.

## Consequences and verification

Debugging relies on state/codes rather than sensitive payloads. Automated canary
scans inject recognizable secret/health strings and fail if any appear in logs,
traces or errors. Staging exercises alerts and runbooks with an operator other
than the author.

