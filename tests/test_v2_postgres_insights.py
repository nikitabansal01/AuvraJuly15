"""Progress and insight aggregates against real data in PostgreSQL 17."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest


pytestmark = pytest.mark.skipif(
    not os.getenv("AUVRA_TEST_DATABASE_URL"),
    reason="Insight aggregates require AUVRA_TEST_DATABASE_URL",
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


def _principal(subject: str):
    from app.v2.domain.identity import VerifiedPrincipal

    return VerifiedPrincipal(
        auth_provider="firebase",
        subject=subject,
        email=None,
        email_verified=True,
        display_name=None,
    )


def _insert_asset(connection, user_id: uuid.UUID) -> uuid.UUID:
    """Plan items require a hero asset, so give each one its own."""
    from sqlalchemy import text

    asset_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO app.media_assets (id, owner_user_id, storage_provider, "
            " bucket, object_key, public_url, content_sha256, mime_type, "
            " alt_text, status) "
            "VALUES (:id, :user_id, 'test', 'plans', :key, :url, :digest, "
            "'image/png', 'Test illustration', 'ready')"
        ),
        {
            "id": asset_id,
            "user_id": user_id,
            "key": f"test/{asset_id}.png",
            "url": f"https://assets.example.test/{asset_id}.png",
            "digest": hashlib.sha256(str(asset_id).encode()).hexdigest(),
        },
    )
    return asset_id


def _seed_history(days: int, completed_per_day: int = 3, items_per_day: int = 4):
    """Seed `days` closed days, each with an adjudicated Daily Review.

    Reviews rather than events, because closed-day adherence is read from the
    immutable adjudication.
    """
    from sqlalchemy import text

    user_id = uuid.uuid4()
    subject = f"insights-{user_id}"
    today = datetime.now(UTC).date()
    expected: dict[date, tuple[int, int]] = {}

    with _engine().begin() as connection:
        connection.execute(
            text(
                "INSERT INTO app.users (id, auth_provider, auth_subject) "
                "VALUES (:id, 'firebase', :subject)"
            ),
            {"id": user_id, "subject": subject},
        )
        connection.execute(
            text(
                "INSERT INTO app.user_profiles (user_id, timezone) "
                "VALUES (:user_id, 'UTC')"
            ),
            {"user_id": user_id},
        )
        for offset in range(1, days + 1):
            day = today - timedelta(days=offset)
            job_id, plan_id, review_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO ops.generation_jobs (id, user_id, job_type, "
                    " request_payload) VALUES (:id, :user_id, 'plan_generation', "
                    "'{}'::jsonb)"
                ),
                {"id": job_id, "user_id": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO app.action_plans (id, user_id, generation_job_id, "
                    " local_date, timezone, revision, is_current, status, "
                    " cycle_snapshot, context_snapshot) "
                    "VALUES (:id, :user_id, :job_id, :day, 'UTC', 1, false, "
                    "'archived', '{}'::jsonb, '{}'::jsonb)"
                ),
                {
                    "id": plan_id,
                    "user_id": user_id,
                    "job_id": job_id,
                    "day": day,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO app.daily_reviews (id, user_id, plan_id, "
                    " local_date, timezone, status, completed_at) "
                    "VALUES (:id, :user_id, :plan_id, :day, 'UTC', 'open', NULL)"
                ),
                {
                    "id": review_id,
                    "user_id": user_id,
                    "plan_id": plan_id,
                    "day": day,
                },
            )
            for slot in range(1, items_per_day + 1):
                item_id = uuid.uuid4()
                category = "nutrition" if slot % 2 else "movement"
                connection.execute(
                    text(
                        "INSERT INTO app.action_plan_items (id, plan_id, slot, "
                        " category, title, purpose, instructions, hero_asset_id) "
                        "VALUES (:id, :plan_id, :slot, :category, :title, "
                        "'test', '{}'::jsonb, :asset_id)"
                    ),
                    {
                        "id": item_id,
                        "plan_id": plan_id,
                        "slot": slot,
                        "category": category,
                        "title": f"Action {slot}",
                        "asset_id": _insert_asset(connection, user_id),
                    },
                )
                outcome = "completed" if slot <= completed_per_day else "skipped"
                connection.execute(
                    text(
                        "INSERT INTO app.daily_review_items (id, daily_review_id, "
                        " plan_item_id, outcome, answered_at) "
                        "VALUES (:id, :review_id, :item_id, :outcome, now())"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "review_id": review_id,
                        "item_id": item_id,
                        "outcome": outcome,
                    },
                )
            connection.execute(
                text(
                    "UPDATE app.daily_reviews SET status = 'completed', "
                    "completed_at = now() WHERE id = :id"
                ),
                {"id": review_id},
            )
            expected[day] = (completed_per_day, items_per_day)
    return subject, expected


@pytest.mark.anyio
async def test_weekly_report_matches_a_python_recomputation() -> None:
    """The reconciliation test that justifies having no materialized projection."""
    from app.v2.application.insights import progress_report
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    subject, expected = _seed_history(days=21)
    async with SqlAlchemyUnitOfWork() as uow:
        report = await progress_report(
            uow, principal=_principal(subject), period="week"
        )

    sql_completed = sum(b.completed for b in report.buckets)
    sql_eligible = sum(b.eligible for b in report.buckets)
    window = {
        day: counts
        for day, counts in expected.items()
        if report.range_start <= day <= report.range_end
    }
    assert sql_completed == sum(c for c, _ in window.values())
    assert sql_eligible == sum(e for _, e in window.values())
    assert report.totals.adherence == pytest.approx(3 / 4)
    assert all(b.bucket_start.isoweekday() == 1 for b in report.buckets)


@pytest.mark.anyio
async def test_month_and_all_grains_cover_the_same_days() -> None:
    from app.v2.application.insights import progress_report
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    subject, _ = _seed_history(days=20)
    totals = {}
    for period in ("week", "month", "all"):
        async with SqlAlchemyUnitOfWork() as uow:
            report = await progress_report(
                uow, principal=_principal(subject), period=period
            )
        totals[period] = (report.totals.completed, report.totals.eligible)
        if period == "all":
            assert len(report.buckets) == 1
    assert totals["week"] == totals["month"] == totals["all"]


@pytest.mark.anyio
async def test_a_user_with_no_history_reports_null_adherence_not_zero() -> None:
    from app.v2.application.insights import progress_report
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    subject, _ = _seed_history(days=0)
    async with SqlAlchemyUnitOfWork() as uow:
        report = await progress_report(uow, principal=_principal(subject))
    assert report.totals.eligible == 0
    assert report.totals.adherence is None
    assert all(b.adherence is None for b in report.buckets)


@pytest.mark.anyio
async def test_an_oversized_range_is_rejected() -> None:
    from app.v2.application.errors import ApplicationProblem
    from app.v2.application.insights import progress_report
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    subject, _ = _seed_history(days=1)
    with pytest.raises(ApplicationProblem) as problem:
        async with SqlAlchemyUnitOfWork() as uow:
            await progress_report(
                uow,
                principal=_principal(subject),
                start=date(2020, 1, 1),
                end=date(2026, 1, 1),
            )
    assert problem.value.code == "report_range"


@pytest.mark.anyio
async def test_category_adherence_splits_by_action_category() -> None:
    from app.v2.application.insights import insights_summary
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    subject, _ = _seed_history(days=7)
    async with SqlAlchemyUnitOfWork() as uow:
        summary = await insights_summary(uow, principal=_principal(subject))
    categories = {c.category: c for c in summary.adherence_by_category}
    assert set(categories) == {"nutrition", "movement"}
    assert summary.days_observed == 7
    assert summary.sufficient is True


@pytest.mark.anyio
async def test_symptom_patterns_flag_insufficient_data() -> None:
    from app.v2.application.contracts import ObservationValue, ObservationWriteRequest
    from app.v2.application.insights import symptom_patterns
    from app.v2.application.observations import record_observation
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    subject, _ = _seed_history(days=1)
    principal = _principal(subject)
    for offset in range(2):
        async with SqlAlchemyUnitOfWork() as uow:
            await record_observation(
                uow,
                principal=principal,
                request=ObservationWriteRequest(
                    client_observation_id=uuid.uuid4(),
                    observation_type="symptom",
                    code="cramps",
                    observed_at=datetime.now(UTC) - timedelta(days=offset),
                    value=ObservationValue(numeric=6, unit="score_0_10"),
                ),
                key=f"k-{uuid.uuid4()}",
            )

    async with SqlAlchemyUnitOfWork() as uow:
        patterns = await symptom_patterns(uow, principal=principal)
    cramps = next(p for p in patterns.patterns if p.code == "cramps")
    assert cramps.occurrences == 2
    assert cramps.mean_severity == pytest.approx(6.0)
    # Two observations is below the threshold, so it is reported as noise.
    assert cramps.sufficient is False


@pytest.mark.anyio
async def test_one_users_report_never_includes_another_users_history() -> None:
    from app.v2.application.insights import progress_report
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    _seed_history(days=20)
    quiet_subject, _ = _seed_history(days=0)
    async with SqlAlchemyUnitOfWork() as uow:
        report = await progress_report(uow, principal=_principal(quiet_subject))
    assert report.totals.completed == 0
    assert report.totals.eligible == 0
    assert report.totals.longest_streak_days == 0
