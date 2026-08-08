"""Selected-variant plan replacement without an implicit provider invocation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.v2.application.contracts import (
    PlanReplacementRequest,
    PlanReplacementResponse,
)
from app.v2.application.engagement import _owned_plan, _user
from app.v2.application.errors import conflict, not_found, unprocessable_content
from app.v2.application.services import _begin_idempotent, _complete_idempotent
from app.v2.domain.enums import PlanStatus
from app.v2.domain.identity import VerifiedPrincipal
from app.v2.persistence.models import (
    ActionPlan,
    ActionPlanItem,
    ActionPlanItemVariant,
    GenerationJob,
)
from app.v2.persistence.models_engagement import (
    DailyReview,
    PlanRefresh,
    ResearchCitation,
)
from app.v2.persistence.uow import SqlAlchemyUnitOfWork


async def replace_plan_with_selected_variant(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: VerifiedPrincipal,
    plan_id: uuid.UUID,
    revision: int,
    key: str,
    request: PlanReplacementRequest,
    now: datetime | None = None,
) -> PlanReplacementResponse:
    """Publish one successor revision with a selected existing variant promoted.

    Selection is fully local to the existing immutable plan graph.  It neither
    calls a provider nor claims to have generated new content.
    """

    now = now or datetime.now(UTC)
    user = await _user(uow, principal)
    decision = await _begin_idempotent(
        uow,
        scope="plan.replace_selected_variant",
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
        return PlanReplacementResponse.model_validate(decision.replay_body)

    plan = await _owned_plan(
        uow.session,
        user_id=user.id,
        plan_id=plan_id,
        revision=revision,
        lock=True,
    )
    _require_replaceable_plan(plan)
    await _ensure_unreviewed(uow, plan.id)
    old_items = await _locked_plan_items(uow, plan.id)
    old_item = next((item for item in old_items if item.id == request.item_id), None)
    if old_item is None:
        raise not_found("Plan item")
    selected_variant = await _selected_variant(
        uow, old_item.id, request.selected_variant_id
    )
    _selected_variant_content(selected_variant)
    successor, new_items = await _make_successor(
        uow, plan, old_items, old_item, selected_variant, now
    )
    new_item = new_items[old_item.id]
    refresh = PlanRefresh(
        id=uuid.uuid4(),
        user_id=user.id,
        idempotency_key=key,
        reason=request.reason,
        local_date=plan.local_date,
        timezone=plan.timezone,
        requested_at=now,
        completed_plan_id=successor.id,
        old_item_id=old_item.id,
        new_item_id=new_item.id,
        accepted_at=now,
    )
    uow.session.add(refresh)
    plan.is_current = False
    plan.status = PlanStatus.ARCHIVED.value
    await uow.flush()
    successor.is_current = True
    successor.status = PlanStatus.READY.value
    successor.published_at = now
    response = PlanReplacementResponse(
        refresh_id=refresh.id,
        plan_id=successor.id,
        revision=successor.revision,
        local_date=successor.local_date,
        timezone=successor.timezone,
        old_item_id=old_item.id,
        new_item_id=new_item.id,
        replacement_mode="selected_variant",
    )
    _complete_idempotent(
        decision, response_status=201, response_body=response.model_dump(mode="json")
    )
    await uow.commit()
    return response


def _require_replaceable_plan(plan: ActionPlan) -> None:
    if not plan.is_current or plan.status != PlanStatus.READY.value:
        raise conflict(
            "plan_not_current", "Only the current ready plan can be replaced."
        )


async def _ensure_unreviewed(uow: SqlAlchemyUnitOfWork, plan_id: uuid.UUID) -> None:
    completed = await uow.session.scalar(
        select(DailyReview.id).where(
            DailyReview.plan_id == plan_id, DailyReview.status == "completed"
        )
    )
    if completed is not None:
        raise conflict(
            "plan_already_reviewed",
            "A plan with an immutable Daily Review cannot be replaced.",
        )


async def _locked_plan_items(
    uow: SqlAlchemyUnitOfWork, plan_id: uuid.UUID
) -> list[ActionPlanItem]:
    return list(
        (
            await uow.session.scalars(
                select(ActionPlanItem)
                .where(
                    ActionPlanItem.plan_id == plan_id, ActionPlanItem.status == "active"
                )
                .order_by(ActionPlanItem.slot)
                .with_for_update()
            )
        ).all()
    )


async def _selected_variant(
    uow: SqlAlchemyUnitOfWork, item_id: uuid.UUID, variant_id: uuid.UUID
) -> ActionPlanItemVariant:
    variant = await uow.session.scalar(
        select(ActionPlanItemVariant)
        .where(
            ActionPlanItemVariant.id == variant_id,
            ActionPlanItemVariant.item_id == item_id,
        )
        .with_for_update()
    )
    if variant is None:
        raise unprocessable_content(
            "selected_variant_not_in_item",
            "The selected variant must belong to the item being replaced.",
        )
    return variant


async def _make_successor(
    uow: SqlAlchemyUnitOfWork,
    plan: ActionPlan,
    old_items: list[ActionPlanItem],
    old_item: ActionPlanItem,
    selected_variant: ActionPlanItemVariant,
    now: datetime,
) -> tuple[ActionPlan, dict[uuid.UUID, ActionPlanItem]]:
    if len(old_items) != 4:
        raise conflict(
            "plan_not_complete", "Only a complete published plan can be replaced."
        )
    job = GenerationJob(
        id=uuid.uuid4(),
        user_id=plan.user_id,
        job_type="plan_variant_replacement",
        state="ready",
        progress=100,
        phase="selected_variant",
        request_payload={
            "source_plan_id": str(plan.id),
            "source_revision": plan.revision,
            "old_item_id": str(old_item.id),
            "selected_variant_id": str(selected_variant.id),
            "mode": "selected_variant",
        },
        result_payload=None,
        available_at=now,
        finished_at=now,
    )
    uow.session.add(job)
    # The replacement job is a durable local command, not a worker task.  It
    # must exist before the successor plan references it because no ORM
    # relationship orders these two independent inserts for us.
    await uow.flush()
    successor = ActionPlan(
        id=uuid.uuid4(),
        user_id=plan.user_id,
        generation_job_id=job.id,
        local_date=plan.local_date,
        timezone=plan.timezone,
        revision=plan.revision + 1,
        is_current=False,
        status=PlanStatus.ARCHIVED.value,
        cycle_snapshot=plan.cycle_snapshot,
        context_snapshot={
            **plan.context_snapshot,
            "replacement": {
                "mode": "selected_variant",
                "source_plan_id": str(plan.id),
                "source_revision": plan.revision,
            },
        },
    )
    uow.session.add(successor)
    await uow.flush()
    new_items: dict[uuid.UUID, ActionPlanItem] = {}
    for item in old_items:
        replacement = selected_variant if item.id == old_item.id else None
        new_item = _copy_item(item, successor.id, replacement)
        new_items[item.id] = new_item
        uow.session.add(new_item)
        await uow.flush()
        await _copy_variants(uow, item, new_item.id, replacement)
        await _copy_citations(uow, item.id, new_item.id)
    job.result_payload = {
        "plan_id": str(successor.id),
        "revision": successor.revision,
        "local_date": successor.local_date.isoformat(),
        "mode": "selected_variant",
    }
    return successor, new_items


def _copy_item(
    old: ActionPlanItem,
    successor_plan_id: uuid.UUID,
    selected: ActionPlanItemVariant | None,
) -> ActionPlanItem:
    content = _selected_variant_content(selected) if selected is not None else None
    return ActionPlanItem(
        id=uuid.uuid4(),
        plan_id=successor_plan_id,
        slot=old.slot,
        category=old.category,
        title=content.get("title", old.title) if content else old.title,
        purpose=old.purpose,
        instructions=(
            {"steps": content["instructions"]}
            if content and "instructions" in content
            else old.instructions
        ),
        hero_asset_id=selected.asset_id if selected is not None else old.hero_asset_id,
        supersedes_item_id=old.id,
        status="active",
    )


async def _copy_variants(
    uow: SqlAlchemyUnitOfWork,
    old_item: ActionPlanItem,
    new_item_id: uuid.UUID,
    selected: ActionPlanItemVariant | None,
) -> None:
    variants = (
        await uow.session.scalars(
            select(ActionPlanItemVariant).where(
                ActionPlanItemVariant.item_id == old_item.id
            )
        )
    ).all()
    if len(variants) != 3:
        raise conflict("plan_not_complete", "Each plan item must have three variants.")
    uow.session.add_all(
        [
            ActionPlanItemVariant(
                id=uuid.uuid4(),
                item_id=new_item_id,
                variant_type=variant.variant_type,
                content=(
                    _original_item_variant_content(old_item)
                    if selected is not None and variant.id == selected.id
                    else variant.content
                ),
                asset_id=(
                    old_item.hero_asset_id
                    if selected is not None and variant.id == selected.id
                    else variant.asset_id
                ),
            )
            for variant in variants
        ]
    )


async def _copy_citations(
    uow: SqlAlchemyUnitOfWork, old_item_id: uuid.UUID, new_item_id: uuid.UUID
) -> None:
    citations = (
        await uow.session.scalars(
            select(ResearchCitation).where(ResearchCitation.plan_item_id == old_item_id)
        )
    ).all()
    uow.session.add_all(
        [
            ResearchCitation(
                id=uuid.uuid4(),
                source_id=citation.source_id,
                plan_item_id=new_item_id,
                claim=citation.claim,
            )
            for citation in citations
        ]
    )


def _selected_variant_content(selected: ActionPlanItemVariant) -> dict[str, object]:
    content = selected.content
    if not isinstance(content, dict):
        raise unprocessable_content(
            "selected_variant_invalid", "The selected variant has no reusable content."
        )
    title = content.get("title")
    instructions = content.get("instructions")
    if (
        not isinstance(title, str)
        or not title.strip()
        or not isinstance(instructions, list)
        or not instructions
        or not all(isinstance(step, str) and step.strip() for step in instructions)
    ):
        raise unprocessable_content(
            "selected_variant_invalid", "The selected variant has no reusable content."
        )
    return {"title": title, "instructions": instructions}


def _original_item_variant_content(old_item: ActionPlanItem) -> dict[str, object]:
    steps = old_item.instructions.get("steps")
    if not isinstance(steps, list) or not all(isinstance(step, str) for step in steps):
        raise conflict("plan_not_complete", "The source item has invalid instructions.")
    return {"title": old_item.title, "instructions": steps}
