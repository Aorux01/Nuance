"""Tests for processor-level helpers that don't need FFmpeg."""

import pytest

from nuance.audio.processor import _estimate_duration
from nuance.audio.effects import EffectSettings


def test_estimate_zero_duration_stays_zero():
    assert _estimate_duration(0.0, EffectSettings()) == 0.0
    assert _estimate_duration(-5.0, EffectSettings(speed=2.0)) == 0.0


def test_estimate_unchanged_when_no_speed_or_loop():
    assert _estimate_duration(120.0, EffectSettings()) == pytest.approx(120.0)


def test_estimate_speed_up_shortens():
    assert _estimate_duration(120.0, EffectSettings(speed=2.0)) == pytest.approx(60.0)


def test_estimate_slow_down_lengthens():
    assert _estimate_duration(60.0, EffectSettings(speed=0.5)) == pytest.approx(120.0)


def test_estimate_loop_multiplies():
    assert _estimate_duration(30.0, EffectSettings(loop_count=4)) == pytest.approx(120.0)


def test_estimate_speed_and_loop_combine():
    got = _estimate_duration(120.0, EffectSettings(speed=2.0, loop_count=3))
    assert got == pytest.approx(180.0)


def test_estimate_loop_count_below_one_treated_as_one():
    assert _estimate_duration(50.0, EffectSettings(loop_count=0)) == pytest.approx(50.0)


def test_estimate_speed_near_one_is_noop():
    assert _estimate_duration(100.0, EffectSettings(speed=1.0005)) == pytest.approx(100.0)


def test_estimate_pitch_shift_does_not_change_duration():
    """A musical pitch shift is tempo-compensated, so length is unaffected."""
    assert _estimate_duration(
        90.0, EffectSettings(pitch_semitones=5.0)
    ) == pytest.approx(90.0)
