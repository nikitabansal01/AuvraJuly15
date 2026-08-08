"""Progress and insight aggregates over the canonical ledgers.

No new tables and no materialized projection. A user accumulates at most about
1,460 review items, 365 streak days and 52 check-ins a year, and every query
here is single-user with a leading ``user_id`` index, so a projection would buy
microseconds and cost a reconciliation surface that can silently drift.

One thing worth stating plainly: closed-day adherence comes from
``daily_review_items``, the immutable adjudication, not from
``action_item_events``, which are provisional and resolved latest-wins. Reading
history from events while streaks are adjudicated from reviews would let the
adherence chart and the streak disagree about the same day.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.v2.application.contracts import (
    AdherenceBucket,
    CategoryAdherence,
    InsightsSummaryResponse,
    ProgressReportResponse,
    ProgressTotals,
    SymptomPattern,
    SymptomPatternsResponse,
    WeeklyTrendPoint,
    WeeklyTrendsResponse,
)
from app.v2.application.errors import not_found, unprocessable_content
from app.v2.application.rewards import qualifying_streak_days
from app.v2.domain.cycle_policy import evaluate as evaluate_cycle
from app.v2.domain.engagement_policy import closed_streak_length
from app.v2.domain.identity import VerifiedPrincipal
from app.v2.domain.insights import (
    ALL,
    Bucket,
    MIN_OBSERVATIONS_FOR_PATTERN,
    PERIODS,
    bucket_end_for,
    bucket_start_for,
    is_sufficient,
    longest_run,
    range_error,
    ratio,
)
from app.v2.persistence.models import ActionPlanItem
from app.v2.persistence.models_engagement import (
    DailyReview,
    DailyReviewItem,
    PlanRefresh,
    RewardLedger,
    StreakLedger,
    WeeklyCheckin,
    WeeklyQuestion,
    WeeklyResponse,
)
from app.v2.persistence.models_observations import UserObservation
from app.v2.persistence.uow import SqlAlchemyUnitOfWork


DEFAULT_REPORT_DAYS = 28


async def _user_and_local_date(uow, principal, now):
    user = await uow.users.get_by_subject(principal.auth_provider, principal.subject)
    if user is None or user.status != "active":
        raise not_found("User")
    profile = await uow.profiles.get(user.id)
    if profile is None:
        raise not_found("Profile")
    return user, profile.timezone, now.astimezone(ZoneInfo(profile.timezone)).date()


def _resolved_range(
    *, start: date | None, end: date | None, today: date
) -> tuple[date, date]:
    resolved_end = end or today
    resolved_start = start or resolved_end - timedelta(days=DEFAULT_REPORT_DAYS - 1)
    problem = range_error(start=resolved_start, end=resolved_end)
    if problem is not None:
        raise unprocessable_content("report_range", problem)
    return resolved_start, resolved_end


async def _closed_day_adherence(
    session, user_id: uuid.UUID, start: date, end: date
) -> dict[date, tuple[int, int]]:
    """Completed and eligible counts per closed local day, from the reviews."""

    rows = (
        await session.execute(
            select(
                DailyReview.local_date,
                func.count()
                .filter(DailyReviewItem.outcome == "completed")
                .label("completed"),
                func.count().label("eligible"),
            )
            .join(DailyReviewItem, DailyReviewItem.daily_review_id == DailyReview.id)
            .where(
                DailyReview.user_id == user_id,
                DailyReview.status == "completed",
                DailyReview.local_date.between(start, end),
            )
            .group_by(DailyReview.local_date)
        )
    ).all()
    return {row.local_date: (row.completed, row.eligible) for row in rows}


async def _streak_states(
    session, user_id: uuid.UUID, start: date, end: date
) -> dict[date, str]:
    rows = (
        await session.execute(
            select(StreakLedger.local_date, StreakLedger.adjudication_state).where(
                StreakLedger.user_id == user_id,
                StreakLedger.kind == "daily",
                StreakLedger.local_date.between(start, end),
            )
        )
    ).all()
    return {local_date: state for local_date, state in rows}


def _accumulate_buckets(
    *,
    adherence: dict[date, tuple[int, int]],
    states: dict[date, str],
    window_start: date,
    window_end: date,
    grain: str,
) -> dict[date, dict[str, int]]:
    """Fold each closed local day into the bucket that owns it."""

    buckets: dict[date, dict[str, int]] = {}
    cursor = window_start
    while cursor <= window_end:
        key = window_start if grain == ALL else bucket_start_for(cursor, grain)
        slot = buckets.setdefault(
            key,
            {"completed": 0, "eligible": 0, "earned": 0, "frozen": 0, "missed": 0},
        )
        completed, eligible = adherence.get(cursor, (0, 0))
        slot["completed"] += completed
        slot["eligible"] += eligible
        state = states.get(cursor)
        if state in ("earned", "frozen", "missed"):
            slot[state] += 1
        cursor += timedelta(days=1)
    return buckets


def _as_buckets(
    buckets: dict[date, dict[str, int]],
    *,
    grain: str,
    window_start: date,
    window_end: date,
    today: date,
) -> list[Bucket]:
    ordered = []
    for key in sorted(buckets):
        slot = buckets[key]
        bucket_end = (
            window_end if grain == ALL else min(bucket_end_for(key, grain), window_end)
        )
        ordered.append(
            Bucket(
                bucket_start=key,
                bucket_end=bucket_end,
                completed=slot["completed"],
                eligible=slot["eligible"],
                days_earned=slot["earned"],
                days_frozen=slot["frozen"],
                days_missed=slot["missed"],
                # The current local day is still open, so any figure that
                # includes it is provisional.
                is_provisional=key <= today <= bucket_end,
            )
        )
    return ordered


async def _progress_totals(
    session, user_id: uuid.UUID, today: date, buckets: list[Bucket]
) -> ProgressTotals:
    qualifying = await qualifying_streak_days(session, user_id, today)
    points = (
        await session.scalar(
            select(func.coalesce(func.sum(RewardLedger.quantity), 0)).where(
                RewardLedger.user_id == user_id, RewardLedger.asset_type == "points"
            )
        )
    ) or 0
    refreshes = (
        await session.scalar(
            select(func.count())
            .select_from(PlanRefresh)
            .where(
                PlanRefresh.user_id == user_id,
                PlanRefresh.local_date == today,
                PlanRefresh.accepted_at.is_not(None),
            )
        )
    ) or 0
    completed = sum(b.completed for b in buckets)
    eligible = sum(b.eligible for b in buckets)
    return ProgressTotals(
        completed=completed,
        eligible=eligible,
        adherence=ratio(completed, eligible),
        current_streak_days=closed_streak_length(qualifying, current_local_date=today),
        longest_streak_days=longest_run(qualifying),
        reward_points=points,
        refreshes_used=refreshes,
    )


async def progress_report(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    period: str = "week",
    start: date | None = None,
    end: date | None = None,
    now: datetime | None = None,
) -> ProgressReportResponse:
    """One route with a period parameter, not three routes at three grains."""

    now = now or datetime.now(UTC)
    if period not in PERIODS:
        raise unprocessable_content(
            "report_period", "Period must be week, month or all."
        )
    user, timezone, today = await _user_and_local_date(uow, principal, now)
    window_start, window_end = _resolved_range(start=start, end=end, today=today)

    session = uow.session
    buckets = _as_buckets(
        _accumulate_buckets(
            adherence=await _closed_day_adherence(
                session, user.id, window_start, window_end
            ),
            states=await _streak_states(session, user.id, window_start, window_end),
            window_start=window_start,
            window_end=window_end,
            grain=period,
        ),
        grain=period,
        window_start=window_start,
        window_end=window_end,
        today=today,
    )
    return ProgressReportResponse(
        period=period,
        timezone=timezone,
        range_start=window_start,
        range_end=window_end,
        buckets=[
            AdherenceBucket(
                bucket_start=b.bucket_start,
                bucket_end=b.bucket_end,
                completed=b.completed,
                eligible=b.eligible,
                adherence=b.adherence,
                days_earned=b.days_earned,
                days_frozen=b.days_frozen,
                days_missed=b.days_missed,
                is_provisional=b.is_provisional,
            )
            for b in buckets
        ],
        totals=await _progress_totals(session, user.id, today, buckets),
    )


async def _category_adherence(
    session, user_id: uuid.UUID, start: date, end: date
) -> list[CategoryAdherence]:
    rows = (
        await session.execute(
            select(
                ActionPlanItem.category,
                func.count()
                .filter(DailyReviewItem.outcome == "completed")
                .label("completed"),
                func.count().label("presented"),
            )
            .select_from(DailyReviewItem)
            .join(DailyReview, DailyReview.id == DailyReviewItem.daily_review_id)
            .join(ActionPlanItem, ActionPlanItem.id == DailyReviewItem.plan_item_id)
            .where(
                DailyReview.user_id == user_id,
                DailyReview.status == "completed",
                DailyReview.local_date.between(start, end),
            )
            .group_by(ActionPlanItem.category)
            .order_by(ActionPlanItem.category)
        )
    ).all()
    return [
        CategoryAdherence(
            category=row.category,
            rate=ratio(row.completed, row.presented),
            presented=row.presented,
        )
        for row in rows
    ]


async def _symptom_patterns(
    session, user_id: uuid.UUID, start: date, end: date
) -> list[SymptomPattern]:
    """Occurrence and mean severity per symptom over the live observations."""

    superseding = select(UserObservation.supersedes_id).where(
        UserObservation.supersedes_id.is_not(None)
    )
    rows = (
        await session.execute(
            select(
                UserObservation.code,
                func.count().label("occurrences"),
                func.avg(UserObservation.value_numeric).label("mean_severity"),
            )
            .where(
                UserObservation.user_id == user_id,
                UserObservation.observation_type == "symptom",
                UserObservation.observed_local_date.between(start, end),
                UserObservation.id.not_in(superseding),
            )
            .group_by(UserObservation.code)
            .order_by(func.count().desc(), UserObservation.code)
        )
    ).all()
    return [
        SymptomPattern(
            code=row.code,
            occurrences=row.occurrences,
            mean_severity=(
                float(row.mean_severity) if row.mean_severity is not None else None
            ),
            sufficient=is_sufficient(row.occurrences),
        )
        for row in rows
    ]


async def symptom_patterns(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    start: date | None = None,
    end: date | None = None,
    now: datetime | None = None,
) -> SymptomPatternsResponse:
    now = now or datetime.now(UTC)
    user, _, today = await _user_and_local_date(uow, principal, now)
    window_start, window_end = _resolved_range(start=start, end=end, today=today)
    patterns = await _symptom_patterns(uow.session, user.id, window_start, window_end)
    return SymptomPatternsResponse(
        range_start=window_start,
        range_end=window_end,
        minimum_observations=MIN_OBSERVATIONS_FOR_PATTERN,
        patterns=patterns,
    )


async def weekly_trends(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    start: date | None = None,
    end: date | None = None,
    now: datetime | None = None,
) -> WeeklyTrendsResponse:
    """Scale answers over time.

    The numeric cast is safe because ck_weekly_scale_answer_numeric guarantees
    a scale answer is a number; the application-layer validator alone would not
    survive a backfill.
    """

    now = now or datetime.now(UTC)
    user, _, today = await _user_and_local_date(uow, principal, now)
    window_start, window_end = _resolved_range(start=start, end=end, today=today)

    rows = (
        await uow.session.execute(
            select(
                WeeklyCheckin.week_start,
                WeeklyQuestion.ordinal,
                WeeklyQuestion.prompt,
                WeeklyResponse.answer["value"].astext.cast(func.numeric().type),
            )
            .select_from(WeeklyResponse)
            .join(WeeklyCheckin, WeeklyCheckin.id == WeeklyResponse.weekly_checkin_id)
            .join(WeeklyQuestion, WeeklyQuestion.id == WeeklyResponse.question_id)
            .where(
                WeeklyCheckin.user_id == user.id,
                WeeklyCheckin.completed_at.is_not(None),
                WeeklyQuestion.answer_type == "scale",
                WeeklyCheckin.week_start.between(window_start, window_end),
            )
            .order_by(WeeklyCheckin.week_start, WeeklyQuestion.ordinal)
        )
    ).all()

    return WeeklyTrendsResponse(
        range_start=window_start,
        range_end=window_end,
        points=[
            WeeklyTrendPoint(
                week_start=week_start,
                ordinal=ordinal,
                prompt=prompt,
                value=float(value) if value is not None else None,
            )
            for week_start, ordinal, prompt, value in rows
        ],
    )


async def insights_summary(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    now: datetime | None = None,
) -> InsightsSummaryResponse:
    now = now or datetime.now(UTC)
    user, timezone, today = await _user_and_local_date(uow, principal, now)
    window_start = today - timedelta(days=DEFAULT_REPORT_DAYS - 1)
    session = uow.session

    categories = await _category_adherence(session, user.id, window_start, today)
    patterns = await _symptom_patterns(session, user.id, window_start, today)

    # Phase is derived per observed day rather than stored on the observation,
    # because a corrected period date must change the answer retroactively.
    starts = list(
        (
            await session.scalars(
                select(UserObservation.observed_local_date).where(
                    UserObservation.user_id == user.id,
                    UserObservation.code == "period_start",
                )
            )
        ).all()
    )
    phase_days: dict[str, int] = {}
    cursor = window_start
    while cursor <= today:
        phase = evaluate_cycle(
            period_starts=starts, declared_bucket=None, as_of_local_date=cursor
        ).phase
        if phase:
            phase_days[phase] = phase_days.get(phase, 0) + 1
        cursor += timedelta(days=1)

    observed_days = await session.scalar(
        select(func.count(func.distinct(DailyReview.local_date))).where(
            DailyReview.user_id == user.id,
            DailyReview.status == "completed",
            DailyReview.local_date.between(window_start, today),
        )
    )
    return InsightsSummaryResponse(
        generated_for_local_date=today,
        timezone=timezone,
        range_start=window_start,
        range_end=today,
        adherence_by_category=categories,
        top_symptoms=patterns[:5],
        phase_distribution=[
            {"phase": phase, "days": days}
            for phase, days in sorted(phase_days.items())
        ],
        days_observed=observed_days or 0,
        sufficient=is_sufficient(observed_days or 0),
    )
