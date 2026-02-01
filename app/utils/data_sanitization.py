"""
═══════════════════════════════════════════════════════════════════════════════
AUVRA - Data Sanitization Layer
═══════════════════════════════════════════════════════════════════════════════

This module provides a SINGLE SOURCE OF TRUTH for cleaning user-submitted health
data before storage or use in AI prompts.

PROBLEM THIS SOLVES:
- UI options like "None of the above" are selections, NOT medical conditions
- When stored as medical data, they leak into LLM prompts causing nonsense like:
  "I picked this for your None of the above because..."
  
DESIGN PRINCIPLES:
1. One place to define invalid values (not scattered filters everywhere)
2. Clean data at INGESTION time, not at every usage point
3. Fail-safe: unknown garbage values are also filtered
4. Preserve meaningful data: only remove known UI placeholders

USAGE:
    from app.utils.data_sanitization import sanitize_health_data, sanitize_list_field
    
    # Sanitize an entire user response dict
    clean_data = sanitize_health_data(raw_data)
    
    # Sanitize a specific list field
    clean_conditions = sanitize_list_field(["PCOS", "None of the above"])
    # Returns: ["PCOS"]
"""

from typing import List, Dict, Any, Optional, Union
import logging

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS: UI Placeholder Values That Are NOT Medical Data
# ═══════════════════════════════════════════════════════════════════════════════

# These are UI options that indicate "nothing selected" - NOT actual conditions/symptoms
UI_PLACEHOLDER_VALUES = frozenset([
    # Exact matches (case-insensitive)
    "none of the above",
    "none",
    "none of above",
    "not applicable",
    "n/a",
    "na",
    "nothing",
    "no",
    "skip",
    "skipped",
    # These are prompts for user input, not conditions
    "others (please specify)",
    "other (please specify)", 
    "please specify",
    "specify",
    # Empty-ish values
    "",
    " ",
    "null",
    "undefined",
])

# These values should trigger clearing the list entirely (user explicitly said "none")
CLEAR_LIST_VALUES = frozenset([
    "none of the above",
    "none",
    "nothing", 
    "no conditions",
    "no diagnosis",
    "not diagnosed",
])


# ═══════════════════════════════════════════════════════════════════════════════
# CORE SANITIZATION FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def is_placeholder_value(value: Any) -> bool:
    """
    Check if a value is a UI placeholder rather than actual data.
    
    Args:
        value: Any value to check
        
    Returns:
        True if this is a placeholder value that should be filtered out
    """
    if value is None:
        return True
    
    if not isinstance(value, str):
        return False
    
    normalized = value.strip().lower()
    
    # Check against known placeholders
    if normalized in UI_PLACEHOLDER_VALUES:
        return True
    
    # Check for empty after normalization
    if not normalized:
        return True
    
    return False


def should_clear_list(values: List[Any]) -> bool:
    """
    Check if the list contains a value indicating "no items selected".
    
    If user selects ONLY "None of the above", the entire list should be empty,
    not contain ["None of the above"].
    
    Args:
        values: List of values to check
        
    Returns:
        True if the list should be cleared entirely
    """
    if not values:
        return False
    
    # If only one item and it's a "clear" indicator
    if len(values) == 1:
        val = values[0]
        if isinstance(val, str) and val.strip().lower() in CLEAR_LIST_VALUES:
            return True
    
    return False


def sanitize_list_field(values: Union[List[Any], str, None], field_name: str = "") -> List[str]:
    """
    Sanitize a list field, removing UI placeholders.
    
    Args:
        values: List of values (or comma-separated string) to sanitize
        field_name: Name of field for logging
        
    Returns:
        Clean list with only valid medical/health data
        
    Examples:
        >>> sanitize_list_field(["PCOS", "None of the above"])
        ["PCOS"]
        
        >>> sanitize_list_field(["None of the above"])
        []
        
        >>> sanitize_list_field(["Endometriosis", "PCOS", ""])
        ["Endometriosis", "PCOS"]
    """
    if values is None:
        return []
    
    # Handle string input (comma-separated)
    if isinstance(values, str):
        if is_placeholder_value(values):
            return []
        values = [v.strip() for v in values.split(",")]
    
    # Handle non-list (shouldn't happen, but be safe)
    if not isinstance(values, list):
        logger.warning(f"[SANITIZE] Unexpected type for {field_name}: {type(values)}")
        return []
    
    # Check if user selected "none" option - return empty list
    if should_clear_list(values):
        logger.debug(f"[SANITIZE] {field_name}: User selected 'none' option, returning empty list")
        return []
    
    # Filter out placeholder values
    original_count = len(values)
    clean_values = []
    
    for val in values:
        if not is_placeholder_value(val):
            if isinstance(val, str):
                clean_values.append(val.strip())
            else:
                clean_values.append(str(val))
    
    removed_count = original_count - len(clean_values)
    if removed_count > 0:
        logger.info(f"[SANITIZE] {field_name}: Removed {removed_count} placeholder values")
    
    return clean_values


def sanitize_dict_field(data: Union[Dict[str, Any], List[Any], None], field_name: str = "") -> Dict[str, bool]:
    """
    Sanitize a dict field (like concerns), filtering out placeholder keys.
    
    Args:
        data: Dict of concern_name -> bool mappings, or list to convert
        field_name: Name of field for logging
        
    Returns:
        Clean dict with only valid entries
    """
    if data is None:
        return {}
    
    # Handle list input (convert to dict)
    if isinstance(data, list):
        # Filter and convert to dict
        clean_list = sanitize_list_field(data, field_name)
        return {item: True for item in clean_list}
    
    if not isinstance(data, dict):
        logger.warning(f"[SANITIZE] Unexpected type for {field_name}: {type(data)}")
        return {}
    
    # Filter dict keys
    clean_dict = {}
    for key, value in data.items():
        if not is_placeholder_value(key):
            clean_dict[key] = value
    
    removed_count = len(data) - len(clean_dict)
    if removed_count > 0:
        logger.info(f"[SANITIZE] {field_name}: Removed {removed_count} placeholder keys")
    
    return clean_dict


def sanitize_string_field(value: Optional[str], field_name: str = "") -> Optional[str]:
    """
    Sanitize a single string field.
    
    Args:
        value: String value to sanitize
        field_name: Name of field for logging
        
    Returns:
        Clean string or None if it was a placeholder
    """
    if value is None:
        return None
    
    if not isinstance(value, str):
        return str(value) if value else None
    
    if is_placeholder_value(value):
        logger.debug(f"[SANITIZE] {field_name}: Removed placeholder value '{value}'")
        return None
    
    return value.strip()


# ═══════════════════════════════════════════════════════════════════════════════
# HIGH-LEVEL SANITIZATION FOR COMPLETE DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

def sanitize_health_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sanitize an entire user health data dictionary.
    
    This is the main entry point for cleaning user-submitted health data.
    Call this before storing data or using it in LLM prompts.
    
    Args:
        data: Raw user health data dictionary
        
    Returns:
        Sanitized dictionary with UI placeholders removed
        
    Fields sanitized:
        - diagnosed_conditions (list)
        - family_history (list)
        - period_concerns (list or dict)
        - body_concerns (list or dict)
        - skin_hair_concerns (list or dict)
        - mental_health_concerns (list or dict)
        - top_concern (string)
        - other_concerns (list)
    """
    if not data:
        return data
    
    # Create a copy to avoid modifying original
    clean_data = dict(data)
    
    # List fields
    list_fields = [
        "diagnosed_conditions",
        "family_history", 
        "other_concerns",
        "birth_control",
    ]
    
    for field in list_fields:
        if field in clean_data:
            clean_data[field] = sanitize_list_field(clean_data[field], field)
    
    # Dict/List fields (concerns)
    concern_fields = [
        "period_concerns",
        "body_concerns",
        "skin_hair_concerns", 
        "mental_health_concerns",
    ]
    
    for field in concern_fields:
        if field in clean_data:
            value = clean_data[field]
            if isinstance(value, list):
                clean_data[field] = sanitize_list_field(value, field)
            elif isinstance(value, dict):
                clean_data[field] = sanitize_dict_field(value, field)
    
    # String fields
    string_fields = ["top_concern"]
    
    for field in string_fields:
        if field in clean_data:
            clean_data[field] = sanitize_string_field(clean_data[field], field)
    
    return clean_data


def sanitize_user_response(user_response: Any) -> None:
    """
    Sanitize a UserResponse ORM object IN PLACE.
    
    Call this before committing a UserResponse to ensure no placeholder
    values are stored.
    
    Args:
        user_response: SQLAlchemy UserResponse object (modified in place)
    """
    if user_response is None:
        return
    
    # Sanitize list fields
    if hasattr(user_response, 'diagnosed_conditions') and user_response.diagnosed_conditions:
        user_response.diagnosed_conditions = sanitize_list_field(
            user_response.diagnosed_conditions, 
            "diagnosed_conditions"
        )
    
    if hasattr(user_response, 'family_history') and user_response.family_history:
        user_response.family_history = sanitize_list_field(
            user_response.family_history,
            "family_history"
        )
    
    if hasattr(user_response, 'other_concerns') and user_response.other_concerns:
        user_response.other_concerns = sanitize_list_field(
            user_response.other_concerns,
            "other_concerns"
        )
    
    # Sanitize concern fields
    concern_attrs = [
        'period_concerns',
        'body_concerns', 
        'skin_hair_concerns',
        'mental_health_concerns',
    ]
    
    for attr in concern_attrs:
        if hasattr(user_response, attr):
            value = getattr(user_response, attr)
            if value:
                if isinstance(value, list):
                    setattr(user_response, attr, sanitize_list_field(value, attr))
                elif isinstance(value, dict):
                    setattr(user_response, attr, sanitize_dict_field(value, attr))
    
    # Sanitize string fields
    if hasattr(user_response, 'top_concern') and user_response.top_concern:
        user_response.top_concern = sanitize_string_field(
            user_response.top_concern,
            "top_concern"
        )
    
    logger.info("[SANITIZE] UserResponse sanitized successfully")


def sanitize_session_data(session_data: Any) -> None:
    """
    Sanitize a SessionData Pydantic model or dict IN PLACE (for dict) or RETURN clean dict.
    
    Args:
        session_data: SessionData pydantic model or dict
        
    Returns:
        Clean dict if input was dict, else modifies in place
    """
    if session_data is None:
        return
    
    # Handle Pydantic model
    if hasattr(session_data, 'diagnosed_conditions'):
        if session_data.diagnosed_conditions:
            # Pydantic models are immutable, so we need to handle this differently
            # The caller should use sanitize_health_data on the dict instead
            pass
    
    # Handle dict
    if isinstance(session_data, dict):
        return sanitize_health_data(session_data)
    
    return session_data
