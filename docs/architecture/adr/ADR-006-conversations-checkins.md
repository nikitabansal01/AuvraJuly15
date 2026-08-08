# ADR-006: Typed conversations with normalized messages

- **Status:** Proposed - written owner approval pending
- **Date:** 2026-08-01
- **Owners:** Conversations and check-ins; Symptom observations

## Context

Legacy chat, care-plan, symptom and weekly systems duplicate raw message arrays,
threads and summaries. LangGraph checkpoint state can be mistaken for history.

## Decision

Use typed `conversations`, ordered replay-safe `conversation_messages` and
versioned summaries with an explicit covered-through message ID. Weekly check-ins
use versioned questions/responses and may link to a conversation. Structured
symptom observations are separate domain facts. LangGraph checkpoints remain
expiring vendor runtime state only.

## Consequences and verification

Conversation type replaces separate table families while preserving behavior.
Deletion and retention cover messages, summaries and checkpoints. Tests cover
message retry/order, summary coverage/factuality, type isolation, checkpoint loss
and reconstruction from canonical history.

