"""Shared helpers for chatbot endpoint contract, telemetry, and idempotency."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


INSIGHT_KEYS: List[str] = [
    "ui_elements",
    "last_options_shown",
    "user_preferences",
    "action_plan",
    "streak",
    "workflow_stage",
]


def ensure_actionable_insights(
    raw: Optional[Dict[str, Any]],
    *,
    flow: str,
) -> Dict[str, Any]:
    """Guarantee stable insight keys so frontend/state replay stays deterministic."""
    insights = dict(raw or {})
    ui_seed = insights.get("ui_elements")
    if not isinstance(ui_seed, list):
        ui_seed = insights.get("ui_blocks")
    if not isinstance(ui_seed, list):
        ui_seed = []

    insights.setdefault("flow", flow)
    insights.setdefault("ui_elements", ui_seed)
    insights.setdefault("last_options_shown", None)
    insights.setdefault("user_preferences", {})
    insights.setdefault("action_plan", [])
    insights.setdefault("streak", 0)
    insights.setdefault("workflow_stage", None)
    return insights


def normalize_history_messages(history: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Normalize history message shape across chatbot flows."""
    out: List[Dict[str, Any]] = []
    for msg in history or []:
        out.append(
            {
                "id": str(msg.get("id") or ""),
                "text": str(msg.get("text") or ""),
                "isBot": bool(msg.get("isBot")),
                "created_at": msg.get("created_at") or datetime.utcnow().isoformat(),
                "ui_blocks": msg.get("ui_blocks") if isinstance(msg.get("ui_blocks"), list) else [],
            }
        )
    return out


def build_chatbot_response_payload(
    *,
    thread_id: str,
    local_date: str,
    history: List[Dict[str, Any]],
    tap_options: List[Dict[str, Any]],
    ui_blocks: List[Any],
    actionable_insights: Optional[Dict[str, Any]],
    flow: str,
    trace: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a stable response payload shape used across chatbot endpoints."""
    normalized_insights = ensure_actionable_insights(actionable_insights, flow=flow)
    payload: Dict[str, Any] = {
        "thread_id": thread_id,
        "local_date": local_date,
        "history": normalize_history_messages(history),
        "tap_options": tap_options or [],
        "ui_blocks": ui_blocks or [],
        "actionable_insights": normalized_insights,
    }
    if trace:
        payload["trace"] = trace
    return payload


def ensure_event_not_duplicate(
    *,
    insights: Dict[str, Any],
    idempotency_key: Optional[str],
    max_keys: int = 300,
) -> bool:
    """Return True if event key has not been seen and record it; False for duplicates."""
    if not idempotency_key:
        return True

    seen = list(insights.get("processed_event_keys") or [])
    if idempotency_key in seen:
        return False

    seen.append(idempotency_key)
    insights["processed_event_keys"] = seen[-max_keys:]
    return True
