"""Rewards, entitlements and streak freezes over the canonical ledgers.

Nothing here stores a derived value. Eligibility is a longest-run query over
``app.streak_days``; balances are a sum over ``app.reward_ledger``. The legacy
system kept both as counter columns that could disagree with the facts they
came from, which is the specific failure this module exists to avoid.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.v2.application.contracts import (
    RewardBalance,
    RewardClaimResponse,
    RewardGrant,
    RewardState,
    RewardsOverviewResponse,
    StreakFreezeRequest,
    StreakFreezeResponse,
)
from app.v2.application.errors import conflict, not_found, unprocessable_content
from app.v2.application.services import _begin_idempotent, _complete_idempotent
from app.v2.domain.engagement_policy import closed_streak_length
from app.v2.domain.identity import VerifiedPrincipal
from app.v2.domain.reward_catalog import (
    ASSET_FREEZE,
    CATALOG_VERSION,
    FREEZE_ASSET_KEY,
    REWARD_CATALOG,
    Reward,
    freeze_rejection_reason,
    is_eligible,
    longest_run,
    reward_or_none,
)
from app.v2.persistence.models import User
from app.v2.persistence.models_engagement import RewardLedger, StreakLedger
from app.v2.persistence.uow import SqlAlchemyUnitOfWork


#: Deterministic namespace so one user claiming one reward under one catalog
#: version always produces the same ledger source_id. The existing
#: uq_reward_ledger_user_id_source_type_source_id then makes a repeated claim
#: idempotent by construction rather than by bookkeeping.
_REWARD_CLAIM_NAMESPACE = uuid.UUID("5f6f1c6e-3b4a-4d2f-9a3e-2c9c1f0b7a51")
_CLAIM_SOURCE_TYPE = "reward_claim"
_FREEZE_SOURCE_TYPE = "streak_freeze"


def _claim_source_id(user_id: uuid.UUID, reward: Reward) -> uuid.UUID:
    return uuid.uuid5(
        _REWARD_CLAIM_NAMESPACE, f"{user_id}:{reward.reward_id}:{CATALOG_VERSION}"
    )


async def _user_and_local_date(
    uow: SqlAlchemyUnitOfWork, principal: VerifiedPrincipal, now: datetime
) -> tuple[User, date]:
    user = await uow.users.get_by_subject(principal.auth_provider, principal.subject)
    if user is None or user.status != "active":
        raise not_found("User")
    profile = await uow.profiles.get(user.id)
    if profile is None:
        raise not_found("Profile")
    return user, now.astimezone(ZoneInfo(profile.timezone)).date()


async def qualifying_streak_days(session, user_id: uuid.UUID, before: date) -> list[date]:
    """Finalized local dates that count toward a streak, newest first."""

    return list(
        (
            await session.scalars(
                select(StreakLedger.local_date)
                .where(
                    StreakLedger.user_id == user_id,
                    StreakLedger.kind == "daily",
                    StreakLedger.local_date < before,
                    StreakLedger.adjudication_state.in_(("earned", "frozen")),
                )
                .order_by(StreakLedger.local_date.desc())
            )
        ).all()
    )


async def _balances(session, user_id: uuid.UUID) -> list[RewardBalance]:
    rows = (
        await session.execute(
            select(
                RewardLedger.asset_type,
                RewardLedger.asset_key,
                func.sum(RewardLedger.quantity),
            )
            .where(RewardLedger.user_id == user_id)
            .group_by(RewardLedger.asset_type, RewardLedger.asset_key)
            .order_by(RewardLedger.asset_type, RewardLedger.asset_key)
        )
    ).all()
    return [
        RewardBalance(asset_type=asset_type, asset_key=asset_key, balance=int(total))
        for asset_type, asset_key, total in rows
    ]


async def _claimed_at_by_reward(
    session, user_id: uuid.UUID
) -> dict[uuid.UUID, datetime]:
    rows = (
        await session.execute(
            select(RewardLedger.source_id, RewardLedger.created_at).where(
                RewardLedger.user_id == user_id,
                RewardLedger.source_type == _CLAIM_SOURCE_TYPE,
                RewardLedger.event_type == "grant",
            )
        )
    ).all()
    return {source_id: created_at for source_id, created_at in rows}


async def rewards_overview(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    now: datetime | None = None,
) -> RewardsOverviewResponse:
    now = now or datetime.now(UTC)
    user, local = await _user_and_local_date(uow, principal, now)
    session = uow.session

    days = await qualifying_streak_days(session, user.id, local)
    best = longest_run(days)
    current = closed_streak_length(days, current_local_date=local)
    claimed = await _claimed_at_by_reward(session, user.id)

    rewards = []
    for reward in REWARD_CATALOG:
        claimed_at = claimed.get(_claim_source_id(user.id, reward))
        if claimed_at is not None:
            state = "claimed"
        elif is_eligible(reward, best_streak_days=best):
            state = "eligible"
        else:
            state = "locked"
        rewards.append(
            RewardState(
                reward_id=reward.reward_id,
                title=reward.title,
                category=reward.category,
                effect=reward.effect,
                icon=reward.icon,
                required_streak_days=reward.required_streak_days,
                state=state,
                claimed_at=claimed_at,
            )
        )

    return RewardsOverviewResponse(
        catalog_version=CATALOG_VERSION,
        current_streak_days=current,
        best_streak_days=best,
        balances=await _balances(session, user.id),
        rewards=rewards,
    )


async def claim_reward(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    reward_id: str,
    key: str,
    now: datetime | None = None,
) -> RewardClaimResponse:
    now = now or datetime.now(UTC)
    reward = reward_or_none(reward_id)
    if reward is None:
        raise not_found("Reward")

    user, local = await _user_and_local_date(uow, principal, now)
    decision = await _begin_idempotent(
        uow,
        scope="reward.claim",
        subject=str(user.id),
        key=key,
        payload={"reward_id": reward_id, "catalog_version": CATALOG_VERSION},
        now=now,
    )
    if decision.replay_body is not None:
        return RewardClaimResponse.model_validate(decision.replay_body)

    session = uow.session
    best = longest_run(await qualifying_streak_days(session, user.id, local))
    if not is_eligible(reward, best_streak_days=best):
        raise conflict(
            "reward_not_eligible",
            f"This reward unlocks at {reward.required_streak_days} qualifying days.",
        )

    source_id = _claim_source_id(user.id, reward)
    already = await session.scalar(
        select(func.count())
        .select_from(RewardLedger)
        .where(
            RewardLedger.user_id == user.id,
            RewardLedger.source_type == _CLAIM_SOURCE_TYPE,
            RewardLedger.source_id == source_id,
        )
    )
    if already:
        raise conflict("reward_already_claimed", "This reward is already claimed.")

    session.add(
        RewardLedger(
            id=uuid.uuid4(),
            user_id=user.id,
            source_type=_CLAIM_SOURCE_TYPE,
            source_id=source_id,
            event_type="grant",
            asset_type=reward.asset_type,
            asset_key=reward.asset_key,
            catalog_version=CATALOG_VERSION,
            quantity=reward.quantity,
        )
    )
    body = RewardClaimResponse(
        reward_id=reward.reward_id,
        catalog_version=CATALOG_VERSION,
        granted=[
            RewardGrant(
                asset_type=reward.asset_type,
                asset_key=reward.asset_key,
                quantity=reward.quantity,
            )
        ],
        claimed_at=now,
    )
    _complete_idempotent(
        decision, response_status=201, response_body=body.model_dump(mode="json")
    )
    await uow.commit()
    return body


async def redeem_streak_freeze(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    request: StreakFreezeRequest,
    key: str,
    now: datetime | None = None,
) -> StreakFreezeResponse:
    now = now or datetime.now(UTC)
    user, local = await _user_and_local_date(uow, principal, now)
    profile = await uow.profiles.get(user.id)

    rejection = freeze_rejection_reason(
        local_date=request.local_date, current_local_date=local
    )
    if rejection is not None:
        raise unprocessable_content("freeze_window", rejection)

    decision = await _begin_idempotent(
        uow,
        scope="streak.freeze",
        subject=str(user.id),
        key=key,
        payload=request.model_dump(mode="json"),
        now=now,
    )
    if decision.replay_body is not None:
        return StreakFreezeResponse.model_validate(decision.replay_body)

    session = uow.session
    existing = await session.scalar(
        select(func.count())
        .select_from(StreakLedger)
        .where(
            StreakLedger.user_id == user.id,
            StreakLedger.kind == "daily",
            StreakLedger.local_date == request.local_date,
        )
    )
    if existing:
        raise conflict(
            "day_already_adjudicated",
            "That day already has a finalized streak decision.",
        )

    balance = await session.scalar(
        select(func.coalesce(func.sum(RewardLedger.quantity), 0)).where(
            RewardLedger.user_id == user.id,
            RewardLedger.asset_type == ASSET_FREEZE,
            RewardLedger.asset_key == FREEZE_ASSET_KEY,
        )
    )
    if (balance or 0) < 1:
        raise conflict("no_freeze_available", "You have no freeze left to use.")

    # The streak day and its ledger row reference each other: the ledger's
    # source_id is the streak day, and the streak day's evidence_id is the
    # ledger row. assert_streak_day_scope verifies exactly that pairing.
    streak_id = uuid.uuid4()
    ledger_id = uuid.uuid4()
    session.add(
        RewardLedger(
            id=ledger_id,
            user_id=user.id,
            source_type=_FREEZE_SOURCE_TYPE,
            source_id=streak_id,
            event_type="redeem",
            asset_type=ASSET_FREEZE,
            asset_key=FREEZE_ASSET_KEY,
            catalog_version=CATALOG_VERSION,
            quantity=-1,
        )
    )
    session.add(
        StreakLedger(
            id=streak_id,
            user_id=user.id,
            local_date=request.local_date,
            kind="daily",
            timezone=profile.timezone,
            evidence_type="freeze",
            evidence_id=ledger_id,
            adjudication_state="frozen",
        )
    )
    await session.flush()

    days = await qualifying_streak_days(session, user.id, local)
    body = StreakFreezeResponse(
        local_date=request.local_date,
        timezone=profile.timezone,
        streak_day_id=streak_id,
        freezes_remaining=(balance or 0) - 1,
        streak_days=closed_streak_length(days, current_local_date=local),
    )
    _complete_idempotent(
        decision, response_status=201, response_body=body.model_dump(mode="json")
    )
    await uow.commit()
    return body
