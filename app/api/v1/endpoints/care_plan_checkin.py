"""Care Plan Check-in API endpoints.

Daily threaded chat that stores one thread per user per local date.
Used for:
- daily adherence / blockers / plan-change requests
- generating condensed insights for action plan updates & replacements

Mobile:
- start returns history immediately
- respond returns updated history + tap options
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Any, Dict, List, Optional

from uuid import uuid4

from app.api.v1.endpoints.auth import get_current_user
from app.core.database import get_db
from app.models.ui_blocks import UIBlock, UIBlockAction, UIEventRequest
from app.services.care_plan_checkin_service import CarePlanCheckInService

router = APIRouter()


class TapOption(BaseModel):
    id: str
    text: str


class ChatMessage(BaseModel):
    id: str
    text: str
    isBot: bool


class StartCarePlanCheckInResponse(BaseModel):
    thread_id: str
    local_date: str
    history: List[ChatMessage]
    tap_options: List[TapOption] = []
    ui_blocks: List[UIBlock] = []


class RespondCarePlanCheckInRequest(BaseModel):
    thread_id: str
    message_text: str


class RespondCarePlanCheckInResponse(BaseModel):
    thread_id: str
    local_date: str
    history: List[ChatMessage]
    tap_options: List[TapOption] = []
    actionable_insights: Dict[str, Any] = {}
    ui_blocks: List[UIBlock] = []


def _ensure_tap_option(tap_options: List[Dict[str, str]], option_id: str, text: str) -> List[Dict[str, str]]:
    existing_ids = {t.get("id") for t in (tap_options or [])}
    if option_id in existing_ids:
        return tap_options
    return list(tap_options or []) + [{"id": option_id, "text": text}]


def _default_ui_blocks_for_start() -> List[UIBlock]:
    """Minimal dynamic UI blocks for Gemini-like behavior.

    These are optional and can be empty; mobile should render if present.
    """
    return [
        UIBlock(
            id=str(uuid4()),
            type="quick_actions",
            title="Care plan",
            subtitle="Only shows when it helps",
            actions=[
                UIBlockAction(
                    id="open_plan_manager",
                    title="Manage plan",
                    action_type="open_modal",
                    payload={"modal": "PlanManagerModal"},
                )
            ],
            dismissible=True,
            priority="low",
            analytics={"surface": "care_plan_checkin", "reason": "entry_point"},
        )
    ]


def _default_tap_options() -> List[Dict[str, str]]:
    return [
        {"id": "want-to-change", "text": "👎 I want to change it"},
        {"id": "alternate-suggestions", "text": "🔁 I want alternate suggestions"},
    ]


def _should_exclude_tap_option(option_id: str, text: str) -> bool:
    oid = (option_id or "").strip().lower()
    t = (text or "").strip().lower()
    # Product decision: do not show "skip actions" in care plan check-in.
    if oid == "skip-actions":
        return True
    if "skip" in t and "action" in t:
        return True
    return False


def _looks_like_confirmation(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    return t in {"ok", "okay", "okk", "yes", "y", "yep", "yeah", "sure", "do it", "go ahead", "confirm"}


def _looks_like_change_intent(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in ["change", "replace", "swap", "skip", "alternate", "another option", "not for me"])


def _looks_like_alternate_suggestions_request(text: str) -> bool:
    """True when the user explicitly asks for alternate suggestions (vs generic change intent)."""
    t = (text or "").strip().lower()
    if not t:
        return False

    # When sent as option id (rare in current mobile), still handle.
    if t in {"alternate-suggestions", "alternate_suggestions", "alternates"}:
        return True

    # When sent as option text (current mobile sends text).
    if "alternate suggestion" in t or "alternate suggestions" in t:
        return True
    if "want alternate" in t or "want alternates" in t:
        return True

    # Common natural-language variants.
    # We intentionally capture "alternatives" / "another option" phrasing so typed
    # requests trigger the same staged UI flow as the chip.
    if "alternative" in t or "alternatives" in t:
        return True
    if "another option" in t or "other option" in t or "different option" in t:
        return True
    if "something else" in t:
        return True

    # “Can you suggest…” patterns.
    if ("suggest" in t or "recommend" in t or "ideas" in t or "options" in t) and (
        "alternate" in t or "alternative" in t or "swap" in t or "replace" in t
    ):
        return True

    # Emoji label in default chip.
    if "🔁" in text:
        return True

    return False


def _looks_like_personalize_intent(text: str) -> bool:
    """True when user wants to change personal settings (diet, allergies, etc.)."""
    t = (text or "").strip().lower()
    if not t:
        return False
    
    # Tap option ID/Text
    if t in {"want-to-personalize", "personalize", "settings", "preferences"}:
        return True
    if "want to personalize" in t:
        return True
        
    # Natural language
    if "diet" in t or "allergy" in t or "allergies" in t or "cuisine" in t:
        return True
    if "change my settings" in t or "update my profile" in t:
        return True
        
    # Emoji
    if "⚙️" in text:
        return True
        
    return False


def _pick_replace_block(items: List[Dict[str, Any]]) -> UIBlock:
    actions: List[UIBlockAction] = []
    for it in items[:8]:
        item_id = it.get("item_id")
        title = (it.get("title") or "").strip()
        if not item_id or not title:
            continue
        actions.append(
            UIBlockAction(
                id=f"care_plan_replace_pick_{item_id}",
                title=f"Replace: {title}",
                action_type="submit_event",
                payload={"item_id": int(item_id)},
                style="secondary",
            )
        )

    actions.append(
        UIBlockAction(
            id="open_plan_manager",
            title="Open full plan manager",
            action_type="open_modal",
            payload={"modal": "PlanManagerModal"},
            style="ghost",
        )
    )

    return UIBlock(
        id=str(uuid4()),
        type="quick_actions",
        title="Which action should we change?",
        subtitle="Pick one — I’ll replace it with a fresh alternative.",
        actions=actions,
        dismissible=True,
        priority="normal",
        analytics={"surface": "care_plan_checkin", "reason": "change_intent"},
    )


def _pick_alternate_item_block(items: List[Dict[str, Any]]) -> UIBlock:
    """Picker for 'alternate suggestions' flow.

    This intentionally differs from the generic replace picker wording so the UX
    is: acknowledge alternates -> ask which item -> show alternates -> replace.
    """
    actions: List[UIBlockAction] = []
    for it in items[:8]:
        item_id = it.get("item_id")
        title = (it.get("title") or "").strip()
        if not item_id or not title:
            continue
        actions.append(
            UIBlockAction(
                id=f"care_plan_alternate_pick_{item_id}",
                title=title,
                action_type="submit_event",
                payload={"item_id": int(item_id)},
                style="secondary",
            )
        )

    actions.append(
        UIBlockAction(
            id="open_plan_manager",
            title="Open full plan manager",
            action_type="open_modal",
            payload={"modal": "PlanManagerModal"},
            style="ghost",
        )
    )

    return UIBlock(
        id=str(uuid4()),
        type="quick_actions",
        title="Which action do you want alternates for?",
        subtitle="Pick one — I’ll show a few swap-in options.",
        actions=actions,
        dismissible=True,
        priority="normal",
        analytics={"surface": "care_plan_checkin", "reason": "alternate_suggestions"},
    )


def _pick_alternate_candidate_block(item_id: int, candidates: List[Dict[str, Any]]) -> UIBlock:
    actions: List[UIBlockAction] = []
    for c in (candidates or [])[:6]:
        cid = (c.get("candidate_id") or "").strip()
        title = (c.get("title") or "").strip()
        if not cid or not title:
            continue
        actions.append(
            UIBlockAction(
                id=f"care_plan_alternate_choose_{cid}",
                title=title,
                action_type="submit_event",
                payload={"item_id": int(item_id), "candidate_id": cid},
                style="primary" if len(actions) == 0 else "secondary",
            )
        )

    actions.append(
        UIBlockAction(
            id="care_plan_alternate_cancel",
            title="Never mind",
            action_type="submit_event",
            payload={},
            style="ghost",
        )
    )

    return UIBlock(
        id=str(uuid4()),
        type="quick_actions",
        title="Here are a few alternatives",
        subtitle="Pick one and I’ll swap it into your plan.",
        actions=actions,
        dismissible=True,
        priority="high",
        analytics={"surface": "care_plan_checkin", "reason": "alternate_candidates"},
    )


def _confirm_replace_block(item_id: int) -> UIBlock:
    return UIBlock(
        id=str(uuid4()),
        type="quick_actions",
        title="Replace this action?",
        subtitle="I’ll swap it with a similar alternative (uses a plan refresh token if available).",
        actions=[
            UIBlockAction(
                id="care_plan_replace_confirm",
                title="Yes, replace it",
                action_type="submit_event",
                payload={"item_id": int(item_id), "reason": "User requested change via chat"},
                style="primary",
            ),
            UIBlockAction(
                id="care_plan_replace_cancel",
                title="No, keep it",
                action_type="submit_event",
                payload={},
                style="secondary",
            ),
        ],
        dismissible=True,
        priority="high",
        analytics={"surface": "care_plan_checkin", "reason": "confirm_replace"},
    )


class TranscribeResponse(BaseModel):
    text: str


def _open_plan_manager_block() -> UIBlock:
    return UIBlock(
        id=str(uuid4()),
        type="open_modal",
        title="Manage plan",
        payload={"modal": "PlanManagerModal"},
        actions=[
            UIBlockAction(
                id="confirm_open",
                title="Open",
                action_type="open_modal",
                payload={"modal": "PlanManagerModal"},
            )
        ],
        dismissible=True,
        priority="normal",
        analytics={"surface": "care_plan_checkin", "reason": "ui_event"},
    )


@router.post("/start", response_model=StartCarePlanCheckInResponse)
async def start_care_plan_checkin(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        uid = current_user["uid"]
        service = CarePlanCheckInService(db)
        thread = service.get_or_create_today_thread(uid)
        history = service.format_history_for_mobile(thread)

        # Default tap options (LLM can override on respond)
        tap_options = _default_tap_options()
        tap_options = _ensure_tap_option(tap_options, "manage_plan", "🧩 Manage plan")

        return {
            "thread_id": thread.id,
            "local_date": thread.local_date.isoformat(),
            "history": history,
            "tap_options": tap_options,
            "ui_blocks": _default_ui_blocks_for_start(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/respond", response_model=RespondCarePlanCheckInResponse)
async def respond_care_plan_checkin(
    payload: RespondCarePlanCheckInRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        uid = current_user["uid"]
        service = CarePlanCheckInService(db)

        # If there's a pending replace and the user confirms naturally, execute it.
        thread = service.get_thread_by_id(uid, payload.thread_id)
        pending = (thread.actionable_insights or {}).get("pending_replace") if thread else None
        if pending and _looks_like_confirmation(payload.message_text):
            item_id = int(pending.get("item_id") or 0)
            reason = (pending.get("reason") or "User confirmed replace").strip()
            if item_id:
                # Append the user's confirmation
                raw = list(thread.raw_messages or [])
                raw.append(
                    {
                        "id": str(uuid4()),
                        "role": "user",
                        "content": payload.message_text,
                        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                    }
                )
                thread.raw_messages = raw
                # Clear pending
                ai = dict(thread.actionable_insights or {})
                ai.pop("pending_replace", None)
                thread.actionable_insights = ai
                db.add(thread)
                db.commit()
                db.refresh(thread)

                result = await service.replace_action_item(uid, item_id, reason)
                # Append bot response
                raw = list(thread.raw_messages or [])
                if result.get("success"):
                    repl = result.get("replacement_action") or {}
                    repl_title = (repl.get("title") or repl.get("specific_action") or "a fresh alternative").strip()
                    raw.append(
                        {
                            "id": str(uuid4()),
                            "role": "bot",
                            "content": f"Done — I replaced it with: {repl_title}. Want to change anything else?",
                            "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                        }
                    )
                else:
                    raw.append(
                        {
                            "id": str(uuid4()),
                            "role": "bot",
                            "content": result.get("error") or "Sorry — I couldn't replace that right now.",
                            "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                        }
                    )
                thread.raw_messages = raw
                db.add(thread)
                db.commit()
                db.refresh(thread)

                history = service.format_history_for_mobile(thread)
                tap_options = _ensure_tap_option(_default_tap_options(), "manage_plan", "🧩 Manage plan")
                return {
                    "thread_id": thread.id,
                    "local_date": thread.local_date.isoformat(),
                    "history": history,
                    "tap_options": tap_options,
                    "actionable_insights": thread.actionable_insights or {},
                    "ui_blocks": [_open_plan_manager_block()],
                }

        # ═══════════════════════════════════════════════════════════════════════
        # 2. PENDING ALTERNATE FLOW HANDLERS (typed input support)
        # ═══════════════════════════════════════════════════════════════════════
        pending_alternate = (thread.actionable_insights or {}).get("pending_alternate") if thread else None
        
        if pending_alternate:
            stage = pending_alternate.get("stage")
            items = service.get_plan_items_for_ui(uid, limit=8)
            
            # Stage: User needs to pick which item to get alternates for
            if stage == "choose_item" or stage is None:
                # Try to match typed text to an item
                user_text_lower = (payload.message_text or "").strip().lower()
                matched_item_id = None
                matched_title = None
                
                for item in items:
                    item_title = (item.get("title") or "").strip().lower()
                    # Flexible matching: exact, substring either way
                    if (item_title == user_text_lower or 
                        user_text_lower in item_title or 
                        item_title in user_text_lower or
                        # Also check for key words (e.g., "salmon" matches "Grilled Salmon Bowl")
                        any(word in item_title for word in user_text_lower.split() if len(word) > 2)):
                        matched_item_id = item.get("item_id")
                        matched_title = item.get("title")
                        break
                
                if matched_item_id:
                    # Append user message
                    raw = list(thread.raw_messages or [])
                    raw.append({
                        "id": str(uuid4()),
                        "role": "user",
                        "content": payload.message_text,
                        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                    })
                    thread.raw_messages = raw
                    
                    # Generate candidates for this item
                    candidates_result = await service.generate_alternate_candidates(
                        uid, item_id=matched_item_id, reason="User selected via text"
                    )
                    
                    if candidates_result.get("success"):
                        # Update pending state to choose_candidate
                        ai_data = dict(thread.actionable_insights or {})
                        ai_data["pending_alternate"] = {"stage": "choose_candidate", "item_id": matched_item_id, "reason": "User selected via text"}
                        ai_data["alternate_candidates"] = candidates_result.get("candidates_by_id") or {}
                        thread.actionable_insights = ai_data
                        
                        # Add bot response
                        raw = list(thread.raw_messages or [])
                        raw.append({
                            "id": str(uuid4()),
                            "role": "bot",
                            "content": f"Great! Here are some alternatives for {matched_title}. Which one would you like?",
                            "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                        })
                        thread.raw_messages = raw
                        db.add(thread)
                        db.commit()
                        db.refresh(thread)
                        
                        history = service.format_history_for_mobile(thread)
                        tap_options = _ensure_tap_option(_default_tap_options(), "manage_plan", "🧩 Manage plan")
                        return {
                            "thread_id": thread.id,
                            "local_date": thread.local_date.isoformat(),
                            "history": history,
                            "tap_options": tap_options,
                            "actionable_insights": thread.actionable_insights or {},
                            "ui_blocks": [_pick_alternate_candidate_block(matched_item_id, candidates_result.get("candidates_ui") or [])],
                        }
            
            # Stage: User needs to pick which candidate to use
            elif stage == "choose_candidate":
                item_id = pending_alternate.get("item_id")
                candidates_by_id = (thread.actionable_insights or {}).get("alternate_candidates") or {}
                user_text_lower = (payload.message_text or "").strip().lower()
                
                # Check for cancellation first
                if any(word in user_text_lower for word in ["cancel", "nevermind", "never mind", "no", "forget", "skip"]):
                    # Clear pending state
                    ai_data = dict(thread.actionable_insights or {})
                    ai_data.pop("pending_alternate", None)
                    ai_data.pop("alternate_candidates", None)
                    thread.actionable_insights = ai_data
                    
                    raw = list(thread.raw_messages or [])
                    raw.append({
                        "id": str(uuid4()),
                        "role": "user",
                        "content": payload.message_text,
                        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                    })
                    raw.append({
                        "id": str(uuid4()),
                        "role": "bot",
                        "content": "No problem! Let me know if you'd like to change anything else.",
                        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                    })
                    thread.raw_messages = raw
                    db.add(thread)
                    db.commit()
                    db.refresh(thread)
                    
                    history = service.format_history_for_mobile(thread)
                    tap_options = _ensure_tap_option(_default_tap_options(), "manage_plan", "🧩 Manage plan")
                    return {
                        "thread_id": thread.id,
                        "local_date": thread.local_date.isoformat(),
                        "history": history,
                        "tap_options": tap_options,
                        "actionable_insights": thread.actionable_insights or {},
                        "ui_blocks": [],  # Clear UI blocks
                    }
                
                # Try to match typed text to a candidate
                matched_candidate_id = None
                matched_candidate = None
                
                # Check for ordinal selection (first, second, 1, 2, etc.)
                ordinal_map = {"first": 0, "1": 0, "one": 0, "second": 1, "2": 1, "two": 1, "third": 2, "3": 2, "three": 2}
                for ordinal, idx in ordinal_map.items():
                    if ordinal in user_text_lower:
                        candidate_keys = list(candidates_by_id.keys())
                        if idx < len(candidate_keys):
                            matched_candidate_id = candidate_keys[idx]
                            matched_candidate = candidates_by_id[matched_candidate_id]
                            break
                
                # Also try to match by candidate title
                if not matched_candidate_id:
                    for cid, candidate in candidates_by_id.items():
                        candidate_title = (candidate.get("title") or candidate.get("specific_action") or "").strip().lower()
                        if candidate_title and (
                            candidate_title in user_text_lower or 
                            user_text_lower in candidate_title or
                            any(word in candidate_title for word in user_text_lower.split() if len(word) > 2)
                        ):
                            matched_candidate_id = cid
                            matched_candidate = candidate
                            break
                
                # Check for confirmation (ok, yes, sure) - use first candidate
                if not matched_candidate_id and any(word in user_text_lower for word in ["ok", "okay", "yes", "sure", "sounds good", "do it", "go ahead"]):
                    candidate_keys = list(candidates_by_id.keys())
                    if candidate_keys:
                        matched_candidate_id = candidate_keys[0]
                        matched_candidate = candidates_by_id[matched_candidate_id]
                
                if matched_candidate_id and matched_candidate and item_id:
                    # Execute the replacement
                    raw = list(thread.raw_messages or [])
                    raw.append({
                        "id": str(uuid4()),
                        "role": "user",
                        "content": payload.message_text,
                        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                    })
                    thread.raw_messages = raw
                    
                    result = await service.replace_action_item_with_candidate(
                        uid, item_id, matched_candidate, "User selected via text"
                    )
                    
                    # Clear pending state
                    ai_data = dict(thread.actionable_insights or {})
                    ai_data.pop("pending_alternate", None)
                    ai_data.pop("alternate_candidates", None)
                    thread.actionable_insights = ai_data
                    
                    raw = list(thread.raw_messages or [])
                    if result.get("success"):
                        repl_title = matched_candidate.get("title") or matched_candidate.get("specific_action") or "the new option"
                        raw.append({
                            "id": str(uuid4()),
                            "role": "bot",
                            "content": f"Done! I replaced it with: {repl_title}. Your plan is updated! 🎉",
                            "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                        })
                    else:
                        raw.append({
                            "id": str(uuid4()),
                            "role": "bot",
                            "content": result.get("error") or "Sorry, I couldn't make that change right now.",
                            "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                        })
                    
                    thread.raw_messages = raw
                    db.add(thread)
                    db.commit()
                    db.refresh(thread)
                    
                    history = service.format_history_for_mobile(thread)
                    tap_options = _ensure_tap_option(_default_tap_options(), "manage_plan", "🧩 Manage plan")
                    return {
                        "thread_id": thread.id,
                        "local_date": thread.local_date.isoformat(),
                        "history": history,
                        "tap_options": tap_options,
                        "actionable_insights": thread.actionable_insights or {},
                        "ui_blocks": [],  # Clear UI blocks after completion
                    }

        # ═══════════════════════════════════════════════════════════════════════
        # 3. Personalization Intent Handling
        # ═══════════════════════════════════════════════════════════════════════
        if _looks_like_personalize_intent(payload.message_text):
            # Save user message first
            raw = list(thread.raw_messages or [])
            raw.append({
                "id": str(uuid4()),
                "role": "user",
                "content": payload.message_text,
                "created_at": __import__("datetime").datetime.utcnow().isoformat(),
            })
            thread.raw_messages = raw
            db.add(thread)
            db.commit()

            # Check rewards
            from app.services.reward_service import RewardService
            reward_service = RewardService(db)
            rewards_status = reward_service.get_all_rewards_status(uid)
            rewards = rewards_status.get("rewards", [])
            
            # Filter for personalization rewards
            personalization_rewards = [r for r in rewards if r.get("effect") == "personalization"]
            unlocked = [r for r in personalization_rewards if r.get("state") in ("claimed", "available")]
            locked = [r for r in personalization_rewards if r.get("state") == "locked"]

            response_lines = []
            
            if unlocked:
                options_list = "\n".join([f"• {r['title']}" for r in unlocked])
                response_lines.append(f"✅ **Unlocked Settings:**\n{options_list}")
                response_lines.append(f"\nWhat would you like to update? (e.g., 'I'm now vegetarian' or 'Update allergies')")
                # Add instruction for AI context in FUTURE messages
                # (We don't do it here because we are bypassing AI for this turn)
            else:
                response_lines.append(f"🔒 **Personalization is locked!**")
                if locked:
                    next_reward = min(locked, key=lambda x: x["required_streak"])
                    days_left = next_reward.get("days_remaining", 0)
                    response_lines.append(f"Keep your streak for **{days_left} more days** to unlock **{next_reward['title']}**!")
                else:
                    response_lines.append("Check the Rewards tab to see what you can unlock.")

            bot_text = "\n\n".join(response_lines)

            # Append bot message
            raw = list(thread.raw_messages or [])
            raw.append({
                "id": str(uuid4()),
                "role": "bot",
                "content": bot_text,
                "created_at": __import__("datetime").datetime.utcnow().isoformat(),
            })
            thread.raw_messages = raw
            db.add(thread)
            db.commit()
            db.refresh(thread)

            history = service.format_history_for_mobile(thread)
            tap_options = _ensure_tap_option(_default_tap_options(), "manage_plan", "🧩 Manage plan")
            
            return {
                "thread_id": thread.id,
                "local_date": thread.local_date.isoformat(),
                "history": history,
                "tap_options": tap_options,
                "actionable_insights": thread.actionable_insights or {},
                "ui_blocks": _default_ui_blocks_for_start(), # Reset UI blocks
            }

        thread, ai_response = await service.respond(uid, payload.thread_id, payload.message_text)
        history = service.format_history_for_mobile(thread)

        tap_options = _default_tap_options()
        for t in (ai_response.tap_options or []):
            if _should_exclude_tap_option(t.id, t.text):
                continue
            tap_options = _ensure_tap_option(tap_options, t.id, t.text)
        tap_options = _ensure_tap_option(tap_options, "manage_plan", "🧩 Manage plan")

        ui_blocks: List[UIBlock] = []
        
        # ═══════════════════════════════════════════════════════════════════════
        # Check if user is doing general chat or completed a previous flow
        # If so, clear any stale pending states
        # ═══════════════════════════════════════════════════════════════════════
        user_action = getattr(ai_response.insights, "user_action", None) if ai_response.insights else None
        pending_alternate = (thread.actionable_insights or {}).get("pending_alternate")
        pending_replace = (thread.actionable_insights or {}).get("pending_replace")
        
        # Clear stale pending states if:
        # 1. User is doing general chat (not making a selection)
        # 2. User explicitly confirmed/cancelled (action already handled above)
        # 3. Neither alternate suggestions nor change intent is detected
        should_clear_pending = False
        if user_action in ("general_chat", "confirm", "cancel"):
            should_clear_pending = True
        elif not _looks_like_change_intent(payload.message_text) and not _looks_like_alternate_suggestions_request(payload.message_text):
            # User is not expressing change/alternate intent, likely moved on
            has_selected_item = bool(getattr(ai_response.insights, "selected_item_title", None))
            if not has_selected_item:
                should_clear_pending = True
        
        if should_clear_pending and (pending_alternate or pending_replace):
            ai_data = dict(thread.actionable_insights or {})
            ai_data.pop("pending_alternate", None)
            ai_data.pop("pending_replace", None)
            ai_data.pop("alternate_candidates", None)
            thread.actionable_insights = ai_data
            db.add(thread)
            db.commit()
            db.refresh(thread)
            # Don't generate new UI blocks - just return the clean state
            return {
                "thread_id": thread.id,
                "local_date": thread.local_date.isoformat(),
                "history": history,
                "tap_options": tap_options,
                "actionable_insights": thread.actionable_insights or {},
                "ui_blocks": [],  # Clear UI blocks
            }
        
        # Get plan items for matching
        items = service.get_plan_items_for_ui(uid, limit=8)
        
        # Try to match user's text directly to a plan item (for typed responses to pickers)
        user_text_lower = (payload.message_text or "").strip().lower()
        matched_item_id = None
        if items and len(user_text_lower) > 2:  # Only try matching for meaningful text
            for item in items:
                item_title = (item.get("title") or "").strip().lower()
                # Match: exact, user text is substring of item, or item is substring of user text
                if (item_title == user_text_lower or 
                    user_text_lower in item_title or 
                    item_title in user_text_lower):
                    matched_item_id = item.get("item_id")
                    break
        # Use directly matched item, or try AI-extracted item title
        selected_item_id = matched_item_id
        if not selected_item_id and ai_response.insights:
            selected_item_title = getattr(ai_response.insights, "selected_item_title", None)
            if selected_item_title and items:
                selected_item_title_lower = selected_item_title.strip().lower()
                for item in items:
                    item_title = (item.get("title") or "").strip().lower()
                    if (item_title == selected_item_title_lower or 
                        selected_item_title_lower in item_title or 
                        item_title in selected_item_title_lower):
                        selected_item_id = item.get("item_id")
                        break
        
        # If we have a specific item selected, generate alternates directly
        if selected_item_id:
            # Generate candidate alternatives directly
            candidates_result = await service.generate_alternate_candidates(
                uid, item_id=selected_item_id, reason="User requested via chat"
            )
            if candidates_result.get("success"):
                # Store pending alternate + candidates
                ai_data = dict(thread.actionable_insights or {})
                ai_data["pending_alternate"] = {"stage": "choose_candidate", "item_id": selected_item_id, "reason": "User requested via chat"}
                ai_data["alternate_candidates"] = candidates_result.get("candidates_by_id") or {}
                thread.actionable_insights = ai_data
                db.add(thread)
                db.commit()
                db.refresh(thread)
                
                ui_blocks.append(_pick_alternate_candidate_block(selected_item_id, candidates_result.get("candidates_ui") or []))
            else:
                # Fallback to picker if generation failed
                if items:
                    ui_blocks.append(_pick_alternate_item_block(items))
        else:
            # Check for explicit "I want to change it" tap option FIRST
            # This takes priority over AI model flags
            msg_lower = (payload.message_text or "").strip().lower()
            is_explicit_change = (
                "want to change" in msg_lower or 
                "👎" in payload.message_text or
                msg_lower in {"want-to-change", "i want to change it"}
            )
            
            if is_explicit_change:
                # Set pending state for the change flow
                ai_data = dict(thread.actionable_insights or {})
                ai_data["pending_alternate"] = {"stage": "choose_item", "reason": "User wants to change"}
                thread.actionable_insights = ai_data
                db.add(thread)
                db.commit()
                db.refresh(thread)
                # Direct replace flow - show which item to replace
                if items:
                    ui_blocks.append(_pick_alternate_item_block(items))
            else:
                # Check for alternate suggestions request
                wants_alternates = bool(getattr(ai_response.insights, "alternate_suggestions_requested", False))
                if not wants_alternates:
                    wants_alternates = _looks_like_alternate_suggestions_request(payload.message_text)

                if wants_alternates:
                    # Set pending state for the alternate flow
                    ai_data = dict(thread.actionable_insights or {})
                    ai_data["pending_alternate"] = {"stage": "choose_item", "reason": "User wants alternates"}
                    thread.actionable_insights = ai_data
                    db.add(thread)
                    db.commit()
                    db.refresh(thread)
                    
                    if items:
                        ui_blocks.append(_pick_alternate_item_block(items))
                    else:
                        ui_blocks.append(_open_plan_manager_block())
                else:
                    if _looks_like_change_intent(payload.message_text) or (
                        ai_response.insights and ai_response.insights.plan_changes_requested
                    ):
                        # Set pending state for the change flow
                        ai_data = dict(thread.actionable_insights or {})
                        ai_data["pending_alternate"] = {"stage": "choose_item", "reason": "User wants to change"}
                        thread.actionable_insights = ai_data
                        db.add(thread)
                        db.commit()
                        db.refresh(thread)
                        
                        if items:
                            ui_blocks.append(_pick_alternate_item_block(items))

        return {
            "thread_id": thread.id,
            "local_date": thread.local_date.isoformat(),
            "history": history,
            "tap_options": tap_options,
            "actionable_insights": thread.actionable_insights or {},
            "ui_blocks": ui_blocks,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/event", response_model=RespondCarePlanCheckInResponse)
async def care_plan_ui_event(
    payload: UIEventRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Handle structured UI events.

    MVP routing:
    - open modal actions return a UI block instructing the client to open a modal.
    - send_text-style events are converted into the existing `/respond` flow.
    """
    try:
        uid = current_user["uid"]
        if not payload.thread_id:
            raise HTTPException(status_code=400, detail="thread_id is required")

        service = CarePlanCheckInService(db)

        action_id = (payload.action_id or "").strip()
        meta = payload.metadata or {}

        if action_id == "open_plan_manager":
            thread = service.get_thread_by_id(uid, payload.thread_id)
            history = service.format_history_for_mobile(thread)
            tap_options = _ensure_tap_option([], "manage_plan", "🧩 Manage plan")
            return {
                "thread_id": thread.id,
                "local_date": thread.local_date.isoformat(),
                "history": history,
                "tap_options": tap_options,
                "actionable_insights": thread.actionable_insights or {},
                "ui_blocks": [_open_plan_manager_block()],
            }

        # Replace flow (picker -> confirm -> execute)
        if action_id.startswith("care_plan_replace_pick_"):
            try:
                item_id = int(action_id.split("care_plan_replace_pick_", 1)[1])
            except Exception:
                item_id = 0
            thread = service.get_thread_by_id(uid, payload.thread_id)
            display_text = (meta.get("display_text") or "").strip() or "Replace this action"
            if display_text:
                raw = list(thread.raw_messages or [])
                raw.append(
                    {
                        "id": str(uuid4()),
                        "role": "user",
                        "content": display_text,
                        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                    }
                )
                thread.raw_messages = raw
            if item_id:
                ai = dict(thread.actionable_insights or {})
                ai["pending_replace"] = {"item_id": item_id, "reason": "User requested change via UI"}
                thread.actionable_insights = ai
            db.add(thread)
            db.commit()
            db.refresh(thread)
            history = service.format_history_for_mobile(thread)
            tap_options = _ensure_tap_option(_default_tap_options(), "manage_plan", "🧩 Manage plan")
            return {
                "thread_id": thread.id,
                "local_date": thread.local_date.isoformat(),
                "history": history,
                "tap_options": tap_options,
                "actionable_insights": thread.actionable_insights or {},
                "ui_blocks": [_confirm_replace_block(item_id)] if item_id else [],
            }

        if action_id == "care_plan_replace_cancel":
            thread = service.get_thread_by_id(uid, payload.thread_id)
            display_text = (meta.get("display_text") or "").strip() or "Cancel"
            if display_text:
                raw = list(thread.raw_messages or [])
                raw.append(
                    {
                        "id": str(uuid4()),
                        "role": "user",
                        "content": display_text,
                        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                    }
                )
                thread.raw_messages = raw
            ai = dict(thread.actionable_insights or {})
            ai.pop("pending_replace", None)
            thread.actionable_insights = ai
            db.add(thread)
            db.commit()
            db.refresh(thread)
            history = service.format_history_for_mobile(thread)
            tap_options = _ensure_tap_option(_default_tap_options(), "manage_plan", "🧩 Manage plan")
            return {
                "thread_id": thread.id,
                "local_date": thread.local_date.isoformat(),
                "history": history,
                "tap_options": tap_options,
                "actionable_insights": thread.actionable_insights or {},
                "ui_blocks": [],
            }

        if action_id == "care_plan_replace_confirm":
            thread = service.get_thread_by_id(uid, payload.thread_id)
            item_id = int((meta.get("item_id") or 0) or 0)
            reason = (meta.get("reason") or "User requested change via UI").strip()
            result = await service.replace_action_item(uid, item_id, reason) if item_id else {"success": False, "error": "Invalid item"}

            raw = list(thread.raw_messages or [])
            display_text = (meta.get("display_text") or "").strip() or "Yes"
            if display_text:
                raw.append(
                    {
                        "id": str(uuid4()),
                        "role": "user",
                        "content": display_text,
                        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                    }
                )
            if result.get("success"):
                repl = result.get("replacement_action") or {}
                repl_title = (repl.get("title") or repl.get("specific_action") or "a fresh alternative").strip()
                raw.append(
                    {
                        "id": str(uuid4()),
                        "role": "bot",
                        "content": f"Done — I replaced it with: {repl_title}.",
                        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                    }
                )
                # Clear pending on success
                ai = dict(thread.actionable_insights or {})
                ai.pop("pending_replace", None)
                thread.actionable_insights = ai
            else:
                raw.append(
                    {
                        "id": str(uuid4()),
                        "role": "bot",
                        "content": result.get("error") or "Sorry — I couldn't replace that right now.",
                        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                    }
                )

            thread.raw_messages = raw
            db.add(thread)
            db.commit()
            db.refresh(thread)

            history = service.format_history_for_mobile(thread)
            tap_options = _ensure_tap_option(_default_tap_options(), "manage_plan", "🧩 Manage plan")
            return {
                "thread_id": thread.id,
                "local_date": thread.local_date.isoformat(),
                "history": history,
                "tap_options": tap_options,
                "actionable_insights": thread.actionable_insights or {},
                "ui_blocks": [_open_plan_manager_block()],
            }

        # Alternate suggestions flow (pick item -> show candidates -> choose -> execute)
        if action_id.startswith("care_plan_alternate_pick_"):
            try:
                item_id = int(action_id.split("care_plan_alternate_pick_", 1)[1])
            except Exception:
                item_id = 0

            thread = service.get_thread_by_id(uid, payload.thread_id)
            display_text = (meta.get("display_text") or "").strip() or "Show alternates"
            if display_text:
                raw = list(thread.raw_messages or [])
                raw.append(
                    {
                        "id": str(uuid4()),
                        "role": "user",
                        "content": display_text,
                        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                    }
                )
                thread.raw_messages = raw

            if not item_id:
                history = service.format_history_for_mobile(thread)
                tap_options = _ensure_tap_option(_default_tap_options(), "manage_plan", "🧩 Manage plan")
                return {
                    "thread_id": thread.id,
                    "local_date": thread.local_date.isoformat(),
                    "history": history,
                    "tap_options": tap_options,
                    "actionable_insights": thread.actionable_insights or {},
                    "ui_blocks": [],
                }

            # Generate candidate alternatives (store in actionable_insights)
            candidates_result = await service.generate_alternate_candidates(uid, item_id=item_id, reason="User requested alternate suggestions")
            if not candidates_result.get("success"):
                raw = list(thread.raw_messages or [])
                raw.append(
                    {
                        "id": str(uuid4()),
                        "role": "bot",
                        "content": candidates_result.get("error") or "Sorry — I couldn't generate alternates right now.",
                        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                    }
                )
                thread.raw_messages = raw
                db.add(thread)
                db.commit()
                db.refresh(thread)
                history = service.format_history_for_mobile(thread)
                tap_options = _ensure_tap_option(_default_tap_options(), "manage_plan", "🧩 Manage plan")
                return {
                    "thread_id": thread.id,
                    "local_date": thread.local_date.isoformat(),
                    "history": history,
                    "tap_options": tap_options,
                    "actionable_insights": thread.actionable_insights or {},
                    "ui_blocks": [_open_plan_manager_block()],
                }

            # Persist pending alternate + candidates
            ai = dict(thread.actionable_insights or {})
            ai["pending_alternate"] = {"stage": "choose_candidate", "item_id": item_id, "reason": "User requested alternate suggestions"}
            ai["alternate_candidates"] = candidates_result.get("candidates_by_id") or {}
            thread.actionable_insights = ai
            db.add(thread)
            db.commit()
            db.refresh(thread)

            history = service.format_history_for_mobile(thread)
            tap_options = _ensure_tap_option(_default_tap_options(), "manage_plan", "🧩 Manage plan")
            return {
                "thread_id": thread.id,
                "local_date": thread.local_date.isoformat(),
                "history": history,
                "tap_options": tap_options,
                "actionable_insights": thread.actionable_insights or {},
                "ui_blocks": [_pick_alternate_candidate_block(item_id, candidates_result.get("candidates_ui") or [])],
            }

        if action_id == "care_plan_alternate_cancel":
            thread = service.get_thread_by_id(uid, payload.thread_id)
            display_text = (meta.get("display_text") or "").strip() or "Never mind"
            if display_text:
                raw = list(thread.raw_messages or [])
                raw.append(
                    {
                        "id": str(uuid4()),
                        "role": "user",
                        "content": display_text,
                        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                    }
                )
                thread.raw_messages = raw

            ai = dict(thread.actionable_insights or {})
            ai.pop("pending_alternate", None)
            ai.pop("alternate_candidates", None)
            thread.actionable_insights = ai
            db.add(thread)
            db.commit()
            db.refresh(thread)

            history = service.format_history_for_mobile(thread)
            tap_options = _ensure_tap_option(_default_tap_options(), "manage_plan", "🧩 Manage plan")
            return {
                "thread_id": thread.id,
                "local_date": thread.local_date.isoformat(),
                "history": history,
                "tap_options": tap_options,
                "actionable_insights": thread.actionable_insights or {},
                "ui_blocks": [],
            }

        if action_id.startswith("care_plan_alternate_choose_"):
            candidate_id = action_id.split("care_plan_alternate_choose_", 1)[1].strip()
            thread = service.get_thread_by_id(uid, payload.thread_id)
            item_id = int((meta.get("item_id") or 0) or 0)
            display_text = (meta.get("display_text") or "").strip() or "Choose this"

            # Echo user's selection
            if display_text:
                raw = list(thread.raw_messages or [])
                raw.append(
                    {
                        "id": str(uuid4()),
                        "role": "user",
                        "content": display_text,
                        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                    }
                )
                thread.raw_messages = raw

            candidates_by_id = (thread.actionable_insights or {}).get("alternate_candidates") or {}
            chosen = candidates_by_id.get(candidate_id)
            if not chosen or not item_id:
                raw = list(thread.raw_messages or [])
                raw.append(
                    {
                        "id": str(uuid4()),
                        "role": "bot",
                        "content": "Sorry — I lost those options. Want to try again?",
                        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                    }
                )
                thread.raw_messages = raw
                db.add(thread)
                db.commit()
                db.refresh(thread)
                history = service.format_history_for_mobile(thread)
                tap_options = _ensure_tap_option(_default_tap_options(), "manage_plan", "🧩 Manage plan")
                return {
                    "thread_id": thread.id,
                    "local_date": thread.local_date.isoformat(),
                    "history": history,
                    "tap_options": tap_options,
                    "actionable_insights": thread.actionable_insights or {},
                    "ui_blocks": [],
                }

            result = await service.replace_action_item_with_candidate(
                uid,
                item_id=item_id,
                candidate_action=chosen,
                reason="User selected an alternate suggestion",
            )

            raw = list(thread.raw_messages or [])
            if result.get("success"):
                repl = result.get("replacement_action") or {}
                repl_title = (repl.get("title") or repl.get("specific_action") or "a fresh alternative").strip()
                raw.append(
                    {
                        "id": str(uuid4()),
                        "role": "bot",
                        "content": f"Done — I swapped it with: {repl_title}.",
                        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                    }
                )
            else:
                raw.append(
                    {
                        "id": str(uuid4()),
                        "role": "bot",
                        "content": result.get("error") or "Sorry — I couldn't swap that in right now.",
                        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                    }
                )

            # Clear pending/candidates
            ai = dict(thread.actionable_insights or {})
            ai.pop("pending_alternate", None)
            ai.pop("alternate_candidates", None)
            thread.actionable_insights = ai
            thread.raw_messages = raw
            db.add(thread)
            db.commit()
            db.refresh(thread)

            history = service.format_history_for_mobile(thread)
            tap_options = _ensure_tap_option(_default_tap_options(), "manage_plan", "🧩 Manage plan")
            return {
                "thread_id": thread.id,
                "local_date": thread.local_date.isoformat(),
                "history": history,
                "tap_options": tap_options,
                "actionable_insights": thread.actionable_insights or {},
                "ui_blocks": [_open_plan_manager_block()] if result.get("success") else [],
            }

        send_text = (meta.get("send_text") or "").strip()
        if send_text:
            thread, ai_response = await service.respond(uid, payload.thread_id, send_text)
            history = service.format_history_for_mobile(thread)
            tap_options = _default_tap_options()
            for t in (ai_response.tap_options or []):
                if _should_exclude_tap_option(t.id, t.text):
                    continue
                tap_options = _ensure_tap_option(tap_options, t.id, t.text)
            tap_options = _ensure_tap_option(tap_options, "manage_plan", "🧩 Manage plan")
            return {
                "thread_id": thread.id,
                "local_date": thread.local_date.isoformat(),
                "history": history,
                "tap_options": tap_options,
                "actionable_insights": thread.actionable_insights or {},
                "ui_blocks": [],
            }

        # Default: no-op
        thread = service.get_thread_by_id(uid, payload.thread_id)
        history = service.format_history_for_mobile(thread)
        tap_options = _ensure_tap_option(_default_tap_options(), "manage_plan", "🧩 Manage plan")
        return {
            "thread_id": thread.id,
            "local_date": thread.local_date.isoformat(),
            "history": history,
            "tap_options": tap_options,
            "actionable_insights": thread.actionable_insights or {},
            "ui_blocks": [],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_audio(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        uid = current_user["uid"]
        service = CarePlanCheckInService(db)
        text = await service.transcribe_audio(uid, file)
        return {"text": text}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
