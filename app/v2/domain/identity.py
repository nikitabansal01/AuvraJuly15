"""Provider-neutral identity established by a verified authentication adapter."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class VerifiedPrincipal:
    auth_provider: str
    subject: str
    email: str | None
    email_verified: bool
    display_name: str | None
    # Provider adapters translate a verified token's authentication instant to
    # this neutral fact.  Missing data is intentionally not treated as recent.
    authenticated_at: datetime | None = None


class AuthenticationUnavailable(RuntimeError):
    """The configured identity provider cannot verify tokens."""


class InvalidIdentityToken(ValueError):
    """An identity token failed verification or has no stable subject."""
