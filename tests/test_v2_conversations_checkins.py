"""Characterization tests for canonical v2 conversations and weekly check-ins."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.v2.application.contracts import (
    ConversationCreateRequest,
    ConversationMessageCreateRequest,
    WeeklyCheckinAnswerRequest,
)
from app.v2.application.conversations import (
    complete_weekly_checkin,
    create_conversation,
    create_conversation_message,
    create_weekly_checkin,
    put_weekly_checkin_answer,
)
from app.v2.application.errors import ApplicationProblem
from app.v2.domain.conversations import (
    WEEKLY_CHECKIN_DEFINITION_VERSION,
    iso_week_start,
)
from app.v2.domain.enums import IdempotencyState, UserStatus
from app.v2.domain.identity import VerifiedPrincipal


NOW = datetime(2026, 12, 31, 18, 45, tzinfo=UTC)
PRINCIPAL = VerifiedPrincipal("firebase", "owner", None, False, None)


class FakeIdempotency:
    def __init__(self) -> None:
        self.records = {}
        self.lock = asyncio.Lock()

    async def reserve(self, record, *, now):
        key = (record.scope, record.subject, record.idempotency_key)
        async with self.lock:
            previous = self.records.get(key)
            if previous is None:
                self.records[key] = record
                return record, True
            return previous, False


class FakeUsers:
    def __init__(self, user) -> None:
        self.user = user

    async def get_by_subject(self, _, subject, *, for_update=False):
        return self.user if subject == "owner" else None


class FakeProfiles:
    def __init__(self, profile) -> None:
        self.profile = profile

    async def get(self, user_id, *, for_update=False):
        return self.profile if user_id == self.profile.user_id else None


class FakeConversations:
    def __init__(self) -> None:
        self.rows, self.messages = {}, []
        self.lock_requests = []

    def add(self, row) -> None:
        self.rows[row.id] = row

    def add_message(self, row) -> None:
        self.messages.append(row)

    async def get_owned(self, conversation_id, user_id, *, for_update=False):
        self.lock_requests.append(for_update)
        row = self.rows.get(conversation_id)
        return row if row is not None and row.user_id == user_id else None

    async def list_owned(self, user_id, *, limit, before_updated_at=None, before_id=None):
        return [row for row in self.rows.values() if row.user_id == user_id][:limit]

    async def list_messages(self, conversation_id, *, before_sequence=None, limit=100):
        return [row for row in self.messages if row.conversation_id == conversation_id]

    async def get_by_client_message(self, conversation_id, client_message_id):
        return next(
            (
                row
                for row in self.messages
                if row.conversation_id == conversation_id
                and row.client_message_id == client_message_id
            ),
            None,
        )

    async def next_sequence(self, conversation_id):
        return 1 + max(
            (row.sequence for row in self.messages if row.conversation_id == conversation_id),
            default=0,
        )


class FakeWeeklyCheckins:
    def __init__(self, questions) -> None:
        self.questions = questions
        self.rows, self.responses = {}, []

    def add(self, row) -> None:
        self.rows[row.id] = row

    def add_response(self, response) -> None:
        self.responses.append(response)

    async def get_for_week(self, user_id, week_start, *, for_update=False):
        return next(
            (
                row
                for row in self.rows.values()
                if row.user_id == user_id and row.week_start == week_start
            ),
            None,
        )

    async def get_owned(self, checkin_id, user_id, *, for_update=False):
        row = self.rows.get(checkin_id)
        return row if row is not None and row.user_id == user_id else None

    async def list_questions(self, version):
        return [row for row in self.questions if row.version == version]

    async def get_question(self, question_id):
        return next((row for row in self.questions if row.id == question_id), None)

    async def get_response(self, checkin_id, question_id):
        return next(
            (
                row
                for row in self.responses
                if row.weekly_checkin_id == checkin_id and row.question_id == question_id
            ),
            None,
        )

    async def list_responses(self, checkin_id):
        return [row for row in self.responses if row.weekly_checkin_id == checkin_id]

    async def count_required_answered(self, checkin_id, version):
        required = {row.id for row in self.questions if row.version == version and row.required}
        answered = {
            row.question_id
            for row in self.responses
            if row.weekly_checkin_id == checkin_id and row.question_id in required
        }
        return len(required), len(answered)


class FakeUow:
    def __init__(self, *, user, profile, questions=()):
        self.users = FakeUsers(user)
        self.profiles = FakeProfiles(profile)
        self.idempotency = FakeIdempotency()
        self.conversations = FakeConversations()
        self.weekly_checkins = FakeWeeklyCheckins(questions)
        self.job_items = []
        self.outbox_items = []
        self.jobs = SimpleNamespace(add=lambda item: self.job_items.append(item))
        self.outbox = SimpleNamespace(add=lambda item: self.outbox_items.append(item))
        self.commits = 0

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1


def user_and_profile(timezone="Asia/Kolkata"):
    user = SimpleNamespace(id=uuid4(), status=UserStatus.ACTIVE.value)
    return user, SimpleNamespace(user_id=user.id, timezone=timezone)


def question(ordinal, *, version=WEEKLY_CHECKIN_DEFINITION_VERSION):
    return SimpleNamespace(
        id=uuid4(),
        version=version,
        ordinal=ordinal,
        prompt=f"Question {ordinal}",
        answer_type="scale",
        answer_schema={"minimum": 0, "maximum": 10},
        required=True,
    )


@pytest.mark.anyio
async def test_message_is_owned_revision_guarded_and_durably_queued():
    user, profile = user_and_profile()
    uow = FakeUow(user=user, profile=profile)
    conversation = await create_conversation(
        uow,
        principal=PRINCIPAL,
        key="conversation-create-0001",
        request=ConversationCreateRequest(thread_type="general"),
        now=NOW,
    )
    request = ConversationMessageCreateRequest(client_message_id=uuid4(), content="Hello")
    accepted = await create_conversation_message(
        uow,
        principal=PRINCIPAL,
        conversation_id=conversation.conversation_id,
        expected_revision=1,
        key="conversation-message-0001",
        request=request,
        now=NOW,
    )
    assert accepted.message.sequence == 1
    assert accepted.conversation_revision == 2
    assert len(uow.job_items) == len(uow.outbox_items) == 1
    assert uow.job_items[0].request_payload["message_id"] == str(accepted.message.message_id)
    assert uow.conversations.lock_requests[-1] is True
    replay = await create_conversation_message(
        uow,
        principal=PRINCIPAL,
        conversation_id=conversation.conversation_id,
        expected_revision=1,
        key="conversation-message-0001",
        request=request,
        now=NOW,
    )
    assert replay.response_job_id == accepted.response_job_id
    assert len(uow.conversations.messages) == 1


@pytest.mark.anyio
async def test_message_rejects_cross_user_and_stale_revision_without_a_second_sequence():
    user, profile = user_and_profile()
    uow = FakeUow(user=user, profile=profile)
    other = SimpleNamespace(
        id=uuid4(), user_id=uuid4(), revision=1, status="active", thread_type="general"
    )
    uow.conversations.rows[other.id] = other
    with pytest.raises(ApplicationProblem) as missing:
        await create_conversation_message(
            uow,
            principal=PRINCIPAL,
            conversation_id=other.id,
            expected_revision=1,
            key="cross-user-message-0001",
            request=ConversationMessageCreateRequest(client_message_id=uuid4(), content="No"),
            now=NOW,
        )
    assert missing.value.status == 404
    own = await create_conversation(
        uow,
        principal=PRINCIPAL,
        key="conversation-create-0002",
        request=ConversationCreateRequest(),
        now=NOW,
    )
    with pytest.raises(ApplicationProblem) as stale:
        await create_conversation_message(
            uow,
            principal=PRINCIPAL,
            conversation_id=own.conversation_id,
            expected_revision=9,
            key="stale-message-0001",
            request=ConversationMessageCreateRequest(client_message_id=uuid4(), content="No"),
            now=NOW,
        )
    assert stale.value.status == 412
    assert not uow.conversations.messages


@pytest.mark.anyio
async def test_weekly_checkin_uses_iana_week_boundary_definition_and_immutable_answers():
    user, profile = user_and_profile("Pacific/Kiritimati")
    questions = [question(1), question(2)]
    uow = FakeUow(user=user, profile=profile, questions=questions)
    checkin = await create_weekly_checkin(
        uow, principal=PRINCIPAL, key="weekly-checkin-create-0001", now=NOW
    )
    assert checkin.week_start == date(2026, 12, 28)
    first = await put_weekly_checkin_answer(
        uow,
        principal=PRINCIPAL,
        checkin_id=checkin.checkin_id,
        question_id=questions[0].id,
        expected_revision=1,
        key="weekly-answer-0001",
        request=WeeklyCheckinAnswerRequest(answer={"value": 7}),
        now=NOW,
    )
    assert first.completed_at is None
    final = await put_weekly_checkin_answer(
        uow,
        principal=PRINCIPAL,
        checkin_id=checkin.checkin_id,
        question_id=questions[1].id,
        expected_revision=2,
        key="weekly-answer-0002",
        request=WeeklyCheckinAnswerRequest(answer={"value": 6}),
        now=NOW,
    )
    assert final.completed_at is None
    completed = await complete_weekly_checkin(
        uow,
        principal=PRINCIPAL,
        checkin_id=checkin.checkin_id,
        expected_revision=3,
        key="weekly-checkin-complete-0001",
        now=NOW,
    )
    assert completed.completed_at == NOW
    with pytest.raises(ApplicationProblem) as duplicate:
        await put_weekly_checkin_answer(
            uow,
            principal=PRINCIPAL,
            checkin_id=checkin.checkin_id,
            question_id=questions[1].id,
            expected_revision=4,
            key="weekly-answer-0003",
            request=WeeklyCheckinAnswerRequest(answer={"value": 6}),
            now=NOW,
        )
    assert duplicate.value.code == "weekly_checkin_completed"


@pytest.mark.anyio
async def test_weekly_checkin_rejects_wrong_definition_and_invalid_scale():
    user, profile = user_and_profile("America/Los_Angeles")
    questions = [question(1), question(2, version="weekly-checkin.v2")]
    uow = FakeUow(user=user, profile=profile, questions=questions)
    checkin = await create_weekly_checkin(
        uow, principal=PRINCIPAL, key="weekly-checkin-create-0002", now=NOW
    )
    with pytest.raises(ApplicationProblem) as wrong_definition:
        await put_weekly_checkin_answer(
            uow,
            principal=PRINCIPAL,
            checkin_id=checkin.checkin_id,
            question_id=questions[1].id,
            expected_revision=1,
            key="weekly-answer-wrong-version",
            request=WeeklyCheckinAnswerRequest(answer={"value": 5}),
            now=NOW,
        )
    assert wrong_definition.value.code == "weekly_checkin_definition_mismatch"
    with pytest.raises(ApplicationProblem) as bad_scale:
        await put_weekly_checkin_answer(
            uow,
            principal=PRINCIPAL,
            checkin_id=checkin.checkin_id,
            question_id=questions[0].id,
            expected_revision=1,
            key="weekly-answer-bad-scale",
            request=WeeklyCheckinAnswerRequest(answer={"value": 12}),
            now=NOW,
        )
    assert bad_scale.value.code == "invalid_weekly_answer"


def test_iso_week_start_handles_dst_and_year_boundaries():
    assert iso_week_start(datetime(2026, 3, 8, 9, 59, tzinfo=UTC), "America/Los_Angeles") == date(
        2026, 3, 2
    )
    assert iso_week_start(datetime(2027, 1, 1, 1, tzinfo=UTC), "Asia/Kolkata") == date(2026, 12, 28)
