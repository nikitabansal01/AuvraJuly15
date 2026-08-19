"""Durable, bounded conversation response handler."""
from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, or_, select

from app.v2.application.conversation_response import (
    ConversationGateway,
    ConversationResponseRequest,
    ConversationSnapshotMessage,
    fixed_escalation_result,
    requires_escalation,
    validate_response,
)
from app.v2.domain.conversation_prompts import PROMPT_VERSION, prompt_contract
from app.v2.infrastructure.worker import (
    ClaimedJob,
    LeaseLost,
    RetryableJobFailure,
    TerminalJobFailure,
)
from app.v2.persistence.models import GenerationJob
from app.v2.persistence.models_engagement import (
    AiInvocation,
    Conversation,
    ConversationMessage,
)
from app.v2.persistence.uow import SqlAlchemyUnitOfWork

UowFactory = Callable[[], SqlAlchemyUnitOfWork]


@dataclass(frozen=True, slots=True)
class ConversationResponseContext:
    request: ConversationResponseRequest
    trigger_message: ConversationSnapshotMessage


class ConversationResponseJobRunner:
    """Load/close, invoke externally, then atomically materialize one reply."""

    def __init__(
        self,
        *,
        gateway: ConversationGateway,
        uow_factory: UowFactory = SqlAlchemyUnitOfWork,
    ) -> None:
        self._gateway = gateway
        self._uow_factory = uow_factory

    async def handle(self, job: ClaimedJob) -> dict[str, Any]:
        if job.job_type != "conversation_response.v1":
            raise TerminalJobFailure("unsupported_job_type")
        context = await self._load_context(job)
        if requires_escalation(context.trigger_message):
            result = fixed_escalation_result()
        else:
            # Provider I/O is deliberately after _load_context's UoW is closed.
            result = await self._gateway.respond(context.request)
        try:
            validate_response(result.content)
        except TerminalJobFailure:
            if result.invocation is not None:
                async with self._uow_factory() as uow:
                    await self._record_rejected_invocation(uow, job, result.invocation)
            raise
        async with self._uow_factory() as uow:
            return await self._materialize(
                uow, job=job, content=result.content, invocation=result.invocation
            )

    async def _load_context(self, job: ClaimedJob) -> ConversationResponseContext:
        conversation_id, message_id = self._ids(job.request_payload)
        async with self._uow_factory() as uow:
            session = self._session(uow)
            stored_job = await session.get(GenerationJob, job.id)
            if (
                stored_job is None
                or stored_job.user_id != job.user_id
                or stored_job.job_type != "conversation_response.v1"
            ):
                raise TerminalJobFailure("invalid_job_payload")
            conversation = await session.scalar(
                select(Conversation).where(
                    Conversation.id == conversation_id,
                    Conversation.user_id == job.user_id,
                )
            )
            source = await session.scalar(
                select(ConversationMessage).where(
                    ConversationMessage.id == message_id,
                    ConversationMessage.conversation_id == conversation_id,
                )
            )
            if conversation is None or source is None or source.role != "user":
                raise TerminalJobFailure("conversation_context_unavailable")
            predecessor = await session.scalar(
                select(GenerationJob.id)
                .where(
                    GenerationJob.job_type == "conversation_response.v1",
                    GenerationJob.id != job.id,
                    GenerationJob.request_payload["conversation_id"].astext == str(conversation_id),
                    GenerationJob.state.in_(("queued", "running", "retry_wait")),
                    or_(
                        GenerationJob.created_at < stored_job.created_at,
                        and_(
                            GenerationJob.created_at == stored_job.created_at,
                            GenerationJob.id < stored_job.id,
                        ),
                    ),
                )
                .limit(1)
            )
            if predecessor is not None:
                raise RetryableJobFailure("conversation_response_predecessor_pending")
            try:
                contract = prompt_contract(conversation.thread_type)
            except ValueError as exc:
                raise TerminalJobFailure("conversation_context_unavailable") from exc
            rows = await session.scalars(
                select(ConversationMessage)
                .where(
                    ConversationMessage.conversation_id == conversation_id,
                    ConversationMessage.sequence <= source.sequence,
                )
                .order_by(ConversationMessage.sequence.desc())
                .limit(12)
            )
            message_rows = list(rows)
            messages = tuple(
                ConversationSnapshotMessage(role=row.role, content=row.content[:1000])
                for row in reversed(message_rows)
            )
            if not any(row.id == source.id for row in message_rows):
                raise TerminalJobFailure("conversation_context_unavailable")
            await uow.commit()
        return ConversationResponseContext(
            ConversationResponseRequest(
                conversation.thread_type,
                PROMPT_VERSION,
                contract.instructions,
                messages,
            ),
            ConversationSnapshotMessage(role=source.role, content=source.content),
        )

    async def _record_rejected_invocation(self, uow, job: ClaimedJob, invocation) -> None:
        """Persist only keyed metadata when a returned provider output is rejected."""

        session = self._session(uow)
        stored = await session.scalar(
            select(GenerationJob).where(GenerationJob.id == job.id).with_for_update()
        )
        if stored is None or stored.state != "running" or stored.lease_owner != job.lease_token:
            raise LeaseLost()
        existing = await session.scalar(
            select(AiInvocation.id).where(
                AiInvocation.generation_job_id == job.id,
                AiInvocation.result_status == "blocked",
            )
        )
        if existing is None:
            session.add(self._invocation_row(job, invocation, result_status="blocked"))
        await uow.commit()

    async def _materialize(
        self, uow: SqlAlchemyUnitOfWork, *, job: ClaimedJob, content: str, invocation
    ) -> dict[str, Any]:
        session = self._session(uow)
        stored = await session.scalar(
            select(GenerationJob).where(GenerationJob.id == job.id).with_for_update()
        )
        if stored is None:
            raise TerminalJobFailure("job_not_found")
        if stored.state == "ready":
            return dict(stored.result_payload or {})
        if stored.state != "running" or stored.lease_owner != job.lease_token:
            raise LeaseLost()
        conversation_id, _ = self._ids(job.request_payload)
        conversation = await session.scalar(
            select(Conversation)
            .where(Conversation.id == conversation_id, Conversation.user_id == job.user_id)
            .with_for_update()
        )
        if conversation is None:
            raise TerminalJobFailure("conversation_context_unavailable")
        response_client_message_id = uuid.uuid5(
            uuid.NAMESPACE_URL, "auvra:conversation-response:" + str(job.id)
        )
        existing = await session.scalar(
            select(ConversationMessage)
            .where(
                ConversationMessage.conversation_id == conversation_id,
                or_(
                    ConversationMessage.metadata_json["response_job_id"].astext == str(job.id),
                    ConversationMessage.client_message_id == response_client_message_id,
                ),
            )
            .with_for_update()
        )
        if existing is None:
            next_sequence = await session.scalar(
                select(ConversationMessage.sequence)
                .where(ConversationMessage.conversation_id == conversation_id)
                .order_by(ConversationMessage.sequence.desc())
                .limit(1)
            )
            existing = ConversationMessage(
                id=uuid.uuid4(),
                conversation_id=conversation_id,
                sequence=(next_sequence or 0) + 1,
                client_message_id=response_client_message_id,
                role="assistant",
                content=content,
                metadata_json={
                    "response_job_id": str(job.id),
                    "prompt_version": PROMPT_VERSION,
                },
            )
            session.add(existing)
            conversation.revision += 1
            if invocation is not None:
                session.add(self._invocation_row(job, invocation))
        result = {
            "conversation_id": str(conversation_id),
            "assistant_message_id": str(existing.id),
            "sequence": existing.sequence,
            "revision": conversation.revision,
        }
        stored.state, stored.phase, stored.progress, stored.result_payload = (
            "ready",
            "ready",
            100,
            result,
        )
        stored.finished_at, stored.lease_owner, stored.lease_expires_at = (
            datetime.now(UTC),
            None,
            None,
        )
        await uow.commit()
        return result

    @staticmethod
    def _ids(payload: Mapping[str, object]) -> tuple[uuid.UUID, uuid.UUID]:
        try:
            return uuid.UUID(str(payload.get("conversation_id"))), uuid.UUID(
                str(payload.get("message_id"))
            )
        except (ValueError, TypeError, AttributeError) as exc:
            raise TerminalJobFailure("invalid_job_payload") from exc

    @staticmethod
    def _session(uow: SqlAlchemyUnitOfWork):
        if uow.session is None:
            raise RuntimeError("UnitOfWork must be entered before runner use")
        return uow.session

    @staticmethod
    def _invocation_row(job: ClaimedJob, invocation, *, result_status: str | None = None):
        return AiInvocation(
            id=uuid.uuid4(),
            user_id=job.user_id,
            generation_job_id=job.id,
            provider=invocation.provider,
            operation=invocation.operation,
            task=invocation.task,
            prompt_version=invocation.prompt_version,
            model=invocation.model,
            input_tokens=invocation.input_tokens,
            output_tokens=invocation.output_tokens,
            cost_minor=invocation.cost_minor,
            currency_code=invocation.currency_code,
            price_version=invocation.price_version,
            latency_ms=invocation.latency_ms,
            result_status=result_status or invocation.result_status,
            input_hash=invocation.input_hash,
            output_hash=invocation.output_hash,
        )
