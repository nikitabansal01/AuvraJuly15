"""Curated display correction for a visually reviewed, mislabeled cache asset.

Keep saved action facts and historical image-library rows intact. Only the
known mismatched Swimming hero URL is substituted in API presentation.
"""
import hashlib
from typing import Optional

_SWIMMING_MISMATCH = "fadbdee9885c368f6ae226cd4cadfa4f11dede05f65474341ed520a0b12014f9"
_SWIMMING_REVIEWED_IMAGE = 'https://res.cloudinary.com/dqir7ej4j/image/upload/v1791714595/action-plan-images/auvra/movement_hero_1791714585365_a2a6ea7b.jpg'

def action_image_url(title: str, original: Optional[str]) -> Optional[str]:
    if isinstance(original, str) and title.strip().casefold() == "swimming" and hashlib.sha256(original.encode()).hexdigest() == _SWIMMING_MISMATCH:
        return _SWIMMING_REVIEWED_IMAGE
    return original
