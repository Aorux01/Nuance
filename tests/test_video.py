"""Tests for video export: the remixed audio over a still picture."""

import json
import subprocess
from pathlib import Path

import pytest

from nuance.audio import ffmpeg, metadata, processor
from nuance.audio.effects import EffectSettings
from tests.conftest import needs_ffmpeg

# -- pure helpers --------------------------------------------------------------------


def test_is_video_output_by_extension():
    assert ffmpeg.is_video_output(Path("a.mp4"))
    assert ffmpeg.is_video_output(Path("a.MKV"))
    assert not ffmpeg.is_video_output(Path("a.mka"))
    assert not ffmpeg.is_video_output(Path("a.mp3"))


def test_video_containers_use_aac_audio():
    for ext in ("mp4", "mkv"):
        assert ffmpeg._encoding_args(Path(f"x.{ext}"), "256k") == [
            "-c:a", "aac", "-b:a", "256k"
        ]


def test_fit_filter_letterboxes_into_the_frame():
    f = ffmpeg._fit_filter(ffmpeg.VideoSpec(width=1280, height=720))
    assert "scale=1280:720:force_original_aspect_ratio=decrease" in f
    assert "pad=1280:720:(ow-iw)/2:(oh-ih)/2" in f


def test_video_input_uses_a_loop_for_pictures_and_a_colour_otherwise():
    with_image = ffmpeg._video_input(ffmpeg.VideoSpec(image=Path("c.png")))
    assert with_image[:2] == ["-loop", "1"] and with_image[-1] == "c.png"
    plain = ffmpeg._video_input(ffmpeg.VideoSpec())
    assert plain[:2] == ["-f", "lavfi"] and "color=c=" in plain[-1]


def test_mp4_gets_faststart_and_mkv_does_not():
    spec = ffmpeg.VideoSpec()
    assert "+faststart" in ffmpeg._video_args(Path("a.mp4"), spec)
    assert "+faststart" not in ffmpeg._video_args(Path("a.mkv"), spec)


def test_scaled_progress_fills_its_band():
    seen = []
    cb = processor._scaled(seen.append, 0.9, 1.0)
    cb(0.0)
    cb(0.5)
    cb(1.0)
    assert seen == [pytest.approx(0.9), pytest.approx(0.95), pytest.approx(1.0)]
    assert processor._scaled(None, 0, 1) is None


def test_cover_file_is_written_only_when_there_is_one(tmp_path):
    assert processor._write_cover_file(metadata.Tags()) is None
    assert processor._write_cover_file(
        metadata.Tags(cover_data=b"x", remove_cover=True)
    ) is None
    path = processor._write_cover_file(
        metadata.Tags(cover_data=b"png-bytes", cover_mime="image/png")
    )
    try:
        assert path.suffix == ".png" and path.read_bytes() == b"png-bytes"
    finally:
        path.unlink()


# -- end to end ------------------------------------------------------------------------


def _streams(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
         "-show_format", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


@needs_ffmpeg
@pytest.mark.parametrize("ext", ["mp4", "mkv"])
def test_video_has_both_streams_and_matches_the_audio_length(tmp_path, make_tone, ext):
    src = make_tone(tmp_path / "s.wav", seconds=4)
    dest = tmp_path / f"out.{ext}"
    processor.process(src, dest, EffectSettings(speed=0.8))

    info = _streams(dest)
    kinds = {s["codec_type"]: s for s in info["streams"]}
    assert kinds["video"]["codec_name"] == "h264"
    assert kinds["audio"]["codec_name"] == "aac"
    assert (kinds["video"]["width"], kinds["video"]["height"]) == (1280, 720)
    # 4 s slowed to 0.8x is 5 s, and the picture must not run on past it.
    assert float(info["format"]["duration"]) == pytest.approx(5.0, abs=0.3)


@needs_ffmpeg
def test_video_uses_the_cover_art_and_fits_it_to_the_frame(tmp_path, make_tone, cover_jpg):
    src = make_tone(tmp_path / "s.wav", seconds=2)
    tags = metadata.Tags(title="T", cover_data=cover_jpg.read_bytes(), cover_mime="image/jpeg")
    dest = tmp_path / "out.mp4"
    processor.process(src, dest, EffectSettings(), tags)

    video = next(s for s in _streams(dest)["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1280, 720)  # square cover, letterboxed


@needs_ffmpeg
def test_video_picture_override_wins_over_cover_art(tmp_path, make_tone, cover_jpg):
    src = make_tone(tmp_path / "s.wav", seconds=2)
    dest = tmp_path / "out.mp4"
    spec = ffmpeg.VideoSpec(image=cover_jpg, width=640, height=360)
    processor.process(src, dest, EffectSettings(), metadata.Tags(), video=spec)
    video = next(s for s in _streams(dest)["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (640, 360)


@needs_ffmpeg
def test_video_carries_tags(tmp_path, make_tone):
    src = make_tone(tmp_path / "s.wav", seconds=2)
    dest = tmp_path / "out.mp4"
    processor.process(src, dest, EffectSettings(), metadata.Tags(title="Hi", artist="Me"))
    tags = _streams(dest)["format"]["tags"]
    assert tags["title"] == "Hi"
    assert tags["artist"] == "Me"


@needs_ffmpeg
def test_video_progress_never_goes_backwards_and_ends_at_one(tmp_path, make_tone):
    src = make_tone(tmp_path / "s.wav", seconds=4)
    seen = []
    processor.process(src, tmp_path / "o.mp4", EffectSettings(), progress_callback=seen.append)
    assert seen[-1] == pytest.approx(1.0)
    assert all(b >= a - 1e-9 for a, b in zip(seen, seen[1:], strict=False))
    assert all(0.0 <= r <= 1.0 for r in seen)


@needs_ffmpeg
def test_video_export_leaves_no_temporary_files(tmp_path, make_tone, cover_jpg):
    src = make_tone(tmp_path / "s.wav", seconds=2)
    tags = metadata.Tags(cover_data=cover_jpg.read_bytes(), cover_mime="image/jpeg")
    processor.process(src, tmp_path / "o.mp4", EffectSettings(reverb=0.3), tags)
    assert sorted(p.name for p in tmp_path.iterdir() if "nuance" in p.name) == []


@needs_ffmpeg
def test_cancelled_video_export_keeps_the_old_file_and_cleans_up(tmp_path, make_tone):
    src = make_tone(tmp_path / "s.wav", seconds=3)
    dest = tmp_path / "keep.mp4"
    dest.write_bytes(b"precious")
    with pytest.raises(ffmpeg.FFmpegCancelled):
        processor.process(
            src, dest, EffectSettings(speed=0.6), on_process_start=lambda p: p.terminate()
        )
    assert dest.read_bytes() == b"precious"
    assert [p.name for p in tmp_path.iterdir() if "nuance" in p.name] == []


@needs_ffmpeg
def test_failed_video_mux_cleans_up_the_audio_intermediate(tmp_path, make_tone):
    src = make_tone(tmp_path / "s.wav", seconds=2)
    missing = tmp_path / "no-such-picture.png"
    with pytest.raises(ffmpeg.FFmpegError):
        processor.process(
            src, tmp_path / "o.mp4", EffectSettings(),
            video=ffmpeg.VideoSpec(image=missing),
        )
    assert [p.name for p in tmp_path.iterdir() if "nuance" in p.name] == []
    assert not (tmp_path / "o.mp4").exists()
