# Authentication outage runbook

1. Confirm Firebase/provider status, token-verification error class and
   environment configuration using redacted observations.
2. Preserve normal authorization: do not disable token validation or use a
   request-body user ID as a fallback.
3. If a key/configuration rotation caused the outage, follow approved rollback
   or correction with security owner. Verify recovery using a test account.
4. Record impact, UTC interval, provider evidence and user communication owner.
