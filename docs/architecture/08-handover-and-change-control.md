# Handover and safe change control

## First-day exercise

A new engineer should be able to explain the system context, locate an owning
module from a feature, trace its screen to operation/table/metric, start API and
worker, bootstrap a blank database, run upgrade and rollback, diagnose an
injected provider/job failure and outline a schema/API/metric change without
creating competing truth. Until the implementation and runbooks make that
exercise pass independently, handover is incomplete.

## Change recipe

1. Find or add the feature record and confirm its owner/disposition.
2. Identify the owning module; do not import another module's repositories.
3. Amend an ADR when the governing decision changes.
4. Update OpenAPI operation/schema, generated client and contract tests together.
5. Update ORM, a single Alembic descendant, table catalog and ERD together.
6. Update metric formula/source/consumer and reconciliation tests when a display
   or SLO changes.
7. Add domain/property, authorization, idempotency, failure and telemetry tests.
8. Add/update the alert and linked runbook together.
9. Prove blank bootstrap, upgrade and rollback in PostgreSQL 17.
10. Record dated evidence; never label a target verified from code presence alone.

## Review questions

- Which single component owns this concept and its lifecycle?
- Can a retry, duplicate delivery, worker death or account switch change the
  outcome incorrectly?
- Is identity derived from the verified token and constrained in the repository
  query?
- Does any provider call occur with a transaction open?
- Could telemetry, a provider request or device storage expose health content?
- Is local-date behavior deterministic across timezone changes, midnight and DST?
- Is deletion/export coverage updated?
- Does migration classify every source row/object without guessing?
- Does the alert point to an independently exercised runbook?

## Decision authority

Product scope, jurisdiction, consent wording, retention and health-product/
clinical classification require recorded owner/legal/clinical authority.
Architecture owners may propose but not invent those decisions. Provider/model
selection requires a staged benchmark and privacy/safety review. Schema and
contract decisions require the affected domain owner plus architecture review.

## Documentation release

The PDF builder validates JSON catalogs, records source hashes, generates the
HTML companion and PDF, checks metadata/bookmarks/links and mandatory content,
runs `pdfinfo`, renders every page with Poppler and produces contact sheets for
visual review. The manifest and SHA-256 file bind the released artifact to its
sources. A visually clean PDF does not convert planned controls into verified
ones; status labels remain evidence-driven.

