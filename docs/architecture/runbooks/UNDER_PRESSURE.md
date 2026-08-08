# Under-pressure index

**Scope:** TARGET PLANNED operating model. It is not evidence that the target
worker, alerts, dashboards or controls are deployed. Do not improvise writes in
legacy production.

## First five minutes

1. Declare an incident owner and record UTC start time, user impact and known
   boundaries. Do not put health content, tokens or prompt text in the ticket.
2. Read the relevant runbook below. Start with read-only checks and preserve
   logs/evidence before retrying or restarting work.
3. Confirm environment, deployed immutable image, migration revision, provider
   status and whether the affected data is legacy or v2.
4. If cross-user exposure, suspected credential exposure, deletion failure or
   unsafe health content is possible: restrict access, preserve evidence and
   escalate to security/privacy/clinical owner. Do not make a compliance claim.

## Situation index

| Signal | First check | Runbook | Unsafe shortcut to avoid |
|---|---|---|---|
| API unavailable/error surge | Readiness, DB/Redis connectivity, deploy change | `API_AND_DATABASE.md` | Restart loops or disabling auth |
| Job backlog/retries | Job state, lease age, provider error class | `JOBS_AND_PROVIDERS.md` | Requeueing all work blindly |
| Plan missing/broken image | Plan status and asset reachability | `JOBS_AND_PROVIDERS.md` | Marking READY without 16 assets |
| Wrong account/data access | Request/audit scope and token subject | `SECURITY_PRIVACY.md` | Querying another user's records |
| Deletion/export request stuck | Erasure job evidence by scope | `SECURITY_PRIVACY.md` | Calling providers without case record |
| Restore/migration issue | PG17 environment and count reconciliation | `RESTORE_AND_MIGRATION.md` | Restoring into serving database |

## Exit discipline

Document timestamps, commands/queries, immutable artifact identifiers, impact,
decision owner and follow-up. An alert is closed only after user impact is
understood and the linked acceptance evidence is attached.
