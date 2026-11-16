"""Tests for the "epic" macro: one slider that scales a whole cinematic recipe."""

from dataclasses import replace

import pytest

from nuance import cli
from nuance.audio import effects, processor
from nuance.audio.effects import EffectSettings, apply_epic
from tests.conftest import needs_ffmpeg

SR = 44100


def test_zero_epic_changes_nothing():
    s = EffectSettings(speed=1.2, reverb=0.3)
    assert apply_epic(s) == s


def test_result_has_the_macro_folded_in_and_reset():
    out = apply_epic(EffectSettings(epic=1.0))
    assert out.epic == 0.0
    assert out.speed < 1.0
    assert out.pitch_semitones < 0
    assert out.reverb > 0 and out.bass_boost_db > 0 and out.punch > 0
    assert out.stereo_width > 1.0 and out.brightness_db > 0
    assert out.fade_in_sec > 0 and out.fade_out_sec > 0


def test_applying_twice_is_the_same_as_once():
    once = apply_epic(EffectSettings(epic=0.7))
    assert apply_epic(once) == once


def test_it_does_not_modify_the_input():
    s = EffectSettings(epic=0.5)
    apply_epic(s)
    assert s.epic == 0.5 and s.speed == 1.0


@pytest.mark.parametrize("attr", [
    "bass_boost_db", "reverb", "punch", "stereo_width", "brightness_db",
    "fade_in_sec", "fade_out_sec", "chorus",
])
def test_more_epic_never_means_less_of_it(attr):
    levels = [getattr(apply_epic(EffectSettings(epic=e / 10)), attr) for e in range(11)]
    assert levels == sorted(levels)


def test_more_epic_means_slower_and_lower():
    speeds = [apply_epic(EffectSettings(epic=e / 10)).speed for e in range(11)]
    pitches = [apply_epic(EffectSettings(epic=e / 10)).pitch_semitones for e in range(11)]
    assert speeds == sorted(speeds, reverse=True)
    assert pitches == sorted(pitches, reverse=True)


def test_it_stacks_on_the_users_own_settings():
    out = apply_epic(EffectSettings(epic=1.0, speed=0.9, bass_boost_db=4.0))
    plain = apply_epic(EffectSettings(epic=1.0))
    assert out.speed == pytest.approx(plain.speed * 0.9)
    assert out.bass_boost_db == pytest.approx(plain.bass_boost_db + 4.0)


def test_it_raises_but_never_lowers_what_the_user_set_higher():
    out = apply_epic(EffectSettings(epic=0.3, reverb=0.9, punch=0.9, fade_in_sec=8.0))
    assert out.reverb == 0.9
    assert out.punch == 0.9
    assert out.fade_in_sec == 8.0


def test_out_of_range_values_are_clamped():
    assert apply_epic(EffectSettings(epic=5.0)) == apply_epic(EffectSettings(epic=1.0))
    assert apply_epic(EffectSettings(epic=-1.0)).speed == 1.0


def test_loudness_is_left_to_the_user():
    assert apply_epic(EffectSettings(epic=1.0)).loudness_lufs == effects.NO_LOUDNORM
    assert apply_epic(EffectSettings(epic=1.0, loudness_lufs=-12.0)).loudness_lufs == -12.0


# -- it reaches the filter chain everywhere settings are used -------------------------


def test_filters_include_the_macros_effects():
    joined = " ".join(effects.build_filters(EffectSettings(epic=1.0), SR, 60.0))
    for expected in ("asetrate=", "bass=", "aecho=", "extrastereo=", "acompressor=",
                     "treble=", "afade=t=in", "afade=t=out"):
        assert expected in joined, expected


def test_plan_uses_the_convolution_reverb_for_epic():
    plan = effects.build_plan(EffectSettings(epic=0.8), SR, reverb_ir="/tmp/ir.wav")
    assert "afir=" in plan.filter_complex
    assert plan.extra_inputs == ["/tmp/ir.wav"]


def test_epic_is_part_of_the_preview_cache_key(tmp_path):
    a = processor._preview_key(tmp_path / "x.mp3", 0.0, 10.0, EffectSettings())
    b = processor._preview_key(tmp_path / "x.mp3", 0.0, 10.0, EffectSettings(epic=0.5))
    assert a != b


def test_duration_estimate_follows_the_slowdown():
    plain = processor._estimate_duration(100.0, EffectSettings())
    epic = processor._estimate_duration(100.0, EffectSettings(epic=1.0))
    assert epic == pytest.approx(plain / 0.88)


# -- the preset, the slider, the command line -----------------------------------------


def test_epic_preset_exists_and_sets_the_macro():
    assert "Epic" in effects.preset_names()
    assert 0.5 < effects.get_preset("Epic").epic <= 1.0


def test_epic_survives_saving_and_loading_a_preset():
    s = EffectSettings(epic=0.6)
    assert effects.settings_from_dict(effects.settings_to_dict(s)).epic == 0.6


def test_command_line_has_an_epic_flag():
    args = cli.build_parser().parse_args(["remix", "x.mp3", "--epic", "0.7"])
    assert cli.settings_from_args(args).epic == 0.7
    assert "epic" in cli._field_docs()["epic"].lower()


def test_cli_epic_flag_stacks_on_the_preset():
    args = cli.build_parser().parse_args(["remix", "x.mp3", "-p", "nightcore", "--epic", "0.5"])
    s = cli.settings_from_args(args)
    assert s.speed == 1.3 and s.epic == 0.5  # both kept; the macro folds in later


@needs_ffmpeg
def test_epic_export_is_longer_and_stays_in_range(tmp_path, make_tone):
    import subprocess

    import numpy as np

    from nuance.audio import ffmpeg

    src = make_tone(tmp_path / "s.wav", seconds=4)
    out = tmp_path / "epic.wav"
    processor.process(src, out, replace(EffectSettings(), epic=1.0))

    assert ffmpeg.probe(out).sample_rate == 44100
    assert ffmpeg.probe(out).duration == pytest.approx(4 / 0.88, abs=0.3)
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(out), "-f", "f32le", "-ac", "2", "-"],
        capture_output=True, check=True,
    ).stdout
    samples = np.frombuffer(raw, dtype="<f4")
    assert np.abs(samples).max() <= 1.0
    # The slow fade-in and fade-out leave the very ends much quieter than the middle.
    n = samples.size
    assert np.abs(samples[: n // 50]).mean() < np.abs(samples[n // 2 - n // 50 : n // 2]).mean() / 2
    assert np.abs(samples[-n // 50 :]).mean() < np.abs(samples[n // 2 - n // 50 : n // 2]).mean() / 2
