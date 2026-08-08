# Setup and bootstrap runbook

**Status:** TARGET PLANNED. A local setup is not production readiness.

1. Obtain company-owned, environment-specific credentials through the approved
   secret manager; never copy production secrets into local files or logs.
2. Start the declared PostgreSQL 17, Redis and provider test dependencies.
3. Bootstrap a blank target using the canonical Alembic chain; record revision,
   image digest and redacted configuration validation result.
4. Run authenticated smoke tests and confirm the API/worker use separate roles.
5. Stop and escalate if migrations, health checks, token verification or secret
   validation fail. Do not bypass checks with wildcard origins/hosts.
