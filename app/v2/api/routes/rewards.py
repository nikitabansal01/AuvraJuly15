"""Authenticated rewards, entitlement claims and streak-freeze routes."""

from __future__ import annotations

from fastapi import Depends, Header, Path, status

from app.v2.api.auth import get_verified_principal
from app.v2.api.dependencies import get_uow
from app.v2.api.routes.common import domain_router
from app.v2.application.contracts import (
    RewardClaimResponse,
    RewardsOverviewResponse,
    StreakFreezeRequest,
    StreakFreezeResponse,
)
from app.v2.application.rewards import (
    claim_reward,
    redeem_streak_freeze,
    rewards_overview,
)
from app.v2.domain.identity import VerifiedPrincipal
from app.v2.persistence.uow import SqlAlchemyUnitOfWork

router = domain_router()


@router.get(
    "/me/rewards",
    response_model=RewardsOverviewResponse,
    tags=["v2-rewards"],
    operation_id="getMyRewardsV2",
)
async def read_rewards(
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> RewardsOverviewResponse:
    return await rewards_overview(uow, principal=principal)


@router.post(
    "/me/rewards/{reward_id}/claim",
    response_model=RewardClaimResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["v2-rewards"],
    operation_id="claimRewardV2",
)
async def claim(
    reward_id: str = Path(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$"),
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> RewardClaimResponse:
    return await claim_reward(uow, principal=principal, reward_id=reward_id, key=idempotency_key)


@router.post(
    "/me/streak-freezes",
    response_model=StreakFreezeResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["v2-rewards"],
    operation_id="createStreakFreezeV2",
)
async def create_streak_freeze(
    body: StreakFreezeRequest,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> StreakFreezeResponse:
    return await redeem_streak_freeze(uow, principal=principal, request=body, key=idempotency_key)
