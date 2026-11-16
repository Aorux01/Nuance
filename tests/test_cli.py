"""Tests for the command line interface."""

from dataclasses import fields

import pytest

from nuance import cli
from nuance.audio import ffmpeg, metadata, userpresets
from nuance.audio.effects import EffectSettings
from tests.conftest import needs_ffmpeg


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("NUANCE_CONFIG_DIR", str(tmp_path / "cfg"))


def parse(*argv):
    return cli.build_parser().parse_args(["remix", "x.mp3", *argv])


# -- flags and settings ---------------------------------------------------------------


def test_every_effect_setting_has_a_flag():
    args = parse()
    for f in fields(EffectSettings):
        assert hasattr(args, f.name), f.name
        assert getattr(args, f.name) is None  # None = "not given"


def test_flag_values_are_typed():
    args = parse("--speed", "1.4", "--loop-count", "3", "--keep-pitch", "--no-reverse")
    assert args.speed == 1.4
    assert args.loop_count == 3 and isinstance(args.loop_count, int)
    assert args.keep_pitch is True
    assert args.reverse is False


def test_flags_override_the_preset():
    s = cli.settings_from_args(parse("--preset", "nightcore", "--speed", "1.5"))
    assert s.speed == 1.5
    assert s.pitch_semitones == 2.0  # still from the preset


def test_preset_lookup_ignores_case_and_punctuation_is_exact():
    assert cli.find_preset("SLOWED + REVERB").reverb > 0
    assert cli.find_preset("lo-fi").vinyl > 0


def test_user_presets_are_found(tmp_path):
    userpresets.save("Mine", EffectSettings(speed=1.11))
    assert cli.find_preset("mine").speed == 1.11


def test_unknown_preset_lists_the_choices():
    with pytest.raises(cli.CliError, match="Nightcore"):
        cli.find_preset("nope")


def test_no_preset_means_defaults():
    assert cli.settings_from_args(parse()) == EffectSettings()


@pytest.mark.parametrize("flags", [["--speed", "0"], ["--speed", "-1"], ["--loop-count", "0"]])
def test_invalid_values_are_rejected(flags):
    with pytest.raises(cli.CliError):
        cli.settings_from_args(parse(*flags))


def test_describe_lists_only_changes():
    assert cli.describe(EffectSettings()) == "no changes"
    text = cli.describe(EffectSettings(speed=1.25, keep_pitch=True))
    assert "speed=1.25" in text and "keep_pitch=on" in text
    assert "reverb" not in text


def test_field_docs_are_extracted():
    docs = cli._field_docs()
    assert "playback speed" in docs["speed"]
    assert set(docs) == {f.name for f in fields(EffectSettings)}


# -- inputs and outputs -------------------------------------------------------------


def test_expand_sources_globs(tmp_path):
    for name in ("a.wav", "b.wav", "c.txt"):
        (tmp_path / name).write_bytes(b"x")
    found = cli.expand_sources([str(tmp_path / "*.wav")])
    assert [p.name for p in found] == ["a.wav", "b.wav"]


def test_expand_sources_rejects_missing_files(tmp_path):
    with pytest.raises(cli.CliError):
        cli.expand_sources([str(tmp_path / "missing.mp3")])
    with pytest.raises(cli.CliError):
        cli.expand_sources([str(tmp_path / "*.nothing")])


def test_expand_sources_drops_duplicates(tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(b"x")
    assert len(cli.expand_sources([str(f), str(tmp_path / "*.wav")])) == 1


def test_format_comes_from_output_or_flag_or_defaults_to_mp3():
    assert cli.resolve_format(parse()) == "mp3"
    assert cli.resolve_format(parse("-f", "flac")) == "flac"
    assert cli.resolve_format(parse("-o", "out.wav")) == "wav"
    assert cli.resolve_format(parse("-o", "out.mp4")) == "mp4"


def test_format_conflicts_and_unknowns_are_errors():
    with pytest.raises(cli.CliError):
        cli.resolve_format(parse("-o", "out.wav", "-f", "flac"))
    with pytest.raises(cli.CliError):
        cli.resolve_format(parse("-o", "out.xyz"))


def test_output_needs_exactly_one_input(tmp_path):
    args = parse("-o", str(tmp_path / "o.mp3"))
    with pytest.raises(cli.CliError):
        cli.build_items(args, [tmp_path / "a.mp3", tmp_path / "b.mp3"], "mp3")


def test_output_refuses_to_overwrite_input_or_existing_file(tmp_path):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    with pytest.raises(cli.CliError, match="overwrite the input"):
        cli.build_items(parse("-o", str(src)), [src], "mp3")

    existing = tmp_path / "o.mp3"
    existing.write_bytes(b"x")
    with pytest.raises(cli.CliError, match="already exists"):
        cli.build_items(parse("-o", str(existing)), [src], "mp3")
    # ...unless asked to.
    items = cli.build_items(parse("-o", str(existing), "--overwrite"), [src], "mp3")
    assert items[0].destination == existing


def test_out_dir_is_created(tmp_path):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    out = tmp_path / "new" / "dir"
    items = cli.build_items(parse("-d", str(out), "-p", "phonk"), [src], "mp3")
    assert out.is_dir()
    assert items[0].destination == out / "a_phonk.mp3"


def test_image_only_for_video_formats(tmp_path):
    img = tmp_path / "i.png"
    img.write_bytes(b"x")
    assert cli.video_spec(parse(), "mp3") is None
    with pytest.raises(cli.CliError):
        cli.video_spec(parse("--image", str(img)), "mp3")
    assert cli.video_spec(parse("--image", str(img)), "mp4").image == img
    assert cli.video_spec(parse(), "mkv").image is None
    with pytest.raises(cli.CliError):
        cli.video_spec(parse("--image", str(tmp_path / "gone.png")), "mp4")


# -- the commands -----------------------------------------------------------------------


def test_presets_command_lists_builtin_and_user_presets(capsys):
    userpresets.save("Mine", EffectSettings(speed=1.11))
    assert cli.main(["presets"]) == 0
    out = capsys.readouterr().out
    assert "Nightcore" in out and "speed=1.3" in out
    assert "Mine" in out and "(yours)" in out


def test_no_command_prints_help_and_fails(capsys):
    assert cli.main([]) == 2
    assert "remix" in capsys.readouterr().out


def test_usage_errors_are_one_line_with_exit_code_2(capsys, tmp_path):
    code = cli.main(["remix", str(tmp_path / "missing.mp3")])
    err = capsys.readouterr().err
    assert code == 2
    assert err.startswith("nuance: error:")
    assert "Traceback" not in err


def test_missing_ffmpeg_is_a_clean_error(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(ffmpeg, "find_binary", lambda name: None)
    f = tmp_path / "a.wav"
    f.write_bytes(b"x")
    assert cli.main(["remix", str(f)]) == 2
    assert "FFmpeg" in capsys.readouterr().err


@needs_ffmpeg
def test_remix_one_file_end_to_end(tmp_path, make_tone, capsys):
    src = make_tone(tmp_path / "song.wav", seconds=2)
    code = cli.main(["remix", str(src), "-p", "sped up", "-o", str(tmp_path / "o.mp3")])
    assert code == 0
    assert ffmpeg.probe(tmp_path / "o.mp3").duration == pytest.approx(2 / 1.25, abs=0.25)


@needs_ffmpeg
def test_remix_several_files_into_a_folder(tmp_path, make_tone):
    a = make_tone(tmp_path / "a.wav", seconds=1.5)
    b = make_tone(tmp_path / "b.wav", seconds=1.5)
    out = tmp_path / "out"
    code = cli.main(["remix", str(a), str(b), "-p", "phonk", "-d", str(out), "-f", "flac", "-q"])
    assert code == 0
    assert sorted(p.name for p in out.iterdir()) == ["a_phonk.flac", "b_phonk.flac"]


@needs_ffmpeg
def test_flags_without_a_preset_apply(tmp_path, make_tone):
    src = make_tone(tmp_path / "s.wav", seconds=2)
    out = tmp_path / "o.wav"
    assert cli.main(["remix", str(src), "--speed", "2", "-o", str(out), "-q"]) == 0
    assert ffmpeg.probe(out).duration == pytest.approx(1.0, abs=0.25)


@needs_ffmpeg
def test_tag_flags_are_written(tmp_path, make_tone):
    src = make_tone(tmp_path / "s.mp3")
    out = tmp_path / "o.mp3"
    cli.main(["remix", str(src), "-o", str(out), "--title", "T", "--artist", "A", "-q"])
    tags = metadata.read_tags(out)
    assert (tags.title, tags.artist) == ("T", "A")


@needs_ffmpeg
def test_failure_gives_exit_code_1_and_names_the_file(tmp_path, capsys):
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"not audio")
    code = cli.main(["remix", str(bad), "-d", str(tmp_path / "o")])
    assert code == 1
    assert "FAIL  bad.mp3" in capsys.readouterr().err


@needs_ffmpeg
def test_existing_outputs_are_not_clobbered(tmp_path, make_tone):
    src = make_tone(tmp_path / "s.wav", seconds=1.5)
    cli.main(["remix", str(src), "-p", "phonk", "-q"])
    cli.main(["remix", str(src), "-p", "phonk", "-q"])
    names = sorted(p.name for p in tmp_path.glob("s_phonk*"))
    assert names == ["s_phonk (2).mp3", "s_phonk.mp3"]
