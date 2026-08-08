# Legacy disposition and reconciliation runbook

This is the v2-only replacement for treating the historical copier as a v2
migrator. It is an offline evidence procedure. It neither accepts database URLs
nor contacts Supabase, Render, or any serving service.

## Inputs and safe output

Use only the checksummed legacy cluster export and storage export in an isolated
workspace. Set `LEGACY_RECONCILIATION_FINGERPRINT_KEY` from the approved secret
store; do not put it in a command history, report, source file, or commit.

```bash
export LEGACY_RECONCILIATION_FINGERPRINT_KEY='provided-from-approved-secret-store'
python scripts/reconcile_legacy_backup.py \
  --database-export '/path/db_cluster-05-09-2025@05-46-59.backup.gz' \
  --storage-export '/path/Storage Objects - dculqiokbqnwuhqpdret.storage.zip' \
  --output /restricted-artifacts/legacy-disposition.dry-run.json \
  --mode dry-run
```

The ledger is versioned and has one record for each of the 1,022 application
COPY rows. A record contains only legacy table/primary ID, proposed canonical
table/UUID mappings, one disposition, a reason code, and an HMAC row
fingerprint. Canonical UUID proposals are derived with the same private HMAC
key, so a public namespace cannot be used to test candidate authentication
IDs. It **never** contains emails, health answers, prompts, or a raw
COPY row/payload. Keep the ledger as a restricted migration artifact because
legacy IDs remain identifiers.

The disposition catalog is
`docs/architecture/catalogs/legacy-disposition-rules.v1.json`. Its JSON Schema
restricts target names to the canonical schemas created by Alembic
`20260801_0002` and `20260808_0003`. A UUID is stable from the legacy table/ID
and intended canonical entity, but it is not a serving-row claim unless its
disposition is `MIGRATE`.

## Disposition policy

The baseline is deliberately conservative:

- Any health, identity, workflow, or processing row without documented consent
  and a proven canonical ownership mapping is `QUARANTINE`.
- Derived recommendations, schedules, assignments, and advice are `ARCHIVE`;
  they do not become a v2 action plan by reconstruction.
- `MIGRATE` requires a separately approved, versioned consent and mapping
  decision. No missing or ambiguous value may be inferred.
- `REMOVE` is only available for an approved, legally authorized deletion rule;
  it is not used by this evidence set.

The current supplied evidence has no consent ledger or canonical item mapping,
so an honest run has no target writes. That is a successful conservative
classification, not migration completion.

## Reconciliation gates

The program first invokes the content-free backup verifier, so the database
must match the exact reviewed 12-table/1,022-row inventory. It then fails on a
duplicate source ID, a missing catalog table, a duplicate ledger identity, or a
referential source value lacking a source mapping. Its report records totals and
the four media states (`referenced`, `duplicate`, `orphaned`, `missing`) using
content hashes and HMACed object-key fingerprints; it does not disclose object
keys. The supplied 22-byte archive has no objects and therefore cannot prove
any retained media available.

Repeat the dry run with the same evidence and secret key. The serialized output
must be byte-identical. Do not compare reports created with different keys.

`--mode apply` is intentionally guarded and does not contain a DB writer. It
requires a local, non-secret attestation JSON such as:

```json
{"postgres_major": 17, "target_revision": "20260808_0011", "fresh_target": true}
```

That mode preserves the same deterministic ledger and can authorize only the
number of rows marked `MIGRATE` (currently zero). A future writer must consume
that immutable ledger in an isolated target and reject any attempt to create a
row not present in it; it must not be added to this script as a connection URL
option.

## Remaining blocking rehearsals

Two clean PostgreSQL 17 rehearsals are still required. For each rehearsal:

1. Restore the legacy dump into an isolated PostgreSQL 17 source and capture
   DDL/COPY success and read-only table counts.
2. Bootstrap a fresh target through the checked-in Alembic head
   `20260808_0011`; run the exact
   versioned dry-run and guarded apply ledger against it.
3. Prove equal input/ledger totals, no duplicate identities, no orphan
   mappings, target foreign-key/business-key reconciliation, and storage hash
   classification. Regenerated retained assets must be durable and reachable
   before any plan becomes READY.
4. Compare the two content-free reports byte-for-byte with the same HMAC key.

Until both clean PG17 rehearsals and asset evidence exist, restore, v2
migration, and production cutover remain blocked.
