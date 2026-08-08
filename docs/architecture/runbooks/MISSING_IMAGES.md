# Missing plan images runbook

1. Read plan status, media asset ID, storage hash and reachability. Never reveal
   a READY plan that lacks four actions and sixteen reachable permanent assets.
2. Determine whether the provider, upload, object store or manifest link failed.
3. Retry only through the durable job policy; quarantine blank/temporary or
   unverifiable output and dead-letter exhausted work.
4. Verify every regenerated asset is permanent, hashed and linked before atomic
   publication. Record the reconciliation result.
