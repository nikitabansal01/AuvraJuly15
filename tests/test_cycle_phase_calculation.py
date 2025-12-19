"""
Test Script for Scientifically Accurate Cycle Phase Calculation
================================================================

This script tests the cycle phase calculation logic to ensure it correctly
calculates phases for different cycle lengths based on the scientific fact
that the LUTEAL PHASE IS CONSTANT (~14 days).

Run with: python test_cycle_phase_calculation.py
"""

import sys
from typing import NamedTuple, Dict

import pytest


# ============================================================================
# COPY OF THE CONFIG AND CALCULATION LOGIC FOR TESTING
# ============================================================================

class CycleLengthConfig(NamedTuple):
    avg_days: int
    luteal_length: int
    menstrual_length: int
    ovulation_window: int
    is_potentially_irregular: bool


CYCLE_LENGTH_CONFIG: Dict[str, CycleLengthConfig] = {
    "Less than 21 days": CycleLengthConfig(
        avg_days=19,
        luteal_length=12,
        menstrual_length=4,
        ovulation_window=2,
        is_potentially_irregular=True
    ),
    "21-25 days": CycleLengthConfig(
        avg_days=23,
        luteal_length=13,
        menstrual_length=5,
        ovulation_window=2,
        is_potentially_irregular=False
    ),
    "26-30 days": CycleLengthConfig(
        avg_days=28,
        luteal_length=14,
        menstrual_length=5,
        ovulation_window=2,
        is_potentially_irregular=False
    ),
    "31-35 days": CycleLengthConfig(
        avg_days=33,
        luteal_length=14,
        menstrual_length=5,
        ovulation_window=2,
        is_potentially_irregular=False
    ),
    "35+ days": CycleLengthConfig(
        avg_days=42,
        luteal_length=14,
        menstrual_length=5,
        ovulation_window=3,
        is_potentially_irregular=True
    ),
}


def determine_phase_scientific(
    cycle_day: int,
    total_cycle_days: int,
    luteal_length: int,
    menstrual_length: int,
    ovulation_window: int
) -> str:
    """
    Determine menstrual phase using SCIENTIFICALLY ACCURATE calculation.
    """
    ovulation_day = total_cycle_days - luteal_length
    ovulation_start = ovulation_day - (ovulation_window // 2)
    ovulation_end = ovulation_day + (ovulation_window // 2)
    ovulation_start = max(menstrual_length + 1, ovulation_start)
    
    if cycle_day <= menstrual_length:
        return "Menses phase"
    elif cycle_day < ovulation_start:
        return "Follicular phase"
    elif cycle_day <= ovulation_end:
        return "Ovulation phase"
    else:
        return "Luteal phase"


def print_cycle_breakdown(cycle_length_str: str):
    """Print detailed phase breakdown for a cycle length."""
    config = CYCLE_LENGTH_CONFIG[cycle_length_str]
    cycle_days = config.avg_days
    luteal_length = config.luteal_length
    menstrual_length = config.menstrual_length
    ovulation_window = config.ovulation_window
    
    ovulation_day = cycle_days - luteal_length
    ovulation_start = max(menstrual_length + 1, ovulation_day - (ovulation_window // 2))
    ovulation_end = ovulation_day + (ovulation_window // 2)
    
    print(f"\n{'='*60}")
    print(f"CYCLE LENGTH: {cycle_length_str}")
    print(f"{'='*60}")
    print(f"Average cycle: {cycle_days} days")
    print(f"Luteal phase: {luteal_length} days (constant)")
    print(f"Ovulation day: Day {ovulation_day} (calculated: {cycle_days} - {luteal_length})")
    print(f"\nPhase Breakdown:")
    print(f"  ├── Menses:     Day 1 - Day {menstrual_length}")
    print(f"  ├── Follicular: Day {menstrual_length + 1} - Day {ovulation_start - 1}")
    print(f"  ├── Ovulation:  Day {ovulation_start} - Day {ovulation_end} (peak: Day {ovulation_day})")
    print(f"  └── Luteal:     Day {ovulation_end + 1} - Day {cycle_days}")
    
    # Calculate phase lengths
    follicular_length = (ovulation_start - 1) - menstrual_length
    ovulation_length = ovulation_end - ovulation_start + 1
    luteal_actual = cycle_days - ovulation_end
    
    print(f"\nPhase Durations:")
    print(f"  ├── Menses:     {menstrual_length} days")
    print(f"  ├── Follicular: {follicular_length} days (VARIES)")
    print(f"  ├── Ovulation:  {ovulation_length} days")
    print(f"  └── Luteal:     {luteal_actual} days (CONSTANT)")


@pytest.mark.parametrize("cycle_length_str", list(CYCLE_LENGTH_CONFIG.keys()))
def test_all_days_for_cycle(cycle_length_str: str):
    """Test phase determination for all days in a cycle.

    This file is intentionally self-contained (it carries a copy of the logic)
    so the test can run without requiring DB/API setup.
    """
    config = CYCLE_LENGTH_CONFIG[cycle_length_str]
    cycle_days = config.avg_days

    phases = {"Menses phase": [], "Follicular phase": [], "Ovulation phase": [], "Luteal phase": []}

    for day in range(1, cycle_days + 1):
        phase = determine_phase_scientific(
            cycle_day=day,
            total_cycle_days=config.avg_days,
            luteal_length=config.luteal_length,
            menstrual_length=config.menstrual_length,
            ovulation_window=config.ovulation_window,
        )
        assert phase in phases, f"Unexpected phase '{phase}' for day {day} in {cycle_length_str}"
        phases[phase].append(day)

    # Every day should be assigned exactly one phase.
    all_days = sorted([d for days in phases.values() for d in days])
    assert all_days == list(range(1, cycle_days + 1))

    # Ovulation day (total - luteal length) should always fall within ovulation phase.
    ovulation_day = cycle_days - config.luteal_length
    assert ovulation_day in phases["Ovulation phase"], (
        f"Ovulation day {ovulation_day} not in ovulation phase for {cycle_length_str}"
    )


def run_edge_case_tests():
    """Test edge cases to ensure calculations are correct."""
    print(f"\n{'='*60}")
    print("EDGE CASE TESTS")
    print(f"{'='*60}")
    
    tests_passed = 0
    tests_failed = 0
    
    # Test 1: 28-day cycle, Day 14 should be Ovulation
    config = CYCLE_LENGTH_CONFIG["26-30 days"]
    phase = determine_phase_scientific(14, 28, 14, 5, 2)
    expected = "Ovulation phase"
    if phase == expected:
        print(f"✅ Test 1: 28-day cycle, Day 14 = {phase}")
        tests_passed += 1
    else:
        print(f"❌ Test 1: 28-day cycle, Day 14 = {phase} (expected: {expected})")
        tests_failed += 1
    
    # Test 2: 33-day cycle, Day 19 should be Ovulation (33 - 14 = 19)
    phase = determine_phase_scientific(19, 33, 14, 5, 2)
    expected = "Ovulation phase"
    if phase == expected:
        print(f"✅ Test 2: 33-day cycle, Day 19 = {phase}")
        tests_passed += 1
    else:
        print(f"❌ Test 2: 33-day cycle, Day 19 = {phase} (expected: {expected})")
        tests_failed += 1
    
    # Test 3: 42-day cycle, Day 28 should be Ovulation (42 - 14 = 28)
    phase = determine_phase_scientific(28, 42, 14, 5, 3)
    expected = "Ovulation phase"
    if phase == expected:
        print(f"✅ Test 3: 42-day cycle, Day 28 = {phase}")
        tests_passed += 1
    else:
        print(f"❌ Test 3: 42-day cycle, Day 28 = {phase} (expected: {expected})")
        tests_failed += 1
    
    # Test 4: 28-day cycle, Day 3 should be Menses
    phase = determine_phase_scientific(3, 28, 14, 5, 2)
    expected = "Menses phase"
    if phase == expected:
        print(f"✅ Test 4: 28-day cycle, Day 3 = {phase}")
        tests_passed += 1
    else:
        print(f"❌ Test 4: 28-day cycle, Day 3 = {phase} (expected: {expected})")
        tests_failed += 1
    
    # Test 5: 28-day cycle, Day 25 should be Luteal
    phase = determine_phase_scientific(25, 28, 14, 5, 2)
    expected = "Luteal phase"
    if phase == expected:
        print(f"✅ Test 5: 28-day cycle, Day 25 = {phase}")
        tests_passed += 1
    else:
        print(f"❌ Test 5: 28-day cycle, Day 25 = {phase} (expected: {expected})")
        tests_failed += 1
    
    # Test 6: 28-day cycle, Day 10 should be Follicular
    phase = determine_phase_scientific(10, 28, 14, 5, 2)
    expected = "Follicular phase"
    if phase == expected:
        print(f"✅ Test 6: 28-day cycle, Day 10 = {phase}")
        tests_passed += 1
    else:
        print(f"❌ Test 6: 28-day cycle, Day 10 = {phase} (expected: {expected})")
        tests_failed += 1
    
    # Test 7: Very short cycle (19 days), Day 7 should be Ovulation (19 - 12 = 7)
    phase = determine_phase_scientific(7, 19, 12, 4, 2)
    expected = "Ovulation phase"
    if phase == expected:
        print(f"✅ Test 7: 19-day cycle, Day 7 = {phase}")
        tests_passed += 1
    else:
        print(f"❌ Test 7: 19-day cycle, Day 7 = {phase} (expected: {expected})")
        tests_failed += 1
    
    # Test 8: 42-day cycle, Day 35 should be Luteal (after ovulation on ~day 28)
    phase = determine_phase_scientific(35, 42, 14, 5, 3)
    expected = "Luteal phase"
    if phase == expected:
        print(f"✅ Test 8: 42-day cycle, Day 35 = {phase}")
        tests_passed += 1
    else:
        print(f"❌ Test 8: 42-day cycle, Day 35 = {phase} (expected: {expected})")
        tests_failed += 1
    
    print(f"\n{'─'*60}")
    print(f"RESULTS: {tests_passed}/{tests_passed + tests_failed} tests passed")
    if tests_failed > 0:
        print(f"⚠️  {tests_failed} tests failed!")
    else:
        print("🎉 All tests passed!")
    print(f"{'─'*60}")


def demonstrate_scientific_vs_proportional():
    """
    Demonstrate why proportional scaling (old method) is WRONG
    vs the scientific method (new method) is CORRECT.
    """
    print(f"\n{'='*60}")
    print("COMPARISON: Scientific vs Proportional Method")
    print(f"{'='*60}")
    
    print("\n📚 SCIENTIFIC FACT:")
    print("   The luteal phase is CONSTANT at ~14 days.")
    print("   The follicular phase VARIES with cycle length.")
    print("   Ovulation = cycle_length - 14 (not cycle_length / 2)")
    
    print("\n35-DAY CYCLE COMPARISON:")
    print("─" * 40)
    
    # Wrong method: proportional scaling
    print("\n❌ OLD (WRONG) - Proportional Method:")
    print("   Scales 28-day phases proportionally to 35 days")
    print("   Day 21: adjusted = (21-1) * 28/35 + 1 = Day 17")
    print("   Phase: Would show 'Luteal' (incorrect!)")
    
    # Correct method: scientific
    print("\n✅ NEW (CORRECT) - Scientific Method:")
    print("   Luteal phase = 14 days (constant)")
    print("   Ovulation = 35 - 14 = Day 21")
    print("   Day 21: Ovulation phase (correct!)")
    
    print("\n📊 PHASE DISTRIBUTION COMPARISON (35-day cycle):")
    print("─" * 40)
    
    print("\n   OLD METHOD (proportional):")
    print("   └── Assumes ovulation at Day 17.5 (35 * 14/28)")
    print("       This is WRONG because it assumes luteal phase varies")
    
    print("\n   NEW METHOD (scientific):")
    print("   └── Calculates ovulation at Day 21 (35 - 14)")
    print("       This is CORRECT because luteal phase is constant")
    
    print("\n💡 CONCLUSION:")
    print("   For longer cycles, the FOLLICULAR phase extends,")
    print("   NOT the luteal phase. Women with 35-day cycles")
    print("   ovulate later (Day 21), not at Day 17-18.")


def main():
    print("="*60)
    print("MENSTRUAL CYCLE PHASE CALCULATION - TEST SUITE")
    print("Based on NCBI Endotext Scientific Literature")
    print("="*60)
    
    # Show breakdown for each cycle length
    for cycle_length in CYCLE_LENGTH_CONFIG.keys():
        print_cycle_breakdown(cycle_length)
        test_all_days_for_cycle(cycle_length)
    
    # Run edge case tests
    run_edge_case_tests()
    
    # Show comparison between methods
    demonstrate_scientific_vs_proportional()
    
    print("\n" + "="*60)
    print("TEST COMPLETE")
    print("="*60)


if __name__ == "__main__":
    main()
