"""UI-kit preset registry (FLAWS §4.3).

A component-driver config block may name a ``preset:`` to adopt a whole kit's
selectors. Explicit selectors win; omitting ``preset`` keeps PrimeNG defaults
byte-identical.
"""

import pytest

from steppy.models import (
    DropdownConfig,
    DialogConfig,
    ToggleConfig,
    DatepickerConfig,
    FieldsetConfig,
)
from steppy.presets import PRESETS, available_presets

ALL = [
    (DropdownConfig, "dropdown"),
    (DialogConfig, "dialog"),
    (ToggleConfig, "toggle"),
    (DatepickerConfig, "datepicker"),
    (FieldsetConfig, "fieldset"),
]


@pytest.mark.parametrize("model, key", ALL)
def test_no_preset_keeps_defaults(model, key):
    """(a) omitting preset leaves every field at its class default."""
    cfg = model()
    assert cfg.preset is None
    for name, value in PRESETS["primeng"][key].items():
        assert getattr(cfg, name) == value


@pytest.mark.parametrize("model, key", ALL)
def test_primeng_preset_equals_defaults(model, key):
    """(b) preset: primeng is equivalent to the defaults (backfill is a no-op)."""
    default = model()
    explicit = model(preset="primeng")
    for name in PRESETS["primeng"][key]:
        assert getattr(explicit, name) == getattr(default, name)


# Every non-primeng preset is a best-effort starter kit; each must fully
# populate all five blocks (a partial block would silently leave PrimeNG
# defaults in place — wrong for a different framework).
NON_PRIMENG_PRESETS = sorted(set(PRESETS) - {"primeng"})


@pytest.mark.parametrize("preset", NON_PRIMENG_PRESETS)
@pytest.mark.parametrize("model, key", ALL)
def test_preset_fills_selectors(model, key, preset):
    """(c) a named preset swaps in that kit's selectors for every block."""
    cfg = model(preset=preset)
    kit = PRESETS[preset]
    assert key in kit, f"preset {preset!r} is missing block {key!r}"
    for name, value in kit[key].items():
        assert getattr(cfg, name) == value


@pytest.mark.parametrize("preset", NON_PRIMENG_PRESETS)
@pytest.mark.parametrize("model, key", ALL)
def test_preset_covers_every_selector_field(model, key, preset):
    """Each starter kit provides the full selector set primeng does — no field
    left accidentally at a PrimeNG default."""
    expected_fields = set(PRESETS["primeng"][key])
    assert set(PRESETS[preset][key]) == expected_fields


@pytest.mark.parametrize("preset", NON_PRIMENG_PRESETS)
def test_new_preset_is_discoverable(preset):
    """Every registered kit shows up in available_presets() (used in errors)."""
    assert preset in available_presets()


def test_explicit_selector_beats_preset():
    """(d) an explicitly set field wins over the preset backfill."""
    cfg = DropdownConfig(preset="material", trigger="custom-select")
    assert cfg.trigger == "custom-select"
    # Other fields still come from material.
    assert cfg.overlay == PRESETS["material"]["dropdown"]["overlay"]


def test_non_selector_field_untouched_by_preset():
    """dropdown.settle is not a selector — a preset never changes it."""
    assert DropdownConfig(preset="material").settle == "api"


def test_unknown_preset_raises():
    """(e) an unknown kit name is a clear validation error."""
    with pytest.raises(ValueError) as exc:
        DropdownConfig(preset="bootstrap")
    assert "Unknown preset" in str(exc.value)
    for kit in available_presets():
        assert kit in str(exc.value)
