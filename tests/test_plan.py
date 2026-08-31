"""Tests for build_plan: how music filters and texture layers become an
FFmpeg invocation (plain -af, or a filter_complex graph with extra inputs).
"""

import pytest

from nuance.audio import effects
from nuance.audio.effects import EffectSettings

SR = 44100


def test_plan_without_textures_uses_plain_filters():
    plan = effects.build_plan(EffectSettings(speed=1.25), SR)
    assert plan.filter_complex == ""
    assert plan.extra_inputs == []
    assert plan.audio_filters == effects.build_filters(EffectSettings(speed=1.25), SR)


def test_plan_ignores_textures_with_zero_amount():
    s = EffectSettings(vinyl=0.0)
    plan = effects.build_plan(s, SR, {"vinyl": "/tmp/vinyl.wav"})
    assert plan.filter_complex == ""
    assert plan.extra_inputs == []


def test_plan_ignores_textures_without_a_path():
    s = EffectSettings(vinyl=0.5)
    plan = effects.build_plan(s, SR, {})
    assert plan.filter_complex == ""


def test_plan_with_one_texture_builds_filter_complex():
    s = EffectSettings(speed=0.9, vinyl=0.5)
    plan = effects.build_plan(s, SR, {"vinyl": "/tmp/vinyl.wav"})

    assert plan.audio_filters == []  # everything moves into the graph
    assert plan.extra_inputs == ["/tmp/vinyl.wav"]

    fc = plan.filter_complex
    assert fc.startswith("[0:a]")
    assert "[music]" in fc
    assert "aloop=loop=-1:size=2147483647" in fc
    assert "volume=" in fc
    assert fc.endswith("[out]")
    assert "amix=inputs=2:duration=first:dropout_transition=0[out]" in fc


def test_plan_with_no_music_filters_uses_anull():
    s = EffectSettings(vinyl=0.5)  # no speed/pitch/etc.
    plan = effects.build_plan(s, SR, {"vinyl": "/tmp/v.wav"})
    assert "[0:a]anull[music]" in plan.filter_complex


def test_plan_with_two_textures_mixes_three_inputs():
    s = EffectSettings(vinyl=0.5, cassette=0.3)
    paths = {"vinyl": "/tmp/vinyl.wav", "cassette": "/tmp/cassette.wav"}
    plan = effects.build_plan(s, SR, paths)

    assert set(plan.extra_inputs) == {"/tmp/vinyl.wav", "/tmp/cassette.wav"}
    assert "amix=inputs=3:duration=first:dropout_transition=0[out]" in plan.filter_complex
    # Inputs 1 and 2 both get their own loop+volume chain.
    assert plan.filter_complex.count("aloop=loop=-1") == 2


def test_plan_texture_volume_scales_with_amount():
    low = effects.build_plan(
        EffectSettings(vinyl=0.2), SR, {"vinyl": "/tmp/v.wav"}
    ).filter_complex
    high = effects.build_plan(
        EffectSettings(vinyl=1.0), SR, {"vinyl": "/tmp/v.wav"}
    ).filter_complex

    def vol(fc):
        token = fc.split("volume=", 1)[1]
        return float(token.split("[", 1)[0].rstrip(","))

    assert vol(low) < vol(high)
    assert 0.0 < vol(high) <= 1.0


def test_plan_none_texture_paths_is_safe():
    plan = effects.build_plan(EffectSettings(vinyl=0.5), SR, None)
    assert plan.filter_complex == ""
