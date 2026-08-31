"""Tests for the ID3 tag editor.

write_tags / read_tags need a real MP3 frame to attach ID3 data to, so these
tests build a tiny silent MP3 with FFmpeg and skip if it is unavailable.
"""

import shutil
import subprocess

import pytest

from nuance.audio import metadata
from nuance.audio.metadata import Tags

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH"
)

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000100ffff03000006"
    "0005a0f0b1c50000000049454e44ae426082"
)


@pytest.fixture
def mp3(tmp_path):
    path = tmp_path / "sample.mp3"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-t", "1", "-b:a", "128k", str(path),
        ],
        check=True,
    )
    return path


def test_round_trip_text_tags(mp3):
    metadata.write_tags(mp3, Tags(title="Song", artist="Artist", album="Album"))
    got = metadata.read_tags(mp3)
    assert (got.title, got.artist, got.album) == ("Song", "Artist", "Album")


def test_missing_tags_read_as_empty(mp3):
    got = metadata.read_tags(mp3)
    assert got.title == "" and got.artist == "" and got.album == ""


def test_write_replaces_existing_values(mp3):
    metadata.write_tags(mp3, Tags(title="First"))
    metadata.write_tags(mp3, Tags(title="Second"))
    assert metadata.read_tags(mp3).title == "Second"


def test_cover_round_trips(mp3):
    metadata.write_tags(mp3, Tags(cover_data=PNG_1PX, cover_mime="image/png"))
    got = metadata.read_tags(mp3)
    assert got.cover_data == PNG_1PX
    assert got.cover_mime == "image/png"


def test_cover_left_untouched_when_data_is_none(mp3):
    metadata.write_tags(mp3, Tags(cover_data=PNG_1PX, cover_mime="image/png"))
    # A later write with no cover_data and remove_cover=False must not drop it.
    metadata.write_tags(mp3, Tags(title="New title"))
    got = metadata.read_tags(mp3)
    assert got.cover_data == PNG_1PX
    assert got.title == "New title"


def test_remove_cover_strips_existing_art(mp3):
    metadata.write_tags(mp3, Tags(cover_data=PNG_1PX, cover_mime="image/png"))
    metadata.write_tags(mp3, Tags(remove_cover=True))
    assert metadata.read_tags(mp3).cover_data is None


def test_load_cover_file_guesses_mime(tmp_path):
    p = tmp_path / "art.png"
    p.write_bytes(PNG_1PX)
    data, mime = metadata.load_cover_file(p)
    assert data == PNG_1PX
    assert mime == "image/png"


def test_load_cover_file_falls_back_for_unknown_type(tmp_path):
    p = tmp_path / "art.bin"
    p.write_bytes(b"\x00\x01\x02")
    _, mime = metadata.load_cover_file(p)
    assert mime == "image/jpeg"
