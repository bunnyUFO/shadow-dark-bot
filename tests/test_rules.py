"""Unit tests for the pure Shadow Dark rules helpers."""

import pytest

from shadowdark_bot.rules import (
    ability_modifier,
    carry_capacity,
    format_modifier,
    spellcasting_modifier,
    stack_slots,
)


@pytest.mark.parametrize(
    "score,expected",
    [
        (1, -4),
        (2, -3),
        (3, -3),
        (4, -2),
        (5, -2),
        (6, -1),
        (8, -1),
        (9, 0),
        (12, 0),
        (13, 1),
        (15, 1),
        (16, 2),
        (17, 2),
        (18, 3),
        # Out-of-range scores clamp to the nearest row.
        (0, -4),
        (20, 3),
    ],
)
def test_ability_modifier_table(score: int, expected: int) -> None:
    assert ability_modifier(score) == expected


def test_format_modifier_signs() -> None:
    assert format_modifier(0) == "+0"
    assert format_modifier(2) == "+2"
    assert format_modifier(-1) == "−1"  # true minus sign


def test_carry_capacity_floor_is_ten() -> None:
    assert carry_capacity(8) == 10
    assert carry_capacity(10) == 10
    assert carry_capacity(14) == 14


def test_spellcasting_modifier_adds_talent_bonus() -> None:
    # WIS 15 → +1 ability modifier, plus a +1 talent bonus.
    assert spellcasting_modifier(15, 1) == 2
    # INT 10 → +0, no bonus.
    assert spellcasting_modifier(10, 0) == 0


def test_stack_slots_bundle_ceiling() -> None:
    # 20 arrows per bundle, 1 slot per bundle.
    assert stack_slots(1, 1.0, 20) == 1.0
    assert stack_slots(20, 1.0, 20) == 1.0
    assert stack_slots(21, 1.0, 20) == 2.0
    # A partial add to an existing bundle can cost zero extra slots.
    before = stack_slots(5, 1.0, 20)
    after = stack_slots(15, 1.0, 20)
    assert after - before == 0.0


def test_stack_slots_default_freeform() -> None:
    # Freeform items default to 1 slot each, bundle of 1.
    assert stack_slots(3, 1.0, 1) == 3.0
