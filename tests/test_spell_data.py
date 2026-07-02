"""Integrity checks for the built-in spell reference data."""

from shadowdark_bot.spell_data import ALIGNMENT_SPELLS, SPELLS


def test_no_duplicate_spell_names() -> None:
    names = [s[0] for s in SPELLS] + [s[0] for s in ALIGNMENT_SPELLS]
    assert len(names) == len(set(names)), "duplicate spell name across the reference"


def test_alignment_spells_are_valid() -> None:
    for name, tier, classes, duration, spell_range, description, alignment in (
        ALIGNMENT_SPELLS
    ):
        assert tier >= 1, f"{name} has non-positive tier"
        assert set(classes) == {"wizard"}, f"{name} should be a wizard spell"
        assert alignment in {"L", "N", "C"}, f"{name} has bad alignment {alignment!r}"
        assert duration and spell_range and description, f"{name} missing fields"


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
