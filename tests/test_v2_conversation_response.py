"""Focused safety and transaction-boundary checks for durable conversation jobs."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.v2.application.conversation_job_runner import (
    ConversationResponseContext,
    ConversationResponseJobRunner,
)
from app.v2.application.conversation_response import (
    ConversationGatewayResult,
    ConversationResponseRequest,
    ConversationSnapshotMessage,
    requires_escalation,
    validate_response,
)
from app.v2.domain.conversation_prompts import (
    EMERGENCY_ESCALATION_TEMPLATE,
    prompt_contract,
)
from app.v2.infrastructure.worker import (
    ClaimedJob,
    RetryableJobFailure,
    TerminalJobFailure,
)


def test_prompt_contracts_cover_every_canonical_thread_and_weekly_is_definition_owned():
    for thread_type in (
        "general",
        "care_plan",
        "symptom_checkin",
        "support",
        "weekly_checkin",
    ):
        assert prompt_contract(thread_type).thread_type == thread_type
    assert "definition-owned" in prompt_contract("weekly_checkin").instructions


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("", "conversation_response_empty"),
        ("x" * 2001, "conversation_response_oversized"),
        ("I am a doctor and you have flu.", "conversation_response_diagnostic_claim"),
        ("You likely have flu.", "conversation_response_diagnostic_claim"),
        ("This is definitely an infection.", "conversation_response_diagnostic_claim"),
        ("Take 500 mg now.", "conversation_response_prescription"),
        (
            "You do not need to seek emergency care.",
            "conversation_response_emergency_mishandling",
        ),
    ],
)
def test_response_safety_has_stable_terminal_codes(content, code):
    with pytest.raises(TerminalJobFailure, match=code):
        validate_response(content)


def test_red_flag_is_deterministic_and_requires_no_provider_call():
    assert requires_escalation(ConversationSnapshotMessage("user", "I want to kill myself"))
    assert "urgent help" in EMERGENCY_ESCALATION_TEMPLATE
    assert not requires_escalation(ConversationSnapshotMessage("user", "I feel safe now"))


@pytest.mark.anyio
async def test_runner_is_least_privilege_for_its_job_type():
    runner = ConversationResponseJobRunner(
        gateway=object(), uow_factory=lambda: Uow(SimpleNamespace())
    )
    with pytest.raises(TerminalJobFailure, match="unsupported_job_type"):
        await runner.handle(ClaimedJob(uuid4(), uuid4(), "plan_generation", {}, 1, 3, "lease"))


class Uow:
    def __init__(self, session):
        self.session, self.closed, self.committed = session, False, False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def commit(self):
        self.committed = True


@pytest.mark.anyio
async def test_provider_is_called_only_after_context_uow_closed(monkeypatch):
    conversation_id, message_id, user_id = uuid4(), uuid4(), uuid4()
    loader = Uow(SimpleNamespace())
    finalized = Uow(SimpleNamespace())
    runner = ConversationResponseJobRunner(gateway=object(), uow_factory=lambda: finalized)

    async def load(job):
        del job
        async with loader:
            await loader.commit()
        return ConversationResponseContext(
            ConversationResponseRequest(
                "general",
                "conversation.v1",
                "support",
                (ConversationSnapshotMessage("user", "hello"),),
            ),
            ConversationSnapshotMessage("user", "hello"),
        )

    observed = {}

    class Gateway:
        async def respond(self, request):
            observed["closed"] = loader.closed
            return ConversationGatewayResult("How are you feeling today?")

    async def materialize(uow, **kwargs):
        observed["final_uow"] = uow is finalized
        return {"assistant_message_id": str(message_id)}

    monkeypatch.setattr(runner, "_load_context", load)
    monkeypatch.setattr(runner, "_materialize", materialize)
    runner._gateway = Gateway()
    result = await runner.handle(
        ClaimedJob(
            uuid4(),
            user_id,
            "conversation_response.v1",
            {"conversation_id": str(conversation_id), "message_id": str(message_id)},
            1,
            3,
            "lease",
        )
    )
    assert result["assistant_message_id"] == str(message_id)
    assert observed == {"closed": True, "final_uow": True}


@pytest.mark.anyio
async def test_red_flag_bypasses_gateway(monkeypatch):
    runner = ConversationResponseJobRunner(
        gateway=object(), uow_factory=lambda: Uow(SimpleNamespace())
    )
    request = ConversationResponseRequest(
        "general",
        "conversation.v1",
        "support",
        (ConversationSnapshotMessage("user", "I cannot breathe"),),
    )
    monkeypatch.setattr(
        runner,
        "_load_context",
        lambda job: _context(request, ConversationSnapshotMessage("user", "I cannot breathe")),
    )
    captured = {}

    async def materialize(uow, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(runner, "_materialize", materialize)
    result = await runner.handle(
        ClaimedJob(uuid4(), uuid4(), "conversation_response.v1", {}, 1, 3, "lease")
    )
    assert result == {"ok": True} and captured["content"] == EMERGENCY_ESCALATION_TEMPLATE


async def _context(request, trigger):
    return ConversationResponseContext(request, trigger)


class ContextSession:
    def __init__(self, *, stored_job, scalar_rows, message_rows):
        self.stored_job = stored_job
        self._scalar_rows = iter(scalar_rows)
        self._message_rows = message_rows

    async def get(self, model, value):
        del model, value
        return self.stored_job

    async def scalar(self, statement):
        del statement
        return next(self._scalar_rows)

    async def scalars(self, statement):
        del statement
        return self._message_rows


@pytest.mark.anyio
async def test_later_conversation_job_waits_for_predecessor_without_provider_context():
    user_id, conversation_id, source_id = uuid4(), uuid4(), uuid4()
    current = SimpleNamespace(
        id=uuid4(), user_id=user_id, job_type="conversation_response.v1", created_at=1
    )
    session = ContextSession(
        stored_job=current,
        scalar_rows=(
            SimpleNamespace(id=conversation_id, user_id=user_id, thread_type="general"),
            SimpleNamespace(id=source_id, role="user", sequence=2, content="second"),
            uuid4(),
        ),
        message_rows=[],
    )
    called = []

    class Gateway:
        async def respond(self, request):
            del request
            called.append(True)
            raise AssertionError("causally blocked job must not call provider")

    runner = ConversationResponseJobRunner(gateway=Gateway(), uow_factory=lambda: Uow(session))
    job = ClaimedJob(
        current.id,
        user_id,
        "conversation_response.v1",
        {"conversation_id": str(conversation_id), "message_id": str(source_id)},
        1,
        3,
        "lease",
    )
    with pytest.raises(RetryableJobFailure, match="conversation_response_predecessor_pending"):
        await runner.handle(job)
    assert called == []


@pytest.mark.anyio
async def test_context_ends_at_triggering_message_and_provider_request_has_no_identity():
    user_id, conversation_id, source_id = uuid4(), uuid4(), uuid4()
    current = SimpleNamespace(
        id=uuid4(), user_id=user_id, job_type="conversation_response.v1", created_at=1
    )
    early = SimpleNamespace(id=uuid4(), role="user", sequence=1, content="first")
    source = SimpleNamespace(id=source_id, role="user", sequence=2, content="second")
    later = SimpleNamespace(id=uuid4(), role="user", sequence=3, content="later secret")
    session = ContextSession(
        stored_job=current,
        scalar_rows=(
            SimpleNamespace(id=conversation_id, user_id=user_id, thread_type="general"),
            source,
            None,
        ),
        message_rows=[source, early],
    )
    runner = ConversationResponseJobRunner(gateway=object(), uow_factory=lambda: Uow(session))
    context = await runner._load_context(
        ClaimedJob(
            current.id,
            user_id,
            "conversation_response.v1",
            {"conversation_id": str(conversation_id), "message_id": str(source_id)},
            1,
            3,
            "lease",
        )
    )
    assert [message.content for message in context.request.messages] == [
        "first",
        "second",
    ]
    assert not hasattr(context.request, "conversation_id")
    assert later.content not in str(context.request)


@pytest.mark.anyio
async def test_rejected_provider_output_records_only_rejected_invocation_metadata(
    monkeypatch,
):
    request = ConversationResponseRequest(
        "general",
        "conversation.v1",
        "support",
        (ConversationSnapshotMessage("user", "hello"),),
    )
    runner = ConversationResponseJobRunner(
        gateway=object(), uow_factory=lambda: Uow(SimpleNamespace())
    )

    class Gateway:
        async def respond(self, request):
            del request
            return ConversationGatewayResult("You likely have flu.", invocation=object())

    recorded = {}

    async def record(uow, job, invocation):
        recorded.update({"uow": uow, "job": job, "invocation": invocation})

    monkeypatch.setattr(
        runner,
        "_load_context",
        lambda job: _context(request, ConversationSnapshotMessage("user", "hello")),
    )
    monkeypatch.setattr(runner, "_record_rejected_invocation", record)
    runner._gateway = Gateway()
    job = ClaimedJob(uuid4(), uuid4(), "conversation_response.v1", {}, 1, 3, "lease")
    with pytest.raises(TerminalJobFailure, match="conversation_response_diagnostic_claim"):
        await runner.handle(job)
    assert recorded["job"] is job and recorded["invocation"] is not None
