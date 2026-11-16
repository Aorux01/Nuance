"""End-to-end regressions for export quality, using the real FFmpeg.

Each test guards a bug that is invisible in the filter strings and only shows in
the rendered audio: a wrong sample rate, a level drop, a damaged output file.
"""

import shutil
import subprocess

import numpy as np
import pytest

from nuance.audio import effects, ffmpeg, processor, textures
from nuance.audio.effects import EffectSettings

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)


@pytest.fixture(scope="module")
def noise_source(tmp_path_factory):
    """Six seconds of stereo pink noise: broadband, so levels are meaningful."""
    path = tmp_path_factory.mktemp("src") / "noise.wav"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i",
            "anoisesrc=color=pink:amplitude=0.3:duration=6:sample_rate=44100",
            "-ac", "2", str(path),
        ],
        check=True,
    )
    return path


def _samples(path) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "2",
         "-ar", "44100", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype="<f4").reshape(-1, 2)


def _rms_db(x: np.ndarray) -> float:
    return 20 * np.log10(np.sqrt(np.mean(x**2)) + 1e-12)


@pytest.mark.parametrize("ext", ["wav", "flac"])
def test_loudnorm_keeps_the_source_sample_rate(noise_source, tmp_path, ext):
    """loudnorm works at 192 kHz; lossless exports must not inherit that."""
    dest = tmp_path / f"out.{ext}"
    processor.process(noise_source, dest, EffectSettings(loudness_lufs=-9.0))
    assert ffmpeg.probe(dest).sample_rate == 44100


def test_textures_do_not_make_the_track_quieter(noise_source, tmp_path):
    plain = tmp_path / "plain.wav"
    textured = tmp_path / "textured.wav"
    processor.process(noise_source, plain, EffectSettings())
    processor.process(noise_source, textured, EffectSettings(vinyl=0.4))

    drop = _rms_db(_samples(plain)[44100:]) - _rms_db(_samples(textured)[44100:])
    # amix used to cost about 6 dB; the vinyl itself may add a touch of level.
    assert drop < 1.0


def test_reverb_rings_out_past_the_end_and_stays_at_a_sane_level(
    noise_source, tmp_path
):
    dry = tmp_path / "dry.wav"
    wet = tmp_path / "wet.wav"
    processor.process(noise_source, dry, EffectSettings())
    processor.process(noise_source, wet, EffectSettings(reverb=0.6))

    tail = effects.reverb_tail_seconds(EffectSettings(reverb=0.6))
    assert tail > 1.0
    assert ffmpeg.probe(wet).duration == pytest.approx(
        ffmpeg.probe(dry).duration + tail, abs=0.1
    )
    # The extra time really is the reverb ringing: quiet but not silent, and it
    # decays towards the end.
    y = _samples(wet)
    ring = y[int(6.2 * 44100) : int(7.0 * 44100)]
    late = y[int(7.7 * 44100) :]
    assert _rms_db(ring) > -75
    assert _rms_db(late) < _rms_db(ring)
    assert np.abs(y).max() <= 1.0
    # A convolution reverb adds energy but must stay in the same ballpark
    # (compared over the span where the track itself plays).
    span = slice(44100, 6 * 44100)
    assert abs(_rms_db(y[span]) - _rms_db(_samples(dry)[span])) < 4.0


def test_reverb_is_aligned_with_the_dry_signal(tmp_path):
    """A click must come out where it went in (not smeared by IR latency)."""
    sr = 44100
    click = np.zeros((sr * 3, 2))
    click[sr] = [0.5, 0.5]
    src = tmp_path / "click.wav"
    import wave

    with wave.open(str(src), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((click * 32767).astype("<i2").tobytes())

    out = tmp_path / "out.wav"
    processor.process(src, out, EffectSettings(reverb=0.4))
    y = _samples(out)
    peak = int(np.argmax(np.abs(y[:, 0])))
    assert abs(peak - sr) < 0.02 * sr


def test_export_writes_through_a_partial_file(noise_source, tmp_path):
    dest = tmp_path / "out.mp3"
    processor.process(noise_source, dest, EffectSettings(speed=1.2))
    assert dest.exists()
    assert not list(tmp_path.glob("*.nuance-part.*"))


def test_cancelled_export_leaves_an_existing_file_untouched(noise_source, tmp_path):
    dest = tmp_path / "keep.mp3"
    dest.write_bytes(b"precious")

    def kill(proc):
        proc.terminate()

    with pytest.raises(ffmpeg.FFmpegCancelled):
        processor.process(
            noise_source, dest, EffectSettings(speed=0.6), on_process_start=kill
        )
    assert dest.read_bytes() == b"precious"
    assert not list(tmp_path.glob("*.nuance-part.*"))


def test_failed_export_leaves_an_existing_file_untouched(noise_source, tmp_path):
    dest = tmp_path / "keep.wav"
    dest.write_bytes(b"precious")
    with pytest.raises(ffmpeg.FFmpegError):
        processor.process(tmp_path / "missing.wav", dest, EffectSettings())
    assert dest.read_bytes() == b"precious"


def test_old_previews_are_pruned(noise_source, tmp_path):
    import os
    import time

    out_dir = tmp_path / "prev"
    out_dir.mkdir()
    stale = out_dir / "preview_stale.mp3"
    stale.write_bytes(b"x")
    old = time.time() - processor.PREVIEW_MAX_AGE_SEC - 60
    os.utime(stale, (old, old))
    fresh = out_dir / "preview_fresh.mp3"
    fresh.write_bytes(b"x")

    processor.render_preview(
        noise_source, EffectSettings(), 0.0, 5.0, out_dir=out_dir
    )
    assert not stale.exists()
    assert fresh.exists()


# -- the synthetic impulse response ---------------------------------------------


def test_reverb_ir_is_stereo_unit_energy_and_decays():
    ir = textures.build_reverb_ir()
    assert ir.shape[1] == 2
    energy = np.sqrt((ir**2).sum(axis=0))
    assert energy == pytest.approx([1.0, 1.0], rel=1e-6)

    sr = 44100
    early = np.abs(ir[: sr // 2]).mean()
    late = np.abs(ir[-sr // 2 :]).mean()
    assert late < early / 10


def test_reverb_ir_channels_differ():
    ir = textures.build_reverb_ir()
    assert not np.allclose(ir[:, 0], ir[:, 1])


def test_reverb_ir_file_is_created_and_readable():
    path = textures.get_reverb_ir_path()
    info = ffmpeg.probe(__import__("pathlib").Path(path))
    assert info.channels == 2
    assert info.duration > 2.0
