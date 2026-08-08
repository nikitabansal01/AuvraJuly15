# Account deletion runbook

1. Verify the Firebase `auth_time` recent-authentication fact, the requester
   identity and the legal-hold/retention decision; create or reuse the durable
   idempotent deletion request. The request records only a keyed subject hash.
2. Before accepting a request, enable the non-secret API and worker deletion
   flags together. Before any irreversible provider call, set the documented
   erasure-release gate in worker composition with an owner/legal approval
   reference and complete Firebase, Redis and receipt-key configuration.
   Without all of these, the API returns `account_deletion_unavailable` or the
   job remains retryable; no Firebase, storage, runtime, cache or PostgreSQL
   erasure is attempted.
3. Track the ordered ledger: Firebase identity, private objects, runtime
   checkpoints, UID-scoped cache, then the trusted PostgreSQL graph erase.
   Each adapter must treat an already-absent object as successful so a crash
   between provider success and ledger persistence can recover safely.
4. Do not claim completion until every scope has durable evidence and the
   pseudonymous receipt exists. Escalate a failed scope using its stable error
   code; never persist a provider response, subject, health answer or object
   URL in logs or job results.
5. The checkpoint adapter is limited to the pinned public LangGraph vendor
   tables and `user:{uuid}:...` thread prefix. It deletes writes, blobs and
   checkpoints in that order, verifies no owned rows remain, and never alters
   global `checkpoint_migrations`. Stop on any unrecognized or partial vendor
   schema; do not manually broaden the query during an incident.
