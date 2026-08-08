"""Reward eligibility, claims and freeze policy as deterministic domain rules."""

from __future__ import annotations

from datetime import date

import pytest

from app.v2.domain.reward_catalog import (
    ASSET_ENTITLEMENT,
    ASSET_FREEZE,
    CATALOG_VERSION,
    FREEZE_LOOKBACK_DAYS,
    REWARD_CATALOG,
    REWARDS_BY_ID,
    freeze_rejection_reason,
    freeze_window,
    is_eligible,
    longest_run,
    reward_or_none,
    unlocked_codes,
)


def test_catalog_ids_are_unique_and_every_entry_names_its_asset() -> None:
    ids = [reward.reward_id for reward in REWARD_CATALOG]
    assert len(ids) == len(set(ids))
    for reward in REWARD_CATALOG:
        assert reward.asset_key, reward.reward_id
        assert reward.required_streak_days > 0
        assert reward.asset_type in {ASSET_FREEZE, ASSET_ENTITLEMENT}
        # Points are granted by daily adjudication, never claimed from the
        # catalog, so no entry may mint them.
        assert reward.asset_type != "points"


def test_catalog_version_is_stamped_and_stable() -> None:
    assert CATALOG_VERSION == "engagement.v1"


def test_longest_run_counts_the_best_streak_not_the_latest() -> None:
    assert longest_run([]) == 0
    assert longest_run([date(2026, 8, 1)]) == 1
    # Two runs of 3 and 2, with a gap: the answer is the best, so a reward
    # already unlocked is never taken away by a later miss.
    days = [
        date(2026, 8, 1),
        date(2026, 8, 2),
        date(2026, 8, 3),
        date(2026, 8, 6),
        date(2026, 8, 7),
    ]
    assert longest_run(days) == 3
    assert longest_run(reversed(days)) == 3
    assert longest_run(days + days) == 3


def test_longest_run_spans_month_and_year_boundaries() -> None:
    assert longest_run([date(2026, 7, 31), date(2026, 8, 1)]) == 2
    assert longest_run([date(2025, 12, 31), date(2026, 1, 1)]) == 2


def test_eligibility_is_a_threshold_on_the_best_run() -> None:
    freeze = REWARDS_BY_ID["streak_freeze"]
    assert freeze.required_streak_days == 3
    assert not is_eligible(freeze, best_streak_days=2)
    assert is_eligible(freeze, best_streak_days=3)
    assert is_eligible(freeze, best_streak_days=99)


def test_unlocked_codes_grow_monotonically_with_the_streak() -> None:
    assert unlocked_codes(0) == frozenset()
    assert "diet_preference" in unlocked_codes(7)
    assert "diet_preference" not in unlocked_codes(6)
    assert unlocked_codes(7) <= unlocked_codes(30)


def test_every_unlocked_code_belongs_to_a_catalog_reward() -> None:
    codes = {r.unlocks_code for r in REWARD_CATALOG if r.unlocks_code}
    assert unlocked_codes(999) == frozenset(codes)


def test_unknown_reward_id_resolves_to_nothing() -> None:
    assert reward_or_none("not_a_reward") is None
    assert reward_or_none("streak_freeze") is not None


def test_freeze_window_excludes_the_open_day_and_is_bounded() -> None:
    today = date(2026, 8, 8)
    earliest, latest = freeze_window(today)
    assert latest == date(2026, 8, 7)
    assert (latest - earliest).days == FREEZE_LOOKBACK_DAYS - 1


@pytest.mark.parametrize(
    "day, expect_rejected",
    [
        (date(2026, 8, 9), True),   # future
        (date(2026, 8, 8), True),   # the current, still-open local day
        (date(2026, 8, 7), False),  # yesterday, the newest closed day
        (date(2026, 8, 1), False),  # oldest day inside the window
        (date(2026, 7, 31), True),  # one day past the window
    ],
)
def test_freeze_rejects_open_and_out_of_window_days(day, expect_rejected) -> None:
    reason = freeze_rejection_reason(local_date=day, current_local_date=date(2026, 8, 8))
    assert (reason is not None) is expect_rejected
