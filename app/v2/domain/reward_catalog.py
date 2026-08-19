"""The versioned reward catalog and the freeze redemption policy.

The catalog is code, not a table. It is deterministic policy: replaying a
user's ledger must produce the same answer next year as it does today, and a
mutable table would make that untrue while creating a second source of truth
alongside this module. Every ledger row therefore stamps ``CATALOG_VERSION``,
so a future catalog revision can be reasoned about without rewriting history.

Eligibility is never stored. "Has this user reached seven qualifying days?" is
a question about ``app.streak_days``, and the legacy system's mistake was
persisting that derived predicate in a ``claimed_rewards`` table where it could
disagree with the ledger it was derived from.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Mapping


CATALOG_VERSION = "engagement.v1"

#: Ledger asset kinds. ``points`` is fungible and has no key; ``freeze`` and
#: ``entitlement`` name the specific thing that moved.
ASSET_POINTS = "points"
ASSET_FREEZE = "freeze"
ASSET_ENTITLEMENT = "entitlement"

#: A freeze protects a closed day that would otherwise break a streak. The
#: window is bounded so a user cannot resurrect an arbitrarily old streak, and
#: the current local day is excluded because it is not yet adjudicated.
FREEZE_LOOKBACK_DAYS = 7
FREEZE_ASSET_KEY = "streak_freeze"


@dataclass(frozen=True, slots=True)
class Reward:
    """One catalog entry: a streak threshold that grants one asset."""

    reward_id: str
    title: str
    category: str
    effect: str
    required_streak_days: int
    asset_type: str
    asset_key: str
    quantity: int
    icon: str

    @property
    def unlocks_code(self) -> str | None:
        """The observation code this reward unlocks, if it gates one."""

        return _REWARD_UNLOCKS.get(self.reward_id)


#: Ported verbatim from the retained legacy product configuration so streak
#: thresholds and titles users already saw do not silently change.
REWARD_CATALOG: tuple[Reward, ...] = (
    Reward(
        "streak_freeze",
        "Streak freeze",
        "seed",
        "freeze_token",
        3,
        ASSET_FREEZE,
        FREEZE_ASSET_KEY,
        1,
        "🧊",
    ),
    Reward(
        "diet_prefs",
        "Diet preferences",
        "seed",
        "personalization",
        7,
        ASSET_ENTITLEMENT,
        "diet_prefs",
        1,
        "🥗",
    ),
    Reward(
        "food_allergies",
        "Food Allergies",
        "seed",
        "personalization",
        8,
        ASSET_ENTITLEMENT,
        "food_allergies",
        1,
        "🥜",
    ),
    Reward(
        "cuisine_prefs",
        "Cuisine preferences",
        "seed",
        "personalization",
        12,
        ASSET_ENTITLEMENT,
        "cuisine_prefs",
        1,
        "🥘",
    ),
    Reward(
        "symptom_patterns",
        "Symptom patterns",
        "seed",
        "insight",
        14,
        ASSET_ENTITLEMENT,
        "symptom_patterns",
        1,
        "✨",
    ),
    Reward(
        "dine_out",
        "Dine out habits",
        "seed",
        "personalization",
        14,
        ASSET_ENTITLEMENT,
        "dine_out",
        1,
        "🍔",
    ),
    Reward(
        "plan_refresh_2x",
        "2x plan refresh",
        "rise",
        "refresh_token",
        16,
        ASSET_ENTITLEMENT,
        "plan_refresh_2x",
        1,
        "🧊",
    ),
    Reward(
        "ethnicity",
        "Ethnicity/cultural habits",
        "rise",
        "personalization",
        18,
        ASSET_ENTITLEMENT,
        "ethnicity",
        1,
        "🌏",
    ),
    Reward(
        "bmi_ratio",
        "BMI/Waist ratio",
        "rise",
        "personalization",
        18,
        ASSET_ENTITLEMENT,
        "bmi_ratio",
        1,
        "⚖️",
    ),
    Reward(
        "cravings_healthy",
        "Cravings made healthy",
        "rise",
        "personalization",
        18,
        ASSET_ENTITLEMENT,
        "cravings_healthy",
        1,
        "🥮",
    ),
    Reward(
        "first_improvement",
        "First signs of improvement",
        "rise",
        "badge",
        21,
        ASSET_ENTITLEMENT,
        "first_improvement",
        1,
        "✨",
    ),
)

#: Which observation codes each personalization reward unlocks. Slice D reads
#: this to gate preference writes; it lives here so the reward and the thing it
#: unlocks cannot drift apart.
_REWARD_UNLOCKS: Mapping[str, str] = {
    "diet_prefs": "diet_preference",
    "food_allergies": "food_allergies",
    "cuisine_prefs": "cuisine_preference",
    "dine_out": "dine_out_frequency",
    "ethnicity": "cultural_background",
    "cravings_healthy": "cravings",
}

REWARDS_BY_ID: Mapping[str, Reward] = {r.reward_id: r for r in REWARD_CATALOG}


def reward_or_none(reward_id: str) -> Reward | None:
    return REWARDS_BY_ID.get(reward_id)


def longest_run(qualifying_days: Iterable[date]) -> int:
    """Return the longest consecutive run among finalized qualifying days.

    Eligibility uses the best run a user has ever achieved, not the current
    one, so a reward already unlocked is never taken away by a later miss.
    """

    days = sorted(set(qualifying_days))
    if not days:
        return 0
    best = run = 1
    for previous, current in zip(days, days[1:]):
        run = run + 1 if current - previous == timedelta(days=1) else 1
        best = max(best, run)
    return best


def is_eligible(reward: Reward, *, best_streak_days: int) -> bool:
    return best_streak_days >= reward.required_streak_days


def unlocked_codes(best_streak_days: int) -> frozenset[str]:
    """Observation codes the user has earned the right to personalize."""

    return frozenset(
        code
        for reward in REWARD_CATALOG
        if (code := reward.unlocks_code) and is_eligible(reward, best_streak_days=best_streak_days)
    )


def freeze_window(current_local_date: date) -> tuple[date, date]:
    """Return the inclusive [earliest, latest] local dates a freeze may cover.

    The latest is yesterday: the current local day is still open, so freezing
    it would adjudicate a day the user can still complete.
    """

    latest = current_local_date - timedelta(days=1)
    return latest - timedelta(days=FREEZE_LOOKBACK_DAYS - 1), latest


def freeze_rejection_reason(*, local_date: date, current_local_date: date) -> str | None:
    """Return why this date cannot be frozen, or None when it may be."""

    earliest, latest = freeze_window(current_local_date)
    if local_date > latest:
        return "A freeze can only cover a day that has already closed."
    if local_date < earliest:
        return "A freeze can only cover the last " f"{FREEZE_LOOKBACK_DAYS} closed days."
    return None
