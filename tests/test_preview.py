"""Tests for processor.render_preview and its caching key."""

import shutil
import subprocess

import pytest

from nuance.audio import ffmpeg, processor
from nuance.audio.effects import EffectSettings, get_preset

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "src.mp3"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=330:duration=40",
            "-b:a", "192k", str(path),
        ],
        check=True,
    )
    return path


def test_preview_key_stable_and_sensitive(tmp_path):
    a = processor._preview_key(tmp_path / "x.mp3", 10.0, 15.0, EffectSettings())
    b = processor._preview_key(tmp_path / "x.mp3", 10.0, 15.0, EffectSettings())
    assert a == b

    c = processor._preview_key(tmp_path / "x.mp3", 12.0, 15.0, EffectSettings())
    assert c != a

    d = processor._preview_key(
        tmp_path / "x.mp3", 10.0, 15.0, EffectSettings(speed=1.1)
    )
    assert d != a


def test_render_preview_makes_a_clip(source, tmp_path):
    out = processor.render_preview(
        source, EffectSettings(), start_sec=5.0, length_sec=10.0,
        out_dir=tmp_path / "prev",
    )
    assert out.exists()
    assert ffmpeg.probe(out).duration == pytest.approx(10.0, abs=0.4)


def test_render_preview_applies_speed(source, tmp_path):
    out = processor.render_preview(
        source, EffectSettings(speed=2.0), start_sec=0.0, length_sec=10.0,
        out_dir=tmp_path / "prev",
    )
    assert ffmpeg.probe(out).duration == pytest.approx(5.0, abs=0.4)


def test_render_preview_reuses_file_on_repeat(source, tmp_path):
    d = tmp_path / "prev"
    first = processor.render_preview(
        source, EffectSettings(), start_sec=3.0, length_sec=8.0, out_dir=d
    )
    mtime = first.stat().st_mtime_ns
    second = processor.render_preview(
        source, EffectSettings(), start_sec=3.0, length_sec=8.0, out_dir=d
    )
    assert second == first
    assert second.stat().st_mtime_ns == mtime  # not re-rendered


def test_render_preview_new_file_when_settings_change(source, tmp_path):
    d = tmp_path / "prev"
    a = processor.render_preview(
        source, EffectSettings(), start_sec=0.0, length_sec=8.0, out_dir=d
    )
    b = processor.render_preview(
        source, EffectSettings(bass_boost_db=6.0), start_sec=0.0, length_sec=8.0,
        out_dir=d,
    )
    assert a != b
    assert a.exists() and b.exists()


def test_render_preview_clamps_length(source, tmp_path):
    d = tmp_path / "prev"
    out = processor.render_preview(
        source, EffectSettings(), start_sec=0.0, length_sec=999.0, out_dir=d
    )
    assert ffmpeg.probe(out).duration == pytest.approx(
        processor.PREVIEW_MAX_SEC, abs=0.5
    )


def test_render_preview_with_textures_and_master(source, tmp_path):
    """A full-featured preset must still render a valid short clip."""
    s = get_preset("Energy Boost")
    s.vinyl = 0.3
    out = processor.render_preview(
        source, s, start_sec=8.0, length_sec=10.0, out_dir=tmp_path / "prev"
    )
    assert out.exists()
    assert ffmpeg.probe(out).duration > 0
