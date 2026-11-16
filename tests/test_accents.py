"""Tests for the epic extras: the reverb tail, drop detection, the riser and impact."""

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from nuance.audio import effects, ffmpeg, highlights, processor, textures
from nuance.audio.effects import Accent, EffectSettings
from nuance.ui import app
from tests.conftest import needs_ffmpeg
from tests.test_plan import _generated_pads

SR = 44100
RATE = 8000  # the analysis rate of highlights


# -- drop detection on samples ------------------------------------------------------


def _sine_track(parts, seconds):
    t = np.arange(int(RATE * seconds)) / RATE
    x = np.zeros_like(t)
    for start, end, gain, freq in parts:
        m = (t >= start) & (t < end)
        x[m] = gain * np.sin(2 * np.pi * freq * t[m])
    return x.astype("f4")


def test_a_step_up_in_the_bass_is_the_drop():
    x = _sine_track([(0, 30, 0.05, 80), (30, 50, 0.6, 80)], 50)
    assert highlights.drop_from_samples(x) == pytest.approx(30.0, abs=0.5)


def test_a_noise_build_up_without_bass_still_finds_the_bass_drop():
    rng = np.random.default_rng(1)
    t = np.arange(RATE * 50) / RATE
    x = np.zeros_like(t)
    quiet = t < 14
    x[quiet] = 0.03 * np.sin(2 * np.pi * 80 * t[quiet])
    build = (t >= 14) & (t < 30)
    x[build] = ((t[build] - 14) / 16) * 0.5 * rng.standard_normal(build.sum())
    drop = t >= 30
    x[drop] = 0.6 * np.sin(2 * np.pi * 70 * t[drop]) + 0.2 * rng.standard_normal(drop.sum())
    assert highlights.drop_from_samples(x.astype("f4")) == pytest.approx(30.0, abs=0.5)


def test_the_biggest_jump_wins():
    x = _sine_track([(0, 15, 0.05, 80), (15, 30, 0.15, 80), (30, 50, 0.9, 80)], 50)
    assert highlights.drop_from_samples(x) == pytest.approx(30.0, abs=0.5)


@pytest.mark.parametrize("make", [
    lambda: _sine_track([(0, 50, 0.3, 80)], 50),   # even level
    lambda: _sine_track([(0, 5, 0.3, 80)], 5),     # too short for the windows
    lambda: np.zeros(RATE * 40, dtype="f4"),       # silence
    lambda: np.zeros(0, dtype="f4"),               # nothing at all
])
def test_no_drop_when_nothing_stands_out(make):
    assert highlights.drop_from_samples(make()) is None


def test_a_gentle_rise_is_not_a_drop():
    x = _sine_track([(0, 25, 0.30, 80), (25, 50, 0.38, 80)], 50)  # about +2 dB
    assert highlights.drop_from_samples(x) is None


# -- find_drop on files ----------------------------------------------------------------


def _track_file(path, seconds=45, quiet_until=25):
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"sine=frequency=80:duration={seconds}",
            "-af", f"volume=0.05,volume=enable='gte(t,{quiet_until})':volume=12",
            "-ac", "2", str(path),
        ],
        check=True,
    )
    return path


@needs_ffmpeg
def test_find_drop_on_a_real_file(tmp_path):
    track = _track_file(tmp_path / "t.wav")
    assert highlights.find_drop(track) == pytest.approx(25.0, abs=0.6)


@needs_ffmpeg
def test_find_drop_gives_none_for_a_short_track(tmp_path):
    track = _track_file(tmp_path / "s.wav", seconds=10, quiet_until=5)
    assert highlights.find_drop(track) is None


def test_find_drop_of_a_missing_file_is_none(tmp_path):
    assert highlights.find_drop(tmp_path / "nope.wav") is None


def test_find_drop_remembers_its_answer_until_the_file_changes(tmp_path, monkeypatch):
    track = tmp_path / "t.wav"
    track.write_bytes(b"a")
    calls = []
    monkeypatch.setattr(highlights, "_detect_drop", lambda p: calls.append(1) or 12.5)

    assert highlights.find_drop(track) == 12.5
    assert highlights.find_drop(track) == 12.5
    assert len(calls) == 1  # the second call did not decode again

    track.write_bytes(b"a longer file now")
    highlights.find_drop(track)
    assert len(calls) == 2


# -- reverb tail ----------------------------------------------------------------------------


def test_no_reverb_no_tail():
    assert effects.reverb_tail_seconds(EffectSettings()) == 0.0


def test_the_tail_grows_with_the_reverb_and_has_a_sensible_range():
    tails = [effects.reverb_tail_seconds(EffectSettings(reverb=r / 10)) for r in range(1, 11)]
    assert tails == sorted(tails)
    assert 0.5 < tails[0] < 1.0
    assert tails[-1] == pytest.approx(3.0)


def test_epic_brings_a_tail():
    assert effects.reverb_tail_seconds(EffectSettings(epic=1.0)) > 2.0


def test_the_graph_pads_the_input_so_the_reverb_can_ring_out():
    plan = effects.build_plan(EffectSettings(reverb=1.0), SR, reverb_ir="/ir.wav")
    assert "apad=pad_dur=3.00" in plan.filter_complex
    assert plan.filter_complex.index("apad=") < plan.filter_complex.index("afir=")


def test_the_estimated_length_includes_the_tail():
    plain = processor._estimate_duration(60.0, EffectSettings())
    wet = processor._estimate_duration(60.0, EffectSettings(reverb=1.0))
    assert wet == pytest.approx(plain + 3.0)


def test_the_fade_out_ends_at_the_real_end_including_the_tail():
    s = EffectSettings(reverb=1.0, fade_out_sec=2.0)
    total = processor._estimate_duration(60.0, s)  # 63
    fc = effects.build_plan(s, SR, output_duration=total, reverb_ir="/ir.wav").filter_complex
    assert f"afade=t=out:st={total - 2.0:.3f}:d=2.00" in fc


# -- accents in the plan --------------------------------------------------------------------


def test_an_accent_is_delayed_to_its_moment():
    plan = effects.build_plan(
        EffectSettings(), SR, accents=[Accent("/a.wav", 12.5, 0.7)]
    )
    assert plan.extra_inputs == ["/a.wav"]
    assert "adelay=12500|12500" in plan.filter_complex
    assert "volume=0.700" in plan.filter_complex


def test_an_accent_that_began_before_the_excerpt_is_trimmed_not_delayed():
    plan = effects.build_plan(
        EffectSettings(), SR, accents=[Accent("/a.wav", -2.5, 0.7)]
    )
    assert "atrim=start=2.500" in plan.filter_complex
    assert "adelay" not in plan.filter_complex


def test_an_accent_at_zero_needs_no_delay():
    fc = effects.build_plan(
        EffectSettings(), SR, accents=[Accent("/a.wav", 0.0, 0.7)]
    ).filter_complex
    assert "adelay" not in fc and "atrim" not in fc


def test_accents_are_kept_alive_so_amix_does_not_boost_the_music():
    """amix rescales the other inputs when one ends; apad keeps the accent 'open'."""
    fc = effects.build_plan(
        EffectSettings(), SR, accents=[Accent("/a.wav", 1.0, 0.5)]
    ).filter_complex
    assert ",apad," in fc
    assert "amix=inputs=2:duration=first:dropout_transition=0,volume=2" in fc


def test_silent_accents_are_ignored():
    plan = effects.build_plan(EffectSettings(), SR, accents=[Accent("/a.wav", 1.0, 0.0)])
    assert plan.filter_complex == "" and plan.extra_inputs == []


def test_accents_mix_with_textures_and_reverb_in_a_consistent_graph():
    plan = effects.build_plan(
        EffectSettings(speed=0.9, reverb=0.7, vinyl=0.3, punch=0.5, loudness_lufs=-10.0),
        SR,
        {"vinyl": "/v.wav"},
        output_duration=60.0,
        reverb_ir="/ir.wav",
        accents=[Accent("/riser.wav", 20.0, 0.6), Accent("/impact.wav", 26.0, 0.9)],
    )
    assert plan.extra_inputs == ["/ir.wav", "/v.wav", "/riser.wav", "/impact.wav"]
    assert "amix=inputs=4:duration=first:dropout_transition=0,volume=4" in plan.filter_complex
    produced, consumed = _generated_pads(plan.filter_complex)
    generated = [p for p in produced if p != "out"]
    assert sorted(generated) == sorted(consumed)
    # The mix (and so the accents) happens before the master chain's loudness.
    assert plan.filter_complex.index("amix=inputs=4") < plan.filter_complex.index("loudnorm=")


def test_volumes_scale_and_clamp():
    assert effects.riser_volume(0.0) == 0.0
    assert effects.riser_volume(0.5) < effects.riser_volume(1.0) <= 1.0
    assert effects.impact_volume(2.0) == effects.impact_volume(1.0)


# -- placing accents on the output timeline ---------------------------------------------


@pytest.fixture
def drop_at(monkeypatch):
    """Make find_drop answer with a chosen time (or None)."""

    def set_drop(value):
        monkeypatch.setattr(highlights, "find_drop", lambda path: value)

    return set_drop


def _by_name(accents):
    names = {Path(textures.get_accent_path(n)).name: n for n in ("riser", "impact")}
    return {names[Path(a.path).name]: a for a in accents}


def test_impact_lands_on_the_drop_and_riser_ends_on_it(drop_at):
    drop_at(60.0)
    got = _by_name(processor._accents(Path("x.mp3"), EffectSettings(riser=1, impact=1)))
    assert got["impact"].start == pytest.approx(60.0)
    assert got["riser"].start + textures.RISER_SEC == pytest.approx(60.0)


def test_a_faster_track_brings_the_drop_earlier(drop_at):
    drop_at(60.0)
    got = _by_name(processor._accents(Path("x.mp3"), EffectSettings(speed=1.5, impact=1, riser=1)))
    assert got["impact"].start == pytest.approx(40.0)


def test_epic_adds_both_accents(drop_at):
    drop_at(60.0)
    assert set(_by_name(processor._accents(Path("x.mp3"), EffectSettings(epic=0.8)))) == {
        "riser", "impact"
    }


def test_no_drop_means_an_impact_at_the_start_and_no_riser(drop_at):
    drop_at(None)
    got = _by_name(processor._accents(Path("x.mp3"), EffectSettings(riser=1, impact=1)))
    assert set(got) == {"impact"}
    assert got["impact"].start == 0.0


def test_reversed_audio_gets_no_accents(drop_at):
    drop_at(60.0)
    assert processor._accents(Path("x.mp3"), EffectSettings(reverse=True, impact=1)) == []


def test_nothing_is_analysed_when_no_accent_is_wanted(monkeypatch):
    def boom(path):
        raise AssertionError("must not decode the track")

    monkeypatch.setattr(highlights, "find_drop", boom)
    assert processor._accents(Path("x.mp3"), EffectSettings(speed=1.2, reverb=0.5)) == []


def test_an_excerpt_starting_before_the_drop_gets_both(drop_at):
    drop_at(60.0)
    got = _by_name(processor._accents(
        Path("x.mp3"), EffectSettings(impact=1, riser=1), seek=54.0, clip_length=15.0
    ))
    assert got["impact"].start == pytest.approx(6.0)
    assert got["riser"].start == pytest.approx(0.0)


def test_an_excerpt_cutting_into_the_riser_joins_it_part_way(drop_at):
    drop_at(60.0)
    got = _by_name(processor._accents(
        Path("x.mp3"), EffectSettings(impact=1, riser=1), seek=57.0, clip_length=15.0
    ))
    assert got["riser"].start == pytest.approx(-3.0)  # trimmed by 3 s when mixed
    assert got["impact"].start == pytest.approx(3.0)


def test_an_excerpt_after_the_drop_gets_neither(drop_at):
    drop_at(60.0)
    assert processor._accents(
        Path("x.mp3"), EffectSettings(impact=1, riser=1), seek=70.0, clip_length=15.0
    ) == []


def test_an_excerpt_ending_just_after_the_riser_starts_has_its_first_second_only(drop_at):
    drop_at(60.0)
    got = _by_name(processor._accents(
        Path("x.mp3"), EffectSettings(impact=1, riser=1), seek=40.0, clip_length=15.0
    ))
    assert "impact" not in got  # the hit is at 60 s, past this 40..55 s excerpt
    assert got["riser"].start == pytest.approx(14.0)  # starts 1 s before the end


def test_an_excerpt_ending_before_the_riser_gets_nothing(drop_at):
    drop_at(60.0)
    assert processor._accents(
        Path("x.mp3"), EffectSettings(impact=1, riser=1), seek=30.0, clip_length=15.0
    ) == []


def test_a_drop_early_in_the_track_trims_the_riser(drop_at):
    drop_at(2.0)
    got = _by_name(processor._accents(Path("x.mp3"), EffectSettings(riser=1)))
    assert got["riser"].start == pytest.approx(2.0 - textures.RISER_SEC)  # negative


# -- the synthesised sounds ----------------------------------------------------------------


def test_riser_climbs_in_pitch_and_level_and_has_its_stated_length():
    riser = textures.build_riser()
    assert riser.shape == (int(SR * textures.RISER_SEC), 2)

    def centroid(x):
        spectrum = np.abs(np.fft.rfft(x))
        return (spectrum * np.fft.rfftfreq(len(x), 1 / SR)).sum() / spectrum.sum()

    mono = riser[:, 0]
    early, mid, late = (mono[int(a * SR):int(b * SR)] for a, b in ((0.5, 1.5), (2.5, 3.5), (4.5, 5.5)))
    assert centroid(early) < centroid(mid) < centroid(late)
    rms = [np.sqrt((x ** 2).mean()) for x in (early, mid, late)]
    assert rms[0] < rms[1] < rms[2]
    assert np.abs(riser).max() <= 0.81
    assert abs(riser[-1, 0]) < 0.01  # ends cleanly, no click


def test_impact_is_mostly_sub_bass_and_dies_away():
    impact = textures.build_impact()
    assert impact.shape == (int(SR * textures.IMPACT_SEC), 2)
    mono = impact[:, 0]
    spectrum = np.abs(np.fft.rfft(mono[:SR])) ** 2
    freqs = np.fft.rfftfreq(SR, 1 / SR)
    assert spectrum[freqs < 150].sum() / spectrum.sum() > 0.8
    assert np.sqrt((mono[: SR // 3] ** 2).mean()) > 4 * np.sqrt((mono[-SR // 2:] ** 2).mean())
    assert np.abs(impact).max() <= 0.96


def test_accent_files_are_made_once_and_unknown_names_are_refused():
    first = textures.get_accent_path("riser")
    assert Path(first).exists()
    assert textures.get_accent_path("riser") == first
    with pytest.raises(ValueError):
        textures.get_accent_path("nope")


# -- real audio -----------------------------------------------------------------------------


def _decode(path):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype="<f4")


def _rms_db(x):
    return 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-12)


@pytest.fixture(scope="module")
def drop_track(tmp_path_factory):
    if not ffmpeg.find_binary("ffmpeg"):
        pytest.skip("ffmpeg not on PATH")
    return _track_file(tmp_path_factory.mktemp("d") / "drop.wav")  # drop at 25 s


@needs_ffmpeg
def test_riser_and_impact_are_audible_exactly_where_the_drop_is(drop_track, tmp_path):
    base = tmp_path / "base.wav"
    both = tmp_path / "both.wav"
    processor.process(drop_track, base, EffectSettings())
    processor.process(drop_track, both, EffectSettings(riser=1.0, impact=1.0))
    a, b = _decode(base), _decode(both)

    def window(x, start, end):
        return x[int(start * SR):int(end * SR)]

    # The quiet build-up just before the drop is much louder with the riser in it.
    assert _rms_db(window(b, 22.5, 24.8)) > _rms_db(window(a, 22.5, 24.8)) + 6
    # Just after the drop the hit adds energy on top of the loud section.
    assert _rms_db(window(b, 25.0, 25.6)) > _rms_db(window(a, 25.0, 25.6)) + 1.5
    # Far from both, the music is untouched (no level jump when the accents end).
    assert _rms_db(window(b, 5, 15)) == pytest.approx(_rms_db(window(a, 5, 15)), abs=0.3)
    assert _rms_db(window(b, 35, 44)) == pytest.approx(_rms_db(window(a, 35, 44)), abs=0.3)
    # And the track is no longer than before.
    assert len(b) == pytest.approx(len(a), abs=SR * 0.1)


@needs_ffmpeg
def test_the_hit_follows_the_drop_when_the_track_is_sped_up(drop_track, tmp_path):
    out = tmp_path / "fast.wav"
    processor.process(drop_track, out, EffectSettings(speed=1.25, impact=1.0))
    x = _decode(out)
    drop_out = 25.0 / 1.25  # 20 s
    # The hit's loud transient sits right at the drop (not seconds away).
    near = np.abs(x[int((drop_out - 0.05) * SR):int((drop_out + 0.5) * SR)]).max()
    before = np.abs(x[int((drop_out - 3.0) * SR):int((drop_out - 1.0) * SR)]).max()
    assert near > 3 * before


@needs_ffmpeg
def test_a_preview_over_the_drop_has_the_accents_and_one_after_it_does_not(
    drop_track, tmp_path
):
    s = EffectSettings(riser=1.0, impact=1.0)
    over = processor.render_preview(drop_track, s, 19.0, 10.0, out_dir=tmp_path / "p1")
    after = processor.render_preview(drop_track, s, 32.0, 8.0, out_dir=tmp_path / "p2")
    plain_over = processor.render_preview(
        drop_track, EffectSettings(), 19.0, 10.0, out_dir=tmp_path / "p3"
    )
    # Over the drop (it falls 6 s in): the build-up before it is far louder.
    assert _rms_db(_decode(over)[: 5 * SR]) > _rms_db(_decode(plain_over)[: 5 * SR]) + 6
    # After the drop: both accents are left out, so it is just the loud section.
    plain_after = processor.render_preview(
        drop_track, EffectSettings(), 32.0, 8.0, out_dir=tmp_path / "p4"
    )
    assert _rms_db(_decode(after)) == pytest.approx(_rms_db(_decode(plain_after)), abs=0.3)


@needs_ffmpeg
def test_epic_export_on_a_track_with_a_drop_keeps_a_sane_level(drop_track, tmp_path):
    out = tmp_path / "epic.wav"
    processor.process(drop_track, out, EffectSettings(epic=1.0))
    x = _decode(out)
    assert np.abs(x).max() <= 1.0
    assert len(x) / SR > 45 / 0.88  # longer: slower, plus the reverb tail


# -- the window ---------------------------------------------------------------------------


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("NUANCE_CONFIG_DIR", str(tmp_path / "cfg"))
    page = MagicMock()
    page.services = []
    return app.NuanceApp(page)


def test_riser_and_impact_have_sliders(window):
    assert hasattr(window, "riser_slider") and hasattr(window, "impact_slider")
    window.preset_dropdown.value = "Epic"
    window._on_preset_change(None)
    assert window.epic_slider.value == pytest.approx(0.85)


def test_find_the_drop_starts_a_riser_before_it(window, tmp_path, monkeypatch):
    window.source_path = tmp_path / "s.mp3"
    window.source_info = ffmpeg.AudioInfo(duration=200.0, sample_rate=44100, channels=2, bitrate=0)
    monkeypatch.setattr(highlights, "find_drop", lambda p: 90.0)
    asyncio.run(window._on_find_hook(None))
    assert window.preview_start == pytest.approx(90.0 - textures.RISER_SEC)


def test_find_the_drop_falls_back_to_the_loudest_passage(window, tmp_path, monkeypatch):
    window.source_path = tmp_path / "s.mp3"
    monkeypatch.setattr(highlights, "find_drop", lambda p: None)
    monkeypatch.setattr(highlights, "find_hook", lambda p: 44.0)
    asyncio.run(window._on_find_hook(None))
    assert window.preview_start == 44.0


def test_a_drop_near_the_start_does_not_give_a_negative_start(window, tmp_path, monkeypatch):
    window.source_path = tmp_path / "s.mp3"
    monkeypatch.setattr(highlights, "find_drop", lambda p: 2.0)
    asyncio.run(window._on_find_hook(None))
    assert window.preview_start == 0.0
