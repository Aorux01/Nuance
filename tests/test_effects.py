"""Tests for the FFmpeg filter-chain builder.

These are pure string-building functions, so no audio or FFmpeg is needed.
"""

import math

import pytest

from nuance.audio import effects
from nuance.audio.effects import EffectSettings

SR = 44100


# -- build_atempo_chain -------------------------------------------------------


def _atempo_product(filters):
    """Multiply every atempo=... factor in a filter list together."""
    product = 1.0
    for f in filters:
        assert f.startswith("atempo=")
        product *= float(f.split("=", 1)[1])
    return product


def test_atempo_noop_near_one():
    assert effects.build_atempo_chain(1.0) == []
    assert effects.build_atempo_chain(1.0005) == []


@pytest.mark.parametrize("speed", [0.5, 0.75, 1.25, 1.5, 2.0])
def test_atempo_single_step_in_range(speed):
    filters = effects.build_atempo_chain(speed)
    assert len(filters) == 1
    assert _atempo_product(filters) == pytest.approx(speed, rel=1e-3)


@pytest.mark.parametrize("speed", [2.5, 3.0, 4.0, 8.0])
def test_atempo_splits_fast(speed):
    filters = effects.build_atempo_chain(speed)
    assert len(filters) >= 2
    for f in filters:
        factor = float(f.split("=", 1)[1])
        assert 0.5 <= factor <= 2.0 + 1e-9
    assert _atempo_product(filters) == pytest.approx(speed, rel=1e-3)


@pytest.mark.parametrize("speed", [0.4, 0.25, 0.1])
def test_atempo_splits_slow(speed):
    filters = effects.build_atempo_chain(speed)
    assert len(filters) >= 2
    for f in filters:
        factor = float(f.split("=", 1)[1])
        assert 0.5 - 1e-9 <= factor <= 2.0
    assert _atempo_product(filters) == pytest.approx(speed, rel=1e-3)


# -- build_filters: individual effects --------------------------------------


def test_no_settings_yields_no_filters():
    assert effects.build_filters(EffectSettings(), SR) == []


def test_reverse_first():
    filters = effects.build_filters(EffectSettings(reverse=True, speed=1.25), SR)
    assert filters[0] == "areverse"


def test_speed_without_keep_pitch_uses_single_resample():
    filters = effects.build_filters(EffectSettings(speed=1.25), SR)
    assert filters == [f"asetrate={int(SR * 1.25)}", f"aresample={SR}:resampler=soxr"]


def test_slowed_uses_single_resample():
    filters = effects.build_filters(EffectSettings(speed=0.8), SR)
    assert filters == [f"asetrate={int(SR * 0.8)}", f"aresample={SR}:resampler=soxr"]


def test_keep_pitch_uses_atempo_only():
    filters = effects.build_filters(EffectSettings(speed=1.5, keep_pitch=True), SR)
    assert filters == ["atempo=1.5000"]


def test_keep_pitch_slow_below_half_splits():
    filters = effects.build_filters(EffectSettings(speed=0.4, keep_pitch=True), SR)
    assert all(f.startswith("atempo=") for f in filters)
    assert _atempo_product(filters) == pytest.approx(0.4, rel=1e-3)


def test_pitch_only_shifts_then_corrects_tempo():
    filters = effects.build_filters(EffectSettings(pitch_semitones=2.0), SR)
    ratio = 2 ** (2.0 / 12.0)
    assert filters[0] == f"asetrate={int(SR * ratio)}"
    assert filters[1] == f"aresample={SR}:resampler=soxr"
    # The trailing atempo undoes the tempo change the resample introduced.
    tail = filters[2:]
    assert _atempo_product(tail) == pytest.approx(1.0 / ratio, rel=1e-3)


def test_negative_pitch():
    filters = effects.build_filters(EffectSettings(pitch_semitones=-3.0), SR)
    ratio = 2 ** (-3.0 / 12.0)
    assert filters[0] == f"asetrate={int(SR * ratio)}"


def test_speed_and_pitch_combine_into_one_asetrate():
    """Nightcore: no keep_pitch, so speed and pitch fold into a single step."""
    s = EffectSettings(speed=1.30, pitch_semitones=2.0)
    filters = effects.build_filters(s, SR)
    asetrate = [f for f in filters if f.startswith("asetrate=")]
    resample = [f for f in filters if f.startswith("aresample=")]
    assert len(asetrate) == 1
    assert len(resample) == 1

    pitch_ratio = 2 ** (2.0 / 12.0)
    combined = 1.30 * pitch_ratio
    assert asetrate[0] == f"asetrate={int(SR * combined)}"

    # Net tempo must end up at the requested speed: the asetrate multiplies
    # tempo by `combined`, the trailing atempo chain multiplies by 1/pitch_ratio.
    tail = [f for f in filters if f.startswith("atempo=")]
    net_tempo = combined * _atempo_product(tail)
    assert net_tempo == pytest.approx(1.30, rel=1e-3)


def test_keep_pitch_with_speed_and_pitch():
    """keep_pitch keeps the two changes separate: atempo for speed, resample for pitch."""
    s = EffectSettings(speed=1.20, pitch_semitones=4.0, keep_pitch=True)
    filters = effects.build_filters(s, SR)
    assert filters[0] == "atempo=1.2000"
    pitch_ratio = 2 ** (4.0 / 12.0)
    assert filters[1] == f"asetrate={int(SR * pitch_ratio)}"
    tail = [f for f in filters if f.startswith("atempo=")][1:]
    assert _atempo_product(tail) == pytest.approx(1.0 / pitch_ratio, rel=1e-3)


def test_bass_boost():
    filters = effects.build_filters(EffectSettings(bass_boost_db=8.0), SR)
    assert any(f.startswith("bass=g=8.00") for f in filters)


def test_bass_boost_zero_omitted():
    filters = effects.build_filters(EffectSettings(bass_boost_db=0.0), SR)
    assert not any(f.startswith("bass=") for f in filters)


def test_reverb_present_when_above_zero():
    filters = effects.build_filters(EffectSettings(reverb=0.5), SR)
    assert any(f.startswith("aecho=") for f in filters)


def test_reverb_zero_omitted():
    assert not any(
        f.startswith("aecho=")
        for f in effects.build_filters(EffectSettings(reverb=0.0), SR)
    )


def test_autopan_adds_apulsator():
    filters = effects.build_filters(EffectSettings(autopan=0.6), SR)
    assert any(f.startswith("apulsator=mode=sine") for f in filters)


def test_autopan_zero_omitted():
    filters = effects.build_filters(EffectSettings(autopan=0.0), SR)
    assert not any(f.startswith("apulsator=") for f in filters)


def test_autopan_scales_width_with_amount():
    def width(amount):
        f = next(
            x for x in effects.build_filters(EffectSettings(autopan=amount), SR)
            if x.startswith("apulsator=")
        )
        return float(f.split("width=", 1)[1])

    assert width(0.1) < width(1.0)
    assert width(1.0) <= 2.0  # apulsator caps width at 2


def test_muffled_adds_lowpass_below_max():
    filters = effects.build_filters(EffectSettings(muffled_hz=3000.0), SR)
    assert "lowpass=f=3000" in filters


def test_muffled_at_max_is_off():
    filters = effects.build_filters(EffectSettings(muffled_hz=effects.MAX_HZ), SR)
    assert not any(f.startswith("lowpass=") for f in filters)


def test_muffled_floor_clamped():
    filters = effects.build_filters(EffectSettings(muffled_hz=100.0), SR)
    assert "lowpass=f=500" in filters


def test_bitcrush_below_clean():
    filters = effects.build_filters(EffectSettings(bitcrush_bits=6.0), SR)
    assert any(f.startswith("acrusher=bits=6.0") for f in filters)


def test_bitcrush_at_clean_is_off():
    filters = effects.build_filters(
        EffectSettings(bitcrush_bits=effects.CLEAN_BITS), SR
    )
    assert not any(f.startswith("acrusher=") for f in filters)


def test_bitcrush_floor_clamped():
    filters = effects.build_filters(EffectSettings(bitcrush_bits=0.0), SR)
    assert any(f.startswith("acrusher=bits=1.0") for f in filters)


def test_loop_adds_aloop_above_one():
    filters = effects.build_filters(EffectSettings(loop_count=3), SR)
    assert "aloop=loop=2:size=2147483647" in filters


def test_loop_one_omitted():
    assert not any(
        f.startswith("aloop=")
        for f in effects.build_filters(EffectSettings(loop_count=1), SR)
    )


# -- mastering chain -----------------------------------------------------------


def test_master_chain_empty_by_default():
    """No mastering settings means no mastering filters."""
    filters = effects.build_filters(EffectSettings(), SR)
    assert not any(
        f.startswith(("treble=", "extrastereo=", "acompressor=", "alimiter=",
                      "loudnorm=", "afade="))
        for f in filters
    )


def test_brightness_adds_high_shelf():
    filters = effects.build_filters(EffectSettings(brightness_db=4.0), SR)
    assert any(f.startswith("treble=g=4.00:f=8000") for f in filters)


def test_brightness_clamped():
    filters = effects.build_filters(EffectSettings(brightness_db=99.0), SR)
    assert any(f.startswith("treble=g=12.00") for f in filters)


def test_stereo_width_adds_extrastereo():
    filters = effects.build_filters(EffectSettings(stereo_width=1.5), SR)
    assert any(f.startswith("extrastereo=m=1.500") for f in filters)


def test_stereo_width_one_is_noop():
    filters = effects.build_filters(EffectSettings(stereo_width=1.0), SR)
    assert not any(f.startswith("extrastereo=") for f in filters)


def test_punch_adds_compressor_then_limiter_in_order():
    filters = effects.build_filters(EffectSettings(punch=0.5), SR)
    comp = next(i for i, f in enumerate(filters) if f.startswith("acompressor="))
    lim = next(i for i, f in enumerate(filters) if f.startswith("alimiter="))
    assert comp < lim


def test_punch_scales_ratio_with_amount():
    def ratio(amount):
        f = next(
            x for x in effects.build_filters(EffectSettings(punch=amount), SR)
            if x.startswith("acompressor=")
        )
        token = f.split("ratio=", 1)[1]
        return float(token.split(":", 1)[0])

    assert ratio(0.1) < ratio(1.0)


def test_punch_zero_omitted():
    filters = effects.build_filters(EffectSettings(punch=0.0), SR)
    assert not any(f.startswith(("acompressor=", "alimiter=")) for f in filters)


def test_loudnorm_present_with_target():
    filters = effects.build_filters(EffectSettings(loudness_lufs=-9.0), SR)
    assert any(f.startswith("loudnorm=I=-9.0:") for f in filters)


def test_loudnorm_skipped_at_sentinel():
    filters = effects.build_filters(
        EffectSettings(loudness_lufs=effects.NO_LOUDNORM), SR
    )
    assert not any(f.startswith("loudnorm=") for f in filters)


def test_loudnorm_target_clamped():
    filters = effects.build_filters(EffectSettings(loudness_lufs=-99.0), SR)
    assert any(f.startswith("loudnorm=I=-30.0:") for f in filters)


def test_loudnorm_is_last_level_stage():
    """loudnorm must come after compression and limiting."""
    s = EffectSettings(punch=0.6, loudness_lufs=-10.0)
    filters = effects.build_filters(s, SR)
    lim = next(i for i, f in enumerate(filters) if f.startswith("alimiter="))
    norm = next(i for i, f in enumerate(filters) if f.startswith("loudnorm="))
    assert lim < norm


def test_master_chain_full_order():
    """brightness -> width -> compressor -> limiter -> loudnorm -> fades."""
    s = EffectSettings(
        brightness_db=3.0, stereo_width=1.3, punch=0.5,
        loudness_lufs=-9.0, fade_in_sec=1.0, fade_out_sec=2.0,
    )
    filters = effects.build_filters(s, SR, output_duration=60.0)
    order = [
        next(i for i, f in enumerate(filters) if f.startswith(prefix))
        for prefix in ("treble=", "extrastereo=", "acompressor=", "alimiter=",
                       "loudnorm=", "afade=t=in", "afade=t=out")
    ]
    assert order == sorted(order)


def test_fade_in_from_zero():
    filters = effects.build_filters(EffectSettings(fade_in_sec=1.5), SR)
    assert "afade=t=in:st=0:d=1.50" in filters


def test_fade_out_needs_output_duration():
    """Without a known length the fade-out is skipped, not guessed."""
    no_dur = effects.build_filters(EffectSettings(fade_out_sec=2.0), SR)
    assert not any(f.startswith("afade=t=out") for f in no_dur)

    with_dur = effects.build_filters(
        EffectSettings(fade_out_sec=2.0), SR, output_duration=30.0
    )
    assert "afade=t=out:st=28.000:d=2.00" in with_dur


def test_fade_out_skipped_when_longer_than_track():
    filters = effects.build_filters(
        EffectSettings(fade_out_sec=10.0), SR, output_duration=5.0
    )
    assert not any(f.startswith("afade=t=out") for f in filters)


def test_energy_boost_preset_has_the_signature_chain():
    filters = effects.build_filters(
        effects.get_preset("Energy Boost"), SR, output_duration=120.0
    )
    joined = " ".join(filters)
    assert "treble=" in joined
    assert "extrastereo=" in joined
    assert "acompressor=" in joined
    assert "alimiter=" in joined
    assert "loudnorm=I=-9.0" in joined


# -- presets ---------------------------------------------------------------


def test_preset_names_stable():
    names = effects.preset_names()
    assert names[0] == "Original"
    assert "Nightcore" in names
    assert "Lo-Fi" in names
    assert "Energy Boost" in names


def test_get_preset_returns_copy():
    a = effects.get_preset("Nightcore")
    a.speed = 99.0
    b = effects.get_preset("Nightcore")
    assert b.speed != 99.0


def test_unknown_preset_is_default():
    assert effects.get_preset("does-not-exist") == EffectSettings()


@pytest.mark.parametrize("name", effects.preset_names())
def test_every_preset_builds_valid_filters(name):
    """Every shipped preset must produce a filter list without raising."""
    filters = effects.build_filters(effects.get_preset(name), SR)
    assert isinstance(filters, list)
    for f in filters:
        assert isinstance(f, str) and f


# -- texture_amounts -----------------------------------------------------------


def test_texture_amounts_reads_settings():
    s = EffectSettings(vinyl=0.4, cassette=0.7)
    assert effects.texture_amounts(s) == {"vinyl": 0.4, "cassette": 0.7}
