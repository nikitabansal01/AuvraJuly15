"""Executable proofs for the canonical observation table's constraints."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest


pytestmark = pytest.mark.skipif(
    not os.getenv("AUVRA_TEST_DATABASE_URL"),
    reason="Observation constraint tests require AUVRA_TEST_DATABASE_URL",
)


@pytest.fixture(autouse=True)
def _dispose_shared_engine():
    yield
    import asyncio

    from app.v2.persistence.database import dispose_database

    asyncio.run(dispose_database())


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _engine():
    from sqlalchemy import create_engine

    return create_engine(os.environ["AUVRA_TEST_DATABASE_URL"])


def _insert_user(connection) -> uuid.UUID:
    from sqlalchemy import text

    user_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO app.users (id, auth_provider, auth_subject) "
            "VALUES (:id, 'firebase', :subject)"
        ),
        {"id": user_id, "subject": f"obs-{user_id}"},
    )
    connection.execute(
        text(
            "INSERT INTO app.user_profiles (user_id, timezone) "
            "VALUES (:user_id, 'UTC')"
        ),
        {"user_id": user_id},
    )
    return user_id


def _insert(connection, user_id, **overrides):
    """Insert one observation, defaulting to a valid numeric symptom."""
    from sqlalchemy import text

    row = {
        "id": uuid.uuid4(),
        "user_id": user_id,
        "observation_type": "symptom",
        "code": "cramps",
        "observed_at": datetime.now(UTC),
        "observed_local_date": datetime.now(UTC).date(),
        "observed_timezone": "UTC",
        "value_numeric": 5,
        "value_unit": "score_0_10",
        "value_codes": None,
        "value_text": None,
        "source": "user",
        "source_id": None,
        "supersedes_id": None,
        "client_observation_id": uuid.uuid4(),
        "note": None,
    }
    row.update(overrides)
    connection.execute(
        text(
            "INSERT INTO app.user_observations "
            "(id, user_id, observation_type, code, observed_at, "
            " observed_local_date, observed_timezone, value_numeric, value_unit, "
            " value_codes, value_text, source, source_id, supersedes_id, "
            " client_observation_id, note) "
            "VALUES (:id, :user_id, :observation_type, :code, :observed_at, "
            ":observed_local_date, :observed_timezone, :value_numeric, "
            ":value_unit, :value_codes, :value_text, :source, :source_id, "
            ":supersedes_id, :client_observation_id, :note)"
        ),
        row,
    )
    return row["id"]


def test_exactly_one_typed_value_is_stored() -> None:
    """The alternative to this constraint is a JSONB dumping ground."""
    from sqlalchemy.exc import IntegrityError

    for overrides in (
        {"value_numeric": 5, "value_unit": "score_0_10", "value_codes": ["present"]},
        {"value_numeric": None, "value_unit": None},
        {"value_numeric": 5, "value_unit": "score_0_10", "value_text": "also"},
    ):
        with pytest.raises(IntegrityError):
            with _engine().begin() as connection:
                _insert(connection, _insert_user(connection), **overrides)


def test_a_unit_is_present_exactly_when_a_number_is() -> None:
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        with _engine().begin() as connection:
            _insert(connection, _insert_user(connection), value_unit=None)

    with pytest.raises(IntegrityError):
        with _engine().begin() as connection:
            _insert(
                connection,
                _insert_user(connection),
                value_numeric=None,
                value_unit="kg",
                value_codes=["present"],
            )


def test_code_sets_must_be_sorted_deduplicated_and_non_empty() -> None:
    from sqlalchemy.exc import IntegrityError

    for codes in (["soy", "dairy"], ["dairy", "dairy"], [], ["  "]):
        with pytest.raises(IntegrityError):
            with _engine().begin() as connection:
                _insert(
                    connection,
                    _insert_user(connection),
                    value_numeric=None,
                    value_unit=None,
                    value_codes=codes,
                )

    with _engine().begin() as connection:
        _insert(
            connection,
            _insert_user(connection),
            observation_type="preference",
            code="food_allergies",
            value_numeric=None,
            value_unit=None,
            value_codes=["dairy", "soy"],
        )


def test_a_client_observation_id_is_unique_per_user() -> None:
    from sqlalchemy.exc import IntegrityError

    client_id = uuid.uuid4()
    with pytest.raises(IntegrityError):
        with _engine().begin() as connection:
            user_id = _insert_user(connection)
            _insert(connection, user_id, client_observation_id=client_id)
            _insert(connection, user_id, client_observation_id=client_id)

    # The same client id belonging to a different user is not a conflict.
    with _engine().begin() as connection:
        _insert(connection, _insert_user(connection), client_observation_id=client_id)
        _insert(connection, _insert_user(connection), client_observation_id=client_id)


def test_a_correction_chain_cannot_fork() -> None:
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        with _engine().begin() as connection:
            user_id = _insert_user(connection)
            original = _insert(connection, user_id)
            _insert(connection, user_id, supersedes_id=original)
            _insert(connection, user_id, supersedes_id=original)


def test_a_correction_must_match_the_prior_user_type_and_code() -> None:
    from sqlalchemy.exc import DBAPIError

    # Another user's observation.
    with pytest.raises(DBAPIError, match="same user"):
        with _engine().begin() as connection:
            owner = _insert_user(connection)
            other = _insert_user(connection)
            theirs = _insert(connection, other)
            _insert(connection, owner, supersedes_id=theirs)

    # The same user, but a different code.
    with pytest.raises(DBAPIError, match="same user"):
        with _engine().begin() as connection:
            user_id = _insert_user(connection)
            original = _insert(connection, user_id, code="cramps")
            _insert(connection, user_id, code="fatigue", supersedes_id=original)

    with _engine().begin() as connection:
        user_id = _insert_user(connection)
        original = _insert(connection, user_id, code="cramps")
        _insert(connection, user_id, code="cramps", supersedes_id=original)


def test_observations_are_immutable() -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError, match="immutable"):
        with _engine().begin() as connection:
            user_id = _insert_user(connection)
            observation_id = _insert(connection, user_id)
            connection.execute(
                text(
                    "UPDATE app.user_observations SET value_numeric = 9 "
                    "WHERE id = :id"
                ),
                {"id": observation_id},
            )


def test_a_non_user_source_must_name_its_origin() -> None:
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        with _engine().begin() as connection:
            _insert(
                connection,
                _insert_user(connection),
                source="onboarding_assessment",
                source_id=None,
            )

    with pytest.raises(IntegrityError):
        with _engine().begin() as connection:
            _insert(
                connection, _insert_user(connection), source="user",
                source_id=uuid.uuid4(),
            )


def test_the_live_view_excludes_superseded_rows() -> None:
    from sqlalchemy import text

    with _engine().begin() as connection:
        user_id = _insert_user(connection)
        original = _insert(connection, user_id, value_numeric=3)
        correction = _insert(connection, user_id, value_numeric=8, supersedes_id=original)

        live = connection.execute(
            text(
                "SELECT id FROM app.user_observations_live WHERE user_id = :user_id"
            ),
            {"user_id": user_id},
        ).scalars().all()
        every = connection.execute(
            text("SELECT id FROM app.user_observations WHERE user_id = :user_id"),
            {"user_id": user_id},
        ).scalars().all()

    assert set(live) == {correction}
    assert set(every) == {original, correction}


def test_an_unknown_observation_type_is_rejected() -> None:
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        with _engine().begin() as connection:
            _insert(connection, _insert_user(connection), observation_type="vibes")


@pytest.mark.anyio
async def test_writing_and_reading_current_state_round_trips() -> None:
    """Current state and history are the same rows read two ways."""
    from app.v2.application.contracts import ObservationValue, ObservationWriteRequest
    from app.v2.application.observations import (
        current_observations,
        list_observations,
        record_observation,
    )
    from app.v2.domain.identity import VerifiedPrincipal
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    with _engine().begin() as connection:
        user_id = _insert_user(connection)
        subject = connection.execute(
            __import__("sqlalchemy").text(
                "SELECT auth_subject FROM app.users WHERE id = :id"
            ),
            {"id": user_id},
        ).scalar_one()

    principal = VerifiedPrincipal(
        auth_provider="firebase",
        subject=subject,
        email=None,
        email_verified=True,
        display_name=None,
    )
    now = datetime.now(UTC)

    async def write(code, observation_type, value, observed_at, supersedes=None):
        async with SqlAlchemyUnitOfWork() as uow:
            return await record_observation(
                uow,
                principal=principal,
                request=ObservationWriteRequest(
                    client_observation_id=uuid.uuid4(),
                    observation_type=observation_type,
                    code=code,
                    observed_at=observed_at,
                    value=value,
                    supersedes_observation_id=supersedes,
                ),
                key=f"k-{uuid.uuid4()}",
            )

    await write("height_cm", "body_metric", ObservationValue(numeric=165, unit="cm"), now)
    old = await write(
        "weight_kg", "body_metric", ObservationValue(numeric=70, unit="kg"),
        now - timedelta(days=30),
    )
    await write(
        "weight_kg", "body_metric", ObservationValue(numeric=60, unit="kg"), now
    )
    await write(
        "diet_preference", "preference", ObservationValue(codes=["vegan"]), now
    )

    async with SqlAlchemyUnitOfWork() as uow:
        current = await current_observations(
            uow, principal=principal, observation_type="body_metric"
        )
    by_code = {entry.code: entry for entry in current.entries}
    # Latest weight wins; the older one is still a live historical row.
    assert by_code["weight_kg"].value.numeric == 60.0
    assert current.derived.bmi == 22.0
    assert current.derived.bmi_band == "typical_range"

    async with SqlAlchemyUnitOfWork() as uow:
        history = await list_observations(
            uow, principal=principal, code="weight_kg"
        )
    assert [o.value.numeric for o in history.observations] == [60.0, 70.0]
    assert old.observation_id in {o.observation_id for o in history.observations}


@pytest.mark.anyio
async def test_an_invalid_value_is_rejected_before_it_reaches_the_database() -> None:
    from app.v2.application.contracts import ObservationValue, ObservationWriteRequest
    from app.v2.application.errors import ApplicationProblem
    from app.v2.application.observations import record_observation
    from app.v2.domain.identity import VerifiedPrincipal
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    with _engine().begin() as connection:
        user_id = _insert_user(connection)
        subject = connection.execute(
            __import__("sqlalchemy").text(
                "SELECT auth_subject FROM app.users WHERE id = :id"
            ),
            {"id": user_id},
        ).scalar_one()

    principal = VerifiedPrincipal(
        auth_provider="firebase",
        subject=subject,
        email=None,
        email_verified=True,
        display_name=None,
    )
    with pytest.raises(ApplicationProblem) as problem:
        async with SqlAlchemyUnitOfWork() as uow:
            await record_observation(
                uow,
                principal=principal,
                request=ObservationWriteRequest(
                    client_observation_id=uuid.uuid4(),
                    observation_type="body_metric",
                    code="weight_kg",
                    observed_at=datetime.now(UTC),
                    value=ObservationValue(numeric=5000, unit="kg"),
                ),
                key=f"k-{uuid.uuid4()}",
            )
    assert problem.value.code == "observation_invalid"
