# Legacy migration evidence register

**Evidence state:** Partial; restore rehearsal not verified  
**Release effect:** Blocking for Phase 0 restore gate and Phase 5 migration gate

## Database backup counts

The supplied SQL backup's COPY sections contain:

| Table | COPY rows |
|---|---:|
| `daily_assignments` | 145 |
| `question_sessions` | 4 |
| `recommendation_advices` | 498 |
| `recommendation_completions` | 1 |
| `recommendation_records` | 169 |
| `recommendation_redistributions` | 0 |
| `recommendation_schedules` | 141 |
| `schedule_redistributions` | 5 |
| `session_processing_status` | 8 |
| `user_profiles` | 25 |
| `user_responses` | 25 |
| `user_schedules` | 1 |
| **Application total** | **1,022** |
| `alembic_version` (not an application row) | 1 |

These counts prove the COPY-section inventory, not a successful restore or v2
migration.

## Storage archive

The supplied storage ZIP is a structurally valid but empty 22-byte archive;
`unzip -t` identifies it as empty. Therefore:

- no legacy media object can be restored;
- a migration report must classify expected legacy object references as missing;
- retained plan imagery must be regenerated using an approved provider or
  permanent category fallback; and
- each regenerated object must be permanently stored, hashed, safety-approved
  and linked before the plan can become READY.

## Remaining restore blocker

PostgreSQL 17.7 is now available locally. A disposable blank target has exercised
the v2 migration chain, selected downgrade paths and executable database
invariants. That target proof does not restore, classify or migrate the supplied
legacy cluster dump. Required closure evidence is still:

1. isolated PostgreSQL 17 source restore logs with application DDL/COPY success;
2. read-only per-table counts matching the inventory above;
3. two versioned dry-run/apply rehearsals into fresh v2 targets;
4. identical row mappings/quarantine outputs and target reconciliation; and
5. regenerated asset reachability/hash evidence.

Until all five exist, do not label backup restoration, data migration, media
migration or production cutover ready.
