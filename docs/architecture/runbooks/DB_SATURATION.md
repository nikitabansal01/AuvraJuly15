# Database saturation runbook

1. Capture read-only pool, connection, lock, query-latency and migration
   evidence. Redact user/health content.
2. Reduce nonessential job concurrency and pause promotion through approved
   controls; do not kill sessions or change schema impulsively.
3. Escalate to database owner if locks, disk, replication or connection limits
   threaten integrity. Validate recovery with an authenticated smoke test.
