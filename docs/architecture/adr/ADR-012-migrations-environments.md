# ADR-012: Clean PostgreSQL 17 root and shadow-database migration

- **Status:** Proposed - migration owner approval pending
- **Date:** 2026-08-01
- **Owners:** Platform engineering; Data migration owner

## Context

Historical migration branches are not a safe v2 foundation and the only legacy
database must not be restructured in place.

## Decision

Create one clean v2 Alembic root for private `app`/`ops` schemas on PostgreSQL 17;
keep vendor checkpoint migrations isolated. Restore legacy backup only into an
isolated source. Versioned dry-run/apply migration writes a fresh target using
explicit legacy-ID-to-UUID mappings and per-row disposition. Rehearse twice.

## Consequences and verification

Cutover avoids dual-write/v1 compatibility. Rollback after v2 writes is only to
compatible v2 or restore. Blank bootstrap, upgrade/rollback, drift, 1,022-row
reconciliation, object hash classification and identical-rehearsal evidence gate
release.

