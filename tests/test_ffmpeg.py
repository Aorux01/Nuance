"""Tests for the FFmpeg wrapper.

The end-to-end run_filters / probe tests build a short signal with FFmpeg and
skip when it is not installed. The parsing helpers need nothing.
"""

import shutil
import subprocess

import pytest

from nuance.audio import ffmpeg
from nuance.audio.effects import EffectSettings, build_filters

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def test_to_float_tolerates_bad_input():
    assert ffmpeg._to_float(None, 1.0) == 1.0
    assert ffmpeg._to_float("not a number", 2.0) == 2.0
    assert ffmpeg._to_float("3.5", 0.0) == 3.5
    assert ffmpeg._to_float("", -1.0) == -1.0


def test_find_binary_missing_returns_none():
    assert ffmpeg.find_binary("definitely-not-a-real-binary-xyz") is None


def test_ensure_available_raises_for_missing(monkeypatch):
    monkeypatch.setattr(ffmpeg, "find_binary", lambda name: None)
    with pytest.raises(ffmpeg.FFmpegNotFoundError):
        ffmpeg.ensure_available()


def test_ensure_available_passes_when_present(monkeypatch):
    monkeypatch.setattr(ffmpeg, "find_binary", lambda name: f"/usr/bin/{name}")
    ffmpeg.ensure_available()  # must not raise


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")
class TestEndToEnd:
    @pytest.fixture
    def source(self, tmp_path):
        path = tmp_path / "src.mp3"
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                "-f", "lavfi", "-i", "sine=frequency=660:duration=2",
                "-filter_complex", "[0][1]amerge=inputs=2",
                "-b:a", "128k", str(path),
            ],
            check=True,
        )
        return path

    def test_probe_reads_basic_info(self, source):
        info = ffmpeg.probe(source)
        assert info.duration == pytest.approx(2.0, abs=0.2)
        assert info.sample_rate == 44100
        assert info.channels == 2

    def test_run_filters_speed_up_produces_shorter_file(self, source, tmp_path):
        dest = tmp_path / "out.mp3"
        filters = build_filters(EffectSettings(speed=2.0), 44100)
        ffmpeg.run_filters(source, dest, filters)
        assert dest.exists()
        assert ffmpeg.probe(dest).duration == pytest.approx(1.0, abs=0.2)

    def test_run_filters_reports_progress(self, source, tmp_path):
        dest = tmp_path / "out.mp3"
        seen = []
        ffmpeg.run_filters(
            source, dest,
            build_filters(EffectSettings(speed=0.9), 44100),
            progress_callback=seen.append,
            total_duration=2.3,
        )
        assert seen
        assert seen[-1] == 1.0
        assert all(0.0 <= r <= 1.0 for r in seen)

    def test_run_filters_raises_on_bad_filter(self, source, tmp_path):
        dest = tmp_path / "out.mp3"
        with pytest.raises(ffmpeg.FFmpegError):
            ffmpeg.run_filters(source, dest, ["not_a_real_filter=1"])

    def test_run_filters_trims_input(self, source, tmp_path):
        """input_seek / input_duration cut the source before filtering."""
        dest = tmp_path / "clip.mp3"
        ffmpeg.run_filters(
            source, dest, [], input_seek=0.5, input_duration=1.0
        )
        assert ffmpeg.probe(dest).duration == pytest.approx(1.0, abs=0.2)

    def test_decode_mono_pcm_returns_float_samples(self, source):
        import numpy as np

        raw = ffmpeg.decode_mono_pcm(source, sample_rate=8000)
        samples = np.frombuffer(raw, dtype="<f4")
        # ~2 seconds at 8 kHz.
        assert 14000 < samples.size < 18000
        assert samples.dtype == np.float32
