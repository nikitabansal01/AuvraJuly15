"""Source-controlled, reviewable prompt contracts for conversation replies.

These contracts are deliberately small.  They are product policy, not a claim
of clinical approval; production release remains gated on owner/clinical
staging approval.
"""
from __future__ import annotations

from dataclasses import dataclass


PROMPT_VERSION = "conversation.v1"
EMERGENCY_ESCALATION_TEMPLATE = (
    "I’m sorry you’re dealing with this. Please seek urgent help now: call your local "
    "emergency number or go to the nearest emergency department. If you can, contact "
    "someone you trust to stay with you."
)


@dataclass(frozen=True, slots=True)
class ConversationPromptContract:
    thread_type: str
    instructions: str


_CONTRACTS = {
    "general": ConversationPromptContract("general", "Offer supportive, non-diagnostic guidance."),
    "care_plan": ConversationPromptContract(
        "care_plan", "Discuss the recorded care plan without prescribing."
    ),
    "symptom_checkin": ConversationPromptContract(
        "symptom_checkin", "Encourage appropriate professional care; do not diagnose."
    ),
    "support": ConversationPromptContract(
        "support", "Be empathetic and practical; do not present as a clinician."
    ),
    "weekly_checkin": ConversationPromptContract(
        "weekly_checkin",
        "Only refer to definition-owned weekly-check-in questions; never invent questions.",
    ),
}


def prompt_contract(thread_type: str) -> ConversationPromptContract:
    try:
        return _CONTRACTS[thread_type]
    except KeyError as exc:
        raise ValueError("unsupported_conversation_type") from exc
