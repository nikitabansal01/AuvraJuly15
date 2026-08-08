"""Aggregate v2 router composed from domain-owned HTTP modules."""

from fastapi import APIRouter

from app.v2.api.problem_details import ProblemDetailsRoute
from app.v2.api.routes import (
    account,
    conversations,
    engagement,
    health,
    identity,
    jobs,
    observations,
    onboarding,
    plans,
    rewards,
)
from app.v2.api.routes.common import problem_responses

router = APIRouter(route_class=ProblemDetailsRoute, responses=problem_responses)
router.include_router(health.router)
router.include_router(onboarding.router)
router.include_router(identity.router)
router.include_router(account.router)
router.include_router(plans.router)
router.include_router(engagement.router)
router.include_router(rewards.router)
router.include_router(observations.router)
router.include_router(conversations.router)
router.include_router(jobs.router)
