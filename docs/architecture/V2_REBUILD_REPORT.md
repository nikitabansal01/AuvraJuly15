# AUVRA v2 — Rebuild and Production Cutover Report

**Status: live in production.** API `https://auvra-v2-api.onrender.com`, a
separate durable worker, and a canonical PostgreSQL 17 schema. The insecure
legacy service is offline. Verified by a 25-check black-box smoke test run
against the live deployment, plus 415 automated tests.

---

## 1. What was wrong

The app was built feature-by-feature, largely by AI, over about a year. Each
feature was added *beside* the previous one rather than integrated into it.
That produces a specific, measurable failure pattern, and this codebase had
every symptom of it.

| Problem | Evidence found |
|---|---|
| Health data readable by anyone | **49 unauthenticated API operations**, 20 of them mutations. 21 endpoints in `chat.py` returned any user's chat transcripts, mood logs and wellness scores by passing a `user_id` in the URL — no token required. |
| A table per feature | **35 tables declared in one 1,614-line file**, with competing plan, scheduling, chat, streak and check-in subsystems. Preferences lived in a free-text `chatbot_memory` JSON blob. |
| Stored values that go stale | `user_rewards` counters, `user_streak_data` JSON date arrays, and a stored `bmi`/`bmi_category` that became wrong the moment a user logged a new weight. |
| Duplicated instead of reused | Three implementations of *each* check-in flow, two chat pipelines, two RAG engines, four memory layers, four caches. On mobile: **12 separate copies** of the API base-URL resolver, 11 of which silently fell back to `localhost` with no HTTPS check. |
| Work that silently disappears | `asyncio.create_task` inside an unauthenticated endpoint, handed a database session that outlived the request. A restart lost the work with no record. |
| Credentials in a public repo | A live account's email and plaintext password committed to a **public** GitHub repository, plus an authentication bypass that accepted any unsigned token as any user. |

These are not opinions about style. Each one is a defect with a specific
consequence, and each is addressed below.

---

## 2. What was built

### 2.1 One canonical data model — 35 tables became 34, with fewer concepts

The governing rule applied throughout:

> **A new table is justified only by a new row grain — never by a new screen.**

The clearest result: preferences, body metrics, symptoms and period dates were
four separate subsystems. They are in fact *one* row grain — the user asserted
that a named observable held a value at an instant — differing only in how they
are read (latest value vs. full series). They became **one table**,
`app.user_observations`, and the entire six-feature rebuild added **zero net new
tables**.

The tempting shortcut would have been a `value JSONB` column. That is the
`chatbot_memory` blob wearing a new name. Instead values live in three *typed*
columns with a database constraint enforcing exactly one is set, plus sorted,
de-duplicated code arrays. A malformed observation is impossible to store.

Rows are immutable. A correction is a new row citing the one it replaces, so
health history is never silently rewritten, and a corrected period date changes
every derived answer retroactively and correctly.

### 2.2 Nothing derived is stored

Streaks, adherence, reward balances, BMI and cycle phase are all computed from
canonical facts on read. A stored aggregate is a second source of truth that
drifts from the data it came from — which is exactly what the legacy counters
did. A test recomputes the SQL rollups in Python from seeded facts and asserts
equality; that is the evidence for the decision, not an assertion about it.

### 2.3 Authentication is structural, not per-endpoint

v1 had no router-level authentication anywhere; each endpoint decided for
itself, and 49 chose not to. v2 derives identity **only** from a verified
Firebase token. A request body can never assert who the caller is.

Verified live: all 15 private routes reject an unauthenticated caller with 401,
a forged bearer token is rejected, and a forged guest proof token of identical
length — one that passes length validation and reaches the cryptographic
comparison — is rejected with 404 rather than 403, so the response never
confirms a session exists to someone holding the wrong token.

### 2.4 Background work is durable

Fire-and-forget tasks were replaced by a PostgreSQL-authoritative job table
drained by a **separately deployed worker**. Work survives a restart, is leased
so two workers cannot claim the same job, and retries with bounded backoff into
a dead-letter state rather than vanishing.

---

## 3. Bugs found and fixed that no test had caught

These matter because they were all invisible to a green test suite. Each was
found by running the system for real, and each now has a regression test.

**1. Every plan was cycle-blind.** `request_plan_generation` never wrote
`cycle_snapshot` into the job payload, so *every plan ever generated* stored an
empty object. The generator had no cycle information at all.

**2. A streak-freeze exploit.** The database function validating streak days
opened with `IF NOT FOUND OR v_evidence_type = 'freeze' THEN RETURN` — every
freeze row skipped validation entirely. A "frozen" day could cite evidence that
pointed at nothing, at another user's ledger row, or at a grant instead of a
spend, extending a streak with no token spent. Fixed in the same migration that
shipped the freeze feature, so the exploit never shipped with it.

**3. The worker could not claim a single job.** Deploying the worker to real
PostgreSQL for the first time surfaced a driver-level bug: the lease interval
was built by string concatenation, which the async driver rejects for an
integer parameter. Every claim and heartbeat failed. Invisible to the test
suite because every worker test used a fake in-memory session; six new tests
now exercise it against real PostgreSQL.

**4. A rate-limit trip reported the wrong status.** The rate limiter runs
outside the layer where the error handler is registered, so a legitimate 429
(with its `Retry-After` header) collapsed into a bare 500. Found live when a
Redis misconfiguration surfaced it.

**5. A transient Redis blip failed a real user's first step.** Managed Redis
drops idle connections routinely, and the client had no retry, so one dropped
connection returned 503 on onboarding. Now retries twice with bounded backoff —
while still failing closed if Redis is genuinely down, which is deliberate.

**6. The account export would have broken on the schema change**, and its
serializer would have exported exact decimal values as lossy floats.

---

## 4. What was deleted

Deletion was verified, not guessed. A reachability analysis walked the import
graph from the app's real entry point.

- **21,539 lines of unreachable mobile code** — over half the source. 63 files
  had no importer at all; 17 more were reachable only from their own tests.
  Type-checking passed with zero errors afterwards, which is the proof nothing
  live depended on any of it.
- **All 10 legacy v1 adapters**, which turned out to be reachable only from
  dead code. The app now has **zero raw network calls and zero `/api/v1`
  references**.
- 20 abandoned scripts, two zero-importer backend modules, and 51 orphaned
  database migrations that sat one configuration line away from corrupting the
  schema.

Both quality gates now run with **zero exceptions**: no oversized-file
carve-outs, no permitted legacy adapters.

---

## 5. Security

| Fix | Detail |
|---|---|
| Legacy service **offline** | All 49 unauthenticated operations now return 503. Suspended, not deleted, so it is reversible; all data preserved. |
| Authentication bypass removed | It decoded any unsigned token and trusted its subject claim — anyone could authenticate as anyone. It was gated on a flag that *staging* also satisfied. |
| Committed credentials removed | A live account's plaintext password in a public repo. **Still in git history — that account's password must be changed.** |
| PII out of logs | User IDs, emails and provider exception strings no longer written to logs or returned in error responses. |
| Secret scanning in CI | `gitleaks` now scans full history on both repositories. Nothing was watching before. |
| Least privilege | The API and worker hold *different* secrets; the API refuses to start if a worker-only credential is present. Account exports are encrypted before upload and stored in a private bucket. |

---

## 6. Evidence

| Check | Result |
|---|---|
| Automated tests | **415 passed**, 2 skipped — run twice from a blank database to prove order-independence |
| Live deployment smoke test | **25/25 passed** against production |
| Schema round-trip | `upgrade → downgrade → upgrade` clean from empty, on PostgreSQL 17 |
| Schema drift | None — ORM, migrations and database agree |
| API contract | 41 operations, OpenAPI 3.1.1, checked in and diffed in CI |
| Mobile | 75 tests, type-check clean, both gates at zero exceptions, Android bundle verified |
| Architecture gate | Passing — no module over 800 lines, no function over 100, no provider SDK outside adapters |

The test suite previously **reported green on tests that never ran**: the
PostgreSQL suites skipped themselves when no database was configured, and CI
had no database. CI now provides one, so those suites actually execute.

---

## 7. Deliberate decisions worth reviewing

Judgement calls made in your absence, each reversible.

**Legacy data was not force-migrated.** 29 users, nearly all inactive 11–12
months, whose free-text onboarding answers do not validate against the new
strict schema. Forcing them through would mean *guessing* at health data. The
legacy tables are untouched and fully queryable. Migrating later is a
deliberate, owner-approved task — not something to improvise.

**Isolation is by schema, not by separate database.** The plan called for a
separate database; this account has no privilege to create one. The v2 schema
owns `app`/`ops`, records its migration head in its own table, and every
migration is additive with respect to the legacy schema. Enforced by tests.

**Two Firebase fields are intentionally unset.** `private_key_id` and
`client_id` are optional metadata — verified against the Google auth library
source, which needs only `client_email`, `token_uri` and `private_key` to build
a valid credential. They are never used to verify a token. The original service
account file was unrecoverable; requiring them would have blocked deployment
for a reason unrelated to security.

**Voice, mood logs and the public research endpoints were cut**, not rebuilt.

---

## 8. What is left

1. **Change the password** on the account exposed in git history. Needs Firebase
   console access.
2. **Test the APK** on a physical device.
3. **Decide on legacy data** — migrate the 29 users, or archive.
4. **Delete the legacy service** once you are satisfied. It is suspended and
   fully reversible today.

---

## 9. Running it

```bash
# Verify a live deployment (safe, read-mostly, creates only expiring guest sessions)
python scripts/smoke_v2_deployment.py https://auvra-v2-api.onrender.com

# Full test suite against a real database
export AUVRA_TEST_DATABASE_URL=postgresql://…
alembic upgrade head && pytest -q tests

# Architecture and contract gates
python scripts/check_v2_architecture.py
python scripts/export_v2_openapi.py    # must produce no diff
```

**Services:** `auvra-v2-api` (web) · `auvra-v2-worker` (background) ·
`auvra-v2-redis` (abuse-control counters only, never business data) ·
Supabase PostgreSQL 17 (`app` + `ops` schemas; legacy `public` untouched).
