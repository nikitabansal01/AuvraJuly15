"""UI Blocks: structured, Gemini-like dynamic UI payloads.

The frontend is a "dumb renderer":
- Backend decides *when* to show UI.
- Mobile renders blocks and sends structured UI events back.

This module is intentionally decoupled from chat/session models to avoid circular imports.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


UIBlockPriority = Literal["low", "normal", "high"]
UIEventType = Literal["action", "dismiss", "slider_submit", "form_submit"]


class UIBlockAction(BaseModel):
    """A single action/CTA rendered on a UI block."""

    id: str
    title: str

    # The frontend can handle some action types locally (e.g., open_modal).
    # For everything else, it should POST a UIEvent to the backend.
    action_type: Literal["submit_event", "open_modal", "send_text"] = "submit_event"

    payload: Dict[str, Any] = Field(default_factory=dict)
    style: Optional[Literal["primary", "secondary", "destructive", "ghost"]] = None
    requires_confirmation: bool = False


class UIBlock(BaseModel):
    """A structured UI element shown inline in the chat."""

    id: str
    type: str

    title: Optional[str] = None
    subtitle: Optional[str] = None

    payload: Dict[str, Any] = Field(default_factory=dict)
    actions: List[UIBlockAction] = Field(default_factory=list)

    dismissible: bool = True
    priority: UIBlockPriority = "normal"
    expires_at: Optional[datetime] = None

    analytics: Optional[Dict[str, Any]] = None


class UIEventRequest(BaseModel):
    """A structured UI interaction event from the frontend."""

    # One of these is expected depending on which surface emitted the event.
    thread_id: Optional[str] = None
    session_id: Optional[str] = None

    block_id: str
    event_type: UIEventType = "action"

    action_id: Optional[str] = None
    value: Optional[Any] = None
    fields: Optional[Dict[str, Any]] = None

    idempotency_key: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
