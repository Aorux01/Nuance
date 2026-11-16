"""Window-level tests for batch mode and video export (MagicMock page)."""

import asyncio
import json
import subprocess
from unittest.mock import MagicMock

import pytest

from nuance.audio import batch, ffmpeg
from nuance.audio.effects import EffectSettings
from nuance.ui import app
from tests.conftest import needs_ffmpeg


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("NUANCE_CONFIG_DIR", str(tmp_path / "cfg"))
    page = MagicMock()
    page.services = []
    return app.NuanceApp(page)


def _pick_folder(window, folder):
    async def get_directory_path(**kwargs):
        return str(folder)

    window.save_picker.get_directory_path = get_directory_path


def _pick_save(window, dest):
    async def save_file(**kwargs):
        return str(dest)

    window.save_picker.save_file = save_file


# -- batch list ---------------------------------------------------------------------


def test_setting_a_batch_shows_one_row_per_file(window, tmp_path):
    paths = [tmp_path / f"{n}.mp3" for n in "abc"]
    window._set_batch(paths)
    assert window.batch_box.visible
    assert len(window.batch_list.controls) == 3
    assert len(window._batch_rows) == 3
    assert "3 files" in window.batch_title.value
    assert window.export_button.text == "Export 3 files (MP3)"


def test_clearing_the_batch_restores_single_file_mode(window, tmp_path):
    window._set_batch([tmp_path / "a.mp3", tmp_path / "b.mp3"])
    window._set_batch([])
    assert not window.batch_box.visible
    assert window.batch_list.controls == []
    assert window.export_button.text == "Export MP3"


def test_batch_list_scrolls_instead_of_growing_forever(window, tmp_path):
    window._set_batch([tmp_path / f"{i}.mp3" for i in range(40)])
    assert window.batch_list.height <= 6 * 30


def test_row_status_follows_the_item(window, tmp_path):
    window._set_batch([tmp_path / "a.mp3", tmp_path / "b.mp3"])
    item = batch.BatchItem(tmp_path / "a.mp3", tmp_path / "o.mp3")

    item.status = batch.RUNNING
    window._on_batch_status(0, item)
    assert window._batch_rows[0][1].value == "Processing"

    item.status = batch.DONE
    window._on_batch_status(0, item)
    bar, text = window._batch_rows[0]
    assert text.value == "Done" and bar.value == 1.0

    item.status, item.error = batch.FAILED, "bad file"
    window._on_batch_status(1, item)
    assert "bad file" in window._batch_rows[1][1].value


def test_overall_progress_blends_finished_files_and_the_current_one(window, tmp_path):
    items = [batch.BatchItem(tmp_path / f"{i}.mp3", tmp_path / f"o{i}.mp3") for i in range(4)]
    window._set_batch([i.source for i in items])
    window._batch_runner = batch.BatchRunner(items, EffectSettings())
    items[0].status = batch.DONE
    items[1].status = batch.DONE
    window._on_batch_progress(2, 0.5)
    assert window.progress.value == pytest.approx((2 + 0.5) / 4)
    assert window._batch_rows[2][0].value == 0.5


def test_stray_progress_after_the_run_is_ignored(window):
    window._batch_runner = None
    window._on_batch_progress(0, 0.5)  # must not raise


# -- video options -------------------------------------------------------------------------


def test_video_options_only_show_for_video_formats(window):
    assert not window.video_options.visible
    window.format_dropdown.value = "mp4"
    window._on_format_change(None)
    assert window.video_options.visible
    assert window.export_button.text == "Export MP4"
    window.format_dropdown.value = "flac"
    window._on_format_change(None)
    assert not window.video_options.visible


def test_format_menu_offers_video(window):
    keys = [o.key for o in window.format_dropdown.options]
    assert "mp4" in keys and "mkv" in keys and "mp3" in keys


def test_video_spec_only_for_video_formats(window, tmp_path):
    assert window._video_spec("mp3") is None
    assert window._video_spec("mp4").image is None
    window.video_image = tmp_path / "pic.png"
    assert window._video_spec("mkv").image == tmp_path / "pic.png"


def test_picking_and_clearing_a_picture(window, tmp_path):
    pic = tmp_path / "pic.png"

    async def pick_files(**kwargs):
        return [MagicMock(path=str(pic))]

    window.image_picker.pick_files = pick_files
    asyncio.run(window._on_pick_video_image(None))
    assert window.video_image == pic
    assert window.video_image_label.value == "pic.png"
    assert window.clear_video_image_button.visible

    asyncio.run(window._on_clear_video_image(None))
    assert window.video_image is None
    assert not window.clear_video_image_button.visible


def test_suggested_name_follows_the_naming_rules(window, tmp_path):
    window.source_path = tmp_path / "song.mp3"
    window.preset_dropdown.value = "Slowed + Reverb"
    window._on_preset_change(None)
    assert window._suggest_output_name("mp4") == "song_slowed_reverb.mp4"
    window.speed_slider.on_change(MagicMock(control=MagicMock(value=1.1)))
    assert window._suggest_output_name("mp3") == "song_nuance.mp3"


# -- exports through the window (real FFmpeg) --------------------------------------------


@needs_ffmpeg
def test_batch_export_through_the_window(window, tmp_path, make_tone):
    sources = [make_tone(tmp_path / f"t{i}.wav", seconds=1.5) for i in range(3)]
    out = tmp_path / "out"
    out.mkdir()
    window.source_path = sources[0]
    window.preset_dropdown.value = "Sped Up"
    window._on_preset_change(None)
    window._set_batch(sources)
    _pick_folder(window, out)

    asyncio.run(window._on_export_click(None))

    assert sorted(p.name for p in out.iterdir()) == [
        "t0_sped_up.mp3", "t1_sped_up.mp3", "t2_sped_up.mp3",
    ]
    assert all(text.value == "Done" for _, text in window._batch_rows)
    assert window.status_label.value == "3 done"
    assert not window._export_busy
    assert window.export_button.disabled is False


@needs_ffmpeg
def test_batch_with_a_bad_file_reports_it_and_finishes_the_rest(window, tmp_path, make_tone):
    good = make_tone(tmp_path / "good.wav", seconds=1.5)
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"nope")
    out = tmp_path / "out"
    out.mkdir()
    window.source_path = good
    window._set_batch([bad, good])
    _pick_folder(window, out)

    asyncio.run(window._on_export_click(None))

    assert window._batch_rows[0][1].value.startswith("Failed")
    assert window._batch_rows[1][1].value == "Done"
    assert window.status_label.value == "1 done, 1 failed"
    assert [p.name for p in out.iterdir()] == ["good_nuance.mp3"]


@needs_ffmpeg
def test_cancelling_a_batch_stops_it(window, tmp_path, make_tone):
    sources = [make_tone(tmp_path / f"t{i}.wav", seconds=1.5) for i in range(3)]
    out = tmp_path / "out"
    out.mkdir()
    window.source_path = sources[0]
    window._set_batch(sources)
    _pick_folder(window, out)

    original = window._on_batch_status

    def cancel_on_first_done(index, item):
        original(index, item)
        if item.status == batch.DONE:
            asyncio.run(window._on_cancel_export(None))

    window._on_batch_status = cancel_on_first_done
    asyncio.run(window._on_export_click(None))

    assert [t.value for _, t in window._batch_rows] == ["Done", "Cancelled", "Cancelled"]
    assert window.status_label.value == "1 done, 2 cancelled"


@needs_ffmpeg
def test_single_video_export_through_the_window(window, tmp_path, make_tone, cover_jpg):
    src = make_tone(tmp_path / "s.wav", seconds=2)
    window.source_path = src
    window.format_dropdown.value = "mp4"
    window._on_format_change(None)
    window.video_image = cover_jpg
    dest = tmp_path / "clip.mp4"
    _pick_save(window, dest)

    asyncio.run(window._on_export_click(None))

    info = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(dest)],
        capture_output=True, text=True, check=True,
    ).stdout)
    assert {s["codec_type"] for s in info["streams"]} == {"audio", "video"}
    assert ffmpeg.probe(dest).duration == pytest.approx(2.0, abs=0.3)
    assert window.status_label.value == "Saved to clip.mp4"
