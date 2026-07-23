# Database recovery runbook

## Supported recovery model

The supported target is a completely blank PostgreSQL 17 database. Schema and
data recovery are intentionally separate:

1. Alembic creates the canonical current schema in the blank target.
2. The legacy cluster dump is restored only into an isolated PostgreSQL 17
   source database.
3. `scripts/migrate_legacy_data.py` copies the reviewed application columns
   from that isolated source into the canonical target.

Do not replay the downloaded Supabase cluster SQL into the new Supabase
project. It contains managed Supabase schemas, roles, ownership, grants, and
event triggers that do not belong in the new project.

## Canonical blank-database bootstrap

Prerequisites:

- PostgreSQL 17-compatible target connection string.
- Application dependencies installed from `requirements.txt`.
- The target must not contain any AUVRA application tables.
- On Supabase, use the owner connection string from the **Connect** panel with
  the real database password and TLS enabled. The publishable API key is not a
  database credential.

Set `DATABASE_URL` in the process environment without writing it to Git, then
run:

```sh
alembic heads
alembic upgrade head
alembic current
alembic check
```

Expected results:

- The only head/current revision is `20260723_0001`.
- There are 35 application tables plus `alembic_version`.
- `alembic check` reports `No new upgrade operations detected.`
- `weekly_checkin_questions` has 9 rows, of which 7 are active.

Useful read-only verification queries:

```sql
SELECT version_num FROM alembic_version;
SELECT count(*) FROM pg_tables WHERE schemaname = 'public';
SELECT count(*) FROM weekly_checkin_questions;
SELECT count(*) FROM weekly_checkin_questions WHERE is_active;
```

Before importing user data, protect the backend-only database from direct
client access. The preferred dashboard control is to remove `public` from the
project's **Data API → Exposed schemas** setting. Also run
`scripts/harden_supabase_backend_only.sql` as the object owner to remove both
existing and default table, sequence, and function privileges from `PUBLIC`,
`anon`, and `authenticated`. The script's two verification result sets must be
empty. This is a cutover prerequisite and is deliberately separate from the
portable Alembic schema because those roles do not exist in ordinary
PostgreSQL.

## Explicit legacy-data transformation

The copier recognizes the 12 application tables from backend commit
`d7495ab`, the last application schema before the 2025-09-05 backup. It copies
only explicitly listed columns. It does not copy `alembic_version`, roles,
grants, extensions, internal Supabase schemas, or storage metadata.

The destination must already be at `20260723_0001`, and every destination
legacy table must be empty. The command refuses to merge into populated
tables, refuses a schema mismatch, refuses a row-count mismatch, and does no
writes unless `--apply` is supplied.

### Build the isolated legacy source

The downloaded file is a plain PostgreSQL cluster SQL dump, not a custom
`pg_restore` archive. Never pipe the whole file into PostgreSQL: its global
preamble includes managed-role changes. The following procedure deliberately
starts at the dump's `\connect postgres` marker, which excludes the role and
`template1` sections, and loads it only into a disposable PostgreSQL 17
container:

```sh
docker run -d --name auvra-legacy-source \
  -e POSTGRES_HOST_AUTH_METHOD=trust \
  -p 127.0.0.1:55433:5432 postgres:17

until docker exec auvra-legacy-source \
  pg_isready -U postgres -d postgres >/dev/null 2>&1; do sleep 1; done

gzip -dc /absolute/path/to/db_cluster.backup.gz \
  | sed -n '/^\\connect postgres$/,$p' \
  | docker exec -i auvra-legacy-source \
      psql -X -U postgres -d postgres \
      > /tmp/auvra-legacy-restore.log 2>&1
```

Supabase-only extensions, schemas, and grants can produce errors in this
vanilla container. That is expected; application DDL or `COPY` errors are not.
Before using the source, require all 13 public snapshot tables and confirm the
12 copier-table counts sum to exactly 1,022. Keep the container bound to
`127.0.0.1`, never expose it to another host, and remove the container and its
restore log after the verified migration.

The isolated source URL for a copier running directly on the host is:

```text
postgresql://postgres@127.0.0.1:55433/postgres
```

For a copier in another Docker container, replace `127.0.0.1` with
`host.docker.internal`.

With `LEGACY_DATABASE_URL` pointing to the isolated PostgreSQL 17 restore and
`DATABASE_URL` pointing to the canonical target, validate first:

```sh
python scripts/migrate_legacy_data.py --expected-total-rows 1022
```

After the data-retention decision is approved and the validation output shows
the expected 1,022 rows, copy within a single destination transaction:

```sh
python scripts/migrate_legacy_data.py --expected-total-rows 1022 --apply
```

The copier verifies every copied legacy column, initializes the new non-null
`user_profiles.feedback_last_count` field, and advances owned integer
sequences. It never prints row contents or connection strings.

After the copy:

```sh
alembic current
alembic check
```

Then record per-table counts, take a fresh logical backup of the validated
target, and only then update Render's `DATABASE_URL` and deploy.

## Historical migration audit

Before the recovery baseline, Alembic reported one nominal head,
`3f9c1e2a6b77`, but that did not make the history a reliable bootstrap path.
The audit found:

- `20a3c53155ff`, `5647d1b57a23`, and `b521a53ac247` are non-merge revisions
  whose upgrade and downgrade functions are empty.
- `add_daily_review_system` comments out creation of
  `action_plan_daily_reviews`, while its downgrade tries to delete that table
  and its indexes.
- `5c6207e75696` performs a large, destructive chatbot-table rebuild but has
  no downgrade implementation.
- `201294a9bf6c`, `d1bfdfd82071`, `d580e1d3df52`, and
  `refactor_session_schema` contain destructive drops or state-dependent data
  rewrites that are inappropriate for a blank recovery replay.
- `migrations/create_ai_model_usage_logs.sql` and
  `migrations/add_action_plan_evaluations.sql` create schema outside Alembic.
- Multiple historical branches and merge revisions make successful stamping
  insufficient evidence that the installed database matches the ORM.

Those files remain unchanged under `alembic/versions` for auditability. New
schema changes must be generated as descendants of `20260723_0001`, reviewed,
and validated with `alembic check` against PostgreSQL 17.

## Rollback boundary

Before Render is switched, rollback is simply to leave Render on its existing
database URL and discard the unused new target if appropriate. After Render is
switched and users can write data, database rollback requires a backup/restore
or forward repair; changing only the Render code revision cannot roll back
database state.
