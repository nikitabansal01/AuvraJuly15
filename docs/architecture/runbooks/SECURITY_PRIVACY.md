# Security, privacy, export and erasure runbook

**Status:** TARGET PLANNED. Legal jurisdiction, retention and clinical
escalation wording remain owner decisions.

1. Treat a suspected exposure as an incident: limit access, preserve redacted
   evidence, identify the environment and escalate to the security/privacy
   owner. Do not expand access to investigate.
2. Verify identity from a token-derived internal user and repository owner
   predicate. A body `user_id` or client state does not authorize access.
3. For export/erasure, record request identity, legal hold decision, approved
   scope and idempotency key. The target scope includes Firebase, PostgreSQL,
   object storage, conversations, summaries, checkpoints, caches and derived
   artifacts.
4. Confirm each scope with durable completion evidence. Failed deletion is not
   silently retried beyond the recorded job policy; escalate with the failed
   scope and redacted reason.
5. Close only after owner review and dated evidence; never infer compliance
   from code or a dashboard.
