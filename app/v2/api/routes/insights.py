"""Progress and insight aggregates. All derived; nothing here is stored."""

from __future__ import annotations

from datetime import date

from fastapi import Depends, Query

from app.v2.api.auth import get_verified_principal
from app.v2.api.dependencies import get_uow
from app.v2.api.routes.common import domain_router
from app.v2.application.contracts import (
    InsightsSummaryResponse,
    ProgressReportResponse,
    SymptomPatternsResponse,
    WeeklyTrendsResponse,
)
from app.v2.application.insights import (
    insights_summary,
    progress_report,
    symptom_patterns,
    weekly_trends,
)
from app.v2.domain.identity import VerifiedPrincipal
from app.v2.persistence.uow import SqlAlchemyUnitOfWork

router = domain_router()

_FROM = Query(default=None, alias="from")
_TO = Query(default=None, alias="to")


@router.get(
    "/me/progress",
    response_model=ProgressReportResponse,
    tags=["v2-insights"],
    operation_id="getMyProgressReportV2",
)
async def read_progress(
    period: str = Query(default="week", pattern=r"^(week|month|all)$"),
    range_start: date | None = _FROM,
    range_end: date | None = _TO,
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> ProgressReportResponse:
    """One route with a period parameter replaces v1's weekly/monthly/overall."""

    return await progress_report(
        uow, principal=principal, period=period, start=range_start, end=range_end
    )


@router.get(
    "/me/insights/summary",
    response_model=InsightsSummaryResponse,
    tags=["v2-insights"],
    operation_id="getInsightsSummaryV2",
)
async def read_summary(
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> InsightsSummaryResponse:
    return await insights_summary(uow, principal=principal)


@router.get(
    "/me/insights/symptom-patterns",
    response_model=SymptomPatternsResponse,
    tags=["v2-insights"],
    operation_id="getSymptomPatternsV2",
)
async def read_symptom_patterns(
    range_start: date | None = _FROM,
    range_end: date | None = _TO,
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> SymptomPatternsResponse:
    return await symptom_patterns(
        uow, principal=principal, start=range_start, end=range_end
    )


@router.get(
    "/me/insights/weekly-trends",
    response_model=WeeklyTrendsResponse,
    tags=["v2-insights"],
    operation_id="getWeeklyTrendsV2",
)
async def read_weekly_trends(
    range_start: date | None = _FROM,
    range_end: date | None = _TO,
    principal: VerifiedPrincipal = Depends(get_verified_principal),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> WeeklyTrendsResponse:
    return await weekly_trends(
        uow, principal=principal, start=range_start, end=range_end
    )
