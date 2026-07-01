"""Integrity checks for the built-in spell reference data."""

from shadowdark_bot.spell_data import SPELLS


def test_no_duplicate_spell_names() -> None:
    names = [s[0] for s in SPELLS]
    assert len(names) == len(set(names)), "duplicate spell name in SPELLS"


def test_classes_and_tiers_are_valid() -> None:
    for name, tier, classes, duration, spell_range, description in SPELLS:
        assert tier >= 1, f"{name} has non-positive tier"
        assert classes, f"{name} has no classes"
        assert set(classes) <= {"priest", "wizard"}, f"{name} has bad classes {classes}"
        assert duration, f"{name} missing duration"
        assert spell_range, f"{name} missing range"
        assert description, f"{name} missing description"


def test_light_is_shared_by_both_classes() -> None:
    light = next(s for s in SPELLS if s[0] == "Light")
    assert set(light[2]) == {"priest", "wizard"}
