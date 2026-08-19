"""Focused behavior tests for canonical v2 engagement facts and metrics."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.v2.application.contracts import (
    ActionEventRequest,
    DailyReviewRequest,
    PlanReplacementRequest,
    SymptomObservationRequest,
)
from app.v2.application.engagement import (
    _latest_events_by_item,
    daily_review,
    record_action_event,
    record_symptom,
)
from app.v2.application.plan_replacement import replace_plan_with_selected_variant
from app.v2.application.errors import ApplicationProblem
from app.v2.application.services import _begin_idempotent
from app.v2.domain.engagement_policy import (
    closed_streak_length,
    daily_review_state,
    is_closed_local_day,
    reward_points_for_streak_state,
)
from app.v2.domain.enums import IdempotencyState, UserStatus
from app.v2.domain.identity import VerifiedPrincipal


OWNER = VerifiedPrincipal("firebase", "owner", None, False, None)
NOW = datetime(2026, 8, 9, 0, 0, tzinfo=UTC)


class FakeIdempotencyRepository:
    def __init__(self) -> None:
        self.records = {}
        self.lock = asyncio.Lock()

    async def reserve(self, record, *, now):
        key = (record.scope, record.subject, record.idempotency_key)
        async with self.lock:
            stored = self.records.get(key)
            if stored is None:
                self.records[key] = record
                return record, True
            return stored, False


class FakeUsers:
    def __init__(self, user):
        self.user = user

    async def get_by_subject(self, _, subject, *, for_update=False):
        return self.user if subject == "owner" else None


class FakeScalarResult:
    def __init__(self, values):
        self.values = values

    def all(self):
        return list(self.values)


class FakeSession:
    def __init__(self, scalar_values=(), scalar_lists=()):
        self.scalar_values = list(scalar_values)
        self.scalar_lists = list(scalar_lists)
        self.added = []

    async def scalar(self, statement):
        if not self.scalar_values:
            raise AssertionError(f"unexpected scalar query: {statement}")
        return self.scalar_values.pop(0)

    async def scalars(self, statement):
        if not self.scalar_lists:
            raise AssertionError(f"unexpected scalars query: {statement}")
        return FakeScalarResult(self.scalar_lists.pop(0))

    def add(self, value):
        self.added.append(value)

    def add_all(self, values):
        self.added.extend(values)


class FakeProfiles:
    """record_symptom reads the timezone to stamp the observation's local day."""

    def __init__(self, timezone: str = "UTC"):
        self.timezone = timezone

    async def get(self, _user_id):
        return SimpleNamespace(timezone=self.timezone)


class FakeUow:
    def __init__(self, session, user):
        self.session = session
        self.users = FakeUsers(user)
        self.profiles = FakeProfiles()
        self.idempotency = FakeIdempotencyRepository()
        self.commits = 0
        self.flushes = 0

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1


def _user():
    return SimpleNamespace(id=uuid4(), status=UserStatus.ACTIVE.value)


def _event_request(occurred_at: datetime, client_event_id: UUID | None = None):
    return ActionEventRequest(
        client_event_id=client_event_id or uuid4(),
        event_type="completed",
        occurred_at=occurred_at,
        payload={"source": "tap"},
    )


def _symptom_request():
    return SymptomObservationRequest(
        observed_at=NOW, symptom_code="cramps", severity=4, note="mild"
    )


@pytest.mark.anyio
async def test_symptom_observation_is_owner_scoped_and_replays() -> None:
    user = _user()
    session = FakeSession()
    uow = FakeUow(session, user)
    first = await record_symptom(
        uow,
        principal=OWNER,
        key="symptom-create-0001",
        request=_symptom_request(),
        now=NOW,
    )
    replay = await record_symptom(
        uow,
        principal=OWNER,
        key="symptom-create-0001",
        request=_symptom_request(),
        now=NOW,
    )
    assert first == replay
    assert first.observation_id == session.added[0].id
    assert session.added[0].user_id == user.id
    assert uow.commits == 1


def test_symptom_observation_rejects_invalid_and_client_owned_fields() -> None:
    with pytest.raises(ValidationError):
        SymptomObservationRequest.model_validate(
            {"observed_at": NOW, "symptom_code": "", "severity": 11}
        )
    with pytest.raises(ValidationError):
        SymptomObservationRequest.model_validate(
            {"observed_at": NOW, "symptom_code": "cramps", "user_id": str(uuid4())}
        )


@pytest.mark.anyio
async def test_action_event_uses_plan_timezone_at_dst_boundary() -> None:
    user = _user()
    plan = SimpleNamespace(
        id=uuid4(),
        user_id=user.id,
        revision=7,
        timezone="America/Los_Angeles",
        local_date=date(2026, 3, 8),
    )
    item = SimpleNamespace(id=uuid4(), plan_id=plan.id, status="active")
    session = FakeSession([plan, item, None])
    uow = FakeUow(session, user)
    result = await record_action_event(
        uow,
        principal=OWNER,
        plan_id=plan.id,
        item_id=item.id,
        revision=7,
        key="action-event-dst-0001",
        request=_event_request(datetime(2026, 3, 8, 10, 30, tzinfo=UTC)),
        now=datetime(2026, 3, 8, 10, 31, tzinfo=UTC),
    )
    assert result.decision_local_date == date(2026, 3, 8)
    assert result.decision_timezone == "America/Los_Angeles"
    assert session.added[0].decision_local_date == date(2026, 3, 8)


@pytest.mark.anyio
async def test_action_event_rejects_cross_user_plan_late_day_and_stale_revision() -> None:
    user = _user()
    missing = FakeUow(FakeSession([None]), user)
    with pytest.raises(ApplicationProblem, match="Plan was not found") as missing_error:
        await record_action_event(
            missing,
            principal=OWNER,
            plan_id=uuid4(),
            item_id=uuid4(),
            revision=1,
            key="action-event-owner-0001",
            request=_event_request(NOW),
            now=NOW,
        )
    assert missing_error.value.status == 404

    plan = SimpleNamespace(
        id=uuid4(),
        user_id=user.id,
        revision=2,
        timezone="Asia/Kolkata",
        local_date=date(2026, 8, 9),
    )
    stale = FakeUow(FakeSession([plan]), user)
    with pytest.raises(ApplicationProblem) as stale_error:
        await record_action_event(
            stale,
            principal=OWNER,
            plan_id=plan.id,
            item_id=uuid4(),
            revision=1,
            key="action-event-revision-0001",
            request=_event_request(NOW),
            now=NOW,
        )
    assert stale_error.value.status == 412

    item = SimpleNamespace(id=uuid4(), plan_id=plan.id, status="active")
    late = FakeUow(FakeSession([plan, item]), user)
    with pytest.raises(ApplicationProblem) as late_error:
        await record_action_event(
            late,
            principal=OWNER,
            plan_id=plan.id,
            item_id=item.id,
            revision=2,
            key="action-event-late-0001",
            request=_event_request(datetime(2026, 8, 8, 18, 29, tzinfo=UTC)),
            now=NOW,
        )
    assert late_error.value.code == "action_event_outside_plan_date"


@pytest.mark.anyio
async def test_duplicate_action_event_replays_without_second_fact() -> None:
    user = _user()
    plan = SimpleNamespace(
        id=uuid4(),
        user_id=user.id,
        revision=1,
        timezone="UTC",
        local_date=date(2026, 8, 9),
    )
    item = SimpleNamespace(id=uuid4(), plan_id=plan.id, status="active")
    request = _event_request(NOW)
    duplicate = SimpleNamespace(
        id=uuid4(),
        user_id=user.id,
        plan_item_id=item.id,
        client_event_id=request.client_event_id,
        event_type=request.event_type,
        occurred_at=request.occurred_at,
        payload=request.payload,
        decision_local_date=plan.local_date,
        decision_timezone="UTC",
    )
    session = FakeSession([plan, item, duplicate])
    uow = FakeUow(session, user)
    result = await record_action_event(
        uow,
        principal=OWNER,
        plan_id=plan.id,
        item_id=item.id,
        revision=1,
        key="action-event-duplicate-0001",
        request=request,
        now=NOW,
    )
    assert result.event_id == duplicate.id
    assert session.added == []
    assert uow.commits == 1


@pytest.mark.anyio
async def test_daily_review_is_complete_plan_scoped_and_immutable() -> None:
    user = _user()
    plan = SimpleNamespace(
        id=uuid4(),
        user_id=user.id,
        revision=1,
        timezone="Asia/Kolkata",
        local_date=date(2026, 8, 8),
    )
    items = [
        SimpleNamespace(id=uuid4(), plan_id=plan.id, status="active", slot=slot) for slot in (1, 2)
    ]
    body = DailyReviewRequest.model_validate(
        {"items": [{"plan_item_id": item.id, "outcome": "completed"} for item in items]}
    )
    session = FakeSession([plan, None], [items])
    uow = FakeUow(session, user)
    result = await daily_review(
        uow,
        principal=OWNER,
        plan_id=plan.id,
        revision=1,
        key="daily-review-0001",
        request=body,
        now=NOW,
    )
    assert result.plan_id == plan.id
    assert result.completed_count == 2
    assert result.total_count == 2
    assert uow.commits == 1
    assert uow.flushes == 2
    assert len(session.added) == 5
    review = session.added[0]
    assert review.status == "completed"
    assert review.completed_at == NOW
    assert result.streak_state == "earned"
    assert result.reward_points_granted == 1
    assert session.added[-2].evidence_id == review.id
    assert session.added[-1].source_id == session.added[-2].id

    bad_body = {"items": [{"plan_item_id": items[0].id, "outcome": "completed"}]}
    with pytest.raises(ValidationError):
        DailyReviewRequest.model_validate({"items": [bad_body["items"][0], bad_body["items"][0]]})


def test_metrics_use_latest_events_and_only_closed_qualifying_days() -> None:
    first_item, second_item = uuid4(), uuid4()
    newest = SimpleNamespace(plan_item_id=first_item, event_type="skipped")
    older = SimpleNamespace(plan_item_id=first_item, event_type="completed")
    completed = SimpleNamespace(plan_item_id=second_item, event_type="completed")
    assert _latest_events_by_item([newest, older, completed]) == {
        first_item: newest,
        second_item: completed,
    }
    assert (
        closed_streak_length(
            [date(2026, 8, 8), date(2026, 8, 7)], current_local_date=date(2026, 8, 9)
        )
        == 2
    )
    assert (
        closed_streak_length(
            [date(2026, 8, 9), date(2026, 8, 8)], current_local_date=date(2026, 8, 9)
        )
        == 1
    )


def test_daily_adjudication_policy_is_closed_day_only_and_freeze_qualifies() -> None:
    assert is_closed_local_day(plan_date=date(2026, 3, 8), current_local_date=date(2026, 3, 9))
    assert not is_closed_local_day(plan_date=date(2026, 3, 8), current_local_date=date(2026, 3, 8))
    assert daily_review_state(completed_count=4, total_count=4) == "earned"
    assert daily_review_state(completed_count=3, total_count=4) == "missed"
    assert reward_points_for_streak_state("earned") == 1
    assert reward_points_for_streak_state("missed") == 0
    assert (
        closed_streak_length(
            [date(2026, 3, 8), date(2026, 3, 7), date(2026, 3, 5)],
            current_local_date=date(2026, 3, 9),
        )
        == 2
    )
    assert (
        closed_streak_length(
            [date(2026, 3, 8), date(2026, 3, 6)], current_local_date=date(2026, 3, 9)
        )
        == 1
    )


@pytest.mark.anyio
async def test_closed_daily_review_replays_one_streak_and_one_reward() -> None:
    user = _user()
    plan = SimpleNamespace(
        id=uuid4(),
        user_id=user.id,
        revision=1,
        timezone="America/Los_Angeles",
        local_date=date(2026, 3, 8),
    )
    items = [SimpleNamespace(id=uuid4(), plan_id=plan.id, status="active", slot=1)]
    request = DailyReviewRequest.model_validate(
        {"items": [{"plan_item_id": items[0].id, "outcome": "completed"}]}
    )
    uow = FakeUow(FakeSession([plan, None], [items]), user)
    first = await daily_review(
        uow,
        principal=OWNER,
        plan_id=plan.id,
        revision=1,
        key="review-dst-replay-0001",
        request=request,
        now=datetime(2026, 3, 9, 8, 0, tzinfo=UTC),
    )
    replay = await daily_review(
        uow,
        principal=OWNER,
        plan_id=plan.id,
        revision=1,
        key="review-dst-replay-0001",
        request=request,
        now=datetime(2026, 3, 9, 8, 0, tzinfo=UTC),
    )
    assert first == replay
    assert len([row for row in uow.session.added if row.__class__.__name__ == "StreakLedger"]) == 1
    assert len([row for row in uow.session.added if row.__class__.__name__ == "RewardLedger"]) == 1


@pytest.mark.anyio
async def test_selected_variant_replacement_creates_one_successor_revision() -> None:
    user = _user()
    plan = SimpleNamespace(
        id=uuid4(),
        user_id=user.id,
        revision=3,
        timezone="UTC",
        local_date=date(2026, 8, 9),
        is_current=True,
        status="ready",
        cycle_snapshot={},
        context_snapshot={},
    )
    items = [
        SimpleNamespace(
            id=uuid4(),
            plan_id=plan.id,
            slot=slot,
            status="active",
            category="wellness",
            title=f"Action {slot}",
            purpose="purpose",
            instructions={"steps": ["original"]},
            hero_asset_id=uuid4(),
        )
        for slot in range(1, 5)
    ]
    copied_variants = [
        [
            SimpleNamespace(id=uuid4(), variant_type=f"v{index}", content={}, asset_id=uuid4())
            for index in range(1, 4)
        ]
        for _ in items
    ]
    selected = copied_variants[0][0]
    selected.item_id = items[0].id
    selected.content = {"title": "Selected", "instructions": ["variant"]}
    query_lists = [items]
    for variants in copied_variants:
        query_lists.extend((variants, []))
    session = FakeSession([plan, None, selected], query_lists)
    uow = FakeUow(session, user)
    result = await replace_plan_with_selected_variant(
        uow,
        principal=OWNER,
        plan_id=plan.id,
        revision=3,
        key="replace-selected-variant-0001",
        request=PlanReplacementRequest(
            item_id=items[0].id,
            selected_variant_id=selected.id,
            reason="not_a_fit",
        ),
        now=NOW,
    )
    assert result.revision == 4
    assert result.old_item_id == items[0].id
    assert result.replacement_mode == "selected_variant"
    assert plan.status == "archived"
    assert uow.commits == 1


@pytest.mark.anyio
async def test_concurrent_idempotency_reservation_has_one_winner() -> None:
    uow = SimpleNamespace(idempotency=FakeIdempotencyRepository())

    async def reserve():
        return await _begin_idempotent(
            uow,
            scope="engagement.test",
            subject="owner",
            key="same-key-0001",
            payload={},
            now=NOW,
        )

    outcomes = await asyncio.gather(reserve(), reserve(), return_exceptions=True)
    assert sum(isinstance(outcome, Exception) for outcome in outcomes) == 1
    assert outcomes[0].record.state == IdempotencyState.STARTED.value
