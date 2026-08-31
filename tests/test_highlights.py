"""Tests for the drop / hook detector.

The detector shells out to ffmpeg to decode, so these build real audio files
and skip when ffmpeg is missing.
"""

import shutil
import subprocess

import pytest

from nuance.audio import highlights

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)


def _make_track(path, duration, loud_from, loud_to, loud_gain=4.0, quiet_gain=0.06):
    """A sine track that is quiet everywhere except a loud window."""
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"sine=frequency=180:duration={duration}",
            "-af",
            f"volume={quiet_gain},"
            f"volume=enable='between(t,{loud_from},{loud_to})':volume={loud_gain / quiet_gain}",
            "-b:a", "192k", str(path),
        ],
        check=True,
    )
    return path


def test_finds_the_loud_window(tmp_path):
    track = _make_track(tmp_path / "t.mp3", duration=60, loud_from=30, loud_to=42)
    start = highlights.find_hook(track)
    assert 28.0 <= start <= 42.0


def test_short_track_falls_back_to_one_third(tmp_path):
    track = _make_track(tmp_path / "s.mp3", duration=20, loud_from=5, loud_to=10)
    start = highlights.find_hook(track, min_track_sec=30.0)
    assert start == pytest.approx(20 / 3, abs=0.5)


def test_missing_file_returns_zero(tmp_path):
    assert highlights.find_hook(tmp_path / "nope.mp3") == 0.0


def test_start_never_runs_off_the_end(tmp_path):
    # Loud right at the end; the detector must still leave room after the start.
    track = _make_track(tmp_path / "e.mp3", duration=50, loud_from=44, loud_to=50)
    info_dur = 50.0
    start = highlights.find_hook(track)
    assert start <= info_dur - 5.0 + 1e-6


def test_result_is_a_plain_float(tmp_path):
    track = _make_track(tmp_path / "f.mp3", duration=45, loud_from=20, loud_to=30)
    start = highlights.find_hook(track)
    assert type(start) is float
