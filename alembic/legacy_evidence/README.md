# Legacy migration evidence

Nothing in this directory is executable by Alembic. It is retained as audit
evidence only, and every file is pinned by `SHA256SUMS`.

## `20260723_0001_canonical_schema_baseline.py`

The superseded v2 baseline. The active chain roots at
`alembic/recovery_versions/20260801_0002_auvra_v2_foundation.py`
(`down_revision = None`).

## `versions/` — 51 legacy v1 revisions

The v1 migration history, rooted at `0001` and ending at `3f9c1e2a6b77`. It
describes the 35-table `public`-schema model that `app/core/database.py`
declared, which the `app`/`ops` canonical schema replaced.

These were archived rather than deleted because they are the only record of how
the legacy database reached the shape the backup was taken from, and the data
migration reconciles against that shape.

They were moved out of `alembic/versions/` because that is Alembic's default
`version_locations`. The active chain is selected by one line in `alembic.ini`:

```ini
version_locations = %(here)s/alembic/recovery_versions
```

While the legacy revisions sat in the default location, deleting or overriding
that single line would have loaded two disjoint chains, producing two heads and
two roots, and `alembic upgrade head` would have failed or applied v1 DDL to the
v2 schema. Archiving them removes that failure mode entirely.

`tests/test_v2_migration_architecture.py` asserts the active chain has one clean
root and no legacy baseline. `.dockerignore` still excludes `alembic/versions`,
and `.github/workflows/v2-ci.yml` asserts the built image contains no such path.
