"""Shared fixtures."""

import shutil
import subprocess
from pathlib import Path

import pytest

HAS_FFMPEG = all(shutil.which(tool) for tool in ("ffmpeg", "ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")


@pytest.fixture
def make_tone():
    """Factory: make_tone(path, seconds=3, freq=440) writes a stereo tone file."""

    def make(path: Path, seconds: float = 3.0, freq: int = 440) -> Path:
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
                "-ac", "2", str(path),
            ],
            check=True,
        )
        return path

    return make


@pytest.fixture
def cover_jpg(tmp_path) -> Path:
    """A small square test picture."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    path = tmp_path / "cover.jpg"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=s=500x500:d=1", "-frames:v", "1", str(path),
        ],
        check=True,
    )
    return path
