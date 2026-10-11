"""Owned edits to the current health answers, preserving historical plans/check-ins."""
from datetime import datetime
from app.core.database import UserProfile, UserResponse
from app.services.root_cause_engine import RootCauseEngine

DIAGNOSIS_OPTIONS = ["PCOS", "PCOD", "Endometriosis", "Dysmenorrhea", "Amenorrhea", "Menorrhagia", "Metrorrhagia",
                     "Cushing's Syndrome", "Premenstrual Syndrome"]
CONCERN_FIELDS = ["period_concerns", "body_concerns", "skin_hair_concerns", "mental_health_concerns", "other_concerns"]
SCORING_FIELDS = ["age", "period_description", "birth_control", "cycle_length", *CONCERN_FIELDS, "top_concern",
                  "diagnosed_conditions", "family_history", "workout_intensity", "sleep_duration", "stress_level"]


def clean_values(value):
    if not isinstance(value, list):
        if isinstance(value, dict):
            value = [item for row in value.values() for item in (row if isinstance(row, list) else [row])]
        else:
            value = []
    result, seen = [], set()
    for item in value:
        if not isinstance(item, str): continue
        item = item.strip()
        if not item or item.lower() in ("none", "none of the above", "others (please specify)") or item.casefold() in seen: continue
        seen.add(item.casefold()); result.append(item)
    return result


def health_record(answers):
    concerns = clean_values([item for field in CONCERN_FIELDS for item in clean_values(getattr(answers, field, None))]) if answers else []
    return {"concerns": concerns, "diagnosis": clean_values(getattr(answers, "diagnosed_conditions", None)) if answers else [],
            "top_concern": getattr(answers, "top_concern", None) if answers else None,
            "diagnosis_options": DIAGNOSIS_OPTIONS}


class ProfileService:
    def __init__(self, db): self.db = db

    def update(self, uid, changes):
        if not uid: raise PermissionError("Sign in to update your profile")
        try:
            profile = self.db.query(UserProfile).filter(UserProfile.uid == uid).with_for_update().first()
            answers = self.db.query(UserResponse).filter(UserResponse.uid == uid).order_by(
                UserResponse.created_at.desc(), UserResponse.id.desc()).with_for_update().first()
            if not profile: raise LookupError("Your profile could not be found")
            if any(field in changes for field in ("top_concern", "diagnosis")) and not answers:
                raise LookupError("Finish your health setup before editing these answers")
            saved = health_record(answers)
            top = changes.get("top_concern", saved["top_concern"])
            if "top_concern" in changes and top not in saved["concerns"] and not (top is None and not saved["concerns"]):
                raise ValueError("Choose a priority from your recorded concerns")
            diagnoses = changes.get("diagnosis", saved["diagnosis"])
            allowed = DIAGNOSIS_OPTIONS + saved["diagnosis"]
            if not isinstance(diagnoses, list) or any(value not in allowed and not (value.startswith("Others: ") and 3 <= len(value[8:].strip()) <= 150) for value in diagnoses):
                raise ValueError("Choose a listed diagnosis or enter a diagnosis given by your clinician")
            diagnoses = clean_values(diagnoses)
            changed_health = top != saved["top_concern"] or set(diagnoses) != set(saved["diagnosis"])
            analysis = None
            if changed_health:
                data = {field: getattr(answers, field, None) for field in SCORING_FIELDS}
                data.update(top_concern=top, diagnosed_conditions=diagnoses)
                # Use the existing assessment rules, including the complete
                # symptom/diagnosis/lifestyle context, before changing any row.
                analysis = RootCauseEngine.analyze_hormone_imbalance(data)
                if (not isinstance(analysis, dict) or "primary_imbalance" not in analysis
                        or (analysis["primary_imbalance"] is not None and not isinstance(analysis["primary_imbalance"], str))
                        or not isinstance(analysis.get("secondary_imbalances"), list)
                        or any(not isinstance(value, str) for value in analysis["secondary_imbalances"])):
                    raise RuntimeError("The updated assessment could not be confirmed")
            if "name" in changes:
                profile.name = changes["name"]
                profile.updated_at = datetime.utcnow()
            if changed_health:
                answers.top_concern = top
                answers.diagnosed_conditions = diagnoses
                answers.primary_hormone = analysis["primary_imbalance"]
                answers.secondary_hormones = analysis["secondary_imbalances"]
                answers.updated_at = datetime.utcnow()
                profile.updated_at = datetime.utcnow()
            result = {"name": profile.name or "", **health_record(answers)}
            self.db.commit()
            return result
        except Exception:
            self.db.rollback()
            raise
