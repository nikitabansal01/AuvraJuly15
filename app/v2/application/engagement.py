"""Authenticated engagement vertical slices, all committed through the v2 UoW."""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.v2.application.contracts import (
    ActionEventRequest,
    ActionEventResponse,
    DailyReviewRequest,
    DailyReviewResponse,
    ProgressSummaryResponse,
    SymptomObservationRequest,
    SymptomObservationResponse,
)
from app.v2.application.errors import (
    conflict,
    not_found,
    precondition_failed,
    unprocessable_content,
)
from app.v2.application.services import _begin_idempotent, _complete_idempotent
from app.v2.domain.engagement_policy import (
    closed_streak_length,
    daily_review_state,
    is_closed_local_day,
    reward_points_for_streak_state,
)
from app.v2.domain.identity import VerifiedPrincipal
from app.v2.persistence.models import ActionPlan, ActionPlanItem, User
from app.v2.persistence.models_engagement import (
    ActionEvent,
    DailyReview,
    DailyReviewItem,
    PlanRefresh,
    RewardLedger,
    StreakLedger,
    SymptomObservation,
)
from app.v2.persistence.uow import SqlAlchemyUnitOfWork


async def _user(uow: SqlAlchemyUnitOfWork, principal: VerifiedPrincipal) -> User:
    user = await uow.users.get_by_subject(principal.auth_provider, principal.subject)
    if user is None or user.status != "active":
        raise not_found("Resource")
    return user


async def _owned_plan(
    session,
    *,
    user_id: uuid.UUID,
    plan_id: uuid.UUID,
    revision: int,
    lock: bool,
) -> ActionPlan:
    statement = select(ActionPlan).where(
        ActionPlan.id == plan_id,
        ActionPlan.user_id == user_id,
    )
    if lock:
        statement = statement.with_for_update()
    plan = await session.scalar(statement)
    if plan is None:
        raise not_found("Plan")
    if plan.revision != revision:
        raise precondition_failed("The plan has changed; fetch its current ETag.")
    return plan


async def _owned_plan_item(
    session,
    *,
    user_id: uuid.UUID,
    plan_id: uuid.UUID,
    item_id: uuid.UUID,
    revision: int,
    lock: bool,
) -> tuple[ActionPlan, ActionPlanItem]:
    plan = await _owned_plan(
        session,
        user_id=user_id,
        plan_id=plan_id,
        revision=revision,
        lock=lock,
    )
    statement = select(ActionPlanItem).where(
        ActionPlanItem.id == item_id,
        ActionPlanItem.plan_id == plan.id,
        ActionPlanItem.status == "active",
    )
    if lock:
        statement = statement.with_for_update()
    item = await session.scalar(statement)
    if item is None:
        raise not_found("Plan item")
    return plan, item


def _same_action_event(
    event: ActionEvent,
    plan_item_id: uuid.UUID,
    request: ActionEventRequest,
) -> bool:
    return (
        event.plan_item_id == plan_item_id
        and event.event_type == request.event_type
        and event.occurred_at == request.occurred_at
        and event.payload == request.payload
    )


def _action_event_response(event: ActionEvent) -> ActionEventResponse:
    return ActionEventResponse(
        event_id=event.id,
        plan_item_id=event.plan_item_id,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        decision_local_date=event.decision_local_date,
        decision_timezone=event.decision_timezone,
    )


def _require_complete_review_items(
    request: DailyReviewRequest,
    plan_items: list[ActionPlanItem],
) -> None:
    expected = {item.id for item in plan_items}
    supplied = {item.plan_item_id for item in request.items}
    if not expected:
        raise conflict(
            "review_has_no_eligible_items", "The plan has no active items to review."
        )
    if supplied != expected:
        raise unprocessable_content(
            "daily_review_items_mismatch",
            "A Daily Review must answer every active item in exactly its plan revision.",
        )


async def _ensure_review_does_not_exist(session, plan_id: uuid.UUID) -> None:
    existing = await session.scalar(
        select(DailyReview).where(DailyReview.plan_id == plan_id)
    )
    if existing is not None:
        raise conflict(
            "daily_review_already_completed",
            "This plan already has an immutable Daily Review.",
        )


async def record_action_event(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    plan_id: uuid.UUID,
    item_id: uuid.UUID,
    revision: int,
    key: str,
    request: ActionEventRequest,
    now: datetime | None = None,
) -> ActionEventResponse:
    now = now or datetime.now(UTC)
    user = await _user(uow, principal)
    decision = await _begin_idempotent(
        uow,
        scope="action.event.create",
        subject=str(user.id),
        key=key,
        payload={
            "plan_id": str(plan_id),
            "item_id": str(item_id),
            "revision": revision,
            **request.model_dump(mode="json"),
        },
        now=now,
    )
    if decision.replay_body is not None:
        return ActionEventResponse.model_validate(decision.replay_body)
    session = uow.session
    plan, item = await _owned_plan_item(
        session,
        user_id=user.id,
        plan_id=plan_id,
        item_id=item_id,
        revision=revision,
        lock=True,
    )
    if request.occurred_at > now + timedelta(minutes=5):
        raise unprocessable_content(
            "future_action_event",
            "An action event cannot be more than five minutes in the future.",
        )
    decision_local_date = request.occurred_at.astimezone(ZoneInfo(plan.timezone)).date()
    if decision_local_date != plan.local_date:
        raise unprocessable_content(
            "action_event_outside_plan_date",
            "The event must belong to the immutable local date of its plan.",
        )
    existing = await session.scalar(
        select(ActionEvent).where(
            ActionEvent.user_id == user.id,
            ActionEvent.client_event_id == request.client_event_id,
        )
    )
    if existing is not None:
        if _same_action_event(existing, item.id, request):
            response = _action_event_response(existing)
            _complete_idempotent(
                decision,
                response_status=201,
                response_body=response.model_dump(mode="json"),
            )
            await uow.commit()
            return response
        raise conflict(
            "client_event_id_reused",
            "The client event identifier already describes a different action event.",
        )
    event = ActionEvent(
        id=uuid.uuid4(),
        user_id=user.id,
        plan_item_id=item.id,
        client_event_id=request.client_event_id,
        event_type=request.event_type,
        occurred_at=request.occurred_at,
        decision_local_date=decision_local_date,
        decision_timezone=plan.timezone,
        payload=request.payload,
    )
    session.add(event)
    response = _action_event_response(event)
    _complete_idempotent(
        decision, response_status=201, response_body=response.model_dump(mode="json")
    )
    await uow.commit()
    return response


async def daily_review(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    plan_id: uuid.UUID,
    revision: int,
    key: str,
    request: DailyReviewRequest,
    now: datetime | None = None,
) -> DailyReviewResponse:
    now = now or datetime.now(UTC)
    user = await _user(uow, principal)
    session = uow.session
    decision = await _begin_idempotent(
        uow,
        scope="daily_review.submit",
        subject=str(user.id),
        key=key,
        payload={
            "plan_id": str(plan_id),
            "revision": revision,
            **request.model_dump(mode="json"),
        },
        now=now,
    )
    if decision.replay_body is not None:
        return DailyReviewResponse.model_validate(decision.replay_body)
    plan, items = await _locked_closed_review_plan(
        session, user.id, plan_id, revision, now
    )
    _require_complete_review_items(request, items)
    await _ensure_review_does_not_exist(session, plan.id)
    review = DailyReview(
        id=uuid.uuid4(),
        user_id=user.id,
        plan_id=plan.id,
        local_date=plan.local_date,
        timezone=plan.timezone,
        status="open",
        completed_at=None,
    )
    session.add(review)
    # Persist the open header before inserting answers.  The database rejects
    # mutations to answers after a review is completed, so publishing the
    # header as completed in the first flush would make its own answers fail.
    await uow.flush()
    answers = {item.plan_item_id: item for item in request.items}
    review_items = [
        DailyReviewItem(
            id=uuid.uuid4(),
            daily_review_id=review.id,
            plan_item_id=item.id,
            outcome=answers[item.id].outcome,
            note=answers[item.id].note,
            answered_at=now,
        )
        for item in items
    ]
    session.add_all(review_items)
    await uow.flush()
    review.status = "completed"
    review.completed_at = now
    completed, streak_state, points = _adjudicate_completed_review(
        session, user, plan, review, review_items, now
    )
    response = DailyReviewResponse(
        review_id=review.id,
        plan_id=plan.id,
        local_date=plan.local_date,
        timezone=plan.timezone,
        status=review.status,
        completed_count=completed,
        total_count=len(review_items),
        streak_state=streak_state,
        reward_points_granted=points,
    )
    _complete_idempotent(
        decision,
        response_status=201,
        response_body=response.model_dump(mode="json"),
    )
    await uow.commit()
    return response


async def _locked_closed_review_plan(
    session,
    user_id: uuid.UUID,
    plan_id: uuid.UUID,
    revision: int,
    now: datetime,
) -> tuple[ActionPlan, list[ActionPlanItem]]:
    plan = await _owned_plan(
        session, user_id=user_id, plan_id=plan_id, revision=revision, lock=True
    )
    if not is_closed_local_day(
        plan_date=plan.local_date,
        current_local_date=now.astimezone(ZoneInfo(plan.timezone)).date(),
    ):
        raise conflict(
            "daily_review_day_not_closed",
            "A Daily Review can be finalized only after its plan local date closes.",
        )
    items = list(
        (
            await session.scalars(
                select(ActionPlanItem)
                .where(
                    ActionPlanItem.plan_id == plan.id, ActionPlanItem.status == "active"
                )
                .order_by(ActionPlanItem.slot)
                .with_for_update()
            )
        ).all()
    )
    return plan, items


def _adjudicate_completed_review(
    session,
    user: User,
    plan: ActionPlan,
    review: DailyReview,
    review_items: list[DailyReviewItem],
    now: datetime,
) -> tuple[int, str, int]:
    completed = sum(item.outcome == "completed" for item in review_items)
    streak_state = daily_review_state(
        completed_count=completed, total_count=len(review_items)
    )
    streak = StreakLedger(
        id=uuid.uuid4(),
        user_id=user.id,
        local_date=plan.local_date,
        kind="daily",
        timezone=plan.timezone,
        evidence_type="daily_review",
        evidence_id=review.id,
        adjudication_state=streak_state,
        earned_at=now,
    )
    session.add(streak)
    points = reward_points_for_streak_state(streak_state)
    if points:
        session.add(
            RewardLedger(
                id=uuid.uuid4(),
                user_id=user.id,
                source_type="streak_day",
                source_id=streak.id,
                event_type="grant",
                asset_type="points",
                quantity=points,
                created_at=now,
            )
        )
    return completed, streak_state, points


async def record_symptom(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    key: str,
    request: SymptomObservationRequest,
    now: datetime | None = None,
) -> SymptomObservationResponse:
    now = now or datetime.now(UTC)
    user = await _user(uow, principal)
    decision = await _begin_idempotent(
        uow,
        scope="symptom.create",
        subject=str(user.id),
        key=key,
        payload=request.model_dump(mode="json"),
        now=now,
    )
    if decision.replay_body is not None:
        return SymptomObservationResponse.model_validate(decision.replay_body)
    observation = SymptomObservation(
        id=uuid.uuid4(), user_id=user.id, **request.model_dump()
    )
    uow.session.add(observation)
    _complete_idempotent(
        decision,
        response_status=201,
        response_body={"observation_id": str(observation.id)},
    )
    await uow.commit()
    return SymptomObservationResponse(observation_id=observation.id)


async def progress_summary(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    now: datetime | None = None,
) -> ProgressSummaryResponse:
    now = now or datetime.now(UTC)
    user = await _user(uow, principal)
    profile = await uow.profiles.get(user.id)
    if profile is None:
        raise not_found("Profile")
    local = now.astimezone(ZoneInfo(profile.timezone)).date()
    session = uow.session
    plan = await session.scalar(
        select(ActionPlan).where(
            ActionPlan.user_id == user.id,
            ActionPlan.local_date == local,
            ActionPlan.is_current.is_(True),
            ActionPlan.status == "ready",
        )
    )
    eligible_items = await _active_plan_items(session, plan)
    completed = await _completed_item_count(session, user.id, eligible_items)
    points = (
        await session.scalar(
            select(func.coalesce(func.sum(RewardLedger.quantity), 0)).where(
                RewardLedger.user_id == user.id, RewardLedger.asset_type == "points"
            )
        )
        or 0
    )
    days = (
        await session.scalars(
            select(StreakLedger.local_date)
            .where(
                StreakLedger.user_id == user.id,
                StreakLedger.kind == "daily",
                StreakLedger.local_date < local,
                StreakLedger.adjudication_state.in_(("earned", "frozen")),
            )
            .order_by(StreakLedger.local_date.desc())
        )
    ).all()
    streak = closed_streak_length(days, current_local_date=local)
    refreshes_used = (
        await session.scalar(
            select(func.count())
            .select_from(PlanRefresh)
            .where(
                PlanRefresh.user_id == user.id,
                PlanRefresh.local_date == local,
                PlanRefresh.accepted_at.is_not(None),
            )
        )
        or 0
    )
    adherence = completed / len(eligible_items) if eligible_items else None
    return ProgressSummaryResponse(
        local_date=local,
        completed_today=completed,
        eligible_today=len(eligible_items),
        daily_adherence=adherence,
        is_current_day_provisional=True,
        streak_days=streak,
        reward_points=points,
        refreshes_used=refreshes_used,
    )


async def _active_plan_items(session, plan: ActionPlan | None) -> list[ActionPlanItem]:
    if plan is None:
        return []
    result = await session.scalars(
        select(ActionPlanItem).where(
            ActionPlanItem.plan_id == plan.id,
            ActionPlanItem.status == "active",
        )
    )
    return list(result)


async def _completed_item_count(
    session, user_id: uuid.UUID, items: list[ActionPlanItem]
) -> int:
    if not items:
        return 0
    event_rows = (
        await session.scalars(
            select(ActionEvent)
            .where(
                ActionEvent.user_id == user_id,
                ActionEvent.plan_item_id.in_([item.id for item in items]),
            )
            .order_by(
                ActionEvent.plan_item_id,
                ActionEvent.occurred_at.desc(),
                ActionEvent.recorded_at.desc(),
                ActionEvent.id.desc(),
            )
        )
    ).all()
    latest = _latest_events_by_item(event_rows)
    return sum(event.event_type == "completed" for event in latest.values())


def _latest_events_by_item(events: list[ActionEvent]) -> dict[uuid.UUID, ActionEvent]:
    latest: dict[uuid.UUID, ActionEvent] = {}
    for event in events:
        latest.setdefault(event.plan_item_id, event)
    return latest
