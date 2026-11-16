"""Sanity checks on the declarative slider table in the UI layer.

The spec table and its formatters are plain data / pure functions. A
MagicMock page is also enough to build the whole window once and exercise the
preset flow, which catches layout and attribute mistakes the unit tests miss.
"""

from unittest.mock import MagicMock

import pytest

from nuance.audio import userpresets
from nuance.audio.effects import NO_LOUDNORM, EffectSettings
from nuance.ui import app


@pytest.fixture
def built_app(tmp_path, monkeypatch):
    monkeypatch.setenv("NUANCE_CONFIG_DIR", str(tmp_path))
    page = MagicMock()
    page.services = []
    return app.NuanceApp(page)


def test_every_spec_targets_a_real_setting():
    known = set(EffectSettings().__dataclass_fields__)
    for spec in app.NuanceApp._SLIDER_SPECS:
        assert spec["attr"] in known, spec["attr"]


def test_spec_names_and_attrs_unique():
    names = [s["name"] for s in app.NuanceApp._SLIDER_SPECS]
    attrs = [s["attr"] for s in app.NuanceApp._SLIDER_SPECS]
    assert len(names) == len(set(names))
    assert len(attrs) == len(set(attrs))


def test_every_spec_default_formats_without_error():
    d = EffectSettings()
    for spec in app.NuanceApp._SLIDER_SPECS:
        text = spec["fmt"](getattr(d, spec["attr"]))
        assert isinstance(text, str) and text


def test_loudness_spec_coerce_and_from_setting_are_inverse_at_off():
    spec = next(s for s in app.NuanceApp._SLIDER_SPECS if s["name"] == "loudness")
    # Slider parked at the top -> stored as the sentinel.
    assert spec["coerce"](-6.0) == NO_LOUDNORM
    # Sentinel -> slider parked back at the top.
    assert spec["from_setting"](NO_LOUDNORM) == -6.0
    # A real target passes straight through both ways.
    assert spec["coerce"](-12.0) == -12.0
    assert spec["from_setting"](-12.0) == -12.0


def test_loop_spec_coerce_rounds_to_int():
    spec = next(s for s in app.NuanceApp._SLIDER_SPECS if s["name"] == "loop")
    assert spec["coerce"](2.6) == 3
    assert isinstance(spec["coerce"](2.6), int)


def test_amount_formatter_off_at_zero():
    assert app._fmt_amount(0.0) == "Off"
    assert app._fmt_amount(0.5) == "50%"


def test_width_formatter_labels():
    assert app._fmt_width(1.0) == "Normal"
    assert app._fmt_width(0.0) == "Mono"
    assert app._fmt_width(1.5) == "1.50x"


# -- whole-window smoke tests (MagicMock page) ------------------------------


def test_window_builds_with_all_sliders(built_app):
    bound = sum(
        1 for s in app.NuanceApp._SLIDER_SPECS
        if hasattr(built_app, s["name"] + "_slider")
        and hasattr(built_app, s["name"] + "_value")
    )
    assert bound == len(app.NuanceApp._SLIDER_SPECS)


def test_applying_preset_updates_sliders(built_app):
    built_app.preset_dropdown.value = "Energy Boost"
    built_app._on_preset_change(None)
    assert built_app.width_slider.value == pytest.approx(1.4)
    assert built_app.loudness_slider.value == pytest.approx(-9.0)
    assert built_app.loudness_value.value == "-9 LUFS"


def test_applying_original_resets_to_defaults(built_app):
    built_app.preset_dropdown.value = "Nightcore"
    built_app._on_preset_change(None)
    built_app.preset_dropdown.value = "Original"
    built_app._on_preset_change(None)
    assert built_app.settings == EffectSettings()
    assert built_app.speed_slider.value == pytest.approx(1.0)


def test_save_and_delete_custom_preset(built_app):
    built_app.settings.autopan = 0.5
    built_app.preset_name_field.value = "Test8D"
    built_app._on_save_preset(None)
    assert "Test8D" in userpresets.names()
    assert "Test8D" in [o.key for o in built_app.preset_dropdown.options]

    built_app.preset_dropdown.value = "Test8D"
    built_app._on_delete_preset(None)
    assert userpresets.names() == []
    assert built_app.preset_dropdown.value == "Original"


def test_loading_a_custom_preset_restores_its_settings(built_app):
    built_app.settings = EffectSettings(speed=1.22, autopan=0.6, punch=0.4)
    built_app.preset_name_field.value = "Mine"
    built_app._on_save_preset(None)

    built_app.preset_dropdown.value = "Original"
    built_app._on_preset_change(None)
    built_app.preset_dropdown.value = "Mine"
    built_app._on_preset_change(None)

    assert built_app.settings.speed == pytest.approx(1.22)
    assert built_app.settings.autopan == pytest.approx(0.6)
    assert built_app.autopan_slider.value == pytest.approx(0.6)
