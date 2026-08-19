"""FastAPI dependencies owned by the v2 HTTP adapter."""

from collections.abc import AsyncIterator
import re
from typing import Annotated

from fastapi import Depends, Header

from app.v2.api.auth import get_verified_principal
from app.v2.application.errors import precondition_required
from app.v2.domain.identity import VerifiedPrincipal

from app.v2.persistence.uow import SqlAlchemyUnitOfWork
from app.v2.runtime.abuse_controls import enforce_costly_mutation_limit


async def get_uow() -> AsyncIterator[SqlAlchemyUnitOfWork]:
    async with SqlAlchemyUnitOfWork() as uow:
        yield uow


async def require_costly_mutation_capacity(
    principal: VerifiedPrincipal = Depends(get_verified_principal),
) -> None:
    """Rate-limit durable AI/export work after verified identity is known."""

    await enforce_costly_mutation_limit(principal)


IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
]

OnboardingProof = Annotated[
    str,
    Header(
        alias="X-Onboarding-Proof",
        min_length=32,
        max_length=256,
    ),
]

_STRONG_REVISION_ETAG = re.compile(r'^"(?P<revision>0|[1-9][0-9]*)"$')


def _require_strong_revision(if_match: str | None, *, minimum: int, resource: str) -> int:
    if not if_match:
        raise precondition_required(f"If-Match with the current {resource} revision is required.")
    match = _STRONG_REVISION_ETAG.fullmatch(if_match.strip())
    if match is None:
        raise precondition_required("If-Match must be a quoted, strong integer ETag.")
    revision = int(match.group("revision"))
    if revision < minimum:
        raise precondition_required(
            f"If-Match must be a {resource} revision of at least {minimum}."
        )
    return revision


async def require_profile_revision(
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> int:
    return _require_strong_revision(if_match, minimum=1, resource="profile")


async def require_assessment_revision(
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> int:
    return _require_strong_revision(if_match, minimum=0, resource="onboarding assessment")


async def require_plan_revision(
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> int:
    """Parse the strong ETag revision required for every plan mutation."""
    return _require_strong_revision(if_match, minimum=1, resource="plan")


async def require_conversation_revision(
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> int:
    return _require_strong_revision(if_match, minimum=1, resource="conversation")


async def require_weekly_checkin_revision(
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> int:
    return _require_strong_revision(if_match, minimum=1, resource="weekly check-in")
