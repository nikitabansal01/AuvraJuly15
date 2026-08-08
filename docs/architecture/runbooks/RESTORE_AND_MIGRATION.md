# Restore and migration runbook

**Status:** blocked until an isolated PostgreSQL 17 restore is evidenced.

1. Use an isolated PostgreSQL 17 source and fresh v2 target. Never restore a
   dump into a serving cluster.
2. Preserve source hashes and run the documented restore procedure. Record DDL
   and COPY success plus read-only counts.
3. Reconcile exactly 1,022 application COPY rows; record the separate one-row
   `alembic_version` entry. This inventory does not itself prove restoration.
4. Run two versioned dry-run/apply rehearsals into fresh targets. Compare row
   mapping, quarantine, foreign-key/key and target reconciliation outputs.
5. The supplied storage ZIP is a valid empty 22-byte archive: classify every
   expected object as missing. Regenerate retained assets, permanently store and
   hash them, then prove reachability before READY publication.
6. Stop on any mismatch. PostgreSQL 17.7 blank-target migration/invariant proof
   exists, but the isolated legacy restore and two source-to-target rehearsals
   remain unverified.

The content-free row-disposition procedure, its JSON-schema policy catalog, and
the precise two-rehearsal evidence required before this gate can close are in
`LEGACY_DISPOSITION_RECONCILIATION.md`.
