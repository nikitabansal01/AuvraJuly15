# Jobs, providers and media runbook

**Status:** TARGET PLANNED. PostgreSQL job state is authoritative; Redis only
transports identifiers.

1. Inspect job ID, idempotency key, state, attempts, lease owner/expiry,
   heartbeat and redacted provider error classification.
2. Check provider health and credential rotation state. Do not paste prompts,
   user content, secrets or generated health advice into operational tooling.
3. A worker may retry only according to the recorded bounded policy. Expired
   leases may be reclaimed through the tested lease mechanism; never invoke a
   provider while a database transaction is open.
4. Before publication, prove the plan has exactly four valid actions and
   sixteen reachable, permanent, approved assets. Missing/temporary/blank media
   leaves the plan unpublished.
5. Escalate exhausted, unsafe or unverifiable output to dead letter and the
   relevant product/safety owner. Record a replay decision and evidence.
