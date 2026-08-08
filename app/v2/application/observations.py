"""Reads and writes over the canonical user-observation table.

Current state and history are the same rows read two ways: the latest live row
per code, or the full live series for a code. That is what removes v1's
competing sources of truth, where a preference lived in a JSON blob while its
history lived nowhere and body metrics carried a stored BMI that went stale.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import Select, select

from app.v2.application.contracts import (
    CurrentObservationsResponse,
    DerivedBodyMetrics,
    ObservationCatalogEntry,
    ObservationCatalogResponse,
    ObservationPageResponse,
    ObservationResponse,
    ObservationValue,
    ObservationWriteRequest,
)
from app.v2.application.errors import forbidden, not_found, unprocessable_content
from app.v2.application.rewards import qualifying_streak_days
from app.v2.application.services import _begin_idempotent, _complete_idempotent
from app.v2.domain.identity import VerifiedPrincipal
from app.v2.domain.observation_catalog import (
    BODY_METRIC,
    CATALOG_VERSION,
    CODES,
    NUMERIC,
    OBSERVATION_CATALOG,
    TEXT,
    bmi_band,
    body_mass_index,
    normalize_codes,
    observable_or_none,
    validation_error,
    waist_height_ratio,
)
from app.v2.domain.reward_catalog import REWARD_CATALOG, longest_run, unlocked_codes
from app.v2.persistence.models import User
from app.v2.persistence.models_observations import UserObservation
from app.v2.persistence.uow import SqlAlchemyUnitOfWork


MAX_PAGE_SIZE = 200

#: Personalization codes a reward unlocks. Sourced from the reward catalog so
#: a reward and the thing it unlocks cannot drift apart.
GATED_CODES = frozenset(
    code for reward in REWARD_CATALOG if (code := reward.unlocks_code)
)


def catalog() -> ObservationCatalogResponse:
    """The public vocabulary. Contains no user data, so it needs no auth."""

    return ObservationCatalogResponse(
        catalog_version=CATALOG_VERSION,
        entries=[
            ObservationCatalogEntry(
                code=o.code,
                observation_type=o.observation_type,
                value_kind=o.value_kind,
                label=o.label,
                unit=o.unit,
                minimum=o.minimum,
                maximum=o.maximum,
                choices=list(o.choices),
                multi_select=o.multi_select,
            )
            for o in OBSERVATION_CATALOG
        ],
    )


def _live(statement: Select) -> Select:
    """Restrict to observations nothing supersedes."""

    superseding = select(UserObservation.supersedes_id).where(
        UserObservation.supersedes_id.is_not(None)
    )
    return statement.where(UserObservation.id.not_in(superseding))


def _value_of(row: UserObservation) -> ObservationValue:
    if row.value_numeric is not None:
        return ObservationValue(
            numeric=float(row.value_numeric), unit=row.value_unit
        )
    if row.value_codes is not None:
        return ObservationValue(codes=list(row.value_codes))
    return ObservationValue(text=row.value_text)


def _response(row: UserObservation) -> ObservationResponse:
    return ObservationResponse(
        observation_id=row.id,
        observation_type=row.observation_type,
        code=row.code,
        catalog_version=row.catalog_version,
        observed_at=row.observed_at,
        observed_local_date=row.observed_local_date,
        value=_value_of(row),
        note=row.note,
        supersedes_observation_id=row.supersedes_id,
        recorded_at=row.recorded_at,
    )


async def _user(uow: SqlAlchemyUnitOfWork, principal: VerifiedPrincipal) -> User:
    user = await uow.users.get_by_subject(principal.auth_provider, principal.subject)
    if user is None or user.status != "active":
        raise not_found("User")
    return user


async def _unlocked_codes(uow: SqlAlchemyUnitOfWork, user_id, local_date) -> frozenset:
    """Which personalization codes this user has earned the right to set.

    Derived from the streak ledger through the reward catalog rather than read
    from a stored flag, so it cannot disagree with the streak it comes from.
    """

    days = await qualifying_streak_days(uow.session, user_id, local_date)
    return unlocked_codes(longest_run(days))


async def record_observation(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    request: ObservationWriteRequest,
    key: str,
    now: datetime | None = None,
) -> ObservationResponse:
    now = now or datetime.now(UTC)
    user = await _user(uow, principal)
    profile = await uow.profiles.get(user.id)
    if profile is None:
        raise not_found("Profile")

    observable = observable_or_none(request.code)
    if observable is None:
        raise not_found("Observation code")

    codes = (
        normalize_codes(request.value.codes) if request.value.codes is not None else None
    )
    problem = validation_error(
        code=request.code,
        observation_type=request.observation_type,
        numeric=request.value.numeric,
        unit=request.value.unit,
        codes=codes,
        text=request.value.text,
        note=request.note,
    )
    if problem is not None:
        raise unprocessable_content("observation_invalid", problem)

    if request.code in GATED_CODES:
        local = now.astimezone(ZoneInfo(profile.timezone)).date()
        if request.code not in await _unlocked_codes(uow, user.id, local):
            raise forbidden(
                "preference_locked",
                "Keep your streak going to unlock this personalization.",
            )

    decision = await _begin_idempotent(
        uow,
        scope="observation.create",
        subject=str(user.id),
        key=key,
        payload=request.model_dump(mode="json"),
        now=now,
    )
    if decision.replay_body is not None:
        return ObservationResponse.model_validate(decision.replay_body)

    if request.supersedes_observation_id is not None:
        prior = await uow.session.get(UserObservation, request.supersedes_observation_id)
        if prior is None or prior.user_id != user.id:
            raise not_found("Observation")

    timezone = profile.timezone
    row = UserObservation(
        id=uuid.uuid4(),
        user_id=user.id,
        observation_type=request.observation_type,
        code=request.code,
        catalog_version=CATALOG_VERSION,
        observed_at=request.observed_at,
        observed_local_date=request.observed_at.astimezone(ZoneInfo(timezone)).date(),
        observed_timezone=timezone,
        value_numeric=(
            Decimal(str(request.value.numeric))
            if request.value.numeric is not None
            else None
        ),
        value_unit=request.value.unit if request.value.numeric is not None else None,
        value_codes=codes,
        value_text=request.value.text,
        source="user",
        supersedes_id=request.supersedes_observation_id,
        client_observation_id=request.client_observation_id,
        note=request.note,
    )
    uow.session.add(row)
    await uow.session.flush()

    body = _response(row)
    _complete_idempotent(
        decision, response_status=201, response_body=body.model_dump(mode="json")
    )
    await uow.commit()
    return body


async def list_observations(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    observation_type: str | None = None,
    code: str | None = None,
    limit: int = 50,
    cursor: uuid.UUID | None = None,
) -> ObservationPageResponse:
    user = await _user(uow, principal)
    limit = max(1, min(limit, MAX_PAGE_SIZE))

    statement = _live(
        select(UserObservation).where(UserObservation.user_id == user.id)
    )
    if observation_type is not None:
        statement = statement.where(
            UserObservation.observation_type == observation_type
        )
    if code is not None:
        statement = statement.where(UserObservation.code == code)
    if cursor is not None:
        anchor = await uow.session.get(UserObservation, cursor)
        if anchor is None or anchor.user_id != user.id:
            raise not_found("Observation")
        # Keyset pagination on the same total order the query sorts by, so a
        # concurrent insert cannot make a row appear twice or be skipped.
        statement = statement.where(
            (UserObservation.observed_at, UserObservation.id)
            < (anchor.observed_at, anchor.id)
        )

    rows = (
        await uow.session.scalars(
            statement.order_by(
                UserObservation.observed_at.desc(), UserObservation.id.desc()
            ).limit(limit + 1)
        )
    ).all()
    has_more = len(rows) > limit
    page = list(rows[:limit])
    return ObservationPageResponse(
        observations=[_response(row) for row in page],
        next_cursor=page[-1].id if has_more and page else None,
    )


async def current_observations(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    observation_type: str | None = None,
) -> CurrentObservationsResponse:
    """The latest live assertion per code, plus values derived from them."""

    user = await _user(uow, principal)
    statement = _live(
        select(UserObservation).where(UserObservation.user_id == user.id)
    )
    if observation_type is not None:
        statement = statement.where(
            UserObservation.observation_type == observation_type
        )
    profile = await uow.profiles.get(user.id)
    local = datetime.now(UTC).astimezone(
        ZoneInfo(profile.timezone if profile else "UTC")
    ).date()
    unlocked = await _unlocked_codes(uow, user.id, local)
    rows = (
        await uow.session.scalars(
            statement.order_by(
                UserObservation.code,
                UserObservation.observed_at.desc(),
                UserObservation.recorded_at.desc(),
                UserObservation.id.desc(),
            )
        )
    ).all()

    latest: dict[str, UserObservation] = {}
    for row in rows:
        latest.setdefault(row.code, row)

    def metric(code: str) -> float | None:
        row = latest.get(code)
        return float(row.value_numeric) if row and row.value_numeric is not None else None

    weight, height, waist = metric("weight_kg"), metric("height_cm"), metric("waist_cm")
    bmi = body_mass_index(weight_kg=weight, height_cm=height)
    return CurrentObservationsResponse(
        entries=[_response(row) for row in latest.values()],
        unlocked_codes=sorted(unlocked),
        derived=DerivedBodyMetrics(
            bmi=bmi,
            bmi_band=bmi_band(bmi),
            waist_height_ratio=waist_height_ratio(
                waist_cm=waist, height_cm=height
            ),
        ),
    )
