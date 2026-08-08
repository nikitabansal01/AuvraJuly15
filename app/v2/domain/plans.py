"""Publication invariant for complete, renderable action plans."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from urllib.parse import urlparse

from app.v2.domain.enums import MediaStatus, PlanItemStatus, PlanStatus


class AssetLike(Protocol):
    id: object
    status: str
    public_url: str


class VariantLike(Protocol):
    variant_type: str
    asset: AssetLike


class ItemLike(Protocol):
    slot: int
    status: str
    hero_asset: AssetLike
    variants: list[VariantLike]


class PlanLike(Protocol):
    status: str
    published_at: datetime | None
    items: list[ItemLike]


class IncompleteReadyPlan(ValueError):
    """Raised when a plan would expose partial or unusable content."""


def _require_https_ready_asset(asset: AssetLike) -> None:
    parsed = urlparse(asset.public_url)
    if asset.status != MediaStatus.READY.value:
        raise IncompleteReadyPlan("all plan media assets must be ready")
    if parsed.scheme != "https" or not parsed.netloc:
        raise IncompleteReadyPlan("all plan media assets must use permanent HTTPS URLs")


def require_ready_plan_complete(plan: PlanLike) -> None:
    """Enforce the four-item/sixteen-image contract before publication/read."""

    if plan.status != PlanStatus.READY.value or plan.published_at is None:
        raise IncompleteReadyPlan("a published plan must be READY with published_at")
    if len(plan.items) != 4 or {item.slot for item in plan.items} != {1, 2, 3, 4}:
        raise IncompleteReadyPlan("a ready plan must contain slots 1 through 4")

    asset_ids: set[object] = set()
    for item in plan.items:
        if item.status != PlanItemStatus.ACTIVE.value:
            raise IncompleteReadyPlan("ready plan items must be active")
        _require_https_ready_asset(item.hero_asset)
        asset_ids.add(item.hero_asset.id)
        if len(item.variants) != 3:
            raise IncompleteReadyPlan("each plan item must contain three variants")
        variant_types = {variant.variant_type for variant in item.variants}
        if len(variant_types) != 3:
            raise IncompleteReadyPlan("variant types must be unique per item")
        for variant in item.variants:
            _require_https_ready_asset(variant.asset)
            asset_ids.add(variant.asset.id)

    if len(asset_ids) != 16:
        raise IncompleteReadyPlan("a ready plan must reference sixteen distinct assets")
