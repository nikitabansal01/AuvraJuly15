# Deploy and rollback runbook

**Status:** TARGET PLANNED. Requires a previously tested PG17 rollback path.

1. Confirm frozen contracts, approved migration, immutable image digest and
   staging evidence. Run migrations once before API/worker rollout.
2. Promote the same image, watch authenticated smoke tests and inspect safe SLI
   signals. Halt on cross-user, safety, deletion or data-integrity concerns.
3. Before v2 writes, abandon cutover only while legacy remains restricted.
4. After v2 writes, roll back only to compatible v2 code/schema or tested v2
   restore. Never return traffic to insecure legacy.
5. Record UTC timeline, revision, image digest, decision owner and outcome.
