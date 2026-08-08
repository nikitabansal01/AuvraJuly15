# Credential exposure runbook

1. Treat the report as an incident. Restrict affected access and preserve only
   redacted evidence; never paste the secret into tickets or chat.
2. Identify credential owner, environment, scope and last known use from safe
   audit data. Rotate/revoke through the owning provider.
3. Replace the secret in the approved manager, redeploy only affected services
   and invalidate dependent sessions/tokens where the owner requires it.
4. Search approved logs/build artifacts for exposure indicators, assess impact
   with security/privacy owner, and document closure evidence.
