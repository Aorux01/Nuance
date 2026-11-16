"""Tests for output file naming."""

from pathlib import Path

from nuance.audio import naming


def test_name_uses_the_preset():
    assert naming.suggest_output_name("song", "Nightcore", "mp3") == "song_nightcore.mp3"


def test_name_flattens_awkward_preset_names():
    assert naming.suggest_output_name("s", "Slowed + Reverb", "wav") == "s_slowed_reverb.wav"
    assert naming.suggest_output_name("s", "Chopped & Screwed", "mp3") == "s_chopped_screwed.mp3"


def test_name_without_a_preset_says_nuance():
    for preset in (None, "", "Original", "__sep__"):
        assert naming.suggest_output_name("song", preset, "flac") == "song_nuance.flac"


def test_unique_path_returns_a_free_name_unchanged(tmp_path):
    path = tmp_path / "a.mp3"
    assert naming.unique_path(path) == path


def test_unique_path_skips_files_on_disk(tmp_path):
    (tmp_path / "a.mp3").write_bytes(b"x")
    (tmp_path / "a (2).mp3").write_bytes(b"x")
    assert naming.unique_path(tmp_path / "a.mp3") == tmp_path / "a (3).mp3"


def test_unique_path_skips_names_already_handed_out(tmp_path):
    taken = [tmp_path / "a.mp3", tmp_path / "a (2).mp3"]
    assert naming.unique_path(tmp_path / "a.mp3", taken) == tmp_path / "a (3).mp3"


def test_same_file_ignores_case_and_dots(tmp_path):
    a = tmp_path / "Song.mp3"
    assert naming.same_file(a, tmp_path / "sub" / ".." / "Song.mp3")
    assert not naming.same_file(a, tmp_path / "other.mp3")
    assert isinstance(naming.unique_path(Path("x.mp3")), Path)
