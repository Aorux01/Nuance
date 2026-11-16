"""Tests for the waveform peak extraction and SVG rendering."""

import base64
import shutil
import subprocess

import numpy as np
import pytest

from nuance.audio import waveform

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)


@pytest.fixture
def loud_quiet_track(tmp_path):
    """20s: quiet 0-8s, loud 8-16s, quiet 16-20s."""
    path = tmp_path / "lq.mp3"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=200:duration=20",
            "-af",
            "volume=0.05,volume=enable='between(t,8,16)':volume=16",
            "-b:a", "192k", str(path),
        ],
        check=True,
    )
    return path


def test_peaks_length_matches_bars(loud_quiet_track):
    p = waveform.peaks(loud_quiet_track, bars=100)
    assert len(p) == 100


def test_peaks_normalised_to_unit_range(loud_quiet_track):
    p = waveform.peaks(loud_quiet_track, bars=120)
    assert p.min() >= 0.0
    assert p.max() == pytest.approx(1.0, abs=1e-6)


def test_peaks_track_the_loud_section(loud_quiet_track):
    p = waveform.peaks(loud_quiet_track, bars=20)
    # bars 0-7 quiet, 8-15 loud, 16-19 quiet
    assert p[2] < 0.2
    assert p[10] > 0.8
    assert p[18] < 0.2


def test_peaks_missing_file_is_flat(tmp_path):
    p = waveform.peaks(tmp_path / "nope.mp3", bars=64)
    assert len(p) == 64
    assert np.all(p == 0.0)


def test_svg_data_uri_is_valid_svg(loud_quiet_track):
    uri = waveform.svg_data_uri(loud_quiet_track, bars=50)
    assert uri.startswith("data:image/svg+xml;base64,")
    svg = base64.b64decode(uri.split(",", 1)[1]).decode()
    assert svg.startswith("<svg")
    assert svg.count("<rect") == 51  # background + 50 bars
    assert "</svg>" in svg


def test_svg_from_peaks_scales_bar_heights():
    peaks = np.array([0.0, 0.5, 1.0])
    svg = base64.b64decode(
        waveform.svg_data_uri_from_peaks(peaks, width=300, height=100)
        .split(",", 1)[1]
    ).decode()
    # Three bars plus the background rect.
    assert svg.count("<rect") == 4
