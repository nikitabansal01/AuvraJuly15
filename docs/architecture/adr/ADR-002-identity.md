# ADR-002: Internal identity mapped from verified Firebase subjects

- **Status:** Proposed - written owner approval pending
- **Date:** 2026-08-01
- **Owners:** Identity and access

## Context

Legacy routes and tables use Firebase UIDs directly and some routes accept user
identifiers in paths/bodies. This weakens ownership boundaries and complicates
provider change or account lifecycle.

## Decision

Firebase remains authentication-session truth. Every private request verifies the
token and maps `(provider, subject)` to one internal UUID `app.users.id`. Request
data never authorizes access. Repositories include the internal user predicate in
object queries. Disabled/revoked/deleted accounts fail closed. Mobile never
stores passwords.

## Consequences and verification

Application foreign keys become provider-independent and cross-user tests become
uniform. Claim, export and deletion require recent-auth policies. Verification
includes expired/revoked tokens, subject collision, hostile object IDs, account
switch and Firebase deletion behavior.

