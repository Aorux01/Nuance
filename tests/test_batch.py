"""Tests for batch planning and the batch runner."""

import pytest

from nuance.audio import batch, ffmpeg, metadata
from nuance.audio.effects import EffectSettings
from tests.conftest import needs_ffmpeg

# -- plan_items (no FFmpeg) ---------------------------------------------------------


def test_plan_puts_results_in_the_out_dir(tmp_path):
    out = tmp_path / "out"
    items = batch.plan_items([tmp_path / "a.mp3", tmp_path / "b.mp3"], out, "Nightcore", "mp3")
    assert [i.destination for i in items] == [out / "a_nightcore.mp3", out / "b_nightcore.mp3"]
    assert all(i.status == batch.PENDING for i in items)


def test_plan_defaults_to_next_to_each_source(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    items = batch.plan_items([tmp_path / "x" / "a.mp3", tmp_path / "y" / "b.mp3"], None, None, "wav")
    assert items[0].destination == tmp_path / "x" / "a_nuance.wav"
    assert items[1].destination == tmp_path / "y" / "b_nuance.wav"


def test_plan_gives_same_named_sources_different_outputs(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    sources = [tmp_path / "x" / "song.mp3", tmp_path / "y" / "song.mp3"]
    items = batch.plan_items(sources, tmp_path / "out", "Phonk", "mp3")
    assert items[0].destination != items[1].destination
    assert items[1].destination.name == "song_phonk (2).mp3"


def test_plan_does_not_overwrite_existing_files_by_default(tmp_path):
    (tmp_path / "a_nuance.mp3").write_bytes(b"old")
    items = batch.plan_items([tmp_path / "a.mp3"], None, None, "mp3")
    assert items[0].destination == tmp_path / "a_nuance (2).mp3"


def test_plan_overwrite_reuses_an_old_output(tmp_path):
    (tmp_path / "a_nuance.mp3").write_bytes(b"old")
    items = batch.plan_items([tmp_path / "a.mp3"], None, None, "mp3", overwrite=True)
    assert items[0].destination == tmp_path / "a_nuance.mp3"


def test_plan_never_lands_on_a_source_even_with_overwrite(tmp_path):
    # The source already has the name the output would get.
    source = tmp_path / "a_nuance.mp3"
    source.write_bytes(b"src")
    other = tmp_path / "a.mp3"
    items = batch.plan_items([other, source], None, None, "mp3", overwrite=True)
    assert source not in [i.destination for i in items]
    assert items[0].destination != source


# -- BatchRunner (FFmpeg) --------------------------------------------------------------


@pytest.fixture
def three_files(tmp_path, make_tone):
    return [make_tone(tmp_path / f"t{i}.wav", seconds=1.5, freq=300 + 100 * i) for i in range(3)]


@needs_ffmpeg
def test_runner_processes_every_file(tmp_path, three_files):
    items = batch.plan_items(three_files, tmp_path / "out", "Sped Up", "mp3")
    (tmp_path / "out").mkdir()
    runner = batch.BatchRunner(items, EffectSettings(speed=1.5))
    runner.run()

    assert all(i.status == batch.DONE for i in items)
    assert runner.counts()[batch.DONE] == 3
    for item in items:
        assert item.destination.exists()
        assert ffmpeg.probe(item.destination).duration == pytest.approx(1.0, abs=0.25)


@needs_ffmpeg
def test_runner_reports_status_changes_and_progress(tmp_path, three_files):
    items = batch.plan_items(three_files[:2], tmp_path, "Phonk", "wav")
    statuses, progress = [], []
    batch.BatchRunner(items, EffectSettings()).run(
        on_progress=lambda i, r: progress.append((i, r)),
        on_status=lambda i, item: statuses.append((i, item.status)),
    )
    assert statuses == [
        (0, batch.RUNNING), (0, batch.DONE), (1, batch.RUNNING), (1, batch.DONE),
    ]
    assert {i for i, _ in progress} == {0, 1}
    assert all(0.0 <= r <= 1.0 for _, r in progress)


@needs_ffmpeg
def test_one_bad_file_does_not_stop_the_rest(tmp_path, three_files):
    broken = tmp_path / "broken.mp3"
    broken.write_bytes(b"this is not audio")
    sources = [three_files[0], broken, three_files[1]]
    items = batch.plan_items(sources, tmp_path / "out", None, "mp3")
    (tmp_path / "out").mkdir()
    runner = batch.BatchRunner(items, EffectSettings())
    runner.run()

    assert [i.status for i in items] == [batch.DONE, batch.FAILED, batch.DONE]
    assert items[1].error  # something readable, not empty
    assert "\n" not in items[1].error
    assert not items[1].destination.exists()


@needs_ffmpeg
def test_cancel_skips_the_remaining_files(tmp_path, three_files):
    items = batch.plan_items(three_files, tmp_path / "out", None, "mp3")
    (tmp_path / "out").mkdir()
    runner = batch.BatchRunner(items, EffectSettings())

    def stop_after_first(index, item):
        if index == 0 and item.status == batch.DONE:
            runner.cancel()

    runner.run(on_status=stop_after_first)
    assert [i.status for i in items] == [batch.DONE, batch.CANCELLED, batch.CANCELLED]
    assert runner.cancelled
    assert not items[1].destination.exists()


@needs_ffmpeg
def test_cancel_before_the_process_starts_still_stops_it(tmp_path, three_files):
    items = batch.plan_items(three_files[:1], tmp_path, None, "mp3")
    runner = batch.BatchRunner(items, EffectSettings(speed=0.6))
    runner.cancel()
    runner.run()
    assert items[0].status == batch.CANCELLED
    assert not items[0].destination.exists()


@needs_ffmpeg
def test_each_file_keeps_its_own_tags(tmp_path, make_tone):
    a = make_tone(tmp_path / "a.mp3")
    b = make_tone(tmp_path / "b.mp3")
    metadata.write_tags(a, metadata.Tags(title="Alpha", artist="One"))
    metadata.write_tags(b, metadata.Tags(title="Beta", artist="Two"))

    items = batch.plan_items([a, b], tmp_path / "out", None, "mp3")
    (tmp_path / "out").mkdir()
    batch.BatchRunner(items, EffectSettings(speed=1.2)).run()

    assert metadata.read_tags(items[0].destination).title == "Alpha"
    assert metadata.read_tags(items[1].destination).title == "Beta"


@needs_ffmpeg
def test_tag_overrides_apply_to_every_file(tmp_path, make_tone):
    a = make_tone(tmp_path / "a.mp3")
    metadata.write_tags(a, metadata.Tags(title="Alpha", artist="One"))
    items = batch.plan_items([a], tmp_path, "Phonk", "mp3")
    batch.BatchRunner(
        items, EffectSettings(), tag_overrides={"title": "Custom", "artist": None}
    ).run()
    tags = metadata.read_tags(items[0].destination)
    assert tags.title == "Custom"
    assert tags.artist == "One"  # None means "leave as is"


def test_counts_start_all_pending(tmp_path):
    items = batch.plan_items([tmp_path / "a.mp3", tmp_path / "b.mp3"], None, None, "mp3")
    counts = batch.BatchRunner(items, EffectSettings()).counts()
    assert counts[batch.PENDING] == 2
    assert sum(counts.values()) == 2


def test_short_error_keeps_only_the_last_line():
    exc = RuntimeError("banner\nnoise\n\nreal problem here\n")
    assert batch._short_error(exc) == "real problem here"
    assert batch._short_error(RuntimeError("")) == "RuntimeError"
