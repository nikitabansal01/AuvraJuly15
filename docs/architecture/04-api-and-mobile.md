# Public interface and mobile integration

## Contract

The target contract is checked-in OpenAPI 3.1.1 with stable operation IDs and
RFC 9457 problem details. The API catalog records 27 proposed operations. It is
the design baseline; implementation and generated-client drift tests must still
prove the checked-in OpenAPI matches runtime behavior.

Private operations derive identity exclusively from the verified Firebase token.
Object access applies both the internal user predicate and resource relationship
in the repository query. Request-body/path `user_id` values never authorize.
Session-specific onboarding operations require a separate guest proof credential.

Every mutation requires `Idempotency-Key`; retries with the same key and request
hash replay the original result, while a different hash returns a stable conflict
problem. Plan mutations also require `If-Match` revision validation. Errors return
`application/problem+json`, stable `code`, safe `detail` and correlation ID; they
never return stack traces, provider exceptions, tokens or prompt contents.

## Native plan representation

The `ActionPlan` DTO contains plan ID, revision, local date, timezone, cycle
snapshot, four canonical items, completion summary and typed image assets. The
client never reconstructs a plan from assignments or merges competing legacy
DTOs. ETags represent resource revisions.

## Generation behavior

`POST /api/v2/plan-generations` commits a durable job and returns its ID. The
client observes `GET /api/v2/jobs/{job_id}` states:

`queued -> running -> retry_wait -> running -> ready`

or a terminal `failed`, `cancelled` or `dead_letter`. A ready response links to
the published plan. Timers and AsyncStorage flags never decide job truth.

On cold start, the client reads `GET /api/v2/me/plans/today` first. When no
plan exists, it may read `GET /api/v2/me/plan-generations/latest` for the
profile-local day (or an explicit local-date recovery target). This owner-scoped
read returns the latest job by `created_at, id` descending, including terminal
jobs, so the client never silently creates a duplicate generation.

## Mobile layering

```text
feature screen/container
    -> feature hook + reducer/state machine
        -> generated OpenAPI client + TanStack Query
            -> auth/token + URL + transport core
```

Raw `fetch`, Firebase token lookup and base URL resolution exist only in the
client transport. Server DTOs come from generation, not copied interfaces.
React Navigation remains; Expo Router and web-only configuration are removed.
The supported release platforms are iOS and Android.

Firebase listener state is the only login-session truth. SecureStore keeps only
necessary credentials; passwords are never stored. Health drafts are server-side
or encrypted with a documented TTL. Logout/account switch cancels requests,
clears TanStack Query, removes secure credentials and deletes all UID-scoped
local/cache data before the next account renders.

One `PlanImage` component uses `expo-image`. All four hero images are prefetched
and verified before the plan is revealed. Permanent approved fallbacks are
category-specific; blank or temporary URLs never render a READY plan.

## Migration order

1. Generated client, auth/bootstrap, typed navigation and storage/query core.
2. Onboarding and claim.
3. Plan, action event, review and progress.
4. General conversation and typed check-ins.
5. Profile/export/deletion and any owner-approved remaining feature.
6. Remove mock screens, duplicate DTOs, fake plans, dead services, temporary
   copies, debug signing assets and unused web/router dependencies.

Each journey passes component tests and Maestro E2E on iOS and Android, including
token expiry, duplicate taps, process death, offline recovery, account switch,
timezone/DST boundaries, provider/image failure and accessibility.
