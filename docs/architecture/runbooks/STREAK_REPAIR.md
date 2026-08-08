# Streak repair runbook

1. Freeze derived display updates and preserve immutable action/review evidence.
2. Recompute the affected user/local-date range using recorded timezone and
   evidence type; never edit a streak counter as truth.
3. Emit auditable correction/revocation ledger evidence under owner approval.
4. Compare projections before/after and add a regression test for the trigger.
