"""Non-identifying image prompt templates for plan artwork providers."""

from __future__ import annotations

import re

from app.v2.domain.plan_generation import CANONICAL_VARIANT_TYPES


_WIRE_PATTERN = re.compile(
    r"\Aauvra-image-v1\|slot=(?P<slot>[1-4])\|role=(?P<role>hero|variant)"
    r"\|variant=(?P<variant>none|low_energy|time_limited|no_equipment)\Z"
)
_VARIANT_COMPOSITIONS = {
    "none": "a practical everyday wellbeing activity",
    "low_energy": "a seated or low-energy wellbeing activity",
    "time_limited": "a short, practical wellbeing activity",
    "no_equipment": "an accessible wellbeing activity without equipment",
}
_LAYOUTS = {
    "1": "wide composition",
    "2": "close activity composition",
    "3": "overhead composition",
    "4": "side-view composition",
}


def image_prompt_token(*, slot: int, role: str, variant_type: str | None) -> str:
    """Create an internal token, never a model-authored prompt, for image generation."""

    if slot not in {1, 2, 3, 4} or role not in {"hero", "variant"}:
        raise ValueError("invalid_plan_image_template")
    variant = variant_type or "none"
    if (role == "hero" and variant != "none") or (
        role == "variant" and variant not in CANONICAL_VARIANT_TYPES
    ):
        raise ValueError("invalid_plan_image_template")
    return f"auvra-image-v1|slot={slot}|role={role}|variant={variant}"


def sanitize_image_prompt(prompt: str) -> str:
    """Render only an allowlisted template before a third-party image call."""

    match = _WIRE_PATTERN.fullmatch(prompt)
    if match is None:
        return _render_template("none", "hero", "1")
    return _render_template(
        match.group("variant"), match.group("role"), match.group("slot")
    )


def _render_template(variant: str, role: str, slot: str) -> str:
    composition = _VARIANT_COMPOSITIONS[variant]
    framing = (
        "editorial hero composition"
        if role == "hero"
        else "supporting card composition"
    )
    return (
        f"Warm inclusive editorial illustration of {composition}, {framing}, {_LAYOUTS[slot]}, "
        "natural light, "
        "no text, no labels, no logos, no medical setting, no identifiable person."
    )
