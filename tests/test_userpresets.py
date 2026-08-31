"""Tests for the user preset store (JSON on disk)."""

import json

import pytest

from nuance.audio import effects, userpresets
from nuance.audio.effects import EffectSettings


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Point the store at a throwaway directory for every test."""
    monkeypatch.setenv("NUANCE_CONFIG_DIR", str(tmp_path))
    yield tmp_path


def test_empty_store_has_no_names():
    assert userpresets.names() == []


def test_save_then_load_round_trips_all_fields():
    s = EffectSettings(
        speed=1.15, pitch_semitones=-1.0, autopan=0.4, punch=0.6,
        loudness_lufs=-11.0, vinyl=0.3, keep_pitch=True,
    )
    userpresets.save("My Vibe", s)
    back = userpresets.load("My Vibe")
    assert back == s


def test_names_are_sorted():
    userpresets.save("Zzz", EffectSettings())
    userpresets.save("Aaa", EffectSettings())
    assert userpresets.names() == ["Aaa", "Zzz"]


def test_save_overwrites_same_name():
    userpresets.save("P", EffectSettings(speed=1.1))
    userpresets.save("P", EffectSettings(speed=0.9))
    assert userpresets.load("P").speed == 0.9
    assert userpresets.names() == ["P"]


def test_delete_removes_one_preset():
    userpresets.save("A", EffectSettings())
    userpresets.save("B", EffectSettings())
    userpresets.delete("A")
    assert userpresets.names() == ["B"]


def test_delete_missing_name_is_noop():
    userpresets.delete("ghost")  # must not raise
    assert userpresets.names() == []


def test_empty_name_rejected():
    with pytest.raises(ValueError):
        userpresets.save("   ", EffectSettings())


def test_builtin_name_rejected():
    with pytest.raises(ValueError):
        userpresets.save("Nightcore", EffectSettings())


def test_name_is_stripped():
    userpresets.save("  Spacey  ", EffectSettings())
    assert "Spacey" in userpresets.names()


def test_corrupt_file_reads_as_empty(isolated_config):
    (isolated_config / "Nuance").mkdir(parents=True, exist_ok=True)
    (isolated_config / "Nuance" / "user_presets.json").write_text("{ not json")
    assert userpresets.names() == []


def test_unknown_keys_ignored_on_load(isolated_config):
    d = isolated_config / "Nuance"
    d.mkdir(parents=True, exist_ok=True)
    (d / "user_presets.json").write_text(
        json.dumps({"P": {"speed": 1.2, "made_up_field": 99}})
    )
    loaded = userpresets.load("P")
    assert loaded.speed == 1.2
    assert not hasattr(loaded, "made_up_field")


def test_missing_fields_keep_defaults(isolated_config):
    d = isolated_config / "Nuance"
    d.mkdir(parents=True, exist_ok=True)
    (d / "user_presets.json").write_text(json.dumps({"P": {"speed": 1.3}}))
    loaded = userpresets.load("P")
    assert loaded.speed == 1.3
    assert loaded.reverb == EffectSettings().reverb


def test_settings_dict_helpers_round_trip():
    s = EffectSettings(speed=1.4, autopan=0.7, reverse=True)
    assert effects.settings_from_dict(effects.settings_to_dict(s)) == s
