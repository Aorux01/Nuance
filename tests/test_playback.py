"""Tests for in-app playback: the ffplay wrapper, the interactive waveform and the
A/B logic in the window. No audio is ever played: ffplay is replaced by a fake.
"""

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nuance.audio import ffmpeg, player
from nuance.ui import app
from nuance.ui.waveform_view import WaveformView, seconds_to_x, x_to_seconds

# -- player ---------------------------------------------------------------------


def test_build_command_plain():
    cmd = player.build_command("ffplay", Path("a.mp3"))
    assert cmd[0] == "ffplay"
    assert "-nodisp" in cmd and "-autoexit" in cmd
    assert "-ss" not in cmd and "-t" not in cmd
    assert cmd[-1] == "a.mp3"


def test_build_command_excerpt():
    cmd = player.build_command("ffplay", Path("a.mp3"), start=12.5, duration=15)
    assert cmd[cmd.index("-ss") + 1] == "12.500"
    assert cmd[cmd.index("-t") + 1] == "15.000"
    # Both seek options must come before the input file.
    assert cmd.index("-ss") < len(cmd) - 1


def test_map_fraction_clamps_and_guards_zero_length():
    assert player.map_fraction(5, 10) == 0.5
    assert player.map_fraction(-1, 10) == 0.0
    assert player.map_fraction(99, 10) == 1.0
    assert player.map_fraction(3, 0) == 0.0


def test_is_available_follows_the_binary(monkeypatch):
    monkeypatch.setattr(ffmpeg, "find_binary", lambda name: None)
    assert player.is_available() is False
    monkeypatch.setattr(ffmpeg, "find_binary", lambda name: "/bin/ffplay")
    assert player.is_available() is True


class _FakeProcess:
    def __init__(self):
        self.terminated = False
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self._alive = False


@pytest.fixture
def fake_popen(monkeypatch):
    spawned = []

    def popen(cmd, **kwargs):
        proc = _FakeProcess()
        proc.cmd = cmd
        spawned.append(proc)
        return proc

    monkeypatch.setattr(ffmpeg, "find_binary", lambda name: "/bin/ffplay")
    monkeypatch.setattr(subprocess, "Popen", popen)
    return spawned


def test_play_starts_and_stop_terminates(fake_popen):
    p = player.Player()
    assert not p.is_playing
    p.play(Path("x.mp3"), start=3.0, duration=10.0)
    assert p.is_playing
    assert fake_popen[0].cmd[-1] == "x.mp3"
    p.stop()
    assert fake_popen[0].terminated
    assert not p.is_playing


def test_play_replaces_the_previous_clip(fake_popen):
    p = player.Player()
    p.play(Path("a.mp3"))
    p.play(Path("b.mp3"))
    assert fake_popen[0].terminated
    assert not fake_popen[1].terminated
    assert p.is_playing


def test_position_starts_at_the_offset_and_is_capped(fake_popen, monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(player.time, "monotonic", lambda: clock["t"])
    p = player.Player()
    p.play(Path("a.mp3"), start=20.0, duration=5.0)
    assert p.position() == pytest.approx(20.0)
    clock["t"] += 2.0
    assert p.position() == pytest.approx(22.0)
    clock["t"] += 60.0
    assert p.position() == pytest.approx(25.0)  # capped at the excerpt end


def test_play_without_ffplay_raises(monkeypatch):
    monkeypatch.setattr(ffmpeg, "find_binary", lambda name: None)
    with pytest.raises(ffmpeg.FFmpegNotFoundError):
        player.Player().play(Path("a.mp3"))


def test_stop_when_idle_is_harmless():
    player.Player().stop()


# -- waveform view -------------------------------------------------------------


def test_x_to_seconds_maps_and_clamps():
    assert x_to_seconds(500, 1000, 200.0) == pytest.approx(100.0)
    assert x_to_seconds(-50, 1000, 200.0) == 0.0
    assert x_to_seconds(5000, 1000, 200.0) == pytest.approx(200.0)


def test_x_to_seconds_leaves_room_for_a_minimum_clip():
    assert x_to_seconds(1000, 1000, 200.0, min_clip=5.0) == pytest.approx(195.0)
    # A track shorter than the clip still starts at zero, never negative.
    assert x_to_seconds(900, 1000, 3.0, min_clip=5.0) == 0.0


def test_x_to_seconds_unknown_size_is_zero():
    assert x_to_seconds(10, 0, 200.0) == 0.0
    assert x_to_seconds(10, 1000, 0.0) == 0.0


def test_seconds_to_x_round_trips():
    assert seconds_to_x(50, 800, 200.0) == pytest.approx(200.0)
    assert seconds_to_x(999, 800, 200.0) == pytest.approx(800.0)
    assert seconds_to_x(5, 0, 200.0) == 0.0


def _sized_view(width=800.0, duration=200.0):
    seen = []
    view = WaveformView(seen.append)
    view._on_resize(MagicMock(width=width))
    view.set_source(duration)
    return view, seen


def test_view_places_marker_and_band():
    view, _ = _sized_view()
    view.set_length(15.0)
    view.set_start(50.0)
    assert view.band.left == pytest.approx(200.0)
    assert view.band.width == pytest.approx(60.0)
    assert view.marker.left == pytest.approx(199.0)


def test_view_band_is_clipped_at_the_track_end():
    view, _ = _sized_view()
    view.set_length(30.0)
    view.set_start(190.0)
    assert view.band.left + view.band.width == pytest.approx(800.0)


def test_view_reports_clicks_in_seconds():
    view, seen = _sized_view()
    view._on_tap(MagicMock(local_position=MagicMock(x=400.0)))
    view._on_drag(MagicMock(local_position=MagicMock(x=200.0)))
    assert seen == [pytest.approx(100.0), pytest.approx(50.0)]


def test_view_ignores_clicks_before_a_track_is_loaded():
    seen = []
    view = WaveformView(seen.append)
    view._on_resize(MagicMock(width=800.0))
    view._on_tap(MagicMock(local_position=MagicMock(x=400.0)))
    assert seen == []


def test_view_playhead_shows_moves_and_hides():
    view, _ = _sized_view()
    view.set_playhead(100.0)
    assert view.playhead.visible
    assert view.playhead.left == pytest.approx(399.0)
    view.set_playhead(None)
    assert not view.playhead.visible


def test_view_is_hidden_until_an_image_arrives():
    view, _ = _sized_view()
    assert not view.control.visible
    view.set_image("data:image/svg+xml;base64,AAAA")
    assert view.control.visible
    view.set_source(10.0)  # a new track hides the old picture again
    assert not view.control.visible


def test_resize_asks_for_a_refresh():
    calls = []
    view = WaveformView(lambda s: None, request_update=lambda: calls.append(1))
    view._on_resize(MagicMock(width=500.0))
    assert calls == [1]


# -- A/B in the window ---------------------------------------------------------


class FakePlayer:
    def __init__(self):
        self.calls = []
        self.playing = False
        self.pos = 0.0

    @property
    def is_playing(self):
        return self.playing

    def play(self, path, start=0.0, duration=0.0):
        self.calls.append((Path(path).name, round(start, 3), round(duration, 3)))
        self.playing = True
        self.pos = start

    def stop(self):
        self.playing = False

    def position(self):
        return self.pos


@pytest.fixture
def ab_app(tmp_path, monkeypatch):
    monkeypatch.setenv("NUANCE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(player, "is_available", lambda: True)
    page = MagicMock()
    page.services = []
    window = app.NuanceApp(page)
    window._player = FakePlayer()
    window.source_path = tmp_path / "song.mp3"
    window.source_info = ffmpeg.AudioInfo(
        duration=200.0, sample_rate=44100, channels=2, bitrate=0
    )
    window.waveform.set_source(200.0)
    window._set_preview_start(40.0)
    window.preview_length = 20.0
    window._preview_path = tmp_path / "clip.mp3"
    window._clip_len = 10.0  # e.g. a 2x speed-up of a 20 s excerpt
    return window


def test_original_plays_the_source_excerpt(ab_app):
    asyncio.run(ab_app._on_original_click(None))
    assert ab_app._player.calls == [("song.mp3", 40.0, 20.0)]
    assert ab_app._play_mode == "original"
    assert ab_app.stop_button.visible


def test_switching_to_original_keeps_the_listening_position(ab_app):
    ab_app._start_playback("processed", 0.0)
    ab_app._player.pos = 5.0  # halfway through the 10 s processed clip
    asyncio.run(ab_app._on_original_click(None))
    name, start, duration = ab_app._player.calls[-1]
    assert name == "song.mp3"
    assert start == pytest.approx(50.0)     # 40 + half of the 20 s excerpt
    assert duration == pytest.approx(10.0)  # the other half remains


def test_switching_to_processed_keeps_the_listening_position(ab_app, monkeypatch):
    async def fake_render():
        return ab_app._preview_path

    monkeypatch.setattr(ab_app, "_render_processed", fake_render)
    ab_app._start_playback("original", 0.0)
    ab_app._player.pos = 40.0 + 15.0  # three quarters through the excerpt
    fraction = ab_app._current_fraction()
    assert fraction == pytest.approx(0.75)

    asyncio.run(ab_app._play_processed(fraction))
    name, start, _ = ab_app._player.calls[-1]
    assert name == "clip.mp3"
    assert start == pytest.approx(7.5)


def test_original_click_while_original_plays_restarts_it(ab_app):
    asyncio.run(ab_app._on_original_click(None))
    ab_app._player.pos = 48.0
    asyncio.run(ab_app._on_original_click(None))
    assert ab_app._player.calls[-1] == ("song.mp3", 40.0, 20.0)


def test_stop_clears_playback_state(ab_app):
    asyncio.run(ab_app._on_original_click(None))
    asyncio.run(ab_app._on_stop_click(None))
    assert ab_app._play_mode is None
    assert not ab_app.stop_button.visible
    assert not ab_app.waveform.playhead.visible


def test_active_side_of_the_ab_is_highlighted(ab_app):
    asyncio.run(ab_app._on_original_click(None))
    assert ab_app.original_button.style is not None
    assert ab_app.preview_button.style is None
    ab_app._stop_playback()
    assert ab_app.original_button.style is None


def test_playhead_loop_ends_when_playback_finishes(ab_app):
    ab_app._start_playback("original", 0.0)
    token = ab_app._playhead_token
    ab_app._player.playing = False  # ffplay exited by itself
    asyncio.run(ab_app._playhead_loop(token))
    assert ab_app._play_mode is None
    assert not ab_app.stop_button.visible


def test_replaced_playhead_loop_does_not_clobber_the_new_one(ab_app):
    ab_app._start_playback("original", 0.0)
    old = ab_app._playhead_token
    ab_app._start_playback("processed", 0.0)  # bumps the token
    ab_app._player.playing = False
    asyncio.run(ab_app._playhead_loop(old))
    assert ab_app._play_mode == "processed"  # the stale loop left it alone


def test_waveform_click_moves_the_preview_start(ab_app):
    ab_app.waveform._on_resize(MagicMock(width=800.0))
    ab_app._on_wave_seek(75.0)
    assert ab_app.preview_start == 75.0
    assert ab_app.preview_start_label.value == "From 1:15"
    assert ab_app.waveform.marker.left > 0
    assert ab_app._preview_path is None  # the old render is stale now


def test_excerpt_length_stops_at_the_end_of_the_track(ab_app):
    ab_app._set_preview_start(190.0)
    assert ab_app._excerpt_len() == pytest.approx(10.0)


def test_loading_a_file_stops_playback(ab_app, monkeypatch):
    asyncio.run(ab_app._on_original_click(None))
    monkeypatch.setattr(
        ffmpeg, "probe",
        lambda p: ffmpeg.AudioInfo(duration=90.0, sample_rate=44100, channels=2, bitrate=0),
    )
    monkeypatch.setattr(app.metadata, "read_tags", lambda p: app.metadata.Tags())
    assert ab_app._load_source(Path("other.mp3"))
    assert ab_app._play_mode is None
    assert ab_app.preview_start == 0.0
