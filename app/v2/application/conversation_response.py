"""Provider-neutral conversation response port and deterministic safety policy."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from app.v2.application.plan_generation import AiInvocationMetadata
from app.v2.domain.conversation_prompts import EMERGENCY_ESCALATION_TEMPLATE
from app.v2.infrastructure.worker import TerminalJobFailure

_RED_FLAG = re.compile(
    r"\b(suicid(?:e|al)|kill myself|self[- ]?harm|overdose|"
    r"can't breathe|cannot breathe|chest pain)\b",
    re.I,
)
_DIAGNOSTIC = re.compile(
    r"\b(i am|i'm|as) (a )?(doctor|physician|clinician)\b|"
    r"\byou (have|are suffering from|likely have)\b|\bthis is definitely\b",
    re.I,
)
_PRESCRIPTION = re.compile(
    r"\b(prescribe|prescription|take \d|\d+\s*(mg|ml)|dosage|dose of)\b", re.I
)
_EMERGENCY_MISHANDLING = re.compile(
    r"\b(no need|don't need|do not need) (to )?(seek|call|get) (emergency|urgent)\b",
    re.I,
)


@dataclass(frozen=True, slots=True)
class ConversationSnapshotMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ConversationResponseRequest:
    thread_type: str
    prompt_version: str
    instructions: str
    messages: tuple[ConversationSnapshotMessage, ...]


@dataclass(frozen=True, slots=True)
class ConversationGatewayResult:
    content: str
    invocation: AiInvocationMetadata | None = None


class ConversationGateway(Protocol):
    async def respond(
        self, request: ConversationResponseRequest
    ) -> ConversationGatewayResult:
        ...


class DeterministicConversationGateway:
    """Test-only fake; production composition must inject a real adapter."""

    async def respond(
        self, request: ConversationResponseRequest
    ) -> ConversationGatewayResult:
        del request
        return ConversationGatewayResult(
            "Thanks for sharing that. What would feel most helpful next?"
        )


def requires_escalation(message: ConversationSnapshotMessage) -> bool:
    """Only the triggering user turn controls this response's escalation path."""

    return message.role == "user" and bool(_RED_FLAG.search(message.content))


def validate_response(content: str) -> str:
    normalized = content.strip()
    if not normalized:
        raise TerminalJobFailure("conversation_response_empty")
    if len(normalized) > 2_000:
        raise TerminalJobFailure("conversation_response_oversized")
    if _DIAGNOSTIC.search(normalized):
        raise TerminalJobFailure("conversation_response_diagnostic_claim")
    if _PRESCRIPTION.search(normalized):
        raise TerminalJobFailure("conversation_response_prescription")
    if _EMERGENCY_MISHANDLING.search(normalized):
        raise TerminalJobFailure("conversation_response_emergency_mishandling")
    return normalized


def fixed_escalation_result() -> ConversationGatewayResult:
    return ConversationGatewayResult(EMERGENCY_ESCALATION_TEMPLATE)
