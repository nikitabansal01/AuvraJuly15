"""End-to-end reward, claim and freeze behaviour against PostgreSQL 17."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest


pytestmark = pytest.mark.skipif(
    not os.getenv("AUVRA_TEST_DATABASE_URL"),
    reason="Reward ledger tests require AUVRA_TEST_DATABASE_URL",
)


@pytest.fixture(autouse=True)
def _dispose_shared_engine():
    """Drop the cached async engine so each test owns its event loop.

    get_engine is lru_cached, so pooled asyncpg connections would outlive the
    anyio event loop that opened them and the next test would fail with
    "Event loop is closed".
    """
    yield
    import asyncio

    from app.v2.persistence.database import dispose_database

    asyncio.run(dispose_database())


@pytest.fixture
def anyio_backend() -> str:
    """asyncpg has no trio support, so these run on asyncio only."""

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


def _seed_user(*, qualifying_days: int, timezone: str = "UTC") -> str:
    """Create a user whose finalized streak history has `qualifying_days`.

    Qualifying days are seeded as frozen days backed by properly paired freeze
    tokens rather than completed Daily Reviews. Both `earned` and `frozen`
    qualify, and a review would need a full published plan with sixteen assets
    per day. Every seeded grant is matched by its redeem, so the user's freeze
    balance starts at zero and the tests can assert on it directly.
    """
    from sqlalchemy import text

    subject = f"rewards-{uuid.uuid4()}"
    user_id = uuid.uuid4()
    today = datetime.now(UTC).date()
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
                "VALUES (:user_id, :timezone)"
            ),
            {"user_id": user_id, "timezone": timezone},
        )
        for offset in range(1, qualifying_days + 1):
            streak_id, ledger_id = uuid.uuid4(), uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO app.reward_ledger "
                    "(id, user_id, source_type, source_id, event_type, "
                    " asset_type, asset_key, quantity) "
                    "VALUES (:id, :user_id, 'seed_grant', :source_id, 'grant', "
                    "'freeze', 'streak_freeze', 1)"
                ),
                {
                    "id": uuid.uuid4(),
                    "user_id": user_id,
                    "source_id": uuid.uuid4(),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO app.reward_ledger "
                    "(id, user_id, source_type, source_id, event_type, "
                    " asset_type, asset_key, quantity) "
                    "VALUES (:id, :user_id, 'streak_freeze', :streak_id, "
                    "'redeem', 'freeze', 'streak_freeze', -1)"
                ),
                {"id": ledger_id, "user_id": user_id, "streak_id": streak_id},
            )
            connection.execute(
                text(
                    "INSERT INTO app.streak_days "
                    "(id, user_id, local_date, kind, timezone, evidence_type, "
                    " evidence_id, adjudication_state) "
                    "VALUES (:id, :user_id, :day, 'daily', :timezone, 'freeze', "
                    ":evidence_id, 'frozen')"
                ),
                {
                    "id": streak_id,
                    "user_id": user_id,
                    "day": today - timedelta(days=offset),
                    "timezone": timezone,
                    "evidence_id": ledger_id,
                },
            )
    return subject


async def _uow():
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    return SqlAlchemyUnitOfWork()


@pytest.mark.anyio
async def test_overview_reports_locked_eligible_and_claimed_states() -> None:
    from app.v2.application.rewards import claim_reward, rewards_overview
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    subject = _seed_user(qualifying_days=3)
    principal = _principal(subject)

    async with SqlAlchemyUnitOfWork() as uow:
        overview = await rewards_overview(uow, principal=principal)
    states = {r.reward_id: r.state for r in overview.rewards}
    assert overview.best_streak_days == 3
    assert states["streak_freeze"] == "eligible"
    assert states["diet_prefs"] == "locked"

    async with SqlAlchemyUnitOfWork() as uow:
        await claim_reward(
            uow, principal=principal, reward_id="streak_freeze", key=f"k-{uuid.uuid4()}"
        )
    async with SqlAlchemyUnitOfWork() as uow:
        overview = await rewards_overview(uow, principal=principal)
    states = {r.reward_id: r.state for r in overview.rewards}
    assert states["streak_freeze"] == "claimed"
    balances = {(b.asset_type, b.asset_key): b.balance for b in overview.balances}
    assert balances[("freeze", "streak_freeze")] == 1


@pytest.mark.anyio
async def test_claiming_an_ineligible_reward_is_rejected() -> None:
    from app.v2.application.errors import ApplicationProblem
    from app.v2.application.rewards import claim_reward
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    principal = _principal(_seed_user(qualifying_days=1))
    with pytest.raises(ApplicationProblem) as problem:
        async with SqlAlchemyUnitOfWork() as uow:
            await claim_reward(
                uow,
                principal=principal,
                reward_id="streak_freeze",
                key=f"k-{uuid.uuid4()}",
            )
    assert problem.value.code == "reward_not_eligible"


@pytest.mark.anyio
async def test_claiming_twice_with_distinct_keys_is_still_one_grant() -> None:
    """The deterministic claim source_id makes a repeated claim a conflict."""
    from app.v2.application.errors import ApplicationProblem
    from app.v2.application.rewards import claim_reward, rewards_overview
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    principal = _principal(_seed_user(qualifying_days=5))
    async with SqlAlchemyUnitOfWork() as uow:
        await claim_reward(
            uow, principal=principal, reward_id="streak_freeze", key=f"k-{uuid.uuid4()}"
        )
    with pytest.raises(ApplicationProblem) as problem:
        async with SqlAlchemyUnitOfWork() as uow:
            await claim_reward(
                uow,
                principal=principal,
                reward_id="streak_freeze",
                key=f"k-{uuid.uuid4()}",
            )
    assert problem.value.code == "reward_already_claimed"

    async with SqlAlchemyUnitOfWork() as uow:
        overview = await rewards_overview(uow, principal=principal)
    balances = {(b.asset_type, b.asset_key): b.balance for b in overview.balances}
    assert balances[("freeze", "streak_freeze")] == 1


@pytest.mark.anyio
async def test_replaying_one_idempotency_key_returns_the_same_body() -> None:
    from app.v2.application.rewards import claim_reward
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    principal = _principal(_seed_user(qualifying_days=5))
    key = f"k-{uuid.uuid4()}"
    async with SqlAlchemyUnitOfWork() as uow:
        first = await claim_reward(
            uow, principal=principal, reward_id="streak_freeze", key=key
        )
    async with SqlAlchemyUnitOfWork() as uow:
        replay = await claim_reward(
            uow, principal=principal, reward_id="streak_freeze", key=key
        )
    assert replay == first


@pytest.mark.anyio
async def test_freeze_spends_a_token_and_protects_the_day() -> None:
    from app.v2.application.contracts import StreakFreezeRequest
    from app.v2.application.rewards import (
        claim_reward,
        redeem_streak_freeze,
        rewards_overview,
    )
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    principal = _principal(_seed_user(qualifying_days=3))
    async with SqlAlchemyUnitOfWork() as uow:
        await claim_reward(
            uow, principal=principal, reward_id="streak_freeze", key=f"k-{uuid.uuid4()}"
        )

    # A day inside the window that has no adjudication yet.
    target = datetime.now(UTC).date() - timedelta(days=5)
    async with SqlAlchemyUnitOfWork() as uow:
        response = await redeem_streak_freeze(
            uow,
            principal=principal,
            request=StreakFreezeRequest(local_date=target),
            key=f"k-{uuid.uuid4()}",
        )
    assert response.freezes_remaining == 0
    assert response.local_date == target

    async with SqlAlchemyUnitOfWork() as uow:
        overview = await rewards_overview(uow, principal=principal)
    balances = {(b.asset_type, b.asset_key): b.balance for b in overview.balances}
    assert balances[("freeze", "streak_freeze")] == 0


@pytest.mark.anyio
async def test_freezing_without_a_token_is_rejected() -> None:
    from app.v2.application.contracts import StreakFreezeRequest
    from app.v2.application.errors import ApplicationProblem
    from app.v2.application.rewards import redeem_streak_freeze
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    principal = _principal(_seed_user(qualifying_days=3))
    target = datetime.now(UTC).date() - timedelta(days=5)
    with pytest.raises(ApplicationProblem) as problem:
        async with SqlAlchemyUnitOfWork() as uow:
            await redeem_streak_freeze(
                uow,
                principal=principal,
                request=StreakFreezeRequest(local_date=target),
                key=f"k-{uuid.uuid4()}",
            )
    assert problem.value.code == "no_freeze_available"


@pytest.mark.anyio
async def test_freezing_an_already_adjudicated_day_is_rejected() -> None:
    from app.v2.application.contracts import StreakFreezeRequest
    from app.v2.application.errors import ApplicationProblem
    from app.v2.application.rewards import claim_reward, redeem_streak_freeze
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    principal = _principal(_seed_user(qualifying_days=3))
    async with SqlAlchemyUnitOfWork() as uow:
        await claim_reward(
            uow, principal=principal, reward_id="streak_freeze", key=f"k-{uuid.uuid4()}"
        )
    already_earned = datetime.now(UTC).date() - timedelta(days=1)
    with pytest.raises(ApplicationProblem) as problem:
        async with SqlAlchemyUnitOfWork() as uow:
            await redeem_streak_freeze(
                uow,
                principal=principal,
                request=StreakFreezeRequest(local_date=already_earned),
                key=f"k-{uuid.uuid4()}",
            )
    assert problem.value.code == "day_already_adjudicated"


@pytest.mark.anyio
async def test_freezing_the_open_local_day_is_rejected() -> None:
    from app.v2.application.contracts import StreakFreezeRequest
    from app.v2.application.errors import ApplicationProblem
    from app.v2.application.rewards import claim_reward, redeem_streak_freeze
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    principal = _principal(_seed_user(qualifying_days=3))
    async with SqlAlchemyUnitOfWork() as uow:
        await claim_reward(
            uow, principal=principal, reward_id="streak_freeze", key=f"k-{uuid.uuid4()}"
        )
    with pytest.raises(ApplicationProblem) as problem:
        async with SqlAlchemyUnitOfWork() as uow:
            await redeem_streak_freeze(
                uow,
                principal=principal,
                request=StreakFreezeRequest(local_date=datetime.now(UTC).date()),
                key=f"k-{uuid.uuid4()}",
            )
    assert problem.value.code == "freeze_window"


@pytest.mark.anyio
async def test_one_users_rewards_never_reflect_another_users_streak() -> None:
    from app.v2.application.rewards import rewards_overview
    from app.v2.persistence.uow import SqlAlchemyUnitOfWork

    _seed_user(qualifying_days=30)
    quiet = _principal(_seed_user(qualifying_days=0))
    async with SqlAlchemyUnitOfWork() as uow:
        overview = await rewards_overview(uow, principal=quiet)
    assert overview.best_streak_days == 0
    assert all(reward.state == "locked" for reward in overview.rewards)
    assert overview.balances == []
