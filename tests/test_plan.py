"""Tests for build_plan: how music filters, the reverb impulse response and
texture layers become an FFmpeg invocation (plain -af, or a filter_complex graph
with extra inputs).
"""

import re

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
    assert "aloop=loop=-1:size=2147483647" in fc
    assert "volume=" in fc
    assert fc.endswith("[out]")
    assert "amix=inputs=2:duration=first:dropout_transition=0,volume=2[out]" in fc


def test_plan_textures_do_not_lower_the_music():
    """amix divides every input by the input count; the graph must undo that."""
    fc = effects.build_plan(
        EffectSettings(vinyl=0.5, cassette=0.3),
        SR,
        {"vinyl": "/tmp/v.wav", "cassette": "/tmp/c.wav"},
    ).filter_complex
    assert "amix=inputs=3:duration=first:dropout_transition=0,volume=3" in fc


def test_plan_with_no_music_filters_feeds_source_straight_to_the_mix():
    s = EffectSettings(vinyl=0.5)  # no speed/pitch/etc.
    plan = effects.build_plan(s, SR, {"vinyl": "/tmp/v.wav"})
    assert "[0:a][g" in plan.filter_complex
    assert "anull" not in plan.filter_complex


def test_plan_with_two_textures_mixes_three_inputs():
    s = EffectSettings(vinyl=0.5, cassette=0.3)
    paths = {"vinyl": "/tmp/vinyl.wav", "cassette": "/tmp/cassette.wav"}
    plan = effects.build_plan(s, SR, paths)

    assert set(plan.extra_inputs) == {"/tmp/vinyl.wav", "/tmp/cassette.wav"}
    assert "amix=inputs=3:duration=first:dropout_transition=0" in plan.filter_complex
    # Inputs 1 and 2 both get their own loop+volume chain.
    assert plan.filter_complex.count("aloop=loop=-1") == 2


def test_plan_master_chain_runs_after_textures_are_mixed():
    """Loudness must be measured on the finished mix, textures included."""
    s = EffectSettings(vinyl=0.5, loudness_lufs=-9.0, punch=0.5)
    fc = effects.build_plan(s, SR, {"vinyl": "/tmp/v.wav"}).filter_complex
    assert fc.index("amix=") < fc.index("acompressor=") < fc.index("loudnorm=")


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


# -- convolution reverb ------------------------------------------------------


def test_reverb_without_impulse_response_falls_back_to_aecho():
    plan = effects.build_plan(EffectSettings(reverb=0.5), SR)
    assert plan.filter_complex == ""
    assert any(f.startswith("aecho=") for f in plan.audio_filters)


def test_reverb_with_impulse_response_uses_afir():
    plan = effects.build_plan(EffectSettings(reverb=0.5), SR, reverb_ir="/tmp/ir.wav")
    assert plan.extra_inputs == ["/tmp/ir.wav"]
    assert plan.audio_filters == []
    assert "afir=" in plan.filter_complex
    assert "aecho" not in plan.filter_complex
    assert plan.filter_complex.endswith("[out]")


def test_reverb_zero_ignores_impulse_response():
    plan = effects.build_plan(EffectSettings(reverb=0.0), SR, reverb_ir="/tmp/ir.wav")
    assert plan.extra_inputs == []
    assert plan.filter_complex == ""


def test_reverb_mix_grows_wet_with_amount():
    dry_lo, wet_lo = effects.reverb_mix(0.1)
    dry_hi, wet_hi = effects.reverb_mix(1.0)
    assert wet_lo < wet_hi
    assert dry_hi <= dry_lo
    assert effects.reverb_mix(0.0) == (1.0, 0.0)


def test_reverb_sits_after_speed_and_before_muffle():
    s = EffectSettings(speed=0.8, reverb=0.5, muffled_hz=4000.0)
    fc = effects.build_plan(s, SR, reverb_ir="/tmp/ir.wav").filter_complex
    assert fc.index("asetrate=") < fc.index("afir=") < fc.index("lowpass=")


def test_inputs_are_numbered_in_the_order_they_are_added():
    s = EffectSettings(reverb=0.5, vinyl=0.4)
    plan = effects.build_plan(s, SR, {"vinyl": "/tmp/v.wav"}, reverb_ir="/tmp/ir.wav")
    assert plan.extra_inputs == ["/tmp/ir.wav", "/tmp/v.wav"]
    # The IR is input 1, the texture input 2.
    assert "[1:a]aresample=" in plan.filter_complex
    assert "[2:a]aloop=" in plan.filter_complex


_PART = re.compile(r"^((?:\[[^\]]+\])+)(.*?)((?:\[[^\]]+\])+)$")


def _generated_pads(graph: str):
    """Return (produced, consumed) generated pad labels of a filter graph."""
    produced, consumed = [], []
    for part in graph.split(";"):
        match = _PART.match(part)
        assert match, part
        ins = re.findall(r"\[([^\]]+)\]", match.group(1))
        outs = re.findall(r"\[([^\]]+)\]", match.group(3))
        consumed += [i for i in ins if i.startswith("g")]
        produced += outs
    return produced, consumed


def test_every_generated_pad_is_produced_and_consumed_once():
    cases = [
        EffectSettings(reverb=0.5),
        EffectSettings(reverb=0.5, vinyl=0.3, cassette=0.3),
        EffectSettings(
            speed=0.8, reverb=0.7, vinyl=0.3, wow_flutter=0.4, chorus=0.5,
            muffled_hz=5000.0, punch=0.5, loudness_lufs=-10.0, fade_in_sec=1.0,
        ),
        EffectSettings(vinyl=0.3),
    ]
    for s in cases:
        plan = effects.build_plan(
            s, SR, {"vinyl": "/v.wav", "cassette": "/c.wav"},
            output_duration=60.0, reverb_ir="/ir.wav",
        )
        produced, consumed = _generated_pads(plan.filter_complex)
        assert produced.count("out") == 1
        generated = [p for p in produced if p != "out"]
        assert len(set(generated)) == len(generated)
        assert sorted(generated) == sorted(consumed), plan.filter_complex
